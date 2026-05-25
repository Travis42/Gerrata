"""Detect coverage gaps where scan text has no corresponding PG alignment.

When PG is missing text (a sentence, paragraph, or passage) that exists in the
scan, the coarse aligner simply skips over it — no alignment is created for
that region, and the diff checker never sees it. This module detects those gaps
by checking which scan page text is not covered by any alignment, then verifies
each gap against the full PG text to avoid false positives from alignment failures.

Three strategies:
1. **Uncovered pages**: Scan pages with text but no alignment at all.
2. **Partial coverage**: Within aligned pages, portions of scan text that don't
   match any PG passage.
3. **Content holes**: Within aligned passages, words present in scan but
   completely missing from PG — partial deletions the aligner missed because
   surrounding words still matched.

All strategies include a **PG text verification step**: before reporting a gap,
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
    strategy: str  # "uncovered", "partial", or "content_hole"
    word_count: int
    scan_text_preview: str
    coverage_ratio: float = 0.0  # 0.0 for uncovered, actual ratio for partial
    pg_verified: bool = False  # True if fuzzy search confirmed text absent from PG
    pg_match_ratio: float = 0.0  # Best similarity ratio found in PG
    non_content: bool = False  # True if filtered as paratext/artifact
    confidence: str = "low"  # "high", "medium", or "low"
    # Content hole fields:
    missing_words: str = ""  # The exact words missing from PG (content_hole strategy)
    pg_context_before: str = ""  # PG text immediately before the hole
    pg_context_after: str = ""  # PG text immediately after the hole

    def to_dict(self) -> dict:
        d = {
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
        if self.strategy == "content_hole":
            d["missing_words"] = self.missing_words
            d["pg_context_before"] = self.pg_context_before
            d["pg_context_after"] = self.pg_context_after
        return d


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


def _tokenize_words(text: str) -> list[str]:
    """Tokenize text into lowercase words, stripping punctuation."""
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower())


def _has_sentence_boundary(words: list[str]) -> bool:
    """Check if any word ends with a sentence-ending punctuation marker.

    Looks at the original form (with punctuation) in the word list.
    Since we tokenize to lowercase stripped words, we check for periods
    in context by looking at the original text.
    """
    for w in words:
        for ending in (".", '."', '!"', '?"', '."', '?"', "?", "!"):
            if w.endswith(ending):
                return True
    return False


def _verify_content_hole(missing_words: list[str], full_pg_text: str) -> bool:
    """Check if the missing words are genuinely absent from PG.

    Prevents false positives where the aligner split a paragraph across
    pages and the words appear in the next alignment.
    """
    if not missing_words or not full_pg_text:
        return True

    phrase = " ".join(missing_words).lower()
    pg_lower = full_pg_text.lower()
    return phrase not in pg_lower


def detect_content_holes(
    alignments: list,
    scan_pages: list,
    pg_text: str,
    pg_full_text: str,
    min_words: int = 4,
    min_anchor_words: int = 4,
    min_alignment_confidence: float = 0.65,
) -> list[CoverageGap]:
    """Detect content holes within aligned passages.

    A content hole is a one-sided gap where the scan has words between
    two matched blocks that PG completely lacks — indicating a partial
    deletion within an aligned passage.

    Filtering heuristics to reduce false positives:
    - min_anchor_words: Both the match block before and after the gap must
      be at least this many words. Short anchors indicate noisy alignment
      where the apparent "gap" is likely an alignment artifact.
    - min_alignment_confidence: Skip pages where the aligner's confidence
      is below this threshold. Low-confidence alignments are generally messy,
      and any holes found are likely artifacts of the misalignment.

    Args:
        alignments: List of alignment dicts/objects with pg_start, pg_end, scan_page, confidence.
        scan_pages: List of scan page dicts/objects with page_num and text fields.
        pg_text: PG body text (between START/END markers).
        pg_full_text: Full PG text (for verification — includes header/footer).
        min_words: Minimum missing words to report.
        min_anchor_words: Minimum words in both adjacent match blocks.
        min_alignment_confidence: Minimum alignment confidence to consider a page.

    Returns:
        List of CoverageGap objects with strategy="content_hole".
    """
    if not alignments or not scan_pages:
        return []

    # Build lookup: page_num -> scan page text
    page_texts: dict[int, str] = {}
    for sp in scan_pages:
        pnum = getattr(sp, "page_num", sp.get("page_num") if isinstance(sp, dict) else None)
        if pnum is None:
            continue
        text = getattr(sp, "vision_text", "") or getattr(sp, "ocr_text", "") or ""
        if isinstance(sp, dict):
            text = sp.get("vision_text", "") or sp.get("ocr_text", "") or ""
        page_texts[pnum] = text

    # Group alignments by scan page, also track max confidence per page
    page_alignments: dict[int, list] = {}
    page_confidence: dict[int, float] = {}
    for a in alignments:
        if isinstance(a, dict):
            pnum = a.get("scan_page")
            conf = a.get("confidence", 0.0)
        else:
            pnum = getattr(a, "scan_page", None)
            conf = getattr(a, "confidence", 0.0)
        if pnum is None:
            continue
        page_alignments.setdefault(pnum, []).append(a)
        page_confidence[pnum] = max(page_confidence.get(pnum, 0.0), conf)

    all_holes: list[CoverageGap] = []

    for pnum, aligns in page_alignments.items():
        # Skip pages with low alignment confidence — these are generally
        # messy alignments where any holes found are likely artifacts
        if page_confidence.get(pnum, 0.0) < min_alignment_confidence:
            continue

        scan_text = page_texts.get(pnum, "")
        if not scan_text:
            continue

        # Get PG range for this page's alignments
        def _get_pg_start(a):
            return a["pg_start"] if isinstance(a, dict) else getattr(a, "pg_start")

        def _get_pg_end(a):
            return a["pg_end"] if isinstance(a, dict) else getattr(a, "pg_end")

        pg_starts = [_get_pg_start(a) for a in aligns]
        pg_ends = [_get_pg_end(a) for a in aligns]
        pg_start = min(pg_starts)
        pg_end = max(pg_ends)

        pg_chunk = pg_text[pg_start:pg_end]
        if not pg_chunk.strip():
            continue

        pg_words = _tokenize_words(pg_chunk)
        scan_words = _tokenize_words(scan_text)

        if len(pg_words) < 4 or len(scan_words) < 4:
            continue

        # Use SequenceMatcher to find matching blocks
        sm = SequenceMatcher(None, pg_words, scan_words, autojunk=False)
        matches = sm.get_matching_blocks()

        # Walk adjacent match pairs to find one-sided gaps
        for i in range(len(matches) - 1):
            m1 = matches[i]
            m2 = matches[i + 1]

            # Skip zero-size sentinel match
            if m1.size == 0:
                continue

            # Filter #1: Both anchor blocks must be long enough.
            # Short anchors indicate noisy alignment where the apparent
            # "gap" is likely an artifact, not a real content hole.
            if m1.size < min_anchor_words or m2.size < min_anchor_words:
                continue

            pg_gap_start = m1.a + m1.size
            pg_gap_end = m2.a
            scan_gap_start = m1.b + m1.size
            scan_gap_end = m2.b

            scan_gap = scan_words[scan_gap_start:scan_gap_end]
            pg_gap = pg_words[pg_gap_start:pg_gap_end]

            # Content hole criteria:
            # 1. Scan has significant text in the gap
            # 2. PG has little or no text in the gap (≤1 word)
            if len(scan_gap) < min_words or len(pg_gap) > 1:
                continue

            # 3. No sentence boundary in PG context before the gap
            pg_before_context = pg_words[max(0, pg_gap_start - 5):pg_gap_start]
            # Use original text to check for sentence boundaries
            pg_before_raw = re.findall(
                r"\S+", pg_chunk
            )
            # Map from tokenized index to raw words (approximate)
            raw_pg_before = pg_before_raw[max(0, pg_gap_start - 5):pg_gap_start]
            if _has_sentence_boundary(raw_pg_before):
                continue

            # 4. Build context
            pg_before = " ".join(pg_words[max(0, pg_gap_start - 3):pg_gap_start])
            pg_after = " ".join(
                pg_words[pg_gap_end:min(len(pg_words), pg_gap_end + 3)]
            )
            missing_phrase = " ".join(scan_gap)

            # 5. Verify against full PG text using fuzzy search
            # Use a fast exact-match check first (handles common case where
            # words exist elsewhere with same punctuation). Falls back to
            # sliding-window fuzzy search for punctuation-variant matches.
            pg_lower = pg_full_text.lower()
            phrase_lower = missing_phrase.lower()

            # Fast path: exact substring match in PG
            found_in_pg = phrase_lower in pg_lower
            pg_match_ratio = 1.0 if found_in_pg else 0.0

            # Slow path: fuzzy search for punctuation variants
            if not found_in_pg and len(scan_gap) >= 5:
                found_in_pg, pg_match_ratio = _gap_text_exists_in_pg(
                    missing_phrase, pg_full_text
                )

            if found_in_pg:
                continue  # Text exists in PG — alignment failure, not real hole

            # Compute confidence
            if len(scan_gap) >= 8:
                confidence = "high"
            elif len(scan_gap) >= 4:
                confidence = "medium"
            else:
                confidence = "low"

            # Check if missing words are all common stop words
            stop_words = {"the", "a", "an", "and", "or", "but", "in", "on", "at",
                          "to", "of", "for", "with", "is", "was", "are", "were",
                          "be", "been", "have", "has", "had", "it", "its", "he",
                          "she", "they", "we", "i", "you", "this", "that", "as"}
            if all(w in stop_words for w in scan_gap):
                confidence = "low"

            gap = CoverageGap(
                page=pnum,
                strategy="content_hole",
                word_count=len(scan_gap),
                scan_text_preview=missing_phrase[:500],
                coverage_ratio=0.0,
                pg_verified=True,
                pg_match_ratio=pg_match_ratio,
                non_content=False,
                confidence=confidence,
                missing_words=missing_phrase,
                pg_context_before=pg_before,
                pg_context_after=pg_after,
            )
            all_holes.append(gap)

    return all_holes


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
    pg_full_text: str | None = None,
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
        pg_full_text: Full PG text (for content hole verification). Falls back to pg_text.

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

    # --- Strategy 3: Content holes within aligned passages ---
    if alignments and not skip_pg_verification:
        full_text = pg_full_text if pg_full_text is not None else pg_text
        content_holes = detect_content_holes(
            alignments=alignments,
            scan_pages=scan_pages,
            pg_text=pg_text,
            pg_full_text=full_text,
            min_words=min_gap_words,
        )
        gaps.extend(content_holes)

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
            or (g.strategy == "content_hole" and g.word_count >= 5)
        )
    ]


def gaps_to_candidate_errors(gaps: list[CoverageGap]) -> list[CandidateError]:
    """Convert CoverageGap objects to CandidateError objects for the pipeline.

    Typically used with filter_for_report() to feed only high-confidence gaps
    into the errata pipeline.
    """
    errors: list[CandidateError] = []
    for g in gaps:
        if g.strategy == "content_hole":
            if not g.pg_verified or g.confidence not in ("high", "medium"):
                continue
            desc = (
                f"Scan page {g.page} has {g.word_count} words missing from PG "
                f'within an aligned passage: "{g.missing_words[:100]}"'
            )
        elif g.strategy == "uncovered":
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
