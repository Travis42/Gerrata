"""Word-level diff checker between aligned PG text and scan OCR.

Uses difflib.SequenceMatcher to find word-level differences between
PG text passages and corresponding scan OCR text.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher

from gerrata.models import CandidateError, ErrorCategory, ErrorSeverity
from gerrata.checker.stitch import stitch_scan_pages, build_token_page_map

logger = logging.getLogger(__name__)


def normalize_for_diff(text: str) -> str:
    """Normalize text for word-level comparison.

    Keeps punctuation (important for catching missing punctuation errors)
    but normalizes whitespace and unicode.
    """
    text = unicodedata.normalize("NFC", text)
    # Strip PG italics markup: _word_ → word
    text = re.sub(r"_([^_]+)_", r"\1", text)
    # Normalize PG footnote markers: [N] → superscript-like for comparison
    # PG uses [1], [2], etc. Scans often have ¹, ², etc. — normalize both to nothing
    # since footnote numbering is a convention, not content
    text = re.sub(r"\[\d+\]", "FOOTNOTE", text)
    text = re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+", "FOOTNOTE", text)
    # Collapse hyphenation artifacts from line breaks: word- word → word
    text = re.sub(r"(\w)-\s+", r"\1", text)
    # Normalize whitespace to single spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text: str) -> list[str]:
    """Split text into word tokens, keeping punctuation attached."""
    return text.split()


class TextDiffChecker:
    """Compare aligned PG text passages against scan OCR to find candidate errors."""

    def __init__(self, min_word_diff_length: int = 1):
        self.min_word_diff_length = min_word_diff_length

    def _is_fragment_candidate(self, pg_text: str, scan_text: str) -> bool:
        """Check if a candidate looks like a word fragment (alignment boundary artifact).

        A fragment candidate is one where the diff caught a partial word at the
        edge of an alignment boundary — e.g., "suff" instead of "suffrages",
        "le." instead of "unreasonable.", or "c" instead of "calling".
        """
        # Long candidates on both sides are rarely fragments
        if len(pg_text) >= 10 and len(scan_text) >= 10:
            return False

        def looks_like_fragment(text: str) -> bool:
            text = text.strip()
            if not text:
                return True
            if ' ' in text:
                return False
            if text[-1] in '.,;:!?)"\'':
                return len(text) < 4  # "le." is fragment, "ancle." could be word
            return len(text) < 5

        if len(pg_text) < 10 or len(scan_text) < 10:
            if looks_like_fragment(pg_text) or looks_like_fragment(scan_text):
                return True
        return False

    def check_aligned_passage(
        self,
        pg_text: str,
        scan_text: str,
        pg_offset: int = 0,
        scan_page: int = 0,
    ) -> list[CandidateError]:
        """Compare a PG text passage against its aligned scan OCR passage.

        Returns list of CandidateError for each difference found.
        """
        pg_norm = normalize_for_diff(pg_text)
        scan_norm = normalize_for_diff(scan_text)

        pg_tokens = tokenize(pg_norm)
        scan_tokens = tokenize(scan_norm)

        matcher = SequenceMatcher(None, pg_tokens, scan_tokens, autojunk=False)

        errors: list[CandidateError] = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue

            pg_segment = " ".join(pg_tokens[i1:i2])
            scan_segment = " ".join(scan_tokens[j1:j2])

            # Skip trivial differences
            if not pg_segment.strip() and not scan_segment.strip():
                continue

            error = self._classify_diff(
                tag, pg_segment, scan_segment,
                pg_tokens, i1, i2,
                pg_offset, scan_page,
            )
            if error:
                # Filter out word-fragment candidates (alignment boundary artifacts)
                if self._is_fragment_candidate(error.pg_text, error.scan_text):
                    logger.debug(
                        f"Filtered fragment candidate: pg='{error.pg_text[:40]}' "
                        f"scan='{error.scan_text[:40]}'"
                    )
                    continue
                # Filter out page reference artifacts ({43}, {190}, {xxiii})
                if re.search(r'\{\d+[a-z]*\}', error.pg_text) or re.search(r'\{\d+[a-z]*\}', error.scan_text):
                    logger.debug(
                        f"Filtered page-ref candidate: pg='{error.pg_text[:40]}'"
                    )
                    continue
                errors.append(error)

        return errors

    def check_all_alignments(
        self,
        pg_text: str,
        alignments: list,
        scan_pages: list,
        use_recursive: bool = True,
    ) -> list[CandidateError]:
        """Check all aligned passages.

        Args:
            pg_text: Full PG body text.
            alignments: List of Alignment objects.
            scan_pages: List of ScanPage objects.
            use_recursive: If True, use recursive sub-page alignment to
                diff segments between unique word anchors independently.
                This reduces false positives from alignment drift within
                a page. Default True.

        Returns:
            All candidate errors found across all alignments.

        Uses vision_text preferentially if available (cleaner than OCR),
        falls back to ocr_text.
        """
        all_errors: list[CandidateError] = []

        for alignment in alignments:
            pg_passage = pg_text[alignment.pg_start:alignment.pg_end]
            # Find matching scan page by page number, not list index
            scan_page = None
            for sp in scan_pages:
                if getattr(sp, 'page_num', None) == alignment.scan_page:
                    scan_page = sp
                    break

            # Prefer vision transcription over OCR text
            if scan_page and getattr(scan_page, 'vision_text', '') and scan_page.vision_text:
                scan_text_full = scan_page.vision_text
            elif scan_page and scan_page.ocr_text:
                scan_text_full = scan_page.ocr_text
            else:
                continue

            if not scan_text_full:
                continue

            # Align scan text to PG passage to find the matching subregion.
            # The full scan transcription may be longer/shorter than the PG
            # aligned region (different editions, paratext, etc.), so we find
            # the best matching window within the scan text.
            pg_norm = normalize_for_diff(pg_passage)
            scan_norm = normalize_for_diff(scan_text_full)
            pg_tokens = tokenize(pg_norm)
            scan_tokens = tokenize(scan_norm)

            if len(pg_tokens) == 0 or len(scan_tokens) == 0:
                continue

            sm = SequenceMatcher(None, pg_tokens, scan_tokens, autojunk=False)
            # Find the longest matching block as anchor
            best = sm.find_longest_match(0, len(pg_tokens), 0, len(scan_tokens))
            if best.size < 3:
                # No significant match; skip this page
                logger.debug(
                    f"Page {alignment.scan_page}: no significant match "
                    f"between PG passage and scan text (longest={best.size})"
                )
                continue

            # Extract a window around the best match in scan text,
            # sized to match the PG passage length
            match_center = best.b + best.size // 2
            half_window = len(pg_tokens) // 2 + 10
            win_start = max(0, match_center - half_window)
            win_end = min(len(scan_tokens), match_center + half_window)

            # But also include any matched blocks that overlap
            for a_start, b_start, size in sm.get_matching_blocks():
                if size < 3:
                    continue
                win_start = min(win_start, max(0, b_start - 2))
                win_end = min(len(scan_tokens), max(win_end, b_start + size + 2))

            scan_text_aligned = " ".join(scan_tokens[win_start:win_end])

            if len(scan_text_aligned) < 20:
                continue

            if use_recursive:
                errors = self._check_with_recursive_anchors(
                    pg_passage=pg_passage,
                    scan_text=scan_text_aligned,
                    pg_offset=alignment.pg_start,
                    scan_page=alignment.scan_page,
                )
            else:
                errors = self.check_aligned_passage(
                    pg_text=pg_passage,
                    scan_text=scan_text_aligned,
                    pg_offset=alignment.pg_start,
                    scan_page=alignment.scan_page,
                )
            all_errors.extend(errors)

        return all_errors

    def check_stitched(
        self,
        pg_text: str,
        alignments: list,
        scan_pages: list,
    ) -> list[CandidateError]:
        """Diff using stitched scan text with page-boundary elimination.

        Groups adjacent alignments into segments and concatenates scan
        pages within each segment. This eliminates page-boundary artifacts
        (split words, truncated sentences) while keeping segments small
        enough for efficient SequenceMatcher computation.

        Uses alignment positions to determine which scan pages are
        consecutive in PG text, then stitches their transcriptions
        together for continuous diffing.

        Page attribution is preserved via a token-to-page mapping.

        Args:
            pg_text: Full PG body text.
            alignments: List of Alignment objects.
            scan_pages: List of ScanPage objects.

        Returns:
            All candidate errors found.
        """
        from gerrata.checker.stitch import stitch_scan_pages, build_token_page_map

        if not alignments or not scan_pages:
            return []

        # Build scan page lookup
        page_lookup = {}
        for sp in scan_pages:
            pnum = getattr(sp, 'page_num', None) if not isinstance(sp, dict) else sp.get('page_num')
            if pnum is not None:
                page_lookup[pnum] = sp

        # Sort alignments by scan page
        sorted_aligns = sorted(alignments, key=lambda a: getattr(a, 'scan_page', 0))

        # Group adjacent alignments into segments (consecutive pages)
        # Pages are adjacent if their PG ranges are consecutive
        segments = []
        current_group = [sorted_aligns[0]]

        for i in range(1, len(sorted_aligns)):
            prev = sorted_aligns[i - 1]
            curr = sorted_aligns[i]
            
            # Check if pages are consecutive
            prev_page = getattr(prev, 'scan_page', 0)
            curr_page = getattr(curr, 'scan_page', 0)
            page_gap = curr_page - prev_page
            
            # Check if PG ranges are roughly consecutive
            prev_end = getattr(prev, 'pg_end', 0)
            curr_start = getattr(curr, 'pg_start', 0)
            pg_gap = curr_start - prev_end
            
            # Adjacent if pages are consecutive and PG gap is small
            # Keep segments small — large segments make page attribution harder
            if page_gap <= 1 and -50 < pg_gap < 500:
                current_group.append(curr)
            else:
                segments.append(current_group)
                current_group = [curr]
        
        segments.append(current_group)

        all_errors: list[CandidateError] = []

        for segment in segments:
            # Get PG text for this segment
            pg_start = min(getattr(a, 'pg_start', 0) for a in segment)
            pg_end = max(getattr(a, 'pg_end', 0) for a in segment)
            pg_passage = pg_text[pg_start:pg_end]

            # Get scan pages for this segment
            segment_pages = sorted(set(getattr(a, 'scan_page', 0) for a in segment))
            segment_scan_pages = [page_lookup[p] for p in segment_pages if p in page_lookup]

            if not segment_scan_pages or len(pg_passage) < 20:
                continue

            # Stitch scan pages in this segment
            scan_concat, page_map = stitch_scan_pages(segment_scan_pages, normalizer=normalize_for_diff)
            if not scan_concat or len(scan_concat) < 20:
                continue

            # Normalize
            pg_norm = normalize_for_diff(pg_passage)
            scan_norm = normalize_for_diff(scan_concat)
            pg_tokens = tokenize(pg_norm)
            scan_tokens = tokenize(scan_norm)

            if not pg_tokens or not scan_tokens:
                continue

            # Build token-to-page mapping
            token_pages = build_token_page_map(scan_tokens, scan_concat, page_map)

            # Find matching blocks
            sm = SequenceMatcher(None, pg_tokens, scan_tokens, autojunk=False)
            matching_blocks = [
                (a, b, size) for a, b, size in sm.get_matching_blocks()
                if size >= 5
            ]

            if len(matching_blocks) < 2:
                # Fall back to simple diff for this segment
                for a in segment:
                    sp = page_lookup.get(getattr(a, 'scan_page', None))
                    if not sp:
                        continue
                    vt = getattr(sp, 'vision_text', '') or ''
                    ocr = getattr(sp, 'ocr_text', '') or ''
                    scan_text = vt if vt else ocr
                    if scan_text:
                        errors = self.check_aligned_passage(
                            pg_text=pg_text[a.pg_start:a.pg_end],
                            scan_text=scan_text,
                            pg_offset=a.pg_start,
                            scan_page=a.scan_page,
                        )
                        all_errors.extend(errors)
                continue

            # Diff gaps between anchors
            prev_a_end = 0
            prev_b_end = 0

            for i, (a_start, b_start, size) in enumerate(matching_blocks):
                gap_pg = pg_tokens[prev_a_end:a_start]
                gap_scan = scan_tokens[prev_b_end:b_start]

                if gap_pg or gap_scan:
                    default_page = token_pages[prev_b_end] if prev_b_end < len(token_pages) else segment_pages[0]
                    errors = self._diff_stitched_gap(
                        gap_pg, gap_scan, pg_start + prev_a_end, prev_b_end,
                        default_page if default_page and default_page >= 0 else segment_pages[0],
                        token_pages,
                    )
                    all_errors.extend(errors)

                prev_a_end = a_start + size
                prev_b_end = b_start + size

            # Trailing gap
            gap_pg = pg_tokens[prev_a_end:]
            gap_scan = scan_tokens[prev_b_end:]
            if gap_pg or gap_scan:
                default_page = token_pages[prev_b_end] if prev_b_end < len(token_pages) else segment_pages[0]
                errors = self._diff_stitched_gap(
                    gap_pg, gap_scan, pg_start + prev_a_end, prev_b_end,
                    default_page if default_page and default_page >= 0 else segment_pages[0],
                    token_pages,
                )
                all_errors.extend(errors)

        return all_errors

    def _diff_stitched_gap(
        self,
        gap_pg: list[str],
        gap_scan: list[str],
        pg_token_offset: int,
        scan_token_offset: int,
        default_page: int,
        token_pages: list[int],
    ) -> list[CandidateError]:
        """Diff a gap in stitched mode, attributing errors to correct pages.

        Diffs at the token level directly so we know each error's position
        in the scan token stream, enabling correct page lookup via token_pages.
        """
        pg_len = len(gap_pg)
        scan_len = len(gap_scan)

        if pg_len == 0 and scan_len == 0:
            return []

        def _page_for_scan_idx(local_idx):
            """Get page number for a scan token index within this gap."""
            global_idx = scan_token_offset + local_idx
            if 0 <= global_idx < len(token_pages):
                p = token_pages[global_idx]
                if p is not None and p >= 0:
                    return p
            return default_page

        # One-sided gaps
        if pg_len == 0:
            if scan_len <= 3:
                errors = []
                for j, tok in enumerate(gap_scan):
                    errors.append(CandidateError(
                        pg_text="",
                        scan_text=tok,
                        pg_offset=pg_token_offset,
                        scan_page=_page_for_scan_idx(j),
                        diff_description=f"Extra word in scan: '{tok}'",
                        category=ErrorCategory.EXTRA_WORD,
                    ))
                return errors
            return []

        if scan_len == 0:
            if pg_len <= 3:
                errors = []
                for j, tok in enumerate(gap_pg):
                    errors.append(CandidateError(
                        pg_text=tok,
                        scan_text="",
                        pg_offset=pg_token_offset + j,
                        scan_page=default_page,
                        diff_description=f"Missing from scan: '{tok}'",
                        category=ErrorCategory.MISSING_WORD,
                    ))
                return errors
            return []

        # Two-sided gap: check similarity
        max_len = max(pg_len, scan_len)
        sm = SequenceMatcher(None, gap_pg, gap_scan, autojunk=False)
        ratio = sm.ratio()

        if ratio < 0.3 and max_len > 8:
            return []

        # Diff this gap using the existing check_aligned_passage for proper
        # classification and filtering, then remap page numbers
        pg_seg = " ".join(gap_pg)
        scan_seg = " ".join(gap_scan)

        errors = self.check_aligned_passage(
            pg_text=pg_seg,
            scan_text=scan_seg,
            pg_offset=pg_token_offset,
            scan_page=default_page,
        )

        # Remap page attribution: build index from scan text tokens to
        # position in gap, then look up page via token_pages
        scan_gap_tokens = tokenize(scan_seg)

        # Build a position lookup: for each token in scan_seg, what's its index?
        token_positions: dict[str, list[int]] = {}
        for idx, tok in enumerate(scan_gap_tokens):
            token_positions.setdefault(tok, []).append(idx)
        # Track which positions we've consumed to handle repeats
        consumed: dict[str, int] = {}

        for e in errors:
            # Try to find this error's scan text in the gap tokens
            if e.scan_text:
                e_scan_tokens = tokenize(e.scan_text)
                if e_scan_tokens:
                    first_tok = e_scan_tokens[0]
                    positions = token_positions.get(first_tok, [])
                    consumed_count = consumed.get(first_tok, 0)
                    if consumed_count < len(positions):
                        local_idx = positions[consumed_count]
                        consumed[first_tok] = consumed_count + 1
                        e.scan_page = _page_for_scan_idx(local_idx)

        return errors

    def _check_with_recursive_anchors(
        self,
        pg_passage: str,
        scan_text: str,
        pg_offset: int,
        scan_page: int,
    ) -> list[CandidateError]:
        """Diff a page using recursive sub-page anchor alignment.

        Finds stable matching blocks between PG and scan passages, then
        diffs only the text BETWEEN consecutive matched regions. This
        localizes alignment drift to small regions instead of letting
        it corrupt the entire page's diff.

        Falls back to flat diff if too few stable blocks are found.
        """
        pg_norm = normalize_for_diff(pg_passage)
        scan_norm = normalize_for_diff(scan_text)
        pg_tokens = tokenize(pg_norm)
        scan_tokens = tokenize(scan_norm)

        if len(pg_tokens) < 20 or len(scan_tokens) < 20:
            return self.check_aligned_passage(
                pg_text=pg_passage,
                scan_text=scan_text,
                pg_offset=pg_offset,
                scan_page=scan_page,
            )

        # Find matching blocks from the FULL page SequenceMatcher
        sm = SequenceMatcher(None, pg_tokens, scan_tokens, autojunk=False)
        matching_blocks = [
            (a, b, size) for a, b, size in sm.get_matching_blocks()
            if size >= 5  # Only use substantial anchors
        ]

        if len(matching_blocks) < 3:
            # Not enough anchors to subdivide meaningfully
            return self.check_aligned_passage(
                pg_text=pg_passage,
                scan_text=scan_text,
                pg_offset=pg_offset,
                scan_page=scan_page,
            )

        # Instead of diffing between anchors independently (which creates
        # spurious missing/extra word candidates at every gap), use the
        # matching blocks to CONSTRAIN the flat diff.
        #
        # The key insight: matching blocks are known-good text. We only
        # need to diff the non-matching regions. But the flat diff checker
        # already does this via SequenceMatcher opcodes — it just doesn't
        # know which non-matches are alignment drift vs real errors.
        #
        # Our approach: compute the edit density within each non-matching
        # gap. If a gap has high edit density (>50% of tokens differ),
        # it's likely alignment drift — skip it. If low density, it's
        # real word-level errors — diff it normally.

        all_errors: list[CandidateError] = []

        # Walk through matching blocks and diff the gaps between them
        prev_a_end = 0
        prev_b_end = 0

        for i, (a_start, b_start, size) in enumerate(matching_blocks):
            # Gap between previous block end and this block start
            gap_pg = pg_tokens[prev_a_end:a_start]
            gap_scan = scan_tokens[prev_b_end:b_start]

            if len(gap_pg) > 0 or len(gap_scan) > 0:
                errors = self._diff_gap(
                    gap_pg, gap_scan, pg_offset, scan_page,
                    prev_a_end, matching_blocks, i,
                )
                all_errors.extend(errors)

            prev_a_end = a_start + size
            prev_b_end = b_start + size

        # Handle trailing gap after last block
        gap_pg = pg_tokens[prev_a_end:]
        gap_scan = scan_tokens[prev_b_end:]
        if len(gap_pg) > 0 or len(gap_scan) > 0:
            errors = self._diff_gap(
                gap_pg, gap_scan, pg_offset, scan_page,
                prev_a_end, matching_blocks, len(matching_blocks),
            )
            all_errors.extend(errors)

        return all_errors

    def _diff_gap(
        self,
        gap_pg: list[str],
        gap_scan: list[str],
        pg_offset: int,
        scan_page: int,
        token_offset: int,
        matching_blocks: list,
        block_idx: int,
    ) -> list[CandidateError]:
        """Diff a gap between two matching anchor blocks.

        Uses edit density to distinguish real errors from alignment drift.
        """
        pg_len = len(gap_pg)
        scan_len = len(gap_scan)

        # Empty gap on one side — pure insertion/deletion
        if pg_len == 0 and scan_len == 0:
            return []

        if pg_len == 0:
            # Scan has text where PG doesn't
            if scan_len <= 3:
                # Small insertion — could be real (heading, page number)
                errors = []
                for tok in gap_scan:
                    errors.append(CandidateError(
                        pg_text="",
                        scan_text=tok,
                        pg_offset=pg_offset + token_offset,
                        scan_page=scan_page,
                        diff_description=f"Extra word in scan: '{tok}'",
                        category=ErrorCategory.EXTRA_WORD,
                    ))
                return errors
            # Large insertion — likely alignment artifact, skip
            return []

        if scan_len == 0:
            if pg_len <= 3:
                errors = []
                for tok in gap_pg:
                    errors.append(CandidateError(
                        pg_text=tok,
                        scan_text="",
                        pg_offset=pg_offset + token_offset,
                        scan_page=scan_page,
                        diff_description=f"Missing from scan: '{tok}'",
                        category=ErrorCategory.MISSING_WORD,
                    ))
                return errors
            return []

        # Both sides have text — compute edit density
        max_len = max(pg_len, scan_len)
        sm = SequenceMatcher(None, gap_pg, gap_scan, autojunk=False)
        ratio = sm.ratio()

        # If the gap texts are very dissimilar, this is alignment drift
        # (two unrelated passages being compared). Skip it.
        if ratio < 0.3 and max_len > 8:
            logger.debug(
                f"Page {scan_page}: skipping gap (ratio={ratio:.2f}, "
                f"pg={pg_len} scan={scan_len})"
            )
            return []

        # Diff this gap normally — it's small enough to be meaningful
        pg_seg = " ".join(gap_pg)
        scan_seg = " ".join(gap_scan)

        return self.check_aligned_passage(
            pg_text=pg_seg,
            scan_text=scan_seg,
            pg_offset=pg_offset + token_offset,
            scan_page=scan_page,
        )

    def _classify_diff(
        self,
        tag: str,
        pg_segment: str,
        scan_segment: str,
        pg_tokens: list[str],
        i1: int,
        i2: int,
        pg_offset: int,
        scan_page: int,
    ) -> CandidateError | None:
        """Classify a diff operation into an error category."""
        # Strip PG markup and normalize, then compare.
        # If the text content is identical, the diff is a false positive.
        def _clean(s: str) -> str:
            s = re.sub(r"_([^_]+)_", r"\1", s)       # _word_ → word
            s = re.sub(r"<[^>]+>", "", s)              # strip anything in < >
            s = s.replace('\u201c', '"').replace('\u201d', '"')   # smart double quotes
            s = s.replace('\u2018', "'").replace('\u2019', "'")   # smart single quotes
            s = re.sub(r'\s+', ' ', s).strip()
            return s
        pg_clean = _clean(pg_segment)
        scan_clean = _clean(scan_segment)
        if pg_clean == scan_clean:
            return None
        if pg_clean.lower() == scan_clean.lower():
            return None

        # Skip diffs that are only footnote marker style differences
        pg_footnotes = re.sub(r"\[\d+\]", "FOOTNOTE", pg_segment)
        scan_footnotes = re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+", "FOOTNOTE", scan_segment)
        if pg_footnotes == scan_footnotes:
            return None

        # Skip hyphenation artifacts
        pg_unhyph = re.sub(r"(\w)-\s+", r"\1", pg_segment)
        scan_unhyph = re.sub(r"(\w)-\s+", r"\1", scan_segment)
        if pg_unhyph == scan_unhyph:
            return None

        if tag == "replace":
            # Words differ between PG and scan
            diff_desc = f"PG has '{pg_segment}' where scan has '{scan_segment}'"
            cat = self._categorize_replacement(pg_segment, scan_segment)
            return CandidateError(
                pg_text=pg_segment,
                scan_text=scan_segment,
                pg_offset=pg_offset,
                scan_page=scan_page,
                diff_description=diff_desc,
                category=cat,
            )

        elif tag == "delete":
            # PG has words that scan doesn't (extra words in PG)
            diff_desc = f"PG has extra text: '{pg_segment}'"
            return CandidateError(
                pg_text=pg_segment,
                scan_text="(absent in scan)",
                pg_offset=pg_offset,
                scan_page=scan_page,
                diff_description=diff_desc,
                category=ErrorCategory.EXTRA_WORD,
            )

        elif tag == "insert":
            # Scan has words that PG doesn't (missing words in PG)
            diff_desc = f"PG is missing text: '{scan_segment}'"
            return CandidateError(
                pg_text="(absent in PG)",
                scan_text=scan_segment,
                pg_offset=pg_offset,
                scan_page=scan_page,
                diff_description=diff_desc,
                category=ErrorCategory.MISSING_WORD,
            )

        return None

    def _categorize_replacement(
        self, pg_word: str, scan_word: str
    ) -> ErrorCategory:
        """Categorize a word replacement.

        Attempts to distinguish between OCR scannos, encoding errors,
        and other types of differences.
        """
        pg_lower = pg_word.lower()
        scan_lower = scan_word.lower()

        # Same word with different case
        if pg_lower == scan_lower:
            return ErrorCategory.FORMATTING_ERROR

        # Single character difference — likely OCR scanno
        if len(pg_word) > 2 and len(scan_word) > 2:
            if self._edit_distance(pg_lower, scan_lower) == 1:
                return ErrorCategory.OCR_SCANNO

        # Similar words (edit distance 2)
        if len(pg_word) > 3 and len(scan_word) > 3:
            if self._edit_distance(pg_lower, scan_lower) <= 2:
                return ErrorCategory.WRONG_WORD

        return ErrorCategory.WRONG_WORD

    @staticmethod
    def _edit_distance(s1: str, s2: str) -> int:
        """Compute Levenshtein edit distance between two strings."""
        if len(s1) < len(s2):
            return TextDiffChecker._edit_distance(s2, s1)

        if len(s2) == 0:
            return len(s1)

        prev_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            curr_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = prev_row[j + 1] + 1
                deletions = curr_row[j] + 1
                substitutions = prev_row[j] + (c1 != c2)
                curr_row.append(min(insertions, deletions, substitutions))
            prev_row = curr_row

        return prev_row[-1]
