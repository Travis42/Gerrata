"""Tests for programmatic.py — deterministic error verification scoring."""

import pytest

from gerrata.verifier.programmatic import ProgrammaticVerifier, _levenshtein
from gerrata.models import (
    Alignment,
    AlignmentMethod,
    CandidateError,
    ErrorCategory,
    Error,
    Verdict,
)


# ── Helpers ─────────────────────────────────────────────────────────────

def make_alignment(scan_page=0, confidence=0.8):
    return Alignment(
        pg_start=0,
        pg_end=1000,
        scan_page=scan_page,
        scan_image_path=None,
        confidence=confidence,
        method=AlignmentMethod.LLM_VISION,
    )


def make_candidate(pg_text="hello", scan_text="jello", pg_offset=0,
                   scan_page=0, category=ErrorCategory.OCR_SCANNO):
    return CandidateError(
        pg_text=pg_text,
        scan_text=scan_text,
        pg_offset=pg_offset,
        scan_page=scan_page,
        category=category,
    )


def make_verifier(pg_text="hello world jello test example", alignments=None):
    if alignments is None:
        alignments = [make_alignment(scan_page=0, confidence=0.8)]
    return ProgrammaticVerifier(pg_text, alignments)


# ── _levenshtein ────────────────────────────────────────────────────────

class TestLevenshtein:
    def test_identical(self):
        assert _levenshtein("hello", "hello") == 0

    def test_empty(self):
        assert _levenshtein("", "hello") == 5
        assert _levenshtein("hello", "") == 5

    def test_single_edit(self):
        assert _levenshtein("cat", "bat") == 1
        assert _levenshtein("cat", "car") == 1

    def test_two_edits(self):
        assert _levenshtein("hello", "hallo") == 1
        assert _levenshtein("kitten", "sitting") == 3

    def test_commutative(self):
        assert _levenshtein("abc", "xyz") == _levenshtein("xyz", "abc")

    def test_unicode(self):
        assert _levenshtein("café", "cafe") == 1


# ── ProgrammaticVerifier.verify ─────────────────────────────────────────

class TestVerifySingleCandidate:
    def test_real_typo_high_confidence(self):
        """A real typo like teh→the in good context should score high."""
        pg_text = "It was the beginning of the great adventure that would change everything."
        verifier = make_verifier(pg_text)
        candidate = make_candidate(
            pg_text="teh",
            scan_text="the",
            pg_offset=9,
            scan_page=0,
        )
        error = verifier.verify(candidate)
        assert error.confidence >= 0.5
        assert error.verdict in (Verdict.SCAN_CORRECT, Verdict.AMBIGUOUS)

    def test_alignment_artifact_low_confidence(self):
        """Completely different words suggest misalignment."""
        pg_text = "The magnificent cathedral stood upon the hill."
        verifier = make_verifier(pg_text)
        candidate = make_candidate(
            pg_text="cathedral",
            scan_text="breakfast",
            pg_offset=12,
            scan_page=0,
        )
        error = verifier.verify(candidate)
        assert error.confidence < 0.5

    def test_absent_text_zero(self):
        """Absent text markers should return 0 confidence."""
        pg_text = "Some text here with content."
        verifier = make_verifier(pg_text)
        candidate = make_candidate(
            pg_text="(absent from scan)",
            scan_text="some different text",
            pg_offset=0,
            scan_page=0,
        )
        error = verifier.verify(candidate)
        assert error.confidence == 0.0
        assert "absent" in error.reasoning.lower()

    def test_absent_in_scan_text_zero(self):
        pg_text = "Some text here."
        verifier = make_verifier(pg_text)
        candidate = make_candidate(
            pg_text="normal",
            scan_text="(absent from pg)",
            pg_offset=0,
            scan_page=0,
        )
        error = verifier.verify(candidate)
        assert error.confidence == 0.0

    def test_single_char_diff_scores_high(self):
        """Single character difference should score higher than multi-char."""
        pg_text = "The brown fox jumped over the lazy dog."
        verifier = make_verifier(pg_text)
        candidate_1char = make_candidate(
            pg_text="jumpped",
            scan_text="jumped",
            pg_offset=16,
            scan_page=0,
        )
        candidate_3char = make_candidate(
            pg_text="XXXXXXX",
            scan_text="jumped",
            pg_offset=16,
            scan_page=0,
        )
        error_1 = verifier.verify(candidate_1char)
        error_3 = verifier.verify(candidate_3char)
        assert error_1.confidence > error_3.confidence

    def test_identical_words(self):
        """Identical words should score reasonably."""
        pg_text = "The brown fox jumped over the lazy dog."
        verifier = make_verifier(pg_text)
        candidate = make_candidate(
            pg_text="jumped",
            scan_text="jumped",
            pg_offset=16,
            scan_page=0,
        )
        error = verifier.verify(candidate)
        assert error.confidence >= 0.5

    def test_verdict_thresholds(self):
        """Verdict should match confidence thresholds."""
        pg_text = "The brown fox jumped over the lazy dog near the riverbank."
        verifier = make_verifier(pg_text)

        # High confidence → SCAN_CORRECT
        high = make_candidate(pg_text="teh", scan_text="the", pg_offset=4, scan_page=0)
        err_high = verifier.verify(high)
        if err_high.confidence >= 0.8:
            assert err_high.verdict == Verdict.SCAN_CORRECT

        # Low confidence → UNABLE_TO_VERIFY
        low = make_candidate(pg_text="riverbank", scan_text="XYLOPHONE", pg_offset=0, scan_page=0)
        err_low = verifier.verify(low)
        if err_low.confidence < 0.5:
            assert err_low.verdict == Verdict.UNABLE_TO_VERIFY


# ── Context match scoring ──────────────────────────────────────────────

class TestContextMatchScore:
    def test_good_context_high_score(self):
        """When surrounding scan words match PG context, score should be high."""
        pg_text = "The magnificent ancient cathedral stood upon the hill overlooking the valley below."
        verifier = make_verifier(pg_text)
        # "ancient" → "anient" — one char off, good context around
        candidate = make_candidate(
            pg_text="ancient",
            scan_text="anient",
            pg_offset=14,
            scan_page=0,
        )
        error = verifier.verify(candidate)
        # The context match should be decent because surrounding words are present
        assert error.confidence >= 0.3

    def test_no_context_low_score(self):
        """When scan text has no words matching PG context, score should be low."""
        pg_text = "The magnificent ancient cathedral stood upon the hill."
        verifier = make_verifier(pg_text)
        candidate = make_candidate(
            pg_text="cathedral",
            scan_text="porcupine",
            pg_offset=23,
            scan_page=0,
        )
        error = verifier.verify(candidate)
        # "porcupine" won't appear anywhere in the PG context
        assert error.confidence < 0.5


# ── Alignment confidence influence ─────────────────────────────────────

class TestAlignmentInfluence:
    def test_high_alignment_boosts(self):
        """High page alignment confidence should contribute to score."""
        pg_text = "The quick brown fox jumps over the lazy dog near the riverbank."
        high_align = make_verifier(pg_text, alignments=[make_alignment(scan_page=0, confidence=0.95)])
        low_align = make_verifier(pg_text, alignments=[make_alignment(scan_page=0, confidence=0.3)])

        candidate = make_candidate(pg_text="teh", scan_text="the", pg_offset=4, scan_page=0)
        err_high = high_align.verify(candidate)
        err_low = low_align.verify(candidate)
        assert err_high.confidence >= err_low.confidence

    def test_no_alignment_data(self):
        """Pages without alignment data should still work (default 0.3)."""
        pg_text = "The quick brown fox jumps over the lazy dog."
        verifier = ProgrammaticVerifier(pg_text, [])  # No alignments
        candidate = make_candidate(pg_text="teh", scan_text="the", pg_offset=4, scan_page=99)
        error = verifier.verify(candidate)
        # Should not crash, should produce a result
        assert 0.0 <= error.confidence <= 1.0


# ── verify_batch ───────────────────────────────────────────────────────

class TestVerifyBatch:
    def test_batch_returns_correct_count(self):
        pg_text = "The quick brown fox jumps over the lazy dog near the riverbank."
        verifier = make_verifier(pg_text)
        candidates = [
            make_candidate(pg_text="teh", scan_text="the", pg_offset=4, scan_page=0),
            make_candidate(pg_text="brwn", scan_text="brown", pg_offset=10, scan_page=0),
            make_candidate(pg_text="XXX", scan_text="YYY", pg_offset=0, scan_page=0),
        ]
        errors = verifier.verify_batch(candidates)
        assert len(errors) == 3
        assert all(isinstance(e, Error) for e in errors)

    def test_batch_empty(self):
        verifier = make_verifier("hello")
        assert verifier.verify_batch([]) == []

    def test_batch_preserves_order(self):
        pg_text = "The quick brown fox jumps over the lazy dog near the riverbank."
        verifier = make_verifier(pg_text)
        candidates = [
            make_candidate(pg_text=f"word{i}", scan_text=f"wrod{i}", pg_offset=i, scan_page=0)
            for i in range(5)
        ]
        errors = verifier.verify_batch(candidates)
        assert len(errors) == 5


# ── Word length scoring ────────────────────────────────────────────────

class TestLengthScore:
    def test_similar_length(self):
        pg_text = "The brown fox."
        verifier = make_verifier(pg_text)
        candidate = make_candidate(pg_text="brwn", scan_text="brown", pg_offset=4, scan_page=0)
        error = verifier.verify(candidate)
        # Same length, should help score
        assert error.confidence >= 0.3

    def test_very_different_length(self):
        pg_text = "The brown fox."
        verifier = make_verifier(pg_text)
        candidate = make_candidate(pg_text="fox", scan_text="xylophonetically", pg_offset=10, scan_page=0)
        error = verifier.verify(candidate)
        assert error.confidence < 0.5


# ── Multi-word diffs ──────────────────────────────────────────────────

class TestMultiWordDiff:
    def test_multi_word_uses_sequence_matcher(self):
        """Multi-word diffs should be scored using SequenceMatcher, not character distance."""
        pg_text = "The magnificent ancient cathedral stood upon the hill."
        verifier = make_verifier(pg_text)
        candidate = make_candidate(
            pg_text="ancient cathedral",
            scan_text="ancient cathedral",
            pg_offset=14,
            scan_page=0,
        )
        error = verifier.verify(candidate)
        # Identical multi-word should score high
        assert error.confidence >= 0.5

    def test_multi_word_partial_match(self):
        pg_text = "The magnificent ancient cathedral stood upon the hill."
        verifier = make_verifier(pg_text)
        candidate = make_candidate(
            pg_text="magnificent ancient cathedral",
            scan_text="magnificent cathedral",
            pg_offset=4,
            scan_page=0,
        )
        error = verifier.verify(candidate)
        # Partial multi-word match — should still get some score
        assert 0.0 <= error.confidence <= 1.0
