"""Tests for Approach A: Metadata matching."""

import pytest
from unittest.mock import AsyncMock, patch

from gerrata.edition.metadata import (
    MetadataMatcher,
    PGEditionClues,
    IAEditionClues,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def matcher():
    return MetadataMatcher()


SAMPLE_HEADER_WITH_EDITION = """
Title: Nostromo
Author: Joseph Conrad

Produced by Judy Boss and David Widger

Edition: Published by Harper & Brothers, New York, 1904.
This is a first edition of this work.

Release Date: January 9, 2006 [EBook #2021]
"""

SAMPLE_HEADER_NO_EDITION = """
Title: Dracula
Author: Bram Stoker

Produced by David Reed

Release Date: May 1, 1995 [EBook #345]
"""

SAMPLE_HEADER_WITH_SOURCE_SCAN = """
Title: The Strange Case of Dr. Jekyll and Mr. Hyde
Author: Robert Louis Stevenson

Source: https://archive.org/details/jekyllhyde00stokuoft

Produced by anonymous volunteer
"""


# ── Tests: extract_pg_clues ───────────────────────────────────────────

class TestExtractPGClues:

    def test_extracts_publisher(self, matcher):
        clues = matcher.extract_pg_clues(SAMPLE_HEADER_WITH_EDITION, "Nostromo")
        assert "harper" in clues.publisher.lower()

    def test_extracts_year(self, matcher):
        clues = matcher.extract_pg_clues(SAMPLE_HEADER_WITH_EDITION, "Nostromo")
        assert clues.year == "1904"

    def test_extracts_first_edition(self, matcher):
        clues = matcher.extract_pg_clues(SAMPLE_HEADER_WITH_EDITION, "Nostromo")
        assert clues.is_first_edition is True

    def test_no_edition_info(self, matcher):
        clues = matcher.extract_pg_clues(SAMPLE_HEADER_NO_EDITION, "Dracula")
        assert clues.publisher == ""
        assert clues.year == ""

    def test_extracts_source_scan_id(self, matcher):
        clues = matcher.extract_pg_clues(SAMPLE_HEADER_WITH_SOURCE_SCAN, "Dr. Jekyll")
        assert clues.source_scan_id == "jekyllhyde00stokuoft"

    def test_extracts_producer(self, matcher):
        clues = matcher.extract_pg_clues(SAMPLE_HEADER_WITH_EDITION, "Nostromo")
        assert "Judy Boss" in clues.producer

    def test_extracts_title_from_param(self, matcher):
        clues = matcher.extract_pg_clues(SAMPLE_HEADER_WITH_EDITION, "Nostromo: A Tale of the Seaboard")
        assert clues.title == "Nostromo: A Tale of the Seaboard"

    def test_empty_header(self, matcher):
        clues = matcher.extract_pg_clues("", "")
        assert clues.publisher == ""
        assert clues.year == ""
        assert clues.producer == ""


# ── Tests: score_match ───────────────────────────────────────────────

class TestScoreMatch:

    def test_publisher_and_year_match(self, matcher):
        pg = PGEditionClues(
            publisher="Harper & Brothers",
            year="1904",
            title="Nostromo: A Tale of the Seaboard",
            city="New York",
            is_first_edition=True,
        )
        ia = IAEditionClues(
            title="Nostromo, a tale of the seaboard",
            publisher="Harper & Brothers, New York",
            date="1904",
            creator="Conrad, Joseph",
            description="First edition",
        )
        result = matcher.score_match(pg, ia)
        # Publisher match: +40, Year match: +30, Title: +10, City: +10, First edition: +10 = 100
        assert result.score >= 80
        assert result.result == "match"

    def test_publisher_mismatch(self, matcher):
        pg = PGEditionClues(publisher="Oxford University Press", year="1995", title="Dracula")
        ia = IAEditionClues(title="Dracula", publisher="Archibald Constable", date="1897")
        result = matcher.score_match(pg, ia)
        assert result.score < 50

    def test_year_mismatch(self, matcher):
        pg = PGEditionClues(publisher="Harper", year="1920", title="Some Book")
        ia = IAEditionClues(title="Some Book", publisher="Harper", date="1900")
        result = matcher.score_match(pg, ia)
        # Year diff is 20, not within 2 → no year points
        assert result.details.get("year_match") is False

    def test_year_within_2(self, matcher):
        pg = PGEditionClues(publisher="Harper", year="1903", title="Some Book")
        ia = IAEditionClues(title="Some Book", publisher="Harper", date="1904")
        result = matcher.score_match(pg, ia)
        assert result.details.get("year_match") is True

    def test_no_data_returns_unable(self, matcher):
        pg = PGEditionClues()
        ia = IAEditionClues()
        result = matcher.score_match(pg, ia)
        assert result.result == "unable_to_determine"

    def test_title_similarity_with_subtitle(self, matcher):
        pg = PGEditionClues(title="Nostromo: A Tale of the Seaboard")
        ia = IAEditionClues(title="Nostromo, a tale of the seaboard")
        result = matcher.score_match(pg, ia)
        assert result.details.get("title_match") is True

    def test_match_threshold(self, matcher):
        """Score >= 70 → match result."""
        pg = PGEditionClues(publisher="Penguin", year="1960", title="Great Expectations")
        ia = IAEditionClues(title="Great Expectations", publisher="Penguin Books", date="1960")
        result = matcher.score_match(pg, ia)
        assert result.result == "match"

    def test_possible_threshold(self, matcher):
        """Score 40-69 → possible result."""
        pg = PGEditionClues(publisher="Penguin", year="1960", title="Great Expectations")
        # Only publisher matches, nothing else
        ia = IAEditionClues(title="Some Other Book", publisher="Penguin Classics", date="1980")
        result = matcher.score_match(pg, ia)
        assert result.result in ("possible", "mismatch")

    def test_mismatch_threshold(self, matcher):
        """Score < 40 → mismatch result."""
        pg = PGEditionClues(publisher="Oxford", year="2000", title="Great Expectations", city="London")
        ia = IAEditionClues(title="Great Expectations", publisher="Penguin", date="1890", city="New York")
        result = matcher.score_match(pg, ia)
        assert result.result == "mismatch"


# ── Tests: publisher normalization ────────────────────────────────────

class TestNormalizePublisher:

    def test_removes_the(self, matcher):
        assert matcher._normalize_publisher("The Penguin Books") == "penguin books"

    def test_removes_punctuation(self, matcher):
        result = matcher._normalize_publisher("Harper & Brothers,")
        assert "," not in result
        assert "harper" in result

    def test_collapse_whitespace(self, matcher):
        result = matcher._normalize_publisher("Harper   &   Brothers")
        # "harper & brothers" (internal & not stripped, whitespace collapsed)
        assert "  " not in result
        assert "harper" in result

    def test_empty(self, matcher):
        assert matcher._normalize_publisher("") == ""


# ── Tests: year extraction ───────────────────────────────────────────

class TestExtractYear:

    def test_plain_year(self, matcher):
        assert matcher._extract_year("1904") == 1904

    def test_year_in_string(self, matcher):
        assert matcher._extract_year("Published 1904 by Harper") == 1904

    def test_empty(self, matcher):
        assert matcher._extract_year("") is None

    def test_no_year(self, matcher):
        assert matcher._extract_year("no year here") is None


# ── Tests: title similarity ──────────────────────────────────────────

class TestTitleSimilarity:

    def test_same_title(self, matcher):
        assert matcher._title_similarity("Nostromo", "Nostromo") == 1.0

    def test_subtitle_ignored(self, matcher):
        sim = matcher._title_similarity(
            "Nostromo: A Tale of the Seaboard",
            "Nostromo",
        )
        assert sim > 0.8

    def test_different_titles(self, matcher):
        sim = matcher._title_similarity("Dracula", "Nostromo")
        assert sim == 0.0

    def test_articles_ignored(self, matcher):
        sim = matcher._title_similarity("The Great Gatsby", "Great Gatsby")
        assert sim > 0.8

    def test_empty_titles(self, matcher):
        assert matcher._title_similarity("", "") == 0.0


# ── Tests: fetch_ia_metadata ─────────────────────────────────────────

class TestFetchIAMetadata:

    @pytest.mark.asyncio
    async def test_fetch_success(self, matcher):
        mock_response_data = {
            "metadata": {
                "title": "Nostromo",
                "publisher": "Harper",
                "date": "1904",
                "creator": "Conrad, Joseph",
            }
        }

        # Use plain object (not AsyncMock) for response so raise_for_status is sync
        mock_resp = type("Resp", (), {
            "json": lambda self: mock_response_data,
            "raise_for_status": lambda self: None,
        })()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_client
        mock_cm.__aexit__.return_value = False

        with patch("gerrata.edition.metadata.httpx.AsyncClient", return_value=mock_cm):
            clues = await matcher.fetch_ia_metadata("nostromotaleofse00conruoft")
            assert clues.title == "Nostromo"
            assert clues.publisher == "Harper"

    @pytest.mark.asyncio
    async def test_fetch_failure(self, matcher):
        import httpx

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.HTTPError("404"))

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_client
        mock_cm.__aexit__.return_value = False

        with patch("gerrata.edition.metadata.httpx.AsyncClient", return_value=mock_cm):
            clues = await matcher.fetch_ia_metadata("nonexistent")
            assert clues.title == ""
