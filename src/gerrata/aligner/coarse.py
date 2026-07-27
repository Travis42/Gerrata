"""Coarse alignment of PG text paragraphs to scan OCR text using LCS.

Strategy:
1. Break PG text and scan OCR into sentences/chunks
2. Build sentence-level LCS to find the longest matching subsequence
3. Map PG text regions to scan text regions based on the LCS
4. Produce a list of Alignment objects mapping PG text offsets to scan pages
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

from gerrata.models import Alignment, AlignmentMethod

logger = logging.getLogger(__name__)

# Sentence-ending punctuation
SENTENCE_END = re.compile(r"[.!?;]\s+")


def normalize_text(text: str) -> str:
    """Normalize text for comparison: lowercase, strip whitespace, NFC normalize."""
    text = unicodedata.normalize("NFC", text)
    text = text.lower()
    # Remove extra whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Remove punctuation for fuzzy matching
    text = re.sub(r"[^\w\s]", "", text)
    return text


def split_sentences(text: str, min_length: int = 20) -> list[str]:
    """Split text into sentence-level chunks for LCS matching."""
    # First try splitting on sentence boundaries
    parts = SENTENCE_END.split(text)
    # Filter too-short parts and strip
    chunks = [p.strip() for p in parts if len(p.strip()) >= min_length]
    # If only one chunk or no chunks, fall back to splitting on newlines
    if len(chunks) <= 1:
        lines = text.split("\n")
        line_chunks = [l.strip() for l in lines if len(l.strip()) >= min_length]
        if len(line_chunks) > 1:
            chunks = line_chunks
    return chunks


def lcs_lengths(a: list[str], b: list[str]) -> list[list[int]]:
    """Compute LCS length table for two lists of strings."""
    m, n = len(a), len(b)
    # Use 1-indexed for cleaner backtracking
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            elif dp[i - 1][j] >= dp[i][j - 1]:
                dp[i][j] = dp[i - 1][j]
            else:
                dp[i][j] = dp[i][j - 1]
    return dp


def backtrack_lcs(dp: list[list[int]], a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """Backtrack through LCS table to find matching indices.

    Returns list of (index_in_a, index_in_b) pairs.
    """
    matches: list[tuple[int, int]] = []
    i, j = len(a), len(b)
    while i > 0 and j > 0:
        if a[i - 1] == b[j - 1]:
            matches.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    matches.reverse()
    return matches


class CoarseAligner:
    """Align PG text to scan OCR text using LCS on sentence chunks."""

    def __init__(
        self,
        match_threshold: float = 0.7,
        min_chunk_length: int = 15,
    ):
        self.match_threshold = match_threshold
        self.min_chunk_length = min_chunk_length

    def align(
        self,
        pg_text: str,
        pg_paragraphs: list[str],
        scan_ocr_text: str,
        scan_pages: Optional[list] = None,
    ) -> list[Alignment]:
        """Align PG text to scan OCR text.

        Args:
            pg_text: Full PG body text.
            pg_paragraphs: PG paragraphs (for offset tracking).
            scan_ocr_text: Full scan OCR text.
            scan_pages: Optional list of scan pages for per-page alignment.

        Returns:
            List of Alignment objects mapping PG text ranges to scan text positions.
        """
        # Normalize texts
        pg_normalized = normalize_text(pg_text)
        scan_normalized = normalize_text(scan_ocr_text)

        # Split into chunks
        pg_chunks = split_sentences(pg_text, min_length=self.min_chunk_length)
        scan_chunks = split_sentences(scan_ocr_text, min_length=self.min_chunk_length)

        # Normalize chunks
        pg_norm_chunks = [normalize_text(c) for c in pg_chunks]
        scan_norm_chunks = [normalize_text(c) for c in scan_chunks]

        if not pg_chunks or not scan_chunks:
            logger.warning("Empty chunks - cannot align")
            return []

        # Build LCS
        dp = lcs_lengths(pg_norm_chunks, scan_norm_chunks)
        matches = backtrack_lcs(dp, pg_norm_chunks, scan_norm_chunks)

        if not matches:
            logger.warning("No LCS matches found between PG text and scan OCR")
            return []

        # Convert matches to character-offset alignments
        # Map chunk indices back to character offsets in pg_text and scan_ocr_text
        alignments = self._matches_to_alignments(
            matches, pg_chunks, scan_chunks,
            pg_text, scan_ocr_text,
            scan_pages,
        )

        return alignments

    def _matches_to_alignments(
        self,
        matches: list[tuple[int, int]],
        pg_chunks: list[str],
        scan_chunks: list[str],
        pg_text: str,
        scan_text: str,
        scan_pages: Optional[list],
    ) -> list[Alignment]:
        """Convert LCS matches to Alignment objects.

        Each alignment represents a passage of PG text matched to a region
        of scan text.
        """
        alignments: list[Alignment] = []

        # Build chunk offset maps
        pg_offsets = self._build_chunk_offsets(pg_chunks, pg_text)
        scan_offsets = self._build_chunk_offsets(scan_chunks, scan_text)

        # Group consecutive matches into passage alignments
        if not matches:
            return []

        # Create alignments from matched chunks
        groups = self._group_consecutive_matches(matches)

        for group in groups:
            pg_start_idx, scan_start_idx = group[0]
            pg_end_idx, scan_end_idx = group[-1]

            # Get character offsets
            pg_start_char = pg_offsets[pg_start_idx]
            pg_end_char = pg_offsets[pg_end_idx + 1] if pg_end_idx + 1 < len(pg_offsets) else len(pg_text)
            scan_start_char = scan_offsets[scan_start_idx]
            scan_end_char = scan_offsets[scan_end_idx + 1] if scan_end_idx + 1 < len(scan_offsets) else len(scan_text)

            # Determine scan page
            scan_page = 0
            if scan_pages:
                scan_page = self._find_scan_page(scan_start_char, scan_text, scan_pages)

            # Compute confidence based on match quality
            confidence = min(1.0, len(group) / max(1, (pg_end_idx - pg_start_idx + 1)))

            alignments.append(Alignment(
                pg_start=pg_start_char,
                pg_end=pg_end_char,
                scan_page=scan_page,
                confidence=confidence,
                method=AlignmentMethod.LCS,
            ))

        return alignments

    def _build_chunk_offsets(self, chunks: list[str], full_text: str) -> list[int]:
        """Build a list of character offsets for each chunk in the full text.

        Returns offsets[len(chunks)] with the last element being len(full_text).
        """
        offsets: list[int] = []
        pos = 0
        for chunk in chunks:
            idx = full_text.find(chunk[:50], pos)  # Match first 50 chars to handle normalization
            if idx == -1:
                idx = pos
            offsets.append(idx)
            pos = idx + len(chunk)
        offsets.append(len(full_text))
        return offsets

    def _group_consecutive_matches(
        self, matches: list[tuple[int, int]]
    ) -> list[list[tuple[int, int]]]:
        """Group consecutive or near-consecutive matches.

        Two matches are "consecutive" if both indices differ by at most 5
        (allowing for skipped chunks).
        """
        if not matches:
            return []

        groups: list[list[tuple[int, int]]] = [[matches[0]]]

        for i in range(1, len(matches)):
            pg_gap = matches[i][0] - matches[i - 1][0]
            scan_gap = matches[i][1] - matches[i - 1][1]
            if pg_gap <= 5 and scan_gap <= 5:
                groups[-1].append(matches[i])
            else:
                groups.append([matches[i]])

        return groups

    def _find_scan_page(self, scan_char_offset: int, scan_text: str, scan_pages: list) -> int:
        """Find which scan page a character offset falls in."""
        if not scan_pages:
            return 0

        # Estimate based on relative position
        total_len = len(scan_text)
        if total_len == 0:
            return 0

        relative_pos = scan_char_offset / total_len
        estimated_page = int(relative_pos * len(scan_pages))
        return max(0, min(estimated_page, len(scan_pages) - 1))

    def alignment_confidence(self, alignments: list[Alignment], pg_text_len: int) -> float:
        """Calculate overall alignment confidence.

        Ratio of PG text that is covered by alignments.
        """
        if pg_text_len == 0:
            return 0.0

        covered = 0
        for a in alignments:
            covered += a.pg_end - a.pg_start

        return min(1.0, covered / pg_text_len)
