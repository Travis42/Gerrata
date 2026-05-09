"""Tests for PG text fetcher and parser."""

import re
from pathlib import Path

import pytest

from gerrata.fetcher.pg import PGFetcher, PGParsedText
from gerrata.models import PGMetadata


FIXTURES = Path(__file__).parent / "fixtures" / "pg43"
HTML_FILE = FIXTURES / "43-h.htm"


@pytest.fixture
def fetcher():
    return PGFetcher()


@pytest.fixture
def parsed(fetcher):
    return fetcher.parse_file(HTML_FILE)


class TestPGParsedText:
    """Test parsing PG #43 HTML file."""

    def test_returns_parsed_text(self, parsed):
        assert isinstance(parsed, PGParsedText)

    def test_has_metadata(self, parsed):
        assert isinstance(parsed.metadata, PGMetadata)
        assert parsed.metadata.pg_id == 43

    def test_has_title(self, parsed):
        assert "Jekyll" in parsed.metadata.title and "Hyde" in parsed.metadata.title

    def test_has_author(self, parsed):
        assert "Stevenson" in parsed.metadata.author

    def test_has_body_text(self, parsed):
        assert len(parsed.body_text) > 10000  # ~26K words

    def test_body_has_no_html_tags(self, parsed):
        assert "<p>" not in parsed.body_text
        assert "</p>" not in parsed.body_text
        assert "<div>" not in parsed.body_text

    def test_body_has_no_start_end_markers(self, parsed):
        assert "START OF THE PROJECT GUTENBERG" not in parsed.body_text
        assert "END OF THE PROJECT GUTENBERG" not in parsed.body_text

    def test_has_paragraphs(self, parsed):
        assert len(parsed.paragraphs) > 50  # Should have many paragraphs

    def test_paragraphs_are_non_empty(self, parsed):
        for p in parsed.paragraphs:
            assert len(p.strip()) > 0

    def test_has_chapters(self, parsed):
        assert len(parsed.chapters) >= 8  # At least the story chapters

    def test_chapter_titles(self, parsed):
        titles = [ch.title for ch in parsed.chapters]
        # Should find at least some known chapter titles
        title_str = " ".join(titles).lower()
        assert "story of the door" in title_str or "door" in title_str

    def test_chapter_offsets_increase(self, parsed):
        offsets = [ch.offset for ch in parsed.chapters]
        assert offsets == sorted(offsets)

    def test_body_contains_known_text(self, parsed):
        # "Mr. Utterson the lawyer" should be in the opening
        assert "Utterson" in parsed.body_text or "utterson" in parsed.body_text.lower()

    def test_full_text_preserved(self, parsed):
        assert len(parsed.full_text) > len(parsed.body_text)


class TestPlaintextParsing:
    """Test parsing plain text PG format."""

    def test_plain_text_with_metadata(self, fetcher):
        text = """This eBook is for the use of anyone anywhere.

Title: The Great Gatsby
Author: F. Scott Fitzgerald
Language: English
Release Date: March 1, 1995 [EBook #200]
Produced by: Someone

*** START OF THE PROJECT GUTENBERG EBOOK 200 ***

In my younger and more vulnerable years my father gave me some advice
that I've been turning over in my mind ever since.

"Whenever you feel like criticizing any one," he told me, "just
remember that all the people in this world haven't had the advantages
that you've had."

He didn't say any more, but we've always been unusually communicative
in a reserved way, and I understood that he meant a great deal more
than that.

*** END OF THE PROJECT GUTENBERG EBOOK 200 ***
"""
        path = Path("/tmp/test_pg_200.txt")
        path.write_text(text)
        try:
            parsed = fetcher.parse_file(path)
            assert parsed.metadata.pg_id == 200
            assert parsed.metadata.title == "The Great Gatsby"
            assert "F. Scott Fitzgerald" in parsed.metadata.author
            assert parsed.metadata.language == "English"
            assert "younger and more vulnerable" in parsed.body_text
            assert "START OF THE PROJECT GUTENBERG" not in parsed.body_text
            assert "END OF THE PROJECT GUTENBERG" not in parsed.body_text
            assert len(parsed.paragraphs) >= 2
        finally:
            path.unlink(missing_ok=True)

    def test_plain_text_no_header(self, fetcher):
        """Handle text without PG markers."""
        text = "Some plain text without any markers."
        path = Path("/tmp/test_pg_no_markers.txt")
        path.write_text(text)
        try:
            parsed = fetcher.parse_file(path)
            assert parsed.body_text == text
        finally:
            path.unlink(missing_ok=True)


class TestPGFetcher:
    def test_extract_pg_id_from_filename(self, fetcher):
        path = Path("/some/path/43-h.htm")
        assert fetcher._extract_pg_id(path, "") == 43

    def test_extract_pg_id_from_content(self, fetcher):
        content = "Some text\nEBook #1234\nmore text"
        path = Path("/tmp/test.txt")
        assert fetcher._extract_pg_id(path, content) == 1234

    def test_extract_paragraphs_splits_on_blank_lines(self, fetcher):
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        paras = fetcher._extract_paragraphs(text)
        assert len(paras) == 3
        assert paras[0] == "First paragraph."
        assert paras[1] == "Second paragraph."

    def test_extract_chapters_finds_all_caps(self, fetcher):
        text = "Intro text.\n\nSTORY OF THE DOOR\n\nChapter body here.\n\nSEARCH FOR MR. HYDE\n\nMore text."
        chapters = fetcher._extract_chapters(text)
        assert len(chapters) >= 2
        assert "STORY OF THE DOOR" in chapters[0].title

    def test_extract_chapters_finds_numbered(self, fetcher):
        text = "Intro.\n\nCHAPTER 1: The Beginning\n\nText.\n\nCHAPTER 2: The Middle\n\nMore text."
        chapters = fetcher._extract_chapters(text)
        assert len(chapters) >= 2
        assert "The Beginning" in chapters[0].title

    def test_chapter_end_offsets(self, fetcher):
        text = "Start.\n\nCHAPTER 1: The Beginning\n\nText here.\n\nCHAPTER 2: The End\n\nMore text here.\n\nEnd."
        chapters = fetcher._extract_chapters(text)
        assert len(chapters) >= 2
        # Last chapter should extend to end of text
        assert chapters[-1].end_offset == len(text)
        # First chapter should end at second chapter start
        if len(chapters) >= 2:
            assert chapters[0].end_offset == chapters[1].offset
