"""Tests for coverage gap detection."""

import pytest
from difflib import SequenceMatcher

from gerrata.checker.gap_detector import (
    CoverageGap,
    _gap_text_exists_in_pg,
    _is_non_content,
    _normalize,
    _strip_page_header,
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
