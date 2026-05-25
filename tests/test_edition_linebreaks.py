"""Tests for Approach B: Line-break fingerprinting."""

import pytest
from unittest.mock import AsyncMock, patch

from gerrata.edition.linebreaks import (
    LineBreakFingerprinter,
    LineBreakDetection,
    Fingerprint,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def fingerprinter():
    return LineBreakFingerprinter()


# Build a synthetic PG text with preserved line breaks (72-char column)
def make_preserved_pg_text() -> str:
    """Create synthetic PG text with consistent 72-char column width."""
    lines = []
    words = "It was the best of times it was the worst of times it was the age of wisdom".split()
    word_idx = 0

    for _ in range(200):
        line_words = []
        line_len = 0
        while word_idx < len(words) * 100:
            w = words[word_idx % len(words)]
            if line_len + len(w) + 1 > 72:
                break
            line_words.append(w)
            line_len += len(w) + 1
            word_idx += 1
        # Pad to exactly 72 chars
        line = " ".join(line_words).ljust(72)
        lines.append(line)

    header = "*** START OF THIS PROJECT GUTENBERG EBOOK ***\n"
    footer = "\n*** END OF THIS PROJECT GUTENBERG EBOOK ***"
    return header + "\n".join(lines) + footer


def make_reflowed_pg_text() -> str:
    """Create synthetic PG text with reflowed (variable-width) lines."""
    sentences = [
        "It was the best of times, it was the worst of times, it was the age of wisdom,",
        "it was the age of foolishness, it was the epoch of belief, it was the epoch of",
        "incredulity, it was the season of Light, it was the season of Darkness,",
        "it was the spring of hope, it was the winter of despair.",
        "",
        "We had everything before us, we had nothing before us, we were all going direct",
        "to Heaven, we were all going direct the other way.",
    ] * 20

    header = "*** START OF THIS PROJECT GUTENBERG EBOOK ***\n"
    footer = "\n*** END OF THIS PROJECT GUTENBERG EBOOK ***"
    return header + "\n".join(sentences) + footer


def make_different_edition_scan() -> str:
    """Create OCR text with a different column width (68 chars)."""
    lines = []
    words = "It was the best of times it was the worst of times it was the age of wisdom".split()
    word_idx = 5  # Start at different word position for different line breaks

    for _ in range(200):
        line_words = []
        line_len = 0
        while word_idx < len(words) * 100:
            w = words[word_idx % len(words)]
            if line_len + len(w) + 1 > 68:
                break
            line_words.append(w)
            line_len += len(w) + 1
            word_idx += 1
        line = " ".join(line_words).ljust(68)
        lines.append(line)

    return "\n".join(lines)


def make_same_edition_scan() -> str:
    """Create OCR text with same column width and similar line breaks as preserved PG."""
    return make_preserved_pg_text().split("*** START OF THIS PROJECT GUTENBERG EBOOK ***\n")[1].split("\n*** END OF")[0]


# ── Tests: detect_line_break_preservation ─────────────────────────────

class TestDetectLineBreakPreservation:

    def test_preserved_breaks(self, fingerprinter):
        text = make_preserved_pg_text()
        result = fingerprinter.detect_line_break_preservation(text)
        assert result.preserved is True
        assert result.column_width == 72
        assert result.confidence > 0.7

    def test_reflowed_breaks(self, fingerprinter):
        text = make_reflowed_pg_text()
        result = fingerprinter.detect_line_break_preservation(text)
        assert result.preserved is False

    def test_no_start_marker(self, fingerprinter):
        text = "Just some text without PG markers."
        result = fingerprinter.detect_line_break_preservation(text)
        assert result.preserved is False

    def test_empty_body(self, fingerprinter):
        text = "*** START OF THIS PROJECT GUTENBERG EBOOK ***\n\n*** END OF THIS PROJECT GUTENBERG EBOOK ***"
        result = fingerprinter.detect_line_break_preservation(text)
        assert result.preserved is False

    def test_column_width_detection(self, fingerprinter):
        """Test that modal line width is correctly detected."""
        # Create text with 65-char column
        lines = ["A" * 65 for _ in range(50)]
        text = "*** START OF THIS PROJECT GUTENBERG EBOOK ***\n" + "\n".join(lines) + "\n*** END OF THIS PROJECT GUTENBERG EBOOK ***"
        result = fingerprinter.detect_line_break_preservation(text)
        assert result.column_width == 65


# ── Tests: build_pg_fingerprint ──────────────────────────────────────

class TestBuildPGFingerprint:

    def test_fingerprint_from_preserved(self, fingerprinter):
        text = make_preserved_pg_text()
        fp = fingerprinter.build_pg_fingerprint(text)
        assert fp.column_width == 72
        assert fp.total_lines > 0
        assert fp.long_lines > 0

    def test_fingerprint_from_reflowed(self, fingerprinter):
        text = make_reflowed_pg_text()
        fp = fingerprinter.build_pg_fingerprint(text)
        assert fp.column_width == 0
        assert fp.total_lines == 0

    def test_fingerprint_lines_have_three_elements(self, fingerprinter):
        text = make_preserved_pg_text()
        fp = fingerprinter.build_pg_fingerprint(text)
        for line in fp.lines:
            assert len(line) == 3  # (length, last_word, first_word)

    def test_last_words_are_lowercase(self, fingerprinter):
        text = make_preserved_pg_text()
        fp = fingerprinter.build_pg_fingerprint(text)
        for length, last_word, first_word in fp.lines:
            assert last_word == last_word.lower()
            assert first_word == first_word.lower()


# ── Tests: build_scan_fingerprint ─────────────────────────────────────

class TestBuildScanFingerprint:

    def test_fingerprint_from_ocr(self, fingerprinter):
        ocr = "line one of text here\nline two of text there\nline three of text everywhere\n"
        fp = fingerprinter.build_scan_fingerprint(ocr)
        assert fp.total_lines == 3
        assert fp.column_width > 0

    def test_empty_ocr(self, fingerprinter):
        fp = fingerprinter.build_scan_fingerprint("")
        assert fp.total_lines == 0
        assert fp.column_width == 0


# ── Tests: compare_line_breaks ───────────────────────────────────────

class TestCompareLineBreaks:

    def test_same_edition(self, fingerprinter):
        pg_text = make_preserved_pg_text()
        scan_text = make_same_edition_scan()

        pg_fp = fingerprinter.build_pg_fingerprint(pg_text)
        scan_fp = fingerprinter.build_scan_fingerprint(scan_text)

        result = fingerprinter.compare_line_breaks(pg_fp, scan_fp)
        assert result.pg_preserves_breaks is True
        # Same column width (within ±2) should give high match
        assert abs(result.pg_column_width - result.scan_column_width) <= 2
        assert result.result == "match"

    def test_different_edition(self, fingerprinter):
        pg_text = make_preserved_pg_text()
        scan_text = make_different_edition_scan()

        pg_fp = fingerprinter.build_pg_fingerprint(pg_text)
        scan_fp = fingerprinter.build_scan_fingerprint(scan_text)

        result = fingerprinter.compare_line_breaks(pg_fp, scan_fp)
        assert result.pg_column_width != result.scan_column_width

    def test_no_pg_fingerprint(self, fingerprinter):
        pg_fp = Fingerprint(column_width=0)
        scan_fp = Fingerprint(lines=[(70, "word", "first")], column_width=70, total_lines=1, long_lines=1)

        result = fingerprinter.compare_line_breaks(pg_fp, scan_fp)
        assert result.result == "unable_to_determine"

    def test_no_scan_fingerprint(self, fingerprinter):
        pg_fp = Fingerprint(lines=[(72, "word", "first")], column_width=72, total_lines=1, long_lines=1)
        scan_fp = Fingerprint(column_width=0)

        result = fingerprinter.compare_line_breaks(pg_fp, scan_fp)
        assert result.result == "unable_to_determine"

    def test_confidence_range(self, fingerprinter):
        pg_text = make_preserved_pg_text()
        scan_text = make_same_edition_scan()

        pg_fp = fingerprinter.build_pg_fingerprint(pg_text)
        scan_fp = fingerprinter.build_scan_fingerprint(scan_text)

        result = fingerprinter.compare_line_breaks(pg_fp, scan_fp)
        assert 0.0 <= result.confidence <= 1.0

    def test_column_width_delta(self, fingerprinter):
        """Large column width difference should lower match score."""
        pg_fp = Fingerprint(
            lines=[(72, f"word{i}", "first") for i in range(100)],
            column_width=72,
            total_lines=100,
            long_lines=100,
        )
        scan_fp = Fingerprint(
            lines=[(50, f"word{i}", "first") for i in range(100)],
            column_width=50,
            total_lines=100,
            long_lines=100,
        )

        result = fingerprinter.compare_line_breaks(pg_fp, scan_fp)
        assert abs(result.pg_column_width - result.scan_column_width) == 22


# ── Tests: fetch_ia_ocr_text ─────────────────────────────────────────

class TestFetchIAOCRText:

    @pytest.mark.asyncio
    async def test_djvu_available(self, fingerprinter):
        """Test that _fetch_djvu_text is called when DJVU file is found in metadata."""
        mock_response_data = {
            "files": [
                {"name": "book_djvu.txt", "format": "DjVuTXT"},
            ]
        }

        mock_resp = type("Resp", (), {
            "json": lambda self: mock_response_data,
            "raise_for_status": lambda self: None,
        })()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_client
        mock_cm.__aexit__.return_value = False

        with patch("gerrata.edition.linebreaks.httpx.AsyncClient", return_value=mock_cm), \
             patch.object(fingerprinter, "_fetch_djvu_text", new_callable=AsyncMock) as mock_djvu:
            mock_djvu.return_value = "some ocr text line one\nline two\nline three"

            result = await fingerprinter.fetch_ia_ocr_text("someid")
            assert result is not None
            assert "line one" in result

    @pytest.mark.asyncio
    async def test_no_ocr_available(self, fingerprinter):
        """Test when no OCR text is available (no abbyy or djvu files)."""
        mock_response_data = {"files": []}

        mock_resp = type("Resp", (), {
            "json": lambda self: mock_response_data,
            "raise_for_status": lambda self: None,
        })()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_client
        mock_cm.__aexit__.return_value = False

        with patch("gerrata.edition.linebreaks.httpx.AsyncClient", return_value=mock_cm):
            result = await fingerprinter.fetch_ia_ocr_text("someid")
            assert result is None


# ── Tests: Edge cases ────────────────────────────────────────────────

class TestEdgeCases:

    def test_single_long_line(self, fingerprinter):
        """A single very long line should be detected as preserved (concentration 100%)."""
        text = "*** START OF THIS PROJECT GUTENBERG EBOOK ***\n" + "A" * 1000 + "\n*** END OF THIS PROJECT GUTENBERG EBOOK ***"
        result = fingerprinter.detect_line_break_preservation(text)
        # Single line → top 1 length is 100%, within_range is trivially true
        assert result.preserved is True
        assert result.column_width == 1000

    def test_all_different_lengths(self, fingerprinter):
        """Lines of all different lengths → reflowed."""
        lines = ["A" * (50 + i) for i in range(100)]
        text = "*** START OF THIS PROJECT GUTENBERG EBOOK ***\n" + "\n".join(lines) + "\n*** END OF THIS PROJECT GUTENBERG EBOOK ***"
        result = fingerprinter.detect_line_break_preservation(text)
        assert result.preserved is False
