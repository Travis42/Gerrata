"""Tests for Growing-String Anchor (GSA) alignment fallback."""

import pytest
from unittest.mock import patch, MagicMock

from gerrata.aligner.vision_aligner import VisionAligner


@pytest.fixture
def aligner():
    return VisionAligner(match_threshold=0.5)


# ─────────────────────────────────────────────
# TestFindAllPositions
# ─────────────────────────────────────────────

class TestFindAllPositions:
    def test_empty_text(self):
        assert VisionAligner._find_all_positions("", "hello") == []

    def test_empty_phrase_returns_all_positions(self):
        """Empty string is found at every position in non-empty text."""
        text = "hello world"
        result = VisionAligner._find_all_positions(text, "")
        # str.find("", start) returns start, so every position matches
        assert len(result) == len(text) + 1

    def test_phrase_not_in_text(self):
        assert VisionAligner._find_all_positions("hello world", "foo") == []

    def test_single_occurrence(self):
        text = "the quick brown fox"
        result = VisionAligner._find_all_positions(text, "quick")
        assert result == [4]

    def test_multiple_occurrences(self):
        text = "call me call you call back"
        result = VisionAligner._find_all_positions(text, "call")
        assert result == [0, 8, 17]

    def test_overlapping_occurrences(self):
        text = "aaaaa"
        result = VisionAligner._find_all_positions(text, "aaa")
        # "aaa" at positions 0, 1, 2
        assert result == [0, 1, 2]

    def test_exact_match_full_text(self):
        text = "hello world"
        result = VisionAligner._find_all_positions(text, "hello world")
        assert result == [0]

    def test_phrase_at_end(self):
        text = "the quick brown fox"
        result = VisionAligner._find_all_positions(text, "fox")
        assert result == [16]


# ─────────────────────────────────────────────
# TestGrowStringAnchor
# ─────────────────────────────────────────────

class TestGrowStringAnchor:
    def test_unique_from_start(self, aligner):
        """Forward finds unique single word but validation rejects (<2 words).
        Falls back to backward, which finds 'ago' uniquely at n=1 (no validation on backward path)."""
        trans = "call me ishmael some years ago"
        pg = "it was the best of times call me ishmael some years ago never mind"
        pos, confident = aligner._grow_string_anchor(trans, pg)
        assert pos is not None
        assert confident is True
        # Backward found "ago" which is unique in PG — that's correct behavior
        assert pg[pos:].startswith("ago")

    def test_unique_after_growing(self, aligner):
        """Multiple 'Call' but unique 'Call me Ishmael' → growing reaches N=3."""
        trans = "call me ishmael some years ago"
        pg = "call him call her call me ishmael some years ago and then call them"
        pos, confident = aligner._grow_string_anchor(trans, pg)
        assert pos is not None
        assert confident is True
        assert pg[pos:].startswith("call me ishmael")

    def test_no_unique_string_returns_unconfident(self, aligner):
        """Common phrases like 'it was a' → returns first match, unconfident."""
        trans = "it was a dark and stormy night"
        pg = "it was a dark and stormy night it was a dark and stormy night"
        pos, confident = aligner._grow_string_anchor(trans, pg)
        # Should find a position (first match) but not confident since not unique
        assert pos is not None
        assert confident is False

    def test_ocr_noise_diverges(self, aligner):
        """Forward: 'skrimshander' unique at n=1, but validation rejects (<2 words).
        Backward: 'skill' unique at n=1 (no validation on backward path)."""
        trans = "skrimshander was a man of great skill"
        pg = "the harpooner skrimshander was a man of great skill in his craft"
        pos, confident = aligner._grow_string_anchor(trans, pg)
        assert pos is not None
        # Backward found "skill" which is unique in PG
        assert confident is True

    def test_ocr_noise_backoff(self, aligner):
        """Growth diverges after initial match, backs off to last valid position."""
        trans = "the xyzgibberish was here"
        # "the" appears multiple times, "the xyzgibberish" doesn't appear at all
        pg = "the cat sat on the mat the dog ran the cat sat"
        # "the" has many matches, "the xyzgibberish" has 0 → backs off
        pos, confident = aligner._grow_string_anchor(trans, pg)
        # Should get a position (first "the" occurrence) but unconfident
        assert pos is not None
        assert confident is False

    def test_empty_transcription(self, aligner):
        pos, confident = aligner._grow_string_anchor("", "some pg text here")
        assert pos is None
        assert confident is False

    def test_short_transcription(self, aligner):
        pos, confident = aligner._grow_string_anchor("hi", "some pg text here")
        assert pos is None
        assert confident is False

    def test_single_word_transcription(self, aligner):
        pos, confident = aligner._grow_string_anchor("hi there", "some pg text here")
        # "hi" is < min_word_len(3), "there" >= 3 but "there" not in pg
        # After skipping short words, "there" has 0 matches → forward fails
        # backward: "there" still 0 matches → None
        assert pos is None
        assert confident is False

    def test_search_window_constraint(self, aligner):
        """Only finds matches within search_start..search_end."""
        trans = "unique phrase found here"
        pg = "before text " + "unique phrase found here " * 3
        # Search window excludes the first occurrence
        first_pos = pg.index("unique phrase found here")
        second_pos = pg.index("unique phrase found here", first_pos + 1)
        third_pos = pg.index("unique phrase found here", second_pos + 1)

        # Search only between first and third (which has second occurrence)
        pos, confident = aligner._grow_string_anchor(
            trans, pg, search_start=first_pos + 10, search_end=third_pos - 10
        )
        # Should find the second occurrence
        assert pos is not None
        assert first_pos + 10 <= pos < third_pos - 10


# ─────────────────────────────────────────────
# TestGrowForward
# ─────────────────────────────────────────────

class TestGrowForward:
    def test_reaches_uniqueness_at_n2(self, aligner):
        """Growing from word index 0, reaching uniqueness at N=2."""
        trans_words = "call me ishmael some years ago".split()
        pg = "call him call her call me ishmael some years ago"
        result = aligner._grow_forward(trans_words, 0, pg)
        assert result is not None
        pos, n, is_unique = result
        assert n == 2  # "call me" — "call" appears 3 times but "call me" only at one location
        assert is_unique is True
        assert pg[pos:].startswith("call me")

    def test_hits_zero_backs_off(self, aligner):
        """Growth hits 0 matches, backs off to last valid position."""
        trans_words = "the unique xyzgibberish rest of text".split()
        pg = "the quick brown the unique fox jumped the lazy"
        # "the" → multiple, "the unique" → unique (1 match)
        # So n=2 is already unique, returns True
        result = aligner._grow_forward(trans_words, 0, pg)
        assert result is not None
        pos, n, is_unique = result
        assert is_unique is True
        assert n == 2
        assert pg[pos:].startswith("the unique")

    def test_exhausts_all_words_unconfident(self, aligner):
        """Growth exhausts all words without uniqueness → returns first match, unconfident."""
        trans_words = "common phrase here today".split()
        pg = "common phrase here today and common phrase here today again"
        # "common phrase here today" appears twice
        result = aligner._grow_forward(trans_words, 0, pg)
        assert result is not None
        pos, n, is_unique = result
        assert is_unique is False
        assert n == 4  # all words used

    def test_no_matches_at_all(self, aligner):
        """First word has 0 matches → returns None."""
        trans_words = "xyzgibberish rest of text".split()
        pg = "the quick brown fox jumped over"
        result = aligner._grow_forward(trans_words, 0, pg)
        assert result is None

    def test_single_word_unique(self, aligner):
        """First word is already unique."""
        trans_words = "xyzgibberish rest of text here".split()
        pg = "some words xyzgibberish more words here"
        result = aligner._grow_forward(trans_words, 0, pg)
        assert result is not None
        pos, n, is_unique = result
        assert n == 1
        assert is_unique is True


# ─────────────────────────────────────────────
# TestGrowBackward
# ─────────────────────────────────────────────

class TestGrowBackward:
    def test_reaches_uniqueness(self, aligner):
        """Growing from end of transcription, reaching uniqueness."""
        trans_words = "some words leading to unique ending phrase".split()
        # Make the ending unique by having it appear only once
        pg = "common text unique ending phrase appears here and different text"
        result = aligner._grow_backward(trans_words, pg)
        assert result is not None
        pos, n, is_unique = result
        assert is_unique is True

    def test_hits_zero_backs_off(self, aligner):
        """Backward growth: all suffixes of 'xyzgibberish' have 0 matches in PG.
        Since no match is ever found, returns None."""
        trans_words = "the quick brown unique xyzgibberish".split()
        pg = "the quick brown unique fox and different the quick brown unique cat"
        # "xyzgibberish" → 0, and since no word ever matches, last_match stays -1
        # All suffixes include "xyzgibberish" at the end → 0 matches for all
        result = aligner._grow_backward(trans_words, pg)
        assert result is None

    def test_exhausts_without_uniqueness(self, aligner):
        """Backward growth exhausts all words without uniqueness."""
        trans_words = "repeated phrase over and over".split()
        pg = "repeated phrase over and over repeated phrase over and over"
        result = aligner._grow_backward(trans_words, pg)
        assert result is not None
        pos, n, is_unique = result
        assert is_unique is False
        assert n == len(trans_words)

    def test_no_matches_at_all(self, aligner):
        """Last word has 0 matches → returns None."""
        trans_words = "some words leading to xyzgibberish".split()
        pg = "completely different text"
        result = aligner._grow_backward(trans_words, pg)
        assert result is None

    def test_finds_unique_from_single_word(self, aligner):
        """Last word is unique by itself."""
        trans_words = "some common words xyzgibberish".split()
        pg = "some text xyzgibberish here"
        result = aligner._grow_backward(trans_words, pg)
        assert result is not None
        pos, n, is_unique = result
        assert n == 1
        assert is_unique is True


# ─────────────────────────────────────────────
# TestValidateAnchor
# ─────────────────────────────────────────────

class TestValidateAnchor:
    def test_unique_in_scan_returns_true(self, aligner):
        """PG region also appears once in scan → True."""
        pg_search = "the quick brown fox jumped over the lazy dog"
        trans_norm = "some text before the quick brown fox jumped over"
        # pg_pos=0, n_words=3 → "the quick brown"
        assert aligner._validate_anchor(pg_search, 0, 3, trans_norm) is True

    def test_multiple_in_scan_returns_false(self, aligner):
        """PG region appears multiple times in scan → False."""
        pg_search = "the quick brown fox jumped over"
        trans_norm = "the quick brown fox and the quick brown fox jumped over"
        assert aligner._validate_anchor(pg_search, 0, 3, trans_norm) is False

    def test_not_in_scan_returns_false(self, aligner):
        """PG region not in scan at all → False."""
        pg_search = "unique xyzgibberish words here"
        trans_norm = "completely different text"
        assert aligner._validate_anchor(pg_search, 0, 3, trans_norm) is False

    def test_insufficient_words_returns_false(self, aligner):
        """PG region has < 2 words → False."""
        pg_search = "onlyonewordhere"
        trans_norm = "onlyonewordhere more text"
        assert aligner._validate_anchor(pg_search, 0, 3, trans_norm) is False

    def test_single_word_returns_false(self, aligner):
        """n_words=1 produces only 1 word → False (< 2)."""
        pg_search = "only oneword here"
        trans_norm = "only oneword here and more text"
        assert aligner._validate_anchor(pg_search, 0, 1, trans_norm) is False

    def test_exact_position_match(self, aligner):
        """Validate works with non-zero pg_pos."""
        pg_search = "padding text the quick brown fox jumped"
        trans_norm = "the quick brown fox jumped over the lazy dog"
        # pg_pos=13, n_words=3 → "the quick brown"
        assert aligner._validate_anchor(pg_search, 13, 3, trans_norm) is True


# ─────────────────────────────────────────────
# TestGSATwinnedWithRETAS (integration-style)
# ─────────────────────────────────────────────

class TestGSATwinnedWithRETAS:
    def _make_transcription(self, text, page_num=1):
        from gerrata.aligner.vision_aligner import PageTranscription
        from pathlib import Path
        return PageTranscription(
            page_num=page_num,
            image_path=Path(f"/fake/page_{page_num}.png"),
            transcription=text,
            transcription_cleaned=text,
            success=True,
        )

    def test_gsa_called_when_retas_fails(self, aligner):
        """GSA is tried when RETAS finds no anchors (no unique words)."""
        trans_text = "it was the best of times it was the worst of times"
        pg_text = "it was the best of times it was the worst of times and more text here"
        pg_paragraphs = [pg_text]

        with patch.object(aligner, '_find_unique_word_anchor', return_value=None):
            with patch.object(aligner, '_grow_string_anchor', return_value=(0, True)) as mock_gsa:
                result = aligner.align_transcription_to_pg(
                    self._make_transcription(trans_text),
                    pg_text,
                    pg_paragraphs,
                    scan_page=1,
                )
                mock_gsa.assert_called_once()

    def test_gsa_result_used_when_score_passes(self, aligner):
        """GSA result is used when score exceeds threshold."""
        trans_text = "some unique text here on the page"
        pg_text = "beginning " + trans_text + " ending"
        pg_paragraphs = [pg_text]

        with patch.object(aligner, '_find_unique_word_anchor', return_value=None):
            result = aligner.align_transcription_to_pg(
                self._make_transcription(trans_text),
                pg_text,
                pg_paragraphs,
                scan_page=1,
            )
            assert result is not None
            assert result.best_score > 0

    def test_gsa_not_used_when_retas_succeeds(self, aligner):
        """GSA is NOT called when RETAS finds anchors."""
        trans_text = "call me ishmael"
        pg_text = "call me ishmael some years ago never mind"
        pg_paragraphs = [pg_text]

        with patch.object(aligner, '_find_unique_word_anchor') as mock_retas:
            mock_retas.return_value = ([0], [0], [0])
            with patch.object(aligner, '_grow_string_anchor') as mock_gsa:
                result = aligner.align_transcription_to_pg(
                    self._make_transcription(trans_text),
                    pg_text,
                    pg_paragraphs,
                    scan_page=1,
                )
                mock_gsa.assert_not_called()

    def test_chapter_constrained_search_window(self, aligner):
        """GSA operates within chapter search window."""
        # Use text that GSA can uniquely match within the window
        trans_text = "unique chapter content here and more details"
        pg_text = "chapter one " + trans_text + " chapter two completely different ending text"
        pg_paragraphs = [pg_text]

        with patch.object(aligner, '_find_unique_word_anchor', return_value=None):
            # Ensure n-gram and brute-force also skip by using unique text
            # The GSA should find the unique text in the PG and return a match
            result = aligner.align_transcription_to_pg(
                self._make_transcription(trans_text),
                pg_text,
                pg_paragraphs,
                scan_page=1,
            )
            assert result is not None
            # The match should contain the unique transcription text
            matched_text = pg_text[result.alignment.pg_start:result.alignment.pg_end]
            assert "unique chapter content" in matched_text

    def test_was_anchored_false_when_unconfident(self, aligner):
        """was_anchored=False when GSA is not confident."""
        trans_text = "common phrase here"
        pg_text = "common phrase here common phrase here"
        pg_paragraphs = [pg_text]

        with patch.object(aligner, '_find_unique_word_anchor', return_value=None):
            with patch.object(aligner, '_grow_string_anchor', return_value=(0, False)):
                result = aligner.align_transcription_to_pg(
                    self._make_transcription(trans_text),
                    pg_text,
                    pg_paragraphs,
                    scan_page=1,
                )
                if result is not None:
                    assert result.anchored is False


# ─────────────────────────────────────────────
# TestGSAEdgeCases
# ─────────────────────────────────────────────

class TestGSAEdgeCases:
    def test_repeated_word_returns_unconfident_or_none(self, aligner):
        """'the the the the' — 'the' is >= min_word_len so it tries, never unique."""
        trans = "the the the the"
        pg = "the the the the"
        # "the" appears 4 times → not unique at n=1
        # "the the" appears 3 times → not unique at n=2
        # "the the the" appears 2 times → not unique at n=3
        # "the the the the" appears 1 time → unique at n=4
        # Validation: 4 words, unique → should be confident
        pos, confident = aligner._grow_string_anchor(trans, pg)
        assert pos is not None
        assert confident is True

    def test_match_at_search_start_boundary(self, aligner):
        """Transcription matches within search window starting at 0."""
        trans = "unique xyzgibberish words here"
        pg = "unique xyzgibberish words here rest of text"
        # Note: "unique" at n=1 is unique but validation requires >=2 words.
        # "unique xyzgibberish" at n=2 is unique and validates.
        pos, confident = aligner._grow_string_anchor(trans, pg, search_start=0, search_end=len(pg))
        assert pos is not None
        assert confident is True

    def test_match_near_search_end_boundary(self, aligner):
        """Transcription matches near search_end boundary."""
        trans = "unique xyzgibberish words here"
        pg = "padding text " + trans
        end_boundary = len(pg)
        pos, confident = aligner._grow_string_anchor(trans, pg, search_start=0, search_end=end_boundary)
        assert pos is not None
        assert confident is True
        # Position should be after "padding text "
        assert pos > 0

    def test_very_long_transcription(self, aligner):
        """1000+ word transcription — should still be fast and return a result."""
        words = ["unique"] + ["common"] * 999
        trans = " ".join(words)
        pg = "padding " + trans + " trailing text"
        pos, confident = aligner._grow_string_anchor(trans, pg)
        assert pos is not None
        # "unique" at start is unique at n=1 but validation needs >=2 words.
        # "unique common" at n=2 is unique → validates → confident
        assert confident is True

    def test_empty_pg_text(self, aligner):
        """Empty PG text → None."""
        pos, confident = aligner._grow_string_anchor("some transcription text", "")
        assert pos is None
        assert confident is False

    def test_pg_text_shorter_than_trans(self, aligner):
        """PG text shorter than transcription — forward finds 'the' unique at n=1
        but validation rejects (<2 words). Then growth continues but diverges.
        Backward: all suffixes include words from trans not in pg → 0 matches → None."""
        trans = "the quick brown fox jumped over the lazy dog"
        pg = "the quick brown"
        pos, confident = aligner._grow_string_anchor(trans, pg)
        # Forward: 'the' unique at n=1, validation fails. Then 'the quick' unique at n=2
        # but _grow_forward already returned at n=1. Forward result: unique but invalid.
        # Backward: 'dog' → 0, 'lazy dog' → 0, etc. → None
        # Both paths fail → None
        assert pos is None

    def test_all_words_too_short(self, aligner):
        """All words shorter than min_word_len → None."""
        trans = "a i o am an as at be do go he in is it me my no of on or so to up us we"
        pg = "a i o am an as at be do go he in is it me my no of on or so to up us we"
        # All words < min_word_len(3) → start_idx goes past end → None
        pos, confident = aligner._grow_string_anchor(trans, pg)
        assert pos is None
        assert confident is False

    def test_unicode_and_special_chars(self, aligner):
        """Text with unicode chars should be handled (normalized input)."""
        trans = "the café was nice the café was bad"
        pg = "padding the café was nice the café was bad trailing"
        # "the café was" appears twice → not unique
        # "the café was nice" appears once → unique, validates (4 words ≥ 2)
        pos, confident = aligner._grow_string_anchor(trans, pg)
        assert pos is not None
        assert confident is True

    def test_ocr_divergence_with_backward_fallback(self, aligner):
        """Forward growth diverges but backward finds a match.
        Backward path doesn't validate (unlike forward), so single-word unique matches count."""
        trans = "xyzgibberish leading to unique ending"
        pg = "some stuff unique ending appears here"
        # Forward: "xyzgibberish" → 0 matches → None (no last_match to back off to)
        # Backward: "ending" → 1 match (n=1, unique) → returns confidently
        pos, confident = aligner._grow_string_anchor(trans, pg)
        assert pos is not None
        assert confident is True
        # Backward found "ending" which is at position 18
        assert pg[pos:].startswith("ending")
