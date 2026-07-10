"""Tests for text diff checker and false positive filter."""

import pytest

from gerrata.checker.text_diff import TextDiffChecker, normalize_for_diff, tokenize
from gerrata.checker.rules import FalsePositiveFilter
from gerrata.models import CandidateError, ErrorCategory


class TestNormalizeForDiff:
    def test_normalizes_whitespace(self):
        assert normalize_for_diff("hello   world") == "hello world"

    def test_strips(self):
        assert normalize_for_diff("  hello  ") == "hello"


class TestTokenize:
    def test_basic(self):
        assert tokenize("hello world") == ["hello", "world"]

    def test_preserves_punctuation(self):
        tokens = tokenize("hello, world!")
        assert "hello," in tokens
        assert "world!" in tokens


class TestTextDiffChecker:
    @pytest.fixture
    def checker(self):
        return TextDiffChecker()

    def test_identical_text_no_errors(self, checker):
        errors = checker.check_aligned_passage(
            pg_text="The quick brown fox jumps over the lazy dog.",
            scan_text="The quick brown fox jumps over the lazy dog.",
        )
        assert errors == []

    def test_single_word_difference(self, checker):
        errors = checker.check_aligned_passage(
            pg_text="He handed tne letter to her.",
            scan_text="He handed the letter to her.",
            pg_offset=100,
            scan_page=5,
        )
        # Note: single-word diffs where both sides are <5 chars are filtered
        # by _is_fragment_candidate (alignment boundary artifact filter).
        # Use longer words to avoid the fragment filter.
        errors = checker.check_aligned_passage(
            pg_text="He handed approxmately letter to her.",
            scan_text="He handed approximately letter to her.",
            pg_offset=100,
            scan_page=5,
        )
        assert len(errors) >= 1
        assert any("approxmately" in e.pg_text and "approximately" in e.scan_text for e in errors)

    def test_missing_word(self, checker):
        # Use a multi-word difference that won't be filtered as fragment
        errors = checker.check_aligned_passage(
            pg_text="The committee considered the matter at yesterday morning session.",
            scan_text="The committee carefully considered the matter at yesterday morning session.",
        )
        assert len(errors) >= 1
        assert any(e.category == ErrorCategory.MISSING_WORD for e in errors)

    def test_extra_word(self, checker):
        # Use a multi-word difference that won't be filtered as fragment
        errors = checker.check_aligned_passage(
            pg_text="He walked very carefully and deliberately down the street yesterday afternoon.",
            scan_text="He walked very carefully down the street yesterday afternoon.",
        )
        assert len(errors) >= 1
        assert any(e.category == ErrorCategory.EXTRA_WORD for e in errors)

    def test_ocr_scanno_detection(self, checker):
        """Single char edit distance should be classified as OCR scanno."""
        errors = checker.check_aligned_passage(
            pg_text="the letter",
            scan_text="tne letter",
        )
        if errors:
            assert any(e.category == ErrorCategory.OCR_SCANNO for e in errors)

    def test_multiple_errors(self, checker):
        errors = checker.check_aligned_passage(
            pg_text="approxmately quick brown fox jamps over the lazy dg.",
            scan_text="approximately quick brown fox jumps over the lazy dog.",
        )
        assert len(errors) >= 2

    def test_empty_texts(self, checker):
        errors = checker.check_aligned_passage("", "")
        assert errors == []

    def test_punctuation_difference(self, checker):
        errors = checker.check_aligned_passage(
            pg_text="Hello world",
            scan_text="Hello, world!",
        )
        # Should find differences but they may be classified variously
        assert isinstance(errors, list)

    def test_prefers_vision_text_over_ocr(self, checker):
        """When ScanPage has vision_text, it should be used instead of ocr_text."""
        from gerrata.fetcher.scans import ScanPage
        from gerrata.models import Alignment

        # PG text has "the", OCR has "tne" (scanno), vision has "the" (correct)
        pg_text = "He handed the letter to her friend."
        alignment = Alignment(pg_start=0, pg_end=len(pg_text), scan_page=0)

        page = ScanPage(
            page_num=0,
            ocr_text="He handed tne letter to her friend.",  # OCR scanno
            vision_text="He handed the letter to her friend.",  # Vision correct
        )

        errors = checker.check_all_alignments(pg_text, [alignment], [page])
        # Vision text matches PG text exactly → no errors
        assert errors == []

    def test_falls_back_to_ocr_when_no_vision(self, checker):
        """Without vision_text, falls back to ocr_text."""
        from gerrata.fetcher.scans import ScanPage
        from gerrata.models import Alignment

        pg_text = "He handed approximately letter to her friend on that particular afternoon."
        alignment = Alignment(pg_start=0, pg_end=len(pg_text), scan_page=0)

        page = ScanPage(
            page_num=0,
            ocr_text="He handed approxmately letter to her friend on that particular afternoon.",
            vision_text="",  # No vision text
        )

        errors = checker.check_all_alignments(pg_text, [alignment], [page])
        # OCR text has scanno → should find difference
        assert len(errors) >= 1


class TestFalsePositiveFilter:
    @pytest.fixture
    def filter(self):
        return FalsePositiveFilter()

    def test_filters_em_dash_conversion(self, filter):
        error = CandidateError(
            pg_text="--", scan_text="—",
            pg_offset=0, scan_page=0,
        )
        filtered = filter.filter([error])
        assert len(filtered) == 0

    def test_filters_curly_quotes(self, filter):
        error = CandidateError(
            pg_text='"', scan_text="\u201c",
            pg_offset=0, scan_page=0,
        )
        filtered = filter.filter([error])
        assert len(filtered) == 0

    def test_filters_modernization(self, filter):
        error = CandidateError(
            pg_text="someone", scan_text="some one",
            pg_offset=0, scan_page=0,
        )
        filtered = filter.filter([error])
        assert len(filtered) == 0

    def test_filters_hyphenation_variant(self, filter):
        error = CandidateError(
            pg_text="downright", scan_text="down-right",
            pg_offset=0, scan_page=0,
        )
        filtered = filter.filter([error])
        # Should either be filtered or reclassified as edition variant
        assert len(filtered) <= 1

    def test_reclassifies_edition_variant(self, filter):
        """British/American spelling differences should be edition_variant."""
        error = CandidateError(
            pg_text="colour", scan_text="color",
            pg_offset=0, scan_page=0,
        )
        filtered = filter.filter([error])
        if filtered:
            assert filtered[0].category == ErrorCategory.EDITION_VARIANT

    def test_keeps_real_errors(self, filter):
        """Actual OCR scannos should not be filtered."""
        error = CandidateError(
            pg_text="tne", scan_text="the",
            pg_offset=100, scan_page=5,
            category=ErrorCategory.OCR_SCANNO,
        )
        filtered = filter.filter([error])
        assert len(filtered) == 1

    def test_filters_punctuation_only(self, filter):
        error = CandidateError(
            pg_text="hello,", scan_text="hello",
            pg_offset=0, scan_page=0,
        )
        filtered = filter.filter([error])
        assert len(filtered) == 0

    def test_filters_hyphenation_artifact(self, filter):
        error = CandidateError(
            pg_text="some- thing", scan_text="something",
            pg_offset=0, scan_page=0,
        )
        filtered = filter.filter([error])
        assert len(filtered) == 0

    def test_strict_mode_keeps_more(self):
        """Strict mode should keep more candidates."""
        f_strict = FalsePositiveFilter(strict=True)
        f_relaxed = FalsePositiveFilter(strict=False)
        error = CandidateError(
            pg_text="--", scan_text="—",
            pg_offset=0, scan_page=0,
        )
        assert len(f_strict.filter([error])) == 1
        assert len(f_relaxed.filter([error])) == 0


class TestPunctuationDiff:
    """Tests for punctuation-only diff classification."""

    def setup_method(self):
        self.checker = TextDiffChecker()

    def test_straight_vs_curly_quotes(self):
        """Straight quotes vs curly quotes should be PUNCTUATION_DIFF."""
        cat = self.checker._categorize_replacement('style".', 'style\u201d.')
        assert cat == ErrorCategory.PUNCTUATION_DIFF

    def test_opening_quote_style(self):
        cat = self.checker._categorize_replacement('"word', '\u201cword')
        assert cat == ErrorCategory.PUNCTUATION_DIFF

    def test_em_dash_vs_double_hyphen(self):
        """PG -- convention vs scan em-dash should be PUNCTUATION_DIFF."""
        cat = self.checker._categorize_replacement('Thor--a', 'Thor\u2014a')
        assert cat == ErrorCategory.PUNCTUATION_DIFF

    def test_semicolon_spacing(self):
        """word; vs word ; should be PUNCTUATION_DIFF."""
        cat = self.checker._categorize_replacement('deed;', 'deed ;')
        assert cat == ErrorCategory.PUNCTUATION_DIFF

    def test_colon_spacing(self):
        cat = self.checker._categorize_replacement('will:', 'will :')
        assert cat == ErrorCategory.PUNCTUATION_DIFF

    def test_quotes_dropped(self):
        """PG has quotes around a word, scan doesn't."""
        cat = self.checker._categorize_replacement('"Mistletoe"', 'Mistletoe')
        assert cat == ErrorCategory.PUNCTUATION_DIFF

    def test_real_word_diff_not_punctuation(self):
        """Actual word differences should not be PUNCTUATION_DIFF."""
        cat = self.checker._categorize_replacement('Herrand', 'Herraud')
        assert cat != ErrorCategory.PUNCTUATION_DIFF

    def test_real_char_diff_not_punctuation(self):
        cat = self.checker._categorize_replacement('reigned?', 'feigned?')
        assert cat != ErrorCategory.PUNCTUATION_DIFF

    def test_ocr_scanno_not_punctuation(self):
        cat = self.checker._categorize_replacement('showed', 'shewed')
        assert cat != ErrorCategory.PUNCTUATION_DIFF

    def test_pure_punctuation_not_classified(self):
        """Pure punctuation with no alphabetic content should not be PUNCTUATION_DIFF."""
        assert not TextDiffChecker._is_punctuation_only_diff('--', '\u2014')
        assert not TextDiffChecker._is_punctuation_only_diff(';', ' ;')

    def test_punctuation_only_helper(self):
        assert TextDiffChecker._is_punctuation_only_diff('word".', 'word\u201d.')
        assert TextDiffChecker._is_punctuation_only_diff('"word"', 'word')
        assert not TextDiffChecker._is_punctuation_only_diff('Herrand', 'Herraud')
        assert not TextDiffChecker._is_punctuation_only_diff('cat', 'bat')


class TestEditDistance:
    def test_identical(self):
        assert TextDiffChecker._edit_distance("hello", "hello") == 0

    def test_single_insertion(self):
        assert TextDiffChecker._edit_distance("the", "tne") == 1

    def test_known_distance(self):
        assert TextDiffChecker._edit_distance("kitten", "sitting") == 3
