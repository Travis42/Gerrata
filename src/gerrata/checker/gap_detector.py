"""Detect coverage gaps where scan text has no corresponding PG alignment.

When PG is missing text (a sentence, paragraph, or passage) that exists in the
scan, the coarse aligner simply skips over it — no alignment is created for
that region, and the diff checker never sees it. This module detects those gaps
by checking which scan page text is not covered by any alignment, then verifies
each gap against the full PG text to avoid false positives from alignment failures.

Two strategies:
1. **Uncovered pages**: Scan pages with text but no alignment at all.
2. **Partial coverage**: Within aligned pages, portions of scan text that don't
   match any PG passage.

Both strategies include a **PG text verification step**: before reporting a gap,
the gap text is fuzzy-searched against the full PG body. If a strong match is
found, the gap is an alignment failure, not real missing content, and is
filtered out rather than discarded — it's included in the JSON data with a
lower confidence score.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from gerrata.models import CandidateError, ErrorCategory

logger = logging.getLogger(__name__)

# Minimum gap size (in words) to detect. Set low to catch individual sentences.
MIN_GAP_WORDS = 5

# Threshold above which gaps are surfaced in the human-readable report.
# Gaps below this are still captured in JSON data.
REPORT_MIN_WORDS_UNCOVERED = 50
REPORT_MIN_WORDS_PARTIAL = 30
REPORT_MIN_COVERAGE_RATIO = 0.6

# Minimum similarity ratio to consider a gap text "found" in PG.
# Below this, the gap text is genuinely absent from PG.
PG_FUZZY_MATCH_THRESHOLD = 0.6

# When searching for gap text in PG, use a sliding window of this many words.
PG_SEARCH_WINDOW_WORDS = 25


@dataclass
class CoverageGap:
    """A coverage gap with full metadata for JSON reporting."""

    page: int
    strategy: str  # "uncovered" or "partial"
    word_count: int
    scan_text_preview: str
    coverage_ratio: float = 0.0  # 0.0 for uncovered, actual ratio for partial
    pg_verified: bool = False  # True if fuzzy search confirmed text absent from PG
    pg_match_ratio: float = 0.0  # Best similarity ratio found in PG
    non_content: bool = False  # True if filtered as paratext/artifact
    confidence: str = "low"  # "high", "medium", or "low"

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "strategy": self.strategy,
            "word_count": self.word_count,
            "scan_text_preview": self.scan_text_preview,
            "coverage_ratio": self.coverage_ratio,
            "pg_verified": self.pg_verified,
            "pg_match_ratio": round(self.pg_match_ratio, 3),
            "non_content": self.non_content,
            "confidence": self.confidence,
        }


def _page_text(scan_page) -> str:
    """Get the best available text for a scan page."""
    vt = getattr(scan_page, "vision_text", "") or ""
    ocr = getattr(scan_page, "ocr_text", "") or ""
    return vt if vt else ocr


def _normalize(text: str) -> str:
    """Normalize text for fuzzy matching: lowercase, collapse whitespace, strip punctuation."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _strip_page_header(text: str, headers: list[str] | None = None) -> str:
    """Remove page header lines from scan text.

    Headers are book title/author repeated on each page. If no explicit headers
    are provided, uses a generic heuristic (first line if it looks like a title).
    """
    lines = text.split("\n")
    check_headers = headers or []

    if lines and not check_headers:
        first = lines[0].strip()
        # Heuristic: strip first line only if it's very short (<40 chars)
        # and looks like a running header (not a sentence)
        if len(first) < 40 and not first.endswith(('.', '!', '?')):
            return "\n".join(lines[1:]).strip()

    for i in range(min(2, len(lines))):
        line = lines[i].strip().lower()
        for header in check_headers:
            if header.lower() in line or line.startswith(header.lower()[:15]):
                return "\n".join(lines[i + 1:]).strip()
    return text


def _is_non_content(text: str) -> bool:
    """Check if text is non-content (paratext, artifacts, etc.).

    Kept generic — no book-specific patterns. Covers:
    - Very short text (< 10 words)
    - Table of contents (dotted leaders)
    - Vision model artifacts (color calibration, empty page responses)
    """
    text_stripped = text.strip()

    if len(text_stripped.split()) < 10:
        return True

    # Table of contents
    if re.search(r"^\s*contents\s*[\n\r]", text_stripped, re.IGNORECASE):
        return True

    # Dotted leaders (TOC entries) — 2+ lines with dots (consecutive or spaced)
    dot_lines = sum(
        1 for line in text_stripped.split("\n")
        if re.search(r"(\.\s*){4,}", line) or re.search(r"\.{4,}", line)
    )
    if dot_lines >= 2:
        return True

    # Single long line with dense dotted leaders (single-line TOC)
    if re.search(r"(\.\s*){8,}", text_stripped):
        return True

    # Vision model artifacts
    if "color calibration" in text_stripped.lower():
        return True
    if "does not contain any text" in text_stripped.lower():
        return True

    return False


def _gap_text_exists_in_pg(gap_text: str, pg_text: str) -> tuple[bool, float]:
    """Check whether the gap text can be found somewhere in the full PG text.

    This catches false positives where the aligner failed to match text that
    actually exists in PG. Uses an index-based lookup for fast exact matching,
    with a fallback fuzzy search for small PG texts.

    Args:
        gap_text: The scan text that was flagged as a gap.
        pg_text: The full PG body text.

    Returns:
        Tuple of (found: bool, best_ratio: float).
    """
    if not gap_text or not pg_text or len(gap_text.split()) < 5:
        return False, 0.0

    gap_words = gap_text.split()
    window_size = min(PG_SEARCH_WINDOW_WORDS, len(gap_words))

    best_ratio = 0.0
    pg_norm = _normalize(pg_text)
    pg_words = pg_norm.split()

    if len(pg_words) < max(10, window_size):
        return False, 0.0

    # Build index: normalized word sequence -> list of PG positions
    sequence_index: dict[str, list[int]] = {}
    for i in range(len(pg_words) - window_size + 1):
        seq = " ".join(pg_words[i:i + window_size])
        if seq not in sequence_index:
            sequence_index[seq] = []
        sequence_index[seq].append(i)

    # Try windows: start, middle, end of gap text
    window_starts = [0]
    if len(gap_words) > window_size * 2:
        window_starts.append((len(gap_words) - window_size) // 2)
    if len(gap_words) > window_size:
        window_starts.append(len(gap_words) - window_size)

    for start in window_starts:
        window_words = gap_words[start:start + window_size]
        if len(window_words) < 5:
            continue
        window_norm = _normalize(" ".join(window_words))

        # Direct lookup in index
        if window_norm in sequence_index:
            for pg_pos in sequence_index[window_norm]:
                pg_window = " ".join(pg_words[pg_pos:pg_pos + window_size])
                ratio = SequenceMatcher(None, window_norm, pg_window, autojunk=False).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    if best_ratio >= PG_FUZZY_MATCH_THRESHOLD:
                        return True, best_ratio

        # Fuzzy fallback — only for small PG texts (performance guard)
        if len(pg_words) > 5000:
            continue

        step = max(10, window_size)
        for i in range(0, len(pg_words) - window_size + 1, step):
            pg_window = " ".join(pg_words[i:i + window_size])
            ratio = SequenceMatcher(None, window_norm, pg_window, autojunk=False).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                if best_ratio >= PG_FUZZY_MATCH_THRESHOLD:
                    return True, best_ratio

    return best_ratio >= PG_FUZZY_MATCH_THRESHOLD, best_ratio


def _compute_confidence(gap: CoverageGap) -> str:
    """Assign confidence level to a gap.

    High: PG-verified absence + large word count
    Medium: PG-verified absence + smaller word count, or unverified but large
    Low: unverified, or flagged as non-content, or very small
    """
    if gap.non_content:
        return "low"

    if gap.pg_verified:
        if gap.word_count >= 50:
            return "high"
        elif gap.word_count >= 20:
            return "medium"
        else:
            return "low"

    # Not PG-verified (verification skipped or text too short to verify)
    if gap.word_count >= 100:
        return "medium"
    else:
        return "low"


def detect_scan_gaps(
    pg_text: str,
    alignments: list,
    scan_pages: list,
    min_gap_words: int | None = None,
    skip_pg_verification: bool = False,
) -> list[CoverageGap]:
    """Find scan page text that has no corresponding PG alignment.

    Returns ALL gaps as CoverageGap objects with confidence scores.
    Callers can filter by confidence/word count for JSON vs report output.

    Args:
        pg_text: Full PG body text.
        alignments: List of Alignment objects.
        scan_pages: List of ScanPage objects (or any with page_num, vision_text, ocr_text).
        min_gap_words: Minimum word count to detect (default: 5).
        skip_pg_verification: If True, skip PG text verification (for testing/debugging).

    Returns:
        List of CoverageGap objects for every detected gap.
    """
    if not scan_pages:
        return []

    if min_gap_words is None:
        min_gap_words = MIN_GAP_WORDS

    # Build set of covered pages
    covered_pages: set[int] = set()
    for alignment in alignments:
        covered_pages.add(alignment.scan_page)

    gaps: list[CoverageGap] = []

    # --- Strategy 1: Completely uncovered pages ---
    for sp in scan_pages:
        pnum = getattr(sp, "page_num", None)
        if pnum is None or pnum in covered_pages:
            continue

        text = _page_text(sp)
        if not text:
            continue

        text_clean = _strip_page_header(text)
        word_count = len(text_clean.split()) if text_clean else 0

        if word_count < min_gap_words:
            continue

        is_nc = _is_non_content(text_clean)

        # PG verification
        pg_verified = False
        pg_match_ratio = 0.0
        if not skip_pg_verification and not is_nc:
            found, ratio = _gap_text_exists_in_pg(text_clean, pg_text)
            pg_verified = found
            pg_match_ratio = ratio

        gap = CoverageGap(
            page=pnum,
            strategy="uncovered",
            word_count=word_count,
            scan_text_preview=text_clean[:500],
            coverage_ratio=0.0,
            pg_verified=pg_verified,
            pg_match_ratio=pg_match_ratio,
            non_content=is_nc,
        )
        gap.confidence = _compute_confidence(gap)
        gaps.append(gap)

    # --- Strategy 2: Partial coverage within aligned pages ---
    for sp in scan_pages:
        pnum = getattr(sp, "page_num", None)
        if pnum is None or pnum not in covered_pages:
            continue

        page_text_raw = _page_text(sp)
        if not page_text_raw:
            continue

        page_text_clean = _strip_page_header(page_text_raw)
        page_wc = len(page_text_clean.split()) if page_text_clean else 0

        if page_wc < min_gap_words:
            continue

        page_alignments = [a for a in alignments if a.scan_page == pnum]
        if not page_alignments:
            continue

        pg_start = min(a.pg_start for a in page_alignments)
        pg_end = max(a.pg_end for a in page_alignments)
        total_pg_wc = len(pg_text[pg_start:pg_end].split())

        coverage_ratio = total_pg_wc / page_wc if page_wc > 0 else 1.0
        if coverage_ratio >= REPORT_MIN_COVERAGE_RATIO:
            continue

        pg_passage = re.sub(r"\s+", " ", pg_text[pg_start:pg_end].strip())
        page_norm = re.sub(r"\s+", " ", page_text_clean.strip())

        pg_words = pg_passage.split()
        page_words = page_norm.split()

        sm = SequenceMatcher(None, pg_words, page_words, autojunk=False)
        matched_scan_indices: set[int] = set()

        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for idx in range(j1, j2):
                    matched_scan_indices.add(idx)

        # Find contiguous unmatched regions
        regions: list[list[int]] = []
        for i in range(len(page_words)):
            if i not in matched_scan_indices:
                if not regions or regions[-1][1] != i:
                    regions.append([i, i + 1])
                else:
                    regions[-1][1] = i + 1

        for gap_start, gap_end in regions:
            gap_words = page_words[gap_start:gap_end]
            gap_text = " ".join(gap_words)
            word_count = len(gap_words)

            if word_count < min_gap_words:
                continue

            is_nc = _is_non_content(gap_text)

            # PG verification
            pg_verified = False
            pg_match_ratio = 0.0
            if not skip_pg_verification and not is_nc:
                found, ratio = _gap_text_exists_in_pg(gap_text, pg_text)
                pg_verified = found
                pg_match_ratio = ratio

            gap = CoverageGap(
                page=pnum,
                strategy="partial",
                word_count=word_count,
                scan_text_preview=gap_text[:500],
                coverage_ratio=coverage_ratio,
                pg_verified=pg_verified,
                pg_match_ratio=pg_match_ratio,
                non_content=is_nc,
            )
            gap.confidence = _compute_confidence(gap)
            gaps.append(gap)

    return gaps


def filter_for_report(gaps: list[CoverageGap]) -> list[CoverageGap]:
    """Filter gaps to only the most egregious for human-readable reports.

    Criteria:
    - Not flagged as non-content
    - PG verification confirms text absent (or verification not possible)
    - Uncovered: >= 50 words
    - Partial: >= 30 words with coverage < 60%
    - Confidence: medium or high
    """
    return [
        g for g in gaps
        if not g.non_content
        and g.confidence in ("high", "medium")
        and (
            (g.strategy == "uncovered" and g.word_count >= REPORT_MIN_WORDS_UNCOVERED)
            or (g.strategy == "partial" and g.word_count >= REPORT_MIN_WORDS_PARTIAL)
        )
    ]


def gaps_to_candidate_errors(gaps: list[CoverageGap]) -> list[CandidateError]:
    """Convert CoverageGap objects to CandidateError objects for the pipeline.

    Typically used with filter_for_report() to feed only high-confidence gaps
    into the errata pipeline.
    """
    errors: list[CandidateError] = []
    for g in gaps:
        if g.strategy == "uncovered":
            desc = (
                f"Scan page {g.page} has ~{g.word_count} words with no PG alignment "
                f"and no match in PG text — likely missing from PG"
            )
        else:
            desc = (
                f"Scan page {g.page} has ~{g.word_count} words not matched by PG text "
                f"(page coverage: {g.coverage_ratio:.0%}) — text not found in PG"
            )
        errors.append(CandidateError(
            pg_text="(not found in PG)",
            scan_text=g.scan_text_preview,
            pg_offset=0,
            scan_page=g.page,
            diff_description=desc,
            category=ErrorCategory.MISSING_CONTENT,
        ))
    return errors
