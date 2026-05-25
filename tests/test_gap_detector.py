"""Tests for coverage gap detection."""

import pytest
from difflib import SequenceMatcher

from gerrata.checker.gap_detector import (
    CoverageGap,
    _gap_text_exists_in_pg,
    _has_sentence_boundary,
    _is_non_content,
    _normalize,
    _strip_page_header,
    _tokenize_words,
    _verify_content_hole,
    detect_content_holes,
    detect_scan_gaps,
    filter_for_report,
    gaps_to_candidate_errors,
)
from gerrata.models import Alignment, AlignmentMethod, CandidateError, ErrorCategory


# ── Test data ──────────────────────────────────────────────────────────────

PG_TEXT = (
    "He walked to the door and opened it slowly. A figure stood outside "
    "in the rain. The night was dark and cold. He could hear footsteps "
    "approaching from the corridor. The storm raged outside, lightning "
    "illuminating the sky."
)

TOC_TEXT = (
    "Contents\n\n"
    "The Silver of the Mine . . . . . . . . . . . . . . 1\n"
    "The Isabels . . . . . . . . . . . . . . . . . . . 25\n"
)

COLOR_CAL = (
    "I am sorry, but the image provided does not contain any text. "
    "It appears to be a color calibration chart."
)


class FakeScanPage:
    """Minimal scan page stand-in for tests."""

    def __init__(self, page_num: int, ocr: str = "", vt: str = ""):
        self.page_num = page_num
        self.ocr_text = ocr
        self.vision_text = vt


def _alignment(pg_start: int, pg_end: int, scan_page: int):
    return Alignment(
        pg_start=pg_start, pg_end=pg_end,
        scan_page=scan_page, confidence=1.0,
        method=AlignmentMethod.LCS,
    )


# ── _strip_page_header ────────────────────────────────────────────────────

class TestStripPageHeader:
    def test_strips_known_header(self):
        text = "Nostromo: A Tale of the Seaboard\nHe walked to the door slowly."
        result = _strip_page_header(text, headers=["Nostromo: A Tale of the Seaboard"])
        assert "He walked" in result
        assert "Nostromo" not in result

    def test_strips_short_title_line(self):
        text = "Chapter Twelve\nThe old man sat down and rested."
        result = _strip_page_header(text)
        assert "The old man" in result
        assert "Chapter Twelve" not in result

    def test_no_header_unchanged(self):
        text = "He walked to the door and opened it. The night was dark."
        result = _strip_page_header(text)
        assert result == text

    def test_sentence_not_stripped(self):
        """A short first line ending with punctuation is not a header."""
        text = "He walked to the door. The night was dark and cold."
        result = _strip_page_header(text)
        assert result == text


# ── _is_non_content ───────────────────────────────────────────────────────

class TestIsNonContent:
    def test_table_of_contents(self):
        assert _is_non_content(TOC_TEXT)

    def test_color_calibration(self):
        assert _is_non_content(COLOR_CAL)

    def test_empty_vision_response(self):
        assert _is_non_content("The image provided is blank.")

    def test_short_text(self):
        assert _is_non_content("Only five words here total.")

    def test_actual_content(self):
        assert not _is_non_content("He walked to the door and opened it slowly. A figure stood outside in the rain. The night was dark and cold.")

    def test_page_header_only_is_non_content(self):
        # 9 words < 10 threshold
        assert _is_non_content("Nostromo: A Tale of the Seaboard\n\nNew York")


# ── _gap_text_exists_in_pg ────────────────────────────────────────────────

class TestGapTextExistsInPg:
    def test_exact_match(self):
        gap = "He walked to the door and opened it slowly"
        found, ratio = _gap_text_exists_in_pg(gap, PG_TEXT)
        assert found
        assert ratio >= 0.8

    def test_no_match(self):
        found, ratio = _gap_text_exists_in_pg(
            "The ambassador declared that the treaty was null and void.", PG_TEXT
        )
        assert not found

    def test_partial_match_below_threshold(self):
        # "walked to the door" is in PG but too short for verification (< 5 words)
        found, _ = _gap_text_exists_in_pg("walked to the door", PG_TEXT)
        assert not found  # < 5 words → skip

    def test_fuzzy_match_with_ocr_noise(self):
        gap = "He walked too the door and opend it slowly"
        found, ratio = _gap_text_exists_in_pg(gap, PG_TEXT)
        assert found
        assert ratio > 0.7

    def test_alignment_miss_is_caught(self):
        """Text that exists in PG but wasn't aligned should be detected."""
        gap = "footsteps approaching from the corridor"
        found, ratio = _gap_text_exists_in_pg(gap, PG_TEXT)
        assert found
        assert ratio > 0.6

    def test_empty_input(self):
        assert _gap_text_exists_in_pg("", PG_TEXT) == (False, 0.0)
        assert _gap_text_exists_in_pg("some text", "") == (False, 0.0)


# ── detect_scan_gaps ──────────────────────────────────────────────────────

class TestDetectScanGaps:
    def test_no_gaps_when_fully_aligned(self):
        pages = [FakeScanPage(0, "He walked to the door and opened it slowly.")]
        alignments = [_alignment(0, 100, 0)]
        gaps = detect_scan_gaps(PG_TEXT, alignments, pages)
        # Page is aligned so no uncovered gap; coverage > 60% so no partial gap
        uncovered = [g for g in gaps if g.strategy == "uncovered"]
        partial = [g for g in gaps if g.strategy == "partial"]
        assert len(uncovered) == 0

    def test_uncovered_page_with_missing_text(self):
        pages = [FakeScanPage(0, "The ambassador declared that the treaty was null and void. Furthermore, the Senate would vote on the matter before dawn.")]
        gaps = detect_scan_gaps(PG_TEXT, [], pages)
        assert len(gaps) >= 1
        assert gaps[0].strategy == "uncovered"
        assert not gaps[0].pg_verified  # text absent from PG

    def test_alignment_miss_filtered_by_pg_search(self):
        """Page with text that exists in PG but wasn't aligned → pg_verified=True."""
        pages = [FakeScanPage(0, "He walked to the door and opened it slowly. A figure stood outside in the rain.")]
        gaps = detect_scan_gaps(PG_TEXT, [], pages)
        uncovered = [g for g in gaps if g.strategy == "uncovered"]
        assert len(uncovered) == 1
        assert uncovered[0].pg_verified  # text found in PG

    def test_small_gap_ignored(self):
        pages = [FakeScanPage(0, "Hello world test.")]
        gaps = detect_scan_gaps(PG_TEXT, [], pages, min_gap_words=5)
        assert len(gaps) == 0

    def test_empty_scan_pages(self):
        assert detect_scan_gaps(PG_TEXT, [], []) == []

    def test_no_alignments(self):
        pages = [FakeScanPage(0, "A long piece of text about things and stuff that goes on for a while with many words.")]
        gaps = detect_scan_gaps(PG_TEXT, [], pages)
        assert len(gaps) == 1
        assert gaps[0].strategy == "uncovered"

    def test_partial_coverage_gap_not_in_pg(self):
        pages = [
            FakeScanPage(
                0,
                "He walked to the door and opened it slowly. "
                "The ambassador declared that the treaty was null and void. "
                "Furthermore, the Senate would vote on the matter before dawn. "
                "This was an unprecedented move in diplomatic history and foreign policy.",
            ),
        ]
        alignments = [_alignment(0, 42, 0)]
        gaps = detect_scan_gaps(PG_TEXT, alignments, pages, min_gap_words=5)
        partial = [g for g in gaps if g.strategy == "partial"]
        assert len(partial) >= 1
        assert "ambassador" in partial[0].scan_text_preview or "Senate" in partial[0].scan_text_preview

    def test_vision_text_preferred_over_ocr(self):
        pages = [FakeScanPage(0, ocr="bad ocr here", vt="He walked to the door slowly.")]
        gaps = detect_scan_gaps(PG_TEXT, [], pages)
        assert len(gaps) == 1
        assert "He walked" in gaps[0].scan_text_preview

    def test_toc_page_flagged_as_non_content(self):
        pages = [FakeScanPage(0, TOC_TEXT)]
        gaps = detect_scan_gaps(PG_TEXT, [], pages, min_gap_words=10)
        assert len(gaps) == 1
        assert gaps[0].non_content is True
        assert gaps[0].confidence == "low"

    def test_color_calibration_flagged(self):
        pages = [FakeScanPage(0, COLOR_CAL)]
        gaps = detect_scan_gaps(PG_TEXT, [], pages, min_gap_words=10)
        assert len(gaps) == 1
        assert gaps[0].non_content is True

    def test_skip_verification_flag(self):
        pages = [FakeScanPage(0, "He walked to the door and opened it slowly.")]
        gaps = detect_scan_gaps(PG_TEXT, [], pages, skip_pg_verification=True)
        uncovered = [g for g in gaps if g.strategy == "uncovered"]
        assert len(uncovered) == 1
        assert not uncovered[0].pg_verified  # verification was skipped


# ── filter_for_report ─────────────────────────────────────────────────────

class TestFilterForReport:
    def test_non_content_excluded(self):
        g = CoverageGap(
            page=10, strategy="uncovered", word_count=100,
            scan_text_preview="Contents...", non_content=True,
            confidence="low",
        )
        assert filter_for_report([g]) == []

    def test_small_gap_excluded(self):
        g = CoverageGap(
            page=10, strategy="uncovered", word_count=10,
            scan_text_preview="Some text here.", pg_verified=True,
            confidence="medium",
        )
        assert filter_for_report([g]) == []

    def test_large_verified_gap_included(self):
        g = CoverageGap(
            page=10, strategy="uncovered", word_count=80,
            scan_text_preview="Long text absent from PG.", pg_verified=True,
            confidence="high",
        )
        assert len(filter_for_report([g])) == 1

    def test_low_confidence_excluded(self):
        g = CoverageGap(
            page=10, strategy="uncovered", word_count=100,
            scan_text_preview="text", pg_verified=False,
            confidence="low",
        )
        assert filter_for_report([g]) == []


# ── gaps_to_candidate_errors ──────────────────────────────────────────────

class TestGapsToCandidateErrors:
    def test_conversion(self):
        g = CoverageGap(
            page=42, strategy="uncovered", word_count=100,
            scan_text_preview="Some missing text here.",
            pg_verified=True, confidence="high",
        )
        errors = gaps_to_candidate_errors([g])
        assert len(errors) == 1
        assert isinstance(errors[0], CandidateError)
        assert errors[0].category == ErrorCategory.MISSING_CONTENT
        assert errors[0].scan_page == 42

    def test_empty_input(self):
        assert gaps_to_candidate_errors([]) == []


# ── Helpers for content hole tests ────────────────────────────────────────

def _dict_alignment(pg_start: int, pg_end: int, scan_page: int):
    """Create an alignment as a dict (like cached JSON data)."""
    return {
        "pg_start": pg_start, "pg_end": pg_end,
        "scan_page": scan_page, "confidence": 0.9,
        "method": "llm_vision",
    }


def _dict_scan_page(page_num: int, ocr: str = "", vt: str = ""):
    """Create a scan page as a dict (like cached JSON data)."""
    return {
        "page_num": page_num,
        "ocr_text": ocr,
        "vision_text": vt,
    }


# ── _tokenize_words ──────────────────────────────────────────────────────

class TestTokenizeWords:
    def test_basic(self):
        assert _tokenize_words("Hello, world!") == ["hello", "world"]

    def test_with_apostrophes(self):
        assert _tokenize_words("don't stop") == ["don't", "stop"]

    def test_numbers(self):
        assert _tokenize_words("page 219") == ["page", "219"]

    def test_empty(self):
        assert _tokenize_words("") == []


# ── _has_sentence_boundary ───────────────────────────────────────────────

class TestHasSentenceBoundary:
    def test_period(self):
        assert _has_sentence_boundary(["done.", "the"])

    def test_question_mark(self):
        assert _has_sentence_boundary(["asked?", "the"])

    def test_exclamation(self):
        assert _has_sentence_boundary(["gone!", "then"])

    def test_no_boundary(self):
        assert not _has_sentence_boundary(["had", "something"])


# ── _verify_content_hole ──────────────────────────────────────────────────

class TestVerifyContentHole:
    def test_truly_missing(self):
        assert _verify_content_hole(["barrios", "with", "rifles"], "some other text")

    def test_present_elsewhere(self):
        assert not _verify_content_hole(["walked", "to", "the"], "he walked to the door")

    def test_empty(self):
        assert _verify_content_hole([], "some text")
        assert _verify_content_hole(["word"], "")


# ── detect_content_holes ──────────────────────────────────────────────────

class TestDetectContentHoles:
    """Tests for content hole detection (Spec test cases 1-6)."""

    def _build_pg_text(self, pg_words: list[str]) -> str:
        """Create PG text containing the given words at a known offset."""
        prefix = "The story begins here. " * 5
        body = " ".join(pg_words)
        suffix = " The story continues there. " * 5
        return prefix + body + suffix

    def test_1_basic_content_hole(self):
        """Spec test 1: The Nostromo example — 7 missing words in middle."""
        pg_words = ["and", "yet", "if", "we", "had", "could", "have", "been", "done"]
        scan_words = ["and", "yet", "if", "we", "had", "barrios", "with", "his",
                      "improved", "rifles", "here", "something", "could", "have",
                      "been", "done"]

        pg_text = self._build_pg_text(pg_words)
        scan_text = " ".join(scan_words)

        # Alignment covering the PG chunk
        prefix = "The story begins here. " * 5
        pg_start = len(prefix)
        pg_end = pg_start + len(" ".join(pg_words))

        alignments = [_dict_alignment(pg_start, pg_end, 219)]
        scan_pages = [_dict_scan_page(219, vt=scan_text)]

        holes = detect_content_holes(alignments, scan_pages, pg_text, pg_text)
        assert len(holes) == 1
        h = holes[0]
        assert h.strategy == "content_hole"
        assert h.page == 219
        assert h.word_count == 7
        assert "barrios" in h.missing_words
        assert "if" in h.pg_context_before or "had" in h.pg_context_before
        assert "could" in h.pg_context_after

    def test_2_no_hole_edition_variant(self):
        """Spec test 2: Rearranged words — not a content hole."""
        pg_words = ["he", "walked", "slowly", "down", "the", "street"]
        scan_words = ["he", "walked", "down", "the", "street", "slowly"]

        pg_text = self._build_pg_text(pg_words)
        scan_text = " ".join(scan_words)

        prefix = "The story begins here. " * 5
        pg_start = len(prefix)
        pg_end = pg_start + len(" ".join(pg_words))

        alignments = [_dict_alignment(pg_start, pg_end, 5)]
        scan_pages = [_dict_scan_page(5, vt=scan_text)]

        holes = detect_content_holes(alignments, scan_pages, pg_text, pg_text)
        assert len(holes) == 0

    def test_3_no_hole_sentence_boundary(self):
        """Spec test 3: Gap crosses a sentence boundary — not a content hole."""
        # PG has a period between "sentence" and "the"
        pg_words = ["end", "of", "sentence", "the", "next", "paragraph", "starts"]
        scan_words = ["end", "of", "sentence", "he", "said", "the",
                      "next", "paragraph", "starts"]

        # Build PG text with actual sentence boundary
        pg_text = "The story begins here. " * 5 + "end of sentence. the next paragraph starts" + " The story continues there. " * 5
        scan_text = " ".join(scan_words)

        prefix = "The story begins here. " * 5
        pg_start = len(prefix)
        pg_end = pg_start + len("end of sentence. the next paragraph starts")

        alignments = [_dict_alignment(pg_start, pg_end, 10)]
        scan_pages = [_dict_scan_page(10, vt=scan_text)]

        holes = detect_content_holes(alignments, scan_pages, pg_text, pg_text)
        assert len(holes) == 0

    def test_4_no_hole_both_sides_have_gaps(self):
        """Spec test 4: Both PG and scan have unmatched words — edition variant."""
        pg_words = ["he", "spoke", "to", "her", "and", "she", "listened"]
        scan_words = ["he", "addressed", "the", "woman", "who", "listened", "attentively"]

        pg_text = self._build_pg_text(pg_words)
        scan_text = " ".join(scan_words)

        prefix = "The story begins here. " * 5
        pg_start = len(prefix)
        pg_end = pg_start + len(" ".join(pg_words))

        alignments = [_dict_alignment(pg_start, pg_end, 15)]
        scan_pages = [_dict_scan_page(15, vt=scan_text)]

        holes = detect_content_holes(alignments, scan_pages, pg_text, pg_text)
        assert len(holes) == 0

    def test_5_small_gap_filtered(self):
        """Spec test 5: Gap is 3 words (< 4 threshold) — filtered out."""
        pg_words = ["she", "said", "and", "then", "left"]
        scan_words = ["she", "said", "with", "a", "sigh", "and", "then", "left"]

        pg_text = self._build_pg_text(pg_words)
        scan_text = " ".join(scan_words)

        prefix = "The story begins here. " * 5
        pg_start = len(prefix)
        pg_end = pg_start + len(" ".join(pg_words))

        alignments = [_dict_alignment(pg_start, pg_end, 20)]
        scan_pages = [_dict_scan_page(20, vt=scan_text)]

        # Default min_words=4 should filter this out
        holes = detect_content_holes(alignments, scan_pages, pg_text, pg_text)
        assert len(holes) == 0

        # With min_words=3, it should be found
        holes = detect_content_holes(alignments, scan_pages, pg_text, pg_text, min_words=3)
        assert len(holes) == 1
        assert holes[0].word_count == 3

    def test_6_large_content_hole_high_confidence(self):
        """Spec test 6: >20 missing words — should get HIGH confidence."""
        pg_words = ["the", "meeting", "began", "and", "then", "concluded"]
        # Insert 25 words in the scan
        missing = ["he", "stood", "up", "and", "addressed", "the", "crowd",
                   "with", "great", "passion", "speaking", "for", "nearly",
                   "an", "hour", "about", "the", "future", "of", "the",
                   "republic", "and", "its", "people"]
        scan_words = pg_words[:2] + missing + pg_words[2:]

        pg_text = self._build_pg_text(pg_words)
        scan_text = " ".join(scan_words)

        prefix = "The story begins here. " * 5
        pg_start = len(prefix)
        pg_end = pg_start + len(" ".join(pg_words))

        alignments = [_dict_alignment(pg_start, pg_end, 30)]
        scan_pages = [_dict_scan_page(30, vt=scan_text)]

        holes = detect_content_holes(alignments, scan_pages, pg_text, pg_text)
        assert len(holes) == 1
        assert holes[0].word_count == len(missing)
        assert holes[0].confidence == "high"

    def test_verify_filters_false_positive(self):
        """Hole words exist elsewhere in full PG text — should be filtered."""
        pg_words = ["he", "spoke", "and", "then", "left"]
        scan_words = ["he", "spoke", "barrios", "with", "rifles", "and", "then", "left"]

        pg_body = self._build_pg_text(pg_words)
        # Full PG text contains "barrios with rifles" somewhere else
        pg_full = pg_body + " In another chapter, barrios with rifles appeared."
        scan_text = " ".join(scan_words)

        prefix = "The story begins here. " * 5
        pg_start = len(prefix)
        pg_end = pg_start + len(" ".join(pg_words))

        alignments = [_dict_alignment(pg_start, pg_end, 40)]
        scan_pages = [_dict_scan_page(40, vt=scan_text)]

        holes = detect_content_holes(alignments, scan_pages, pg_body, pg_full)
        assert len(holes) == 0  # Filtered because words exist elsewhere

    def test_empty_inputs(self):
        assert detect_content_holes([], [], "pg", "full") == []
        assert detect_content_holes(
            [_dict_alignment(0, 10, 1)], [], "pg text", "full"
        ) == []

    def test_multiple_holes_same_page(self):
        """Multiple content holes on the same page."""
        pg_words = ["first", "part", "middle", "part", "last", "part"]
        scan_words = (["first", "part"] +
                      ["hole", "one", "alpha", "beta"] +
                      ["middle", "part"] +
                      ["hole", "two", "gamma", "delta"] +
                      ["last", "part"])

        pg_text = self._build_pg_text(pg_words)
        scan_text = " ".join(scan_words)

        prefix = "The story begins here. " * 5
        pg_start = len(prefix)
        pg_end = pg_start + len(" ".join(pg_words))

        alignments = [_dict_alignment(pg_start, pg_end, 50)]
        scan_pages = [_dict_scan_page(50, vt=scan_text)]

        holes = detect_content_holes(alignments, scan_pages, pg_text, pg_text)
        assert len(holes) == 2
        assert all(h.strategy == "content_hole" for h in holes)

    def test_coverage_gap_backward_compat(self):
        """New CoverageGap fields have defaults — existing code unaffected."""
        g = CoverageGap(page=1, strategy="uncovered", word_count=10, scan_text_preview="text")
        assert g.missing_words == ""
        assert g.pg_context_before == ""
        assert g.pg_context_after == ""

    def test_coverage_gap_to_dict_includes_content_hole_fields(self):
        """to_dict includes new fields for content_hole strategy."""
        g = CoverageGap(
            page=219, strategy="content_hole", word_count=7,
            scan_text_preview="barrios with rifles",
            missing_words="barrios with rifles",
            pg_context_before="if we had",
            pg_context_after="could have been done",
        )
        d = g.to_dict()
        assert "missing_words" in d
        assert d["missing_words"] == "barrios with rifles"
        assert d["pg_context_before"] == "if we had"

    def test_coverage_gap_to_dict_excludes_fields_for_other_strategies(self):
        """to_dict excludes content hole fields for uncovered/partial."""
        g = CoverageGap(page=1, strategy="uncovered", word_count=10, scan_text_preview="text")
        d = g.to_dict()
        assert "missing_words" not in d
        assert "pg_context_before" not in d


# ── detect_scan_gaps integration with content holes ──────────────────────

class TestDetectScanGapsContentHoles:
    def test_content_holes_included_in_scan_gaps(self):
        """detect_scan_gaps should include content holes when pg_full_text provided."""
        pg_words = ["and", "yet", "if", "we", "had", "could", "have", "been", "done"]
        scan_words = ["and", "yet", "if", "we", "had", "barrios", "with", "his",
                      "improved", "rifles", "here", "something", "could", "have",
                      "been", "done"]

        pg_text = "The story begins here. " * 5 + " ".join(pg_words) + " The story continues there. " * 5
        scan_text = " ".join(scan_words)

        prefix = "The story begins here. " * 5
        pg_start = len(prefix)
        pg_end = pg_start + len(" ".join(pg_words))

        pages = [FakeScanPage(219, vt=scan_text)]
        alignments = [_alignment(pg_start, pg_end, 219)]

        gaps = detect_scan_gaps(pg_text, alignments, pages, pg_full_text=pg_text)
        content_holes = [g for g in gaps if g.strategy == "content_hole"]
        assert len(content_holes) == 1
        assert "barrios" in content_holes[0].missing_words

    def test_content_holes_skipped_when_verification_disabled(self):
        """Content holes should not run when skip_pg_verification is True."""
        pg_words = ["and", "yet", "if", "we", "had", "could", "have", "been", "done"]
        scan_words = ["and", "yet", "if", "we", "had", "barrios", "with", "his",
                      "improved", "rifles", "here", "something", "could", "have",
                      "been", "done"]

        pg_text = "The story begins here. " * 5 + " ".join(pg_words) + " The story continues there. " * 5
        scan_text = " ".join(scan_words)

        prefix = "The story begins here. " * 5
        pg_start = len(prefix)
        pg_end = pg_start + len(" ".join(pg_words))

        pages = [FakeScanPage(219, vt=scan_text)]
        alignments = [_alignment(pg_start, pg_end, 219)]

        gaps = detect_scan_gaps(pg_text, alignments, pages, skip_pg_verification=True)
        content_holes = [g for g in gaps if g.strategy == "content_hole"]
        assert len(content_holes) == 0
