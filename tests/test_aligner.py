"""Tests for coarse aligner."""

import pytest

from gerrata.aligner.coarse import (
    CoarseAligner,
    normalize_text,
    split_sentences,
    lcs_lengths,
    backtrack_lcs,
)
from gerrata.models import Alignment, AlignmentMethod


class TestNormalizeText:
    def test_lowercases(self):
        assert normalize_text("Hello World") == "hello world"

    def test_strips_whitespace(self):
        assert normalize_text("  hello   world  ") == "hello world"

    def test_removes_punctuation(self):
        assert normalize_text("Hello, world!") == "hello world"

    def test_handles_unicode(self):
        result = normalize_text("café résumé")
        # After normalization: lowercase, NFC, strip punctuation
        # accented chars are letters, not punctuation, so they stay
        assert "café" in result or "cafe" in result
        assert len(result) > 0


class TestSplitSentences:
    def test_basic_split(self):
        text = "First sentence. Second sentence. Third sentence."
        chunks = split_sentences(text, min_length=5)
        assert len(chunks) >= 3

    def test_filters_short(self):
        text = "Hi. A longer sentence here. Another one."
        chunks = split_sentences(text, min_length=5)
        assert all(len(c) >= 5 for c in chunks)

    def test_fallback_to_lines(self):
        text = "This is a line of text that is long enough\nAnd another line here that works too"
        chunks = split_sentences(text, min_length=10)
        assert len(chunks) >= 2


class TestLCS:
    def test_identical_sequences(self):
        a = ["hello world", "foo bar", "baz qux"]
        b = ["hello world", "foo bar", "baz qux"]
        dp = lcs_lengths(a, b)
        assert dp[-1][-1] == 3

    def test_partial_match(self):
        a = ["alpha", "bravo", "charlie", "delta"]
        b = ["bravo", "charlie", "echo"]
        dp = lcs_lengths(a, b)
        assert dp[-1][-1] == 2

    def test_no_match(self):
        a = ["one", "two", "three"]
        b = ["four", "five", "six"]
        dp = lcs_lengths(a, b)
        assert dp[-1][-1] == 0

    def test_backtrack(self):
        a = ["first", "second", "third"]
        b = ["first", "other", "third"]
        dp = lcs_lengths(a, b)
        matches = backtrack_lcs(dp, a, b)
        assert len(matches) == 2
        assert matches[0] == (0, 0)  # "first" at pos 0 in both
        assert matches[1] == (2, 2)  # "third" at pos 2 in both


class TestCoarseAligner:
    @pytest.fixture
    def aligner(self):
        return CoarseAligner()

    def test_aligns_matching_texts(self, aligner):
        pg_text = "The quick brown fox jumps over the lazy dog. It was a bright cold day in April."
        scan_text = "The quick brown fox jumps over the lazy dog. It was a bright cold day in April."
        alignments = aligner.align(pg_text, [pg_text], scan_text)
        assert len(alignments) >= 1
        assert alignments[0].confidence > 0.5

    def test_aligns_with_omissions(self, aligner):
        """PG text may have sections the scan doesn't (transcriber's notes, etc.)"""
        pg_text = (
            "The quick brown fox jumps over the lazy dog. "
            "This paragraph appears only in the PG version. "
            "It was a bright cold day in April."
        )
        scan_text = (
            "The quick brown fox jumps over the lazy dog. "
            "It was a bright cold day in April."
        )
        alignments = aligner.align(pg_text, [pg_text], scan_text)
        assert len(alignments) >= 1

    def test_alignment_coverage(self, aligner):
        pg_text = "First sentence here. Second sentence here. Third sentence here. Fourth sentence."
        scan_text = pg_text
        alignments = aligner.align(pg_text, [pg_text], scan_text)
        coverage = aligner.alignment_confidence(alignments, len(pg_text))
        assert coverage > 0.3  # At least some coverage

    def test_empty_texts(self, aligner):
        alignments = aligner.align("", [], "")
        assert alignments == []

    def test_no_common_text(self, aligner):
        pg_text = "Alpha beta gamma delta epsilon zeta."
        scan_text = "One two three four five six seven eight nine ten."
        alignments = aligner.align(pg_text, [pg_text], scan_text)
        # May find some matches due to normalization, but shouldn't crash
        assert isinstance(alignments, list)

    def test_alignment_offsets_within_bounds(self, aligner):
        pg_text = "The quick brown fox. It was a bright cold day. The clocks were striking thirteen."
        scan_text = pg_text
        alignments = aligner.align(pg_text, [pg_text], scan_text)
        for a in alignments:
            assert 0 <= a.pg_start < a.pg_end <= len(pg_text)

    def test_group_consecutive(self, aligner):
        matches = [(0, 0), (1, 1), (2, 2), (10, 10), (11, 11)]
        groups = aligner._group_consecutive_matches(matches)
        assert len(groups) == 2
        assert len(groups[0]) == 3
        assert len(groups[1]) == 2

    def test_alignment_method(self, aligner):
        pg_text = "Hello world. Goodbye world."
        scan_text = pg_text
        alignments = aligner.align(pg_text, [pg_text], scan_text)
        for a in alignments:
            assert a.method == AlignmentMethod.LCS
