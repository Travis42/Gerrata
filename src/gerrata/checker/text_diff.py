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
    ) -> list[CandidateError]:
        """Check all aligned passages.

        Args:
            pg_text: Full PG body text.
            alignments: List of Alignment objects.
            scan_pages: List of ScanPage objects.

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

            errors = self.check_aligned_passage(
                pg_text=pg_passage,
                scan_text=scan_text_aligned,
                pg_offset=alignment.pg_start,
                scan_page=alignment.scan_page,
            )
            all_errors.extend(errors)

        return all_errors

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
        # Strip PG markup, normalize quotes/whitespace, then compare.
        # If the text content is identical, the diff is a false positive.
        def _clean(s: str) -> str:
            s = re.sub(r"_([^_]+)_", r"\1", s)       # _word_ → word
            s = re.sub(r"</?[ib]>", "", s)             # strip HTML tags
            s = s.replace('\u201c', '"').replace('\u201d', '"')   # smart double quotes
            s = s.replace('\u2018', "'").replace('\u2019', "'")   # smart single quotes
            s = re.sub(r'\s+', ' ', s).strip()
            s = re.sub(r'^[^a-zA-Z0-9]+', '', s)     # strip leading punctuation/quotes
            s = re.sub(r'[^a-zA-Z0-9]+$', '', s)     # strip trailing punctuation/quotes
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
