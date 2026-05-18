"""Tests for global_anchor.py — phrase extraction, matching, monotonic assignment, alignment."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from gerrata.aligner.global_anchor import (
    tokenize_words,
    find_word_sequence,
    extract_distinctive_phrases,
    build_monotonic_assignment,
    score_window,
    GlobalAnchorAligner,
)
from gerrata.aligner.vision_aligner import PageTranscription
from gerrata.models import Alignment, AlignmentMethod


# ── Helpers ─────────────────────────────────────────────────────────────

def make_transcription(page_num, text, success=True):
    return PageTranscription(
        page_num=page_num,
        image_path=None,
        transcription=text,
        transcription_cleaned=text,
        success=success,
        error=None,
        model_used="test",
    )


def make_pg_text():
    """Create a long enough PG text with distinctive phrases for testing."""
    # Make paragraphs long enough and distinctive enough for phrase extraction
    # Each paragraph is ~200+ chars, well above skip_chars=60
    parts = [
        "The quick brown fox jumps over the lazy dog near the riverbank in springtime. "
        "It watched the children playing by the water while fishermen cast their lines. "
        "The morning sun reflected golden patterns on the surface of the tranquil stream. ",

        "It was a bright cold day in April and the clocks were striking thirteen. "
        "Winston Smith his chin nuzzled into his breast in an effort to escape the "
        "vile wind slipped quickly through the glass doors of Victory Mansions. ",

        "In a hole in the ground there lived a hobbit not a nasty dirty wet hole "
        "filled with the ends of worms and an oozy smell nor yet a dry bare sandy hole "
        "with nothing in it to sit down on or to eat it was a hobbit-hole and that means comfort. ",

        "The ancient cathedral stood upon the hill overlooking the valley below. "
        "Pilgrims traveled from distant provinces to admire its magnificent stained glass windows "
        "and hear the organ music echo through the vaulted stone arches. ",

        "Several distinguished gentlemen gathered at the assembly rooms for the annual ball. "
        "The orchestra played a lively waltz while ladies in silk gowns danced elegantly "
        "across the polished marble floor beneath glittering crystal chandeliers. ",

        "The parliamentary committee debated the proposed reform bill with great vehemence. "
        "Ministers exchanged heated arguments about constitutional amendments while the "
        "stenographer recorded every word for the official parliamentary proceedings. ",

        "She walked along the promenade contemplating the extraordinary beauty of the sunset. "
        "The Mediterranean stretched endlessly before her its waters shimmering with "
        "reflections of crimson and gold from the disappearing sun. ",

        "The pharmaceutical establishment dispensed various medicinal compounds to patients. "
        "Apothecaries in white coats carefully measured tinctures and powders while "
        "consulting leather-bound pharmacopoeias for the correct dosages. ",
    ]
    return "".join(parts * 5)  # ~10000+ chars


# ── tokenize_words ──────────────────────────────────────────────────────

class TestTokenizeWords:
    def test_basic(self):
        result = tokenize_words("Hello World")
        assert len(result) == 2
        assert result[0] == (0, "hello")
        assert result[1] == (6, "world")

    def test_preserves_position(self):
        result = tokenize_words("abc def ghi")
        assert result[0][0] == 0
        assert result[1][0] == 4
        assert result[2][0] == 8

    def test_lowercases(self):
        result = tokenize_words("HELLO World")
        assert result[0][1] == "hello"
        assert result[1][1] == "world"

    def test_skips_punctuation(self):
        result = tokenize_words("hello, world!")
        assert len(result) == 2
        assert result[0][1] == "hello"
        assert result[1][1] == "world"

    def test_empty_string(self):
        assert tokenize_words("") == []

    def test_only_punctuation(self):
        assert tokenize_words("...!!!") == []

    def test_numbers_excluded(self):
        result = tokenize_words("hello 123 world")
        assert len(result) == 2
        assert result[0][1] == "hello"
        assert result[1][1] == "world"

    def test_long_text(self):
        text = "The quick brown fox jumps over the lazy dog. " * 100
        result = tokenize_words(text)
        assert len(result) > 0
        # Check positions are increasing
        for i in range(1, len(result)):
            assert result[i][0] > result[i - 1][0]


# ── find_word_sequence ─────────────────────────────────────────────────

class TestFindWordSequence:
    def test_exact_match(self):
        pg_words = tokenize_words("the quick brown fox jumps over the lazy dog")
        target = ["quick", "brown", "fox"]
        matches = find_word_sequence(pg_words, target)
        assert len(matches) >= 1
        assert matches[0][0] == 4  # offset of "quick" in original text

    def test_no_match(self):
        pg_words = tokenize_words("the quick brown fox")
        target = ["zebra", "giraffe"]
        matches = find_word_sequence(pg_words, target)
        assert matches == []

    def test_multiple_matches(self):
        pg_words = tokenize_words("hello world hello world hello world")
        target = ["hello", "world"]
        matches = find_word_sequence(pg_words, target)
        assert len(matches) == 3

    def test_empty_target(self):
        pg_words = tokenize_words("hello world")
        assert find_word_sequence(pg_words, []) == []

    def test_empty_pg(self):
        assert find_word_sequence([], ["hello"]) == []

    def test_start_from(self):
        pg_words = tokenize_words("hello world hello world hello world")
        target = ["hello", "world"]
        matches = find_word_sequence(pg_words, target, start_from=1)
        # Should skip the first match at word index 0
        assert all(m[1] >= 1 for m in matches)
        assert len(matches) == 2

    def test_returns_char_offset(self):
        text = "abc hello def"
        pg_words = tokenize_words(text)
        target = ["hello"]
        matches = find_word_sequence(pg_words, target)
        assert len(matches) == 1
        assert matches[0][0] == 4  # "hello" starts at char 4

    def test_long_phrase(self):
        text = "the quick brown fox jumps over the lazy dog near the riverbank"
        pg_words = tokenize_words(text)
        target = ["quick", "brown", "fox", "jumps", "over"]
        matches = find_word_sequence(pg_words, target)
        assert len(matches) == 1


# ── extract_distinctive_phrases ────────────────────────────────────────

class TestExtractDistinctivePhrases:
    def test_basic(self):
        text = "The quick brown fox jumps over the lazy dog near the riverbank. " * 3
        phrases = extract_distinctive_phrases(text, min_words=5, max_phrases=3)
        assert len(phrases) >= 1
        assert all(len(p) >= 5 for p in phrases)

    def test_short_text(self):
        assert extract_distinctive_phrases("Too short") == []

    def test_empty_text(self):
        assert extract_distinctive_phrases("") == []

    def test_respects_max_phrases(self):
        long_text = ("The quick brown fox jumps over the lazy dog near the riverbank. "
                     "It was a bright cold day in April and the clocks were striking thirteen. "
                     "Several distinguished gentlemen gathered at the assembly rooms for the annual ball. "
                     "The parliamentary committee debated the proposed reform bill with great vehemence. ")
        phrases = extract_distinctive_phrases(long_text, min_words=6, max_phrases=2)
        assert len(phrases) <= 2

    def test_skip_chars(self):
        text = "HEADER TITLE " + "The quick brown fox jumps over the lazy dog near the riverbank. " * 3
        # With skip_chars=15, should skip the header
        phrases = extract_distinctive_phrases(text, min_words=5, max_phrases=3, skip_chars=15)
        assert len(phrases) >= 1
        # None should start with "header" or "title"
        if phrases:
            assert phrases[0][0] not in ("header", "title")

    def test_returns_lowercase(self):
        text = "The QUICK Brown FOX jumps over the lazy dog near the riverbank."
        phrases = extract_distinctive_phrases(text, min_words=5, max_phrases=3)
        for phrase in phrases:
            assert all(w == w.lower() for w in phrase)

    def test_common_words_scored_lower(self):
        # Text with only common words shouldn't produce phrases (too low distinctiveness)
        text = "the and the and the and the and the and the and the and the and the and the."
        phrases = extract_distinctive_phrases(text, min_words=8, max_phrases=5)
        assert len(phrases) == 0


# ── build_monotonic_assignment ─────────────────────────────────────────

class TestBuildMonotonicAssignment:
    def test_basic_monotonic(self):
        candidates = {
            0: [(100, 0.9), (500, 0.5)],
            1: [(200, 0.8), (600, 0.4)],
            2: [(300, 0.9)],
        }
        assignment = build_monotonic_assignment(candidates)
        assert assignment[0] == 100
        assert assignment[1] == 200
        assert assignment[2] == 300
        # Verify monotonic
        assert assignment[0] < assignment[1] < assignment[2]

    def test_empty_candidates(self):
        assert build_monotonic_assignment({}) == {}

    def test_skips_no_candidate_pages(self):
        candidates = {
            0: [(100, 0.9)],
            1: [],  # no candidates
            2: [(200, 0.8)],
        }
        assignment = build_monotonic_assignment(candidates)
        assert 1 not in assignment
        assert assignment[0] < assignment[2]

    def test_all_before_threshold(self):
        """When all candidates for page N are before page N-1's position,
        the tolerance window (-200) should still allow some assignment."""
        candidates = {
            0: [(1000, 0.9)],
            1: [(800, 0.9)],  # 200 behind page 0 — at the tolerance edge
        }
        assignment = build_monotonic_assignment(candidates)
        # Page 1 should still get assigned via tolerance
        assert 1 in assignment

    def test_spacing_influences_choice(self):
        """With 3+ confirmed pages, spacing should influence candidate selection."""
        candidates = {
            0: [(100, 0.5)],
            1: [(200, 0.5)],
            2: [(400, 0.5), (250, 0.4)],  # 400 is more consistent with spacing
            3: [(600, 0.5)],
        }
        assignment = build_monotonic_assignment(candidates)
        # With confirmed pages 0=100, 1=200, spacing is 100 chars/page
        # Expected for page 2: ~300, so 400 is closer than 250
        # But the scoring is weighted: 0.7*score + 0.3*position
        assert assignment[2] == 400

    def test_unordered_input(self):
        """Page candidates can come in any order; assignment should still be monotonic."""
        candidates = {
            5: [(500, 0.9)],
            2: [(200, 0.9)],
            8: [(800, 0.9)],
            0: [(100, 0.9)],
        }
        assignment = build_monotonic_assignment(candidates)
        pages = sorted(assignment.keys())
        for i in range(1, len(pages)):
            assert assignment[pages[i - 1]] < assignment[pages[i]]


# ── score_window ───────────────────────────────────────────────────────

class TestScoreWindow:
    def test_matching_window(self):
        pg_text = "the quick brown fox jumps over the lazy dog near the riverbank. "
        trans_text = "quick brown fox jumps over the lazy dog near the riverbank"
        score = score_window(trans_text, pg_text, 4, 60)
        assert score > 0.5

    def test_non_matching_window(self):
        pg_text = "the quick brown fox jumps over the lazy dog"
        trans_text = "zebra giraffe elephant lion tiger"
        score = score_window(trans_text, pg_text, 0, 43)
        assert score < 0.3

    def test_empty_transcription(self):
        score = score_window("", "hello world", 0, 11)
        assert score == 0.0

    def test_empty_window(self):
        score = score_window("hello", "hello world", 0, 0)
        assert score == 0.0

    def test_out_of_bounds(self):
        score = score_window("hello", "hi", 0, 100)
        assert score == 0.0

    def test_negative_start(self):
        score = score_window("hello", "world", -1, 5)
        assert score == 0.0

    def test_partial_match(self):
        pg_text = "the quick brown fox jumps over the lazy dog near the riverbank"
        trans_text = "quick brown fox zebra giraffe"
        score = score_window(trans_text, pg_text, 4, 50)
        assert 0 < score < 0.8


# ── GlobalAnchorAligner ───────────────────────────────────────────────

class TestGlobalAnchorAligner:
    @pytest.fixture
    def pg_text(self):
        return make_pg_text()

    def test_align_matching_pages(self, pg_text):
        """Pages with distinctive phrases from PG text should align."""
        # Use long enough transcriptions to produce distinctive phrases
        transcriptions = [
            make_transcription(0, "The quick brown fox jumps over the lazy dog near the riverbank in springtime. It watched the children playing by the water while fishermen cast their lines."),
            make_transcription(1, "It was a bright cold day in April and the clocks were striking thirteen. Winston Smith his chin nuzzled into his breast in an effort to escape the vile wind."),
            make_transcription(2, "The ancient cathedral stood upon the hill overlooking the valley below. Pilgrims traveled from distant provinces to admire its magnificent stained glass windows."),
        ]
        aligner = GlobalAnchorAligner(min_phrase_words=6, min_score=0.15)
        alignments = aligner.align_all_pages(transcriptions, pg_text)
        assert len(alignments) >= 2  # At least 2 should align

    def test_no_distinctive_phrases_skipped(self, pg_text):
        """Pages with only common words should not produce alignments."""
        transcriptions = [
            make_transcription(0, "the and the and the and the and the and the and the."),
        ]
        aligner = GlobalAnchorAligner(min_phrase_words=6, min_score=0.2)
        alignments = aligner.align_all_pages(transcriptions, pg_text)
        assert len(alignments) == 0

    def test_failed_transcriptions_skipped(self, pg_text):
        """Failed transcriptions should not produce alignments."""
        transcriptions = [
            make_transcription(0, "hello", success=True),
            make_transcription(1, "", success=False),
            make_transcription(2, "world", success=True),
        ]
        aligner = GlobalAnchorAligner(min_phrase_words=2, min_score=0.2)
        alignments = aligner.align_all_pages(transcriptions, pg_text)
        # No alignments expected since text is too short/distinctive
        assert 1 not in [a.scan_page for a in alignments]

    def test_low_score_filtered(self, pg_text):
        """Alignments below min_score should be filtered out."""
        # Use very different text
        transcriptions = [
            make_transcription(0, "The quick brown fox jumps over the lazy dog near the riverbank."),
        ]
        aligner = GlobalAnchorAligner(min_phrase_words=6, min_score=0.99)  # Very high threshold
        alignments = aligner.align_all_pages(transcriptions, pg_text)
        # Unlikely to meet 0.99 threshold
        assert len(alignments) == 0

    def test_alignments_sorted_by_pg_start(self, pg_text):
        """Result alignments should be sorted by pg_start."""
        transcriptions = [
            make_transcription(2, "The parliamentary committee debated the proposed reform bill with great vehemence. Ministers exchanged heated arguments about constitutional amendments."),
            make_transcription(0, "The quick brown fox jumps over the lazy dog near the riverbank in springtime. It watched the children playing by the water while fishermen cast their lines."),
            make_transcription(1, "It was a bright cold day in April and the clocks were striking thirteen. Winston Smith his chin nuzzled into his breast in an effort to escape the vile wind."),
        ]
        aligner = GlobalAnchorAligner(min_phrase_words=6, min_score=0.15)
        alignments = aligner.align_all_pages(transcriptions, pg_text)
        for i in range(1, len(alignments)):
            assert alignments[i].pg_start >= alignments[i - 1].pg_start

    def test_alignment_confidence(self, pg_text):
        """alignment_confidence should return coverage ratio."""
        aligner = GlobalAnchorAligner()
        alignments = [
            Alignment(pg_start=0, pg_end=500, scan_page=0, scan_image_path=None,
                      confidence=0.8, method=AlignmentMethod.LLM_VISION),
            Alignment(pg_start=500, pg_end=1000, scan_page=1, scan_image_path=None,
                      confidence=0.8, method=AlignmentMethod.LLM_VISION),
        ]
        conf = aligner.alignment_confidence(alignments, pg_text_len=2000)
        assert conf == pytest.approx(0.5)

    def test_empty_transcriptions(self, pg_text):
        aligner = GlobalAnchorAligner()
        alignments = aligner.align_all_pages([], pg_text)
        assert alignments == []

    def test_empty_pg_text(self):
        transcriptions = [make_transcription(0, "hello world")]
        aligner = GlobalAnchorAligner()
        alignments = aligner.align_all_pages(transcriptions, "")
        assert alignments == []

    def test_long_text_coverage(self, pg_text):
        """With enough matching pages, coverage should be reasonable."""
        # Create many pages with PG text snippets (long enough for phrase extraction)
        transcriptions = []
        snippets = [
            "The quick brown fox jumps over the lazy dog near the riverbank in springtime. It watched the children playing by the water.",
            "It was a bright cold day in April and the clocks were striking thirteen. Winston Smith his chin nuzzled into his breast.",
            "In a hole in the ground there lived a hobbit not a nasty dirty wet hole filled with the ends of worms and an oozy smell.",
            "The ancient cathedral stood upon the hill overlooking the valley below. Pilgrims traveled from distant provinces.",
            "Several distinguished gentlemen gathered at the assembly rooms for the annual ball. The orchestra played a lively waltz.",
            "The parliamentary committee debated the proposed reform bill with great vehemence. Ministers exchanged heated arguments.",
            "She walked along the promenade contemplating the extraordinary beauty of the sunset. The Mediterranean stretched endlessly.",
            "The pharmaceutical establishment dispensed various medicinal compounds to patients. Apothecaries in white coats carefully measured.",
        ]
        for i, snippet in enumerate(snippets):
            transcriptions.append(make_transcription(i, snippet))

        aligner = GlobalAnchorAligner(min_phrase_words=6, min_score=0.10)
        alignments = aligner.align_all_pages(transcriptions, pg_text)
        # Most should align
        assert len(alignments) >= 3
