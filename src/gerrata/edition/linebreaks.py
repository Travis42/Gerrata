"""Approach B: Line-break fingerprinting for edition matching.

Detects whether PG text preserves original line breaks, builds fingerprints
from both PG text and scan OCR, and compares them to determine edition match.
"""

from __future__ import annotations

import gzip
import io
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional
from xml.etree import ElementTree as ET

import httpx
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


@dataclass
class LineBreakDetection:
    """Result of detecting whether PG text preserves line breaks."""

    preserved: bool
    confidence: float  # 0-1
    column_width: int | None  # Modal line length if preserved
    line_lengths: list[int] = field(default_factory=list)
    rationale: str = ""


@dataclass
class Fingerprint:
    """Line-break fingerprint for a text."""

    lines: list[tuple[int, str, str]] = field(default_factory=list)
    # Each entry: (line_length, last_word, first_word)

    column_width: int = 0
    total_lines: int = 0
    long_lines: int = 0  # Lines >= column_width - 5


@dataclass
class LineBreakMatchResult:
    """Result of comparing line-break fingerprints."""

    pg_preserves_breaks: bool = False
    pg_column_width: int = 0
    scan_column_width: int = 0
    line_endings_matched: int = 0
    line_endings_total: int = 0
    sequence_similarity: float = 0.0
    result: str = "unable_to_determine"  # match, mismatch, unable_to_determine
    confidence: float = 0.0
    rationale: str = ""


class LineBreakFingerprinter:
    """Detect and compare line-break patterns for edition verification."""

    # START/END markers for PG text
    START_MARKER = re.compile(r"\*\*\* START OF (?:THIS )?(?:THE )?PROJECT GUTENBERG", re.IGNORECASE)
    END_MARKER = re.compile(r"\*\*\* END OF (?:THIS )?(?:THE )?PROJECT GUTENBERG", re.IGNORECASE)

    def detect_line_break_preservation(self, raw_pg_text: str) -> LineBreakDetection:
        """Detect whether PG text preserves original line breaks.

        The detection heuristic:
        - Extract body text between START/END markers
        - Work with the RAW text (before paragraph joining) to check if
          individual lines have consistent widths
        - If the top 3 line lengths account for >60% of non-blank lines
          AND are within ±3 chars of each other → PRESERVED
        - Otherwise → REFLOWED

        Args:
            raw_pg_text: The full PG text file content (before any reflowing).

        Returns:
            LineBreakDetection with preservation status.
        """
        # Extract raw body text (before any reflowing)
        start_match = self.START_MARKER.search(raw_pg_text)
        end_match = self.END_MARKER.search(raw_pg_text)

        if not start_match:
            return LineBreakDetection(
                preserved=False,
                confidence=0.0,
                column_width=None,
                rationale="No START marker found in PG text",
            )

        # Get raw lines between markers (not reflowed)
        start = start_match.end()
        nl = raw_pg_text.find("\n", start)
        if nl != -1:
            start = nl + 1
        end = end_match.start() if end_match else len(raw_pg_text)
        raw_body = raw_pg_text[start:end]

        # Get line lengths (skip blank lines)
        all_line_lengths = [len(line) for line in raw_body.splitlines() if line.strip()]

        if not all_line_lengths:
            return LineBreakDetection(
                preserved=False,
                confidence=0.0,
                column_width=None,
                rationale="No non-blank lines found in PG body text",
            )

        # Focus on lines that are long enough to be body text (>40 chars).
        # Short lines (headings, chapter titles, dialogue fragments) dilute
        # the signal. Real typeset body text is consistently near the column
        # width; short lines are formatting artifacts.
        body_line_lengths = [l for l in all_line_lengths if l > 40]

        if not body_line_lengths:
            return LineBreakDetection(
                preserved=False,
                confidence=0.0,
                column_width=None,
                rationale="No long lines found (all lines ≤40 chars) — likely reflowed or poetry",
            )

        # Use up to 2000 lines for a reliable sample
        line_lengths = body_line_lengths[:2000]

        # Compute histogram of line lengths
        counter = Counter(line_lengths)
        top3_lengths = counter.most_common(3)
        modal_length = counter.most_common(1)[0][0]

        # Check concentration within ±5 of the modal length.
        # Real typesetting has a tight cluster around the column width.
        # Using a range rather than just top-3 individual lengths because
        # typesetting varies by ±3-5 chars depending on character widths
        # (e.g., 'm' vs 'i').
        near_modal = sum(
            count for length, count in counter.items()
            if abs(length - modal_length) <= 5
        )
        concentration = near_modal / len(line_lengths) if line_lengths else 0

        # Check spread of top 3 individual lengths
        top3_sorted = sorted([length for length, _ in top3_lengths])
        if len(top3_sorted) >= 2:
            spread = top3_sorted[-1] - top3_sorted[0]
            within_range = spread <= 5  # Widened from 3
        else:
            within_range = True
            spread = 0

        preserved = concentration > 0.50 and within_range

        if preserved:
            confidence = min(1.0, concentration)

            return LineBreakDetection(
                preserved=True,
                confidence=round(confidence, 2),
                column_width=modal_length,
                line_lengths=line_lengths[:200],  # Keep sample
                rationale=(
                    f"Column width: {modal_length} chars, "
                    f"concentration: {concentration:.0%}, "
                    f"spread: {spread}"
                ),
            )
        else:
            return LineBreakDetection(
                preserved=False,
                confidence=round(1.0 - concentration, 2),
                column_width=None,
                line_lengths=line_lengths[:200],
                rationale=(
                    f"Reflowed text: concentration={concentration:.0%}, "
                    f"spread={spread if len(top3_sorted) >= 2 else 0}"
                ),
            )

    def build_pg_fingerprint(self, raw_pg_text: str) -> Fingerprint:
        """Build line-break fingerprint from PG text.

        Only meaningful if line breaks are preserved.

        Args:
            raw_pg_text: Full PG text file content.

        Returns:
            Fingerprint with line-length and last-word entries.
        """
        detection = self.detect_line_break_preservation(raw_pg_text)

        if not detection.preserved:
            return Fingerprint(column_width=0)

        # Extract raw body text
        start_match = self.START_MARKER.search(raw_pg_text)
        end_match = self.END_MARKER.search(raw_pg_text)
        if not start_match:
            return Fingerprint(column_width=0)

        start = start_match.end()
        nl = raw_pg_text.find("\n", start)
        if nl != -1:
            start = nl + 1
        end = end_match.start() if end_match else len(raw_pg_text)
        raw_body = raw_pg_text[start:end]

        col_width = detection.column_width or 0
        threshold = col_width - 5 if col_width >= 10 else 0

        lines: list[tuple[int, str, str]] = []
        for line in raw_body.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            length = len(stripped)
            words = stripped.split()
            last_word = words[-1].rstrip(".,;:!?\"'") if words else ""
            first_word = words[0] if words else ""
            lines.append((length, last_word.lower(), first_word.lower()))

        long_lines = [l for l in lines if l[0] >= threshold]

        return Fingerprint(
            lines=lines,
            column_width=col_width,
            total_lines=len(lines),
            long_lines=len(long_lines),
        )

    async def fetch_ia_ocr_text(self, scan_id: str) -> str | None:
        """Fetch OCR text from IA scan.

        Tries ABBYY XML first, then DJVU text.

        Args:
            scan_id: Internet Archive identifier.

        Returns:
            OCR text string, or None if unavailable.
        """
        metadata_url = f"https://archive.org/metadata/{scan_id}"

        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(metadata_url)
                resp.raise_for_status()
                data = resp.json()

            files = data.get("files", [])

            # Try ABBYY XML (gzipped)
            abbyy_file = None
            djvu_file = None
            for f in files:
                name = f.get("name", "")
                fmt = f.get("format", "")
                if name.endswith("_abbyy.gz") or "abbyy" in name.lower():
                    abbyy_file = f
                elif name.endswith("_djvu.txt") or "djvu" in name.lower() and name.endswith(".txt"):
                    djvu_file = f

            if abbyy_file:
                text = await self._fetch_abbyy(scan_id, abbyy_file["name"])
                if text:
                    return text

            if djvu_file:
                text = await self._fetch_djvu_text(scan_id, djvu_file["name"])
                if text:
                    return text

            logger.info(f"No OCR text available for {scan_id}")
            return None

        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch IA metadata for {scan_id}: {e}")
            return None

    async def _fetch_abbyy(self, scan_id: str, filename: str) -> str | None:
        """Fetch and parse ABBYY OCR XML from IA."""
        url = f"https://archive.org/download/{scan_id}/{filename}"
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                gz_data = resp.content

            # Decompress gzip
            try:
                xml_text = gzip.decompress(gz_data).decode("utf-8", errors="replace")
            except Exception:
                # Maybe it's not gzipped despite the name
                xml_text = gz_data.decode("utf-8", errors="replace")

            # Parse ABBYY XML — extract text from page/block/line elements
            lines = []
            try:
                root = ET.fromstring(xml_text)
                # ABBYY XML namespace
                ns = {"a": "http://www.abbyy.com/FineReader_xml/FineReader10-schema-v1.xml"}
                # Try with namespace
                for line_elem in root.iter("{http://www.abbyy.com/FineReader_xml/FineReader10-schema-v1.xml}line"):
                    text = ""
                    for char_elem in line_elem.iter("{http://www.abbyy.com/FineReader_xml/FineReader10-schema-v1.xml}charParams"):
                        if char_elem.text:
                            text += char_elem.text
                    if text.strip():
                        lines.append(text)
                if not lines:
                    # Try without namespace
                    for line_elem in root.iter("line"):
                        text = "".join(
                            (e.text or "") for e in line_elem.iter("charParams")
                        )
                        if text.strip():
                            lines.append(text)
                if not lines:
                    # Try generic text extraction
                    for line_elem in root.iter("line"):
                        text = "".join(
                            e.text or "" for e in line_elem.iter()
                            if e.text
                        )
                        if text.strip():
                            lines.append(text)
            except ET.ParseError:
                logger.warning(f"Failed to parse ABBYY XML for {scan_id}/{filename}")
                return None

            if lines:
                logger.info(f"Extracted {len(lines)} lines from ABBYY OCR")
                return "\n".join(lines)
            return None

        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch ABBYY file: {e}")
            return None

    async def _fetch_djvu_text(self, scan_id: str, filename: str) -> str | None:
        """Fetch DJVU OCR text from IA."""
        url = f"https://archive.org/download/{scan_id}/{filename}"
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                text = resp.text
            if text.strip():
                logger.info(f"Extracted {len(text.splitlines())} lines from DJVU text")
                return text
            return None
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch DJVU text: {e}")
            return None

    def build_scan_fingerprint(self, ocr_text: str) -> Fingerprint:
        """Build line-break fingerprint from scan OCR text.

        Args:
            ocr_text: Text extracted from scan OCR.

        Returns:
            Fingerprint with line-length and last-word entries.
        """
        lines: list[tuple[int, str, str]] = []

        for line in ocr_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            length = len(stripped)
            words = stripped.split()
            last_word = words[-1].rstrip(".,;:!?\"'") if words else ""
            first_word = words[0] if words else ""
            lines.append((length, last_word.lower(), first_word.lower()))

        if not lines:
            return Fingerprint(column_width=0)

        # Determine modal line length for the scan
        counter = Counter(length for length, _, _ in lines)
        col_width = counter.most_common(1)[0][0] if counter else 0
        threshold = col_width - 5 if col_width >= 10 else 0

        long_lines = [l for l in lines if l[0] >= threshold]

        return Fingerprint(
            lines=lines,
            column_width=col_width,
            total_lines=len(lines),
            long_lines=len(long_lines),
        )

    def compare_line_breaks(
        self,
        pg_fingerprint: Fingerprint,
        scan_fingerprint: Fingerprint,
    ) -> LineBreakMatchResult:
        """Compare line-break fingerprints between PG text and scan.

        Scoring weights:
          - Column width match: 30%
          - Line-ending word match: 50%
          - Sequence similarity: 20%

        Args:
            pg_fingerprint: Fingerprint from PG text.
            scan_fingerprint: Fingerprint from scan OCR.

        Returns:
            LineBreakMatchResult with match assessment.
        """
        if not pg_fingerprint.lines or pg_fingerprint.column_width == 0:
            return LineBreakMatchResult(
                result="unable_to_determine",
                rationale="PG text does not preserve line breaks",
            )

        if not scan_fingerprint.lines or scan_fingerprint.column_width == 0:
            return LineBreakMatchResult(
                result="unable_to_determine",
                rationale="No scan OCR text available for fingerprinting",
            )

        pg_col = pg_fingerprint.column_width
        scan_col = scan_fingerprint.column_width

        # 1. Column width comparison (30% weight)
        col_delta = abs(pg_col - scan_col)
        col_score = max(0.0, 1.0 - col_delta / max(pg_col, scan_col)) if max(pg_col, scan_col) > 0 else 0.0

        # 2. Line-ending word matching (50% weight)
        threshold = pg_col - 5 if pg_col >= 10 else 0
        pg_long_lines = [(l, lw, fw) for l, lw, fw in pg_fingerprint.lines if l >= threshold]
        scan_threshold = scan_col - 5 if scan_col >= 10 else 0
        scan_long_lines = [(l, lw, fw) for l, lw, fw in scan_fingerprint.lines if l >= scan_threshold]

        pg_last_words = [lw for _, lw, _ in pg_long_lines if lw]
        scan_last_words = [lw for _, lw, _ in scan_long_lines if lw]

        # Build a position-aware lookup: for each word in scan, track positions
        scan_word_positions: dict[str, list[int]] = {}
        for idx, lw in enumerate(scan_last_words):
            scan_word_positions.setdefault(lw, []).append(idx)

        # Count matches: a PG last word should appear at approximately the same position
        matched_endings = 0
        for pg_idx, pg_lw in enumerate(pg_last_words):
            if not pg_lw:
                continue
            positions = scan_word_positions.get(pg_lw, [])
            # Check if any scan position is near the PG position
            for scan_pos in positions:
                # Allow ±10% position tolerance
                tolerance = max(5, len(pg_last_words) * 0.10)
                if abs(pg_idx - scan_pos) <= tolerance:
                    matched_endings += 1
                    break  # Count each PG line at most once

        total_pg_long = len(pg_last_words)
        ending_score = matched_endings / total_pg_long if total_pg_long > 0 else 0.0

        # 3. Sequence similarity (20% weight)
        pg_seq = [length for length, _, _ in pg_long_lines]
        scan_seq = [length for length, _, _ in scan_long_lines]

        # Use SequenceMatcher on truncated sequences for performance
        max_compare = min(500, len(pg_seq), len(scan_seq))
        seq_pg = pg_seq[:max_compare]
        seq_scan = scan_seq[:max_compare]

        if seq_pg and seq_scan:
            sm = SequenceMatcher(None, seq_pg, seq_scan)
            seq_score = sm.ratio()
        else:
            seq_score = 0.0

        # Weighted confidence score
        # Column width is the strongest structural signal: different editions
        # have different column widths. Line-ending words overlap even across
        # different editions (same book text), so weight them lower.
        overall_confidence = (
            col_score * 0.50 +
            ending_score * 0.30 +
            seq_score * 0.20
        )

        # Column width veto: large column width difference (>5 chars) is
        # near-definitive evidence of different typesetting/edition.
        # This overrides the weighted score.
        if col_delta > 5:
            result = "mismatch"
            overall_confidence = min(overall_confidence, 0.4)
        elif overall_confidence >= 0.6:
            result = "match"
        elif overall_confidence >= 0.3:
            result = "possible"
        else:
            result = "mismatch"

        # Build rationale
        parts = []
        parts.append(f"Column widths: PG={pg_col}, Scan={scan_col}")
        if col_delta > 0:
            parts.append(f"Δ{col_delta}")
        parts.append(
            f"Line-ending words matched: {matched_endings}/{total_pg_long} "
            f"({ending_score:.1%})"
        )
        parts.append(f"Sequence similarity: {seq_score:.2f}")

        return LineBreakMatchResult(
            pg_preserves_breaks=True,
            pg_column_width=pg_col,
            scan_column_width=scan_col,
            line_endings_matched=matched_endings,
            line_endings_total=total_pg_long,
            sequence_similarity=round(seq_score, 3),
            result=result,
            confidence=round(overall_confidence, 2),
            rationale=", ".join(parts),
        )
