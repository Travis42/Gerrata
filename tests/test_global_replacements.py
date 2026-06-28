"""Tests for global replacement detection."""

import pytest
from gerrata.checker.global_replacements import GlobalReplacementDetector, GlobalReplacement
from gerrata.text_utils import trim_shared_edges, normalize_possessive


class TestNormalizePossessive:
    """Test possessive suffix stripping."""

    def test_straight_apostrophe(self):
        assert normalize_possessive("Bor's") == "Bor"

    def test_smart_apostrophe(self):
        assert normalize_possessive("Bor\u2019s") == "Bor"

    def test_curly_apostrophe(self):
        assert normalize_possessive("Bor\u2018s") == "Bor"

    def test_no_possessive(self):
        assert normalize_possessive("Aesir") == "Aesir"

    def test_empty(self):
        assert normalize_possessive("") == ""


class TestTrimSharedEdges:
    """Test the shared edge trimming utility."""

    def test_trailing_comma(self):
        assert trim_shared_edges("Bor,", "Bör,") == ("Bor", "Bör")

    def test_no_shared_edges(self):
        assert trim_shared_edges("swoard", "sword") == ("swoard", "sword")

    def test_period(self):
        assert trim_shared_edges("Balidr.", "Balldr.") == ("Balidr", "Balldr")

    def test_empty_fallback(self):
        assert trim_shared_edges(",", ",") == (",", ",")


class TestGlobalReplacementDetection:
    """Test the GlobalReplacementDetector."""

    def make_error(self, pg_text, scan_text, page=1, confidence=0.5):
        return {
            "candidate": {
                "pg_text": pg_text,
                "scan_text": scan_text,
                "scan_page": page,
            },
            "confidence": confidence,
        }

    def test_recurring_name_qualifies(self):
        """Aesir → Æsir appearing 5x in PG text qualifies."""
        errors = [
            self.make_error("Aesir", "Æsir", page=116),
            self.make_error("Aesir", "Æsir", page=125),
        ]
        pg_text = "The Aesir were gods. Aesir lived in Asgard. Aesir were mighty. Aesir came from fire. Aesir ruled."
        detector = GlobalReplacementDetector()
        results = detector.detect(errors, pg_text)
        assert len(results) == 1
        assert results[0].pg_text == "Aesir"
        assert results[0].scan_text == "Æsir"
        assert results[0].occurrences_in_pg == 5
        assert results[0].caught_by_errata == 2

    def test_single_occurrence_does_not_qualify(self):
        """swoard → sword appearing 1x in PG text does NOT qualify."""
        errors = [self.make_error("swoard", "sword", page=46)]
        pg_text = "The swoard Gram is replaced by Balmung."
        detector = GlobalReplacementDetector()
        results = detector.detect(errors, pg_text)
        assert len(results) == 0

    def test_possessive_collapses_to_root(self):
        """Bor's and Bor collapse to the same global replacement."""
        errors = [
            self.make_error("Bor", "Bör", page=25),
            self.make_error("Bor's", "Bör's", page=25),
            self.make_error("Bor's", "Bör's", page=26),
        ]
        pg_text = "Bor had sons. Bor's wife was Bestla. Bor's children were Odin, Vili, and Ve."
        detector = GlobalReplacementDetector()
        results = detector.detect(errors, pg_text)
        assert len(results) == 1
        assert results[0].pg_text == "Bor"
        assert results[0].scan_text == "Bör"
        assert results[0].occurrences_in_pg == 3  # Bor + Bor's + Bor's

    def test_identical_after_trim_skipped(self):
        """Pairs that are identical after trimming are skipped."""
        errors = [self.make_error("word;", "word;", page=1)]
        pg_text = "word; word; word; word;"
        detector = GlobalReplacementDetector()
        results = detector.detect(errors, pg_text)
        assert len(results) == 0

    def test_smart_quote_diff_still_qualifies(self):
        """Mimir's → Mímir's: smart vs straight quote is NOT the only diff (Mimir vs Mímir)."""
        errors = [self.make_error("Mimir's", "Mímir\u2019s", page=28)]
        pg_text = "Mimir's hall was under the root. Mimir's wisdom was great."
        detector = GlobalReplacementDetector()
        results = detector.detect(errors, pg_text)
        # This SHOULD qualify — Mimir vs Mímir is a real word diff,
        # not just a quote character difference
        assert len(results) == 1
        assert results[0].pg_text == "Mimir"

    def test_footnote_marker_skipped(self):
        """OCR garbage markers don't qualify."""
        errors = [self.make_error("Viking (1)", "Viking*", page=13)]
        pg_text = "Viking Viking Viking Viking Viking"
        detector = GlobalReplacementDetector()
        # The trim should strip the footnote markers, leaving "Viking" == "Viking" -> skipped
        results = detector.detect(errors, pg_text)
        assert len(results) == 0

    def test_alignment_artifact_skipped(self):
        """Very different length pairs (alignment artifacts) are skipped."""
        errors = [
            self.make_error("Iceland has", "* These pirates are from the north", page=32),
        ]
        pg_text = "Iceland has Iceland has Iceland has"
        detector = GlobalReplacementDetector()
        results = detector.detect(errors, pg_text)
        assert len(results) == 0

    def test_results_sorted_by_frequency(self):
        """Results are sorted by occurrences descending."""
        errors = [
            self.make_error("Aesir", "Æsir", page=10),
            self.make_error("Bor", "Bör", page=20),
        ]
        pg_text = "Aesir Aesir Aesir Aesir Aesir Bor Bor Bor Bor"
        detector = GlobalReplacementDetector()
        results = detector.detect(errors, pg_text)
        assert len(results) == 2
        assert results[0].pg_text == "Aesir"  # 5 occurrences
        assert results[1].pg_text == "Bor"    # 4 occurrences

    def test_multi_word_replacement_excluded(self):
        """Multi-word replacement pairs are excluded from global replacements."""
        errors = [
            self.make_error("Bor", "Bör's sons", page=25),
        ]
        pg_text = "Bor Bor Bor Bor Bor"
        detector = GlobalReplacementDetector()
        results = detector.detect(errors, pg_text)
        # "Bör's sons" normalizes to "Bör's sons" (multi-word after normalize)
        # Should be excluded
        assert len(results) == 0

    def test_very_different_lengths_excluded(self):
        """Pairs with very different lengths are excluded (insertion/deletion)."""
        errors = [
            self.make_error("cat", "catastrophe", page=1),
        ]
        pg_text = "cat cat cat cat cat"
        detector = GlobalReplacementDetector()
        results = detector.detect(errors, pg_text)
        assert len(results) == 0  # length difference too large

    def test_case_sensitive_counting(self):
        """Counting is case-sensitive — 'aesir' doesn't match 'Aesir'."""
        errors = [self.make_error("Aesir", "Æsir", page=10)]
        pg_text = "aesir aesir Aesir aesir"
        detector = GlobalReplacementDetector()
        results = detector.detect(errors, pg_text)
        assert len(results) == 0  # Only 1 case-sensitive occurrence of "Aesir"

    def test_word_boundary_matching(self):
        """Word-boundary matching — 'Bor' doesn't match inside 'Borrow'."""
        errors = [self.make_error("Bor", "Bör", page=10)]
        pg_text = "Borrow Borrow Borrow Borrow Borrow"
        detector = GlobalReplacementDetector()
        results = detector.detect(errors, pg_text)
        assert len(results) == 0  # No standalone "Bor" matches


class TestIsGlobalInstance:
    """Test checking if an individual error is covered by a global replacement."""

    def test_matches_global(self):
        gr = GlobalReplacement(pg_text="Aesir", scan_text="Æsir", occurrences_in_pg=5, caught_by_errata=2)
        error = {"candidate": {"pg_text": "Aesir", "scan_text": "Æsir"}}
        assert GlobalReplacementDetector().is_global_instance(error, [gr]) == True

    def test_matches_with_possessive(self):
        gr = GlobalReplacement(pg_text="Bor", scan_text="Bör", occurrences_in_pg=3, caught_by_errata=2)
        error = {"candidate": {"pg_text": "Bor's", "scan_text": "Bör's"}}
        assert GlobalReplacementDetector().is_global_instance(error, [gr]) == True

    def test_matches_with_punctuation(self):
        gr = GlobalReplacement(pg_text="Aesir", scan_text="Æsir", occurrences_in_pg=5, caught_by_errata=2)
        error = {"candidate": {"pg_text": "Aesir,", "scan_text": "Æsir,"}}
        assert GlobalReplacementDetector().is_global_instance(error, [gr]) == True

    def test_does_not_match_different_word(self):
        gr = GlobalReplacement(pg_text="Aesir", scan_text="Æsir", occurrences_in_pg=5, caught_by_errata=2)
        error = {"candidate": {"pg_text": "swoard", "scan_text": "sword"}}
        assert GlobalReplacementDetector().is_global_instance(error, [gr]) == False

    def test_empty_replacements_list(self):
        error = {"candidate": {"pg_text": "Aesir", "scan_text": "Æsir"}}
        assert GlobalReplacementDetector().is_global_instance(error, []) == False
