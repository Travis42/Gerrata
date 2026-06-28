"""Tests for _trim_shared_edges in the report generator.

Verifies that common leading/trailing punctuation is stripped from
both sides of an arrow fix, showing only the actual difference.
"""

import pytest
from gerrata.reporter.generator import ReportGenerator


class TestTrimSharedEdges:
    """Test the _trim_shared_edges static method."""

    def test_trailing_comma_stripped(self):
        """'Bor,' vs 'Bör,' should become 'Bor' vs 'Bör'."""
        pg, scan = ReportGenerator._trim_shared_edges("Bor,", "Bör,")
        assert pg == "Bor"
        assert scan == "Bör"

    def test_trailing_period_stripped(self):
        """'Vigfusson' vs 'Vigfússon' — no shared punctuation, unchanged."""
        pg, scan = ReportGenerator._trim_shared_edges("Vigfusson", "Vigfússon")
        assert pg == "Vigfusson"
        assert scan == "Vigfússon"

    def test_leading_quote_not_shared(self):
        """'"Vatsdaelasaga".' vs 'Vatsdælasaga.' — quote is only on PG side,
        but trailing period IS shared so it gets trimmed."""
        pg, scan = ReportGenerator._trim_shared_edges('"Vatsdaelasaga".', "Vatsdælasaga.")
        # Leading quote only on pg side — not trimmed (not shared)
        # But trailing period is shared on both sides — trimmed
        assert pg == '"Vatsdaelasaga"'
        assert scan == "Vatsdælasaga"

    def test_both_trailing_period_stripped(self):
        """'Balidr.' vs 'Balldr.' — shared trailing period."""
        pg, scan = ReportGenerator._trim_shared_edges("Balidr.", "Balldr.")
        assert pg == "Balidr"
        assert scan == "Balldr"

    def test_trailing_semicolon_stripped(self):
        """'Seggeir;' vs 'Siggeir,' — different trailing punct, not stripped."""
        # semicolon vs comma — not the same char, so not trimmed
        pg, scan = ReportGenerator._trim_shared_edges("Seggeir;", "Siggeir,")
        # The trailing chars differ (; vs ,) so nothing gets trimmed
        assert pg == "Seggeir;"
        assert scan == "Siggeir,"

    def test_shared_semicolon_stripped(self):
        """'king's;' vs 'crow's son and not a king's;' — shared trailing ;."""
        pg, scan = ReportGenerator._trim_shared_edges("king's;", "crow's son and not a king's;")
        assert pg == "king's"
        assert scan == "crow's son and not a king's"

    def test_no_punctuation_unchanged(self):
        """Plain words without punctuation pass through unchanged."""
        pg, scan = ReportGenerator._trim_shared_edges("swoard", "sword")
        assert pg == "swoard"
        assert scan == "sword"

    def test_empty_string_fallback(self):
        """If trimming would empty a string, return originals."""
        # Both are just punctuation — trimming would empty both
        pg, scan = ReportGenerator._trim_shared_edges(",", ",")
        assert pg == ","
        assert scan == ","

    def test_space_before_punct_stripped(self):
        """'word ;' vs 'word;' — shared leading 'word' isn't trimmed (alphanumeric),
        but trailing ' ;' vs ';' differ at the space."""
        # Actually: 'word ;' and 'word;' share trailing ';' but the char before differs
        # (' ' vs 'd' at position -2). So only the ';' gets trimmed.
        pg, scan = ReportGenerator._trim_shared_edges("word ;", "word;")
        assert pg == "word "
        assert scan == "word"

    def test_multiple_trailing_punct(self):
        """'places, (3)' vs 'places ;*' — different punctuation patterns."""
        # These don't share trailing chars, so nothing gets trimmed
        pg, scan = ReportGenerator._trim_shared_edges("places, (3)", "places ;*")
        assert pg == "places, (3)"
        assert scan == "places ;*"

    def test_only_alphanumeric_not_trimmed(self):
        """Leading/trailing alphanumeric characters are never trimmed."""
        # Even though 's' is shared, it's not in edge_chars
        pg, scan = ReportGenerator._trim_shared_edges("sons", "sons")
        assert pg == "sons"
        assert scan == "sons"

    def test_mioclnir_case(self):
        """'Miolnir,' vs 'Miölnir,' — shared trailing comma."""
        pg, scan = ReportGenerator._trim_shared_edges("Miolnir,", "Miölnir,")
        assert pg == "Miolnir"
        assert scan == "Miölnir"

    def test_rudiger_case(self):
        """'Rudiger,' vs 'Rüdiger,' — shared trailing comma."""
        pg, scan = ReportGenerator._trim_shared_edges("Rudiger,", "Rüdiger,")
        assert pg == "Rudiger"
        assert scan == "Rüdiger"

    def test_long_texts_not_over_trimmed(self):
        """Long alignment artifacts shouldn't be aggressively trimmed."""
        pg_long = "The first and eldest of gods is hight Allfather"
        scan_short = "INTRODUCTION."
        pg, scan = ReportGenerator._trim_shared_edges(pg_long, scan_short)
        # No shared leading/trailing chars — unchanged
        assert pg == pg_long
        assert scan == scan_short
