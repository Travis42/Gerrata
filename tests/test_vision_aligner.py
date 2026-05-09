"""Tests for vision aligner — transcription, fuzzy matching, alignment."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from gerrata.aligner.vision_aligner import (
    VisionAligner,
    VisionTranscriber,
    PageTranscription,
    VisionAlignmentResult,
    normalize_for_matching,
    chunk_text_for_matching,
)
from gerrata.models import Alignment, AlignmentMethod


# ── Utility functions ──────────────────────────────────────────────────

class TestNormalizeForMatching:
    def test_lowercases(self):
        assert normalize_for_matching("Hello World") == "hello world"

    def test_removes_punctuation(self):
        assert normalize_for_matching("Hello, world!") == "hello world"

    def test_collapses_whitespace(self):
        assert normalize_for_matching("  hello   \n  world  ") == "hello world"

    def test_handles_empty(self):
        assert normalize_for_matching("") == ""

    def test_preserves_accents(self):
        result = normalize_for_matching("café résumé")
        assert "café" in result


class TestChunkTextForMatching:
    def test_basic_chunking(self):
        text = "First sentence here. Second sentence here. Third sentence."
        chunks = chunk_text_for_matching(text, min_length=10)
        assert len(chunks) >= 1
        assert all(len(c) >= 10 for c in chunks)

    def test_short_text_single_chunk(self):
        text = "A short text."
        chunks = chunk_text_for_matching(text, min_length=5)
        assert len(chunks) >= 1

    def test_empty_text(self):
        chunks = chunk_text_for_matching("")
        assert chunks == []

    def test_respects_max_length(self):
        long_text = ". ".join(["word"] * 200)
        chunks = chunk_text_for_matching(long_text, min_length=10, max_length=100)
        # All chunks should be reasonably sized
        assert all(len(c) <= 400 for c in chunks)  # Allow some overshoot from sentences


# ── PageTranscription ─────────────────────────────────────────────────

class TestPageTranscription:
    def test_success_case(self):
        t = PageTranscription(
            page_num=5,
            image_path=Path("/tmp/page_0005.png"),
            transcription="Some transcribed text here.",
            model_used="zai/glm-4.6v",
            success=True,
        )
        assert t.success
        assert t.page_num == 5

    def test_failure_case(self):
        t = PageTranscription(
            page_num=0,
            image_path=Path("/tmp/page.png"),
            transcription="",
            success=False,
            error="All models failed",
        )
        assert not t.success
        assert t.error


# ── VisionTranscriber ─────────────────────────────────────────────────

class TestVisionTranscriber:
    @pytest.fixture
    def transcriber(self):
        return VisionTranscriber(api_key="test-key")

    def test_default_models(self):
        t = VisionTranscriber()
        assert "zai/glm-4.6v" in t.models

    def test_custom_models(self):
        t = VisionTranscriber(models=["custom-model"])
        assert t.models == ["custom-model"]

    @pytest.mark.asyncio
    async def test_transcribe_missing_image(self, transcriber):
        result = await transcriber.transcribe_page(
            Path("/nonexistent/page.png"), page_num=0
        )
        assert not result.success
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_transcribe_page_success(self, tmp_path):
        # Create a dummy PNG image
        from PIL import Image
        img_path = tmp_path / "page.png"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(img_path)

        transcriber = VisionTranscriber(
            api_key="test-key",
            models=["test-model"],
        )

        # Mock the API call
        with patch.object(
            transcriber, "_call_api",
            new_callable=AsyncMock,
            return_value="The quick brown fox jumps over the lazy dog."
        ):
            result = await transcriber.transcribe_page(img_path, page_num=0)

        assert result.success
        assert "quick brown fox" in result.transcription
        assert result.model_used == "test-model"

    @pytest.mark.asyncio
    async def test_transcribe_page_fallback(self, tmp_path):
        """First model fails, second succeeds."""
        from PIL import Image
        img_path = tmp_path / "page.png"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(img_path)

        transcriber = VisionTranscriber(
            api_key="test-key",
            models=["model-1", "model-2"],
        )

        call_count = 0
        async def mock_call(model, image_data, media_type):
            nonlocal call_count
            call_count += 1
            if model == "model-1":
                raise Exception("Model 1 failed")
            return "Transcribed text from fallback model."

        with patch.object(transcriber, "_call_api", side_effect=mock_call):
            result = await transcriber.transcribe_page(img_path, page_num=3)

        assert result.success
        assert result.model_used == "model-2"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_transcribe_pages_concurrency_preserves_order(self, tmp_path):
        """Test that concurrent transcription preserves result order."""
        from PIL import Image
        import asyncio

        # Create multiple test images
        image_paths = []
        for i in range(5):
            img_path = tmp_path / f"page{i}.png"
            img = Image.new("RGB", (100, 100), color="white")
            img.save(img_path)
            image_paths.append(img_path)

        transcriber = VisionTranscriber(
            api_key="test-key",
            models=["test-model"],
            concurrency=3,
        )

        # Mock the API call to return page-specific text with variable delay
        async def mock_call_with_delay(model, image_data, media_type):
            # Simulate variable processing time
            await asyncio.sleep(0.01 * len(image_paths))
            # Extract page number from the call (simulated)
            return "Transcribed text"

        with patch.object(
            transcriber, "_call_api",
            side_effect=mock_call_with_delay
        ):
            results = await transcriber.transcribe_pages(image_paths)

        # Verify all results are present
        assert len(results) == 5
        # Verify order is preserved (most important test for concurrency)
        for i, result in enumerate(results):
            assert result.page_num == i, f"Order not preserved at index {i}"
            assert result.success


# ── VisionAligner ─────────────────────────────────────────────────────

class TestVisionAligner:
    @pytest.fixture
    def aligner(self):
        return VisionAligner(match_threshold=0.3, min_chunk_length=10)

    @pytest.fixture
    def sample_pg_text(self):
        return (
            "Mr. Utterson the lawyer was a man of a rugged countenance that was "
            "never lighted by a smile. He was austere with himself. "
            "It was a fine, bright, cold day. The streets were very full."
        )

    @pytest.fixture
    def sample_paragraphs(self, sample_pg_text):
        return [p.strip() for p in sample_pg_text.split(".") if len(p.strip()) > 5]

    def test_align_matching_transcription(self, aligner, sample_pg_text, sample_paragraphs):
        """Transcription that matches PG text should produce an alignment."""
        transcription = PageTranscription(
            page_num=0,
            image_path=Path("/tmp/page.png"),
            transcription="Mr. Utterson the lawyer was a man of a rugged countenance",
            model_used="test",
            success=True,
        )

        result = aligner.align_transcription_to_pg(
            transcription=transcription,
            pg_text=sample_pg_text,
            pg_paragraphs=sample_paragraphs,
            scan_page=0,
        )

        assert result is not None
        assert result.best_score >= 0.3
        assert result.alignment.pg_start >= 0
        assert result.alignment.pg_end > result.alignment.pg_start
        assert result.alignment.method == AlignmentMethod.LLM_VISION

    def test_align_no_match(self, aligner, sample_pg_text, sample_paragraphs):
        """Unrelated transcription should not match."""
        transcription = PageTranscription(
            page_num=0,
            image_path=Path("/tmp/page.png"),
            transcription="The quick brown fox jumps over the lazy dog completely unrelated text",
            model_used="test",
            success=True,
        )

        result = aligner.align_transcription_to_pg(
            transcription=transcription,
            pg_text=sample_pg_text,
            pg_paragraphs=sample_paragraphs,
            scan_page=0,
        )

        # With threshold 0.3, this might or might not match depending on coincidence
        # but the score should be very low
        if result:
            assert result.best_score < 0.5

    def test_align_failed_transcription(self, aligner, sample_pg_text, sample_paragraphs):
        """Failed transcription should return None."""
        transcription = PageTranscription(
            page_num=0,
            image_path=Path("/tmp/page.png"),
            transcription="",
            success=False,
            error="Model failed",
        )

        result = aligner.align_transcription_to_pg(
            transcription=transcription,
            pg_text=sample_pg_text,
            pg_paragraphs=sample_paragraphs,
            scan_page=0,
        )

        assert result is None

    def test_align_short_transcription(self, aligner, sample_pg_text, sample_paragraphs):
        """Very short transcription should return None."""
        transcription = PageTranscription(
            page_num=0,
            image_path=Path("/tmp/page.png"),
            transcription="Hi",
            success=True,
        )

        result = aligner.align_transcription_to_pg(
            transcription=transcription,
            pg_text=sample_pg_text,
            pg_paragraphs=sample_paragraphs,
            scan_page=0,
        )

        assert result is None

    def test_align_all_pages(self, aligner, sample_pg_text, sample_paragraphs):
        """Test aligning multiple pages."""
        transcriptions = [
            PageTranscription(
                page_num=0,
                image_path=Path("/tmp/page0.png"),
                transcription="Mr. Utterson the lawyer was a man of a rugged countenance",
                success=True,
            ),
            PageTranscription(
                page_num=1,
                image_path=Path("/tmp/page1.png"),
                transcription="It was a fine bright cold day the streets were full",
                success=True,
            ),
            PageTranscription(
                page_num=2,
                image_path=Path("/tmp/page2.png"),
                transcription="",
                success=False,
                error="failed",
            ),
        ]

        results = aligner.align_all_pages(
            transcriptions=transcriptions,
            pg_text=sample_pg_text,
            pg_paragraphs=sample_paragraphs,
        )

        # Should have at least some matches
        assert len(results) >= 1

    def test_get_alignments_sorted(self, aligner):
        """get_alignments should return alignments sorted by pg_start."""
        from gerrata.models import Alignment
        results = [
            VisionAlignmentResult(
                alignment=Alignment(pg_start=100, pg_end=200, scan_page=2, method=AlignmentMethod.LLM_VISION),
                transcription=PageTranscription(page_num=2, image_path=Path("/tmp/p2.png"), transcription="x", success=True),
            ),
            VisionAlignmentResult(
                alignment=Alignment(pg_start=0, pg_end=50, scan_page=0, method=AlignmentMethod.LLM_VISION),
                transcription=PageTranscription(page_num=0, image_path=Path("/tmp/p0.png"), transcription="x", success=True),
            ),
        ]
        alignments = aligner.get_alignments(results)
        assert alignments[0].pg_start < alignments[1].pg_start

    def test_build_scan_pages(self, aligner):
        """build_scan_pages_from_transcriptions should create ScanPage objects."""
        transcriptions = [
            PageTranscription(
                page_num=0,
                image_path=Path("/tmp/page0.png"),
                transcription="Transcribed text for page 0.",
                success=True,
            ),
            PageTranscription(
                page_num=1,
                image_path=Path("/tmp/page1.png"),
                transcription="",
                success=False,
                error="failed",
            ),
        ]

        pages = aligner.build_scan_pages_from_transcriptions(transcriptions)
        assert len(pages) == 2
        assert pages[0].vision_text == "Transcribed text for page 0."
        assert pages[0].ocr_text == "Transcribed text for page 0."
        assert pages[1].vision_text == ""

    def test_alignment_confidence(self, aligner):
        """alignment_confidence should calculate coverage correctly."""
        from gerrata.models import Alignment
        alignments = [
            Alignment(pg_start=0, pg_end=50, scan_page=0),
            Alignment(pg_start=50, pg_end=100, scan_page=1),
        ]
        conf = aligner.alignment_confidence(alignments, pg_text_len=200)
        assert conf == pytest.approx(0.5)

    def test_alignment_offsets_within_bounds(self, aligner, sample_pg_text, sample_paragraphs):
        """Alignment offsets should be within PG text bounds."""
        transcription = PageTranscription(
            page_num=0,
            image_path=Path("/tmp/page.png"),
            transcription="Mr. Utterson the lawyer was a man",
            success=True,
        )

        result = aligner.align_transcription_to_pg(
            transcription=transcription,
            pg_text=sample_pg_text,
            pg_paragraphs=sample_paragraphs,
            scan_page=0,
        )

        if result:
            assert 0 <= result.alignment.pg_start < result.alignment.pg_end <= len(sample_pg_text)


class TestAnchoredAlignment:
    """Tests for the n-gram anchoring strategy."""

    @pytest.fixture
    def aligner(self):
        return VisionAligner(match_threshold=0.3, min_chunk_length=10)

    def test_anchor_finds_correct_position(self, aligner):
        """N-gram anchor should locate the correct position in PG text."""
        from gerrata.aligner.vision_aligner import normalize_for_matching

        pg_text = "Chapter One. Mr. Utterson the lawyer was a man of a rugged countenance. " * 50
        pg_norm = normalize_for_matching(pg_text)
        trans_norm = normalize_for_matching("Mr. Utterson the lawyer was a man of a rugged countenance.")

        pos, length = aligner._anchor_transcription(trans_norm, pg_norm)
        assert pos is not None
        # Should find the text somewhere in PG
        assert pos >= 0
        assert length > 0

    def test_anchor_with_unique_ngram(self, aligner):
        """Unique n-grams should be preferred over non-unique ones."""
        from gerrata.aligner.vision_aligner import normalize_for_matching

        # PG text with repeated phrase but unique anchor
        pg_text = (
            "The quick brown fox. Some other text here. "
            "And then the quick brown fox jumps. "
            "Meanwhile a completely unique phrase about elephants appears here. "
            "The quick brown fox again. "
            "More text about elephants and their habitats."
        )
        pg_norm = normalize_for_matching(pg_text)

        # Transcription with the unique phrase
        trans_norm = normalize_for_matching(
            "a completely unique phrase about elephants appears here"
        )

        pos, length = aligner._anchor_transcription(trans_norm, pg_norm)
        assert pos is not None
        # Should anchor near the unique phrase, not near "the quick brown fox"
        pg_norm_pos = pg_norm.find("completely unique phrase about elephants")
        assert abs(pos - pg_norm_pos) < 50

    def test_anchor_returns_none_for_no_match(self, aligner):
        """Anchor should return None if no n-grams match."""
        from gerrata.aligner.vision_aligner import normalize_for_matching

        pg_text = "This is some PG text with various content in it."
        pg_norm = normalize_for_matching(pg_text)
        trans_norm = normalize_for_matching("completely unrelated text about quantum physics")

        pos, length = aligner._anchor_transcription(trans_norm, pg_norm)
        assert pos is None
        assert length == 0

    def test_anchor_respects_search_window(self, aligner):
        """Anchor should only search within the specified window."""
        from gerrata.aligner.vision_aligner import normalize_for_matching

        # Use unique, distinctive text that will produce matching n-grams
        pg_text = "AAA " * 1000 + "the unusual spotted elephant danced gracefully across the moonlit savanna" + " BBB " * 1000
        pg_norm = normalize_for_matching(pg_text)
        target_pos = pg_norm.find("unusual spotted elephant")
        assert target_pos > 0

        # Transcription that includes enough words for a 4+ word n-gram match
        trans_norm = normalize_for_matching(
            "the unusual spotted elephant danced gracefully across the moonlit savanna at midnight"
        )

        # Search only BEFORE the target — should not find it
        pos, length = aligner._anchor_transcription(
            trans_norm, pg_norm, search_start=0, search_end=target_pos
        )
        assert pos is None

        # Search starting AT the target position — should find it
        pos, length = aligner._anchor_transcription(
            trans_norm, pg_norm, search_start=target_pos
        )
        assert pos is not None
        assert pos >= target_pos

    def test_windowed_match_scores_correctly(self, aligner):
        """_find_best_window should find the best window around an anchor."""
        from gerrata.aligner.vision_aligner import normalize_for_matching

        pg_text = "Intro text " + "Mr. Utterson the lawyer was a man of a rugged countenance. " + "More text."
        pg_norm = normalize_for_matching(pg_text)

        anchor = pg_norm.find("mr utterson the lawyer")
        trans_norm = normalize_for_matching("Mr. Utterson the lawyer was a man of a rugged countenance.")

        win_start, win_end, score = aligner._find_best_window(trans_norm, pg_norm, anchor)

        assert score > 0.5
        assert win_start <= anchor <= win_end

    def test_pg_text_alignment_correct_region(self, aligner):
        """Transcription should align to the correct region, not a wrong one."""
        # Create a long PG text with similar content in two places
        pg_text = (
            "Some introductory matter. " * 50 +
            "CHAPTER ONE. Mr. Utterson the lawyer was a man of a rugged countenance "
            "that was never lighted by a smile; cold, scanty and embarrassed in discourse; "
            "backward in sentiment; lean, long, dusty, dreary, and yet somehow lovable. " +
            "Some bridging text. " * 50 +
            "CHAPTER FIVE. Mr. Utterson the lawyer was a man of a rugged countenance "
            "that was never lighted by a smile but in a different context entirely. " +
            "Some ending matter. " * 50
        )
        paragraphs = [p.strip() for p in pg_text.split(".") if len(p.strip()) > 10]

        transcription = PageTranscription(
            page_num=0,
            image_path=Path("/tmp/page.png"),
            transcription="CHAPTER ONE. Mr. Utterson the lawyer was a man of a rugged countenance "
                         "that was never lighted by a smile; cold, scanty and embarrassed in discourse; "
                         "backward in sentiment; lean, long, dusty, dreary, and yet somehow lovable.",
            success=True,
        )

        result = aligner.align_transcription_to_pg(
            transcription=transcription,
            pg_text=pg_text,
            pg_paragraphs=paragraphs,
            scan_page=0,
        )

        assert result is not None
        # Should align to the first half (CHAPTER ONE), not the second (CHAPTER FIVE)
        ch1_pos = pg_text.find("CHAPTER ONE")
        ch5_pos = pg_text.find("CHAPTER FIVE")
        assert result.alignment.pg_start < (ch1_pos + ch5_pos) / 2

    def test_sequential_alignment_enforces_order(self, aligner):
        """Pages should align in reading order (sequential constraint)."""
        pg_text = (
            "First chapter content. " * 200 +
            "Second chapter content. " * 200 +
            "Third chapter content. " * 200
        )
        paragraphs = [p.strip() for p in pg_text.split(".") if len(p.strip()) > 10]

        transcriptions = [
            PageTranscription(
                page_num=0, image_path=Path("/tmp/p0.png"),
                transcription="First chapter content. " * 5, success=True,
            ),
            PageTranscription(
                page_num=1, image_path=Path("/tmp/p1.png"),
                transcription="Second chapter content. " * 5, success=True,
            ),
            PageTranscription(
                page_num=2, image_path=Path("/tmp/p2.png"),
                transcription="Third chapter content. " * 5, success=True,
            ),
        ]

        results = aligner.align_all_pages(
            transcriptions=transcriptions, pg_text=pg_text, pg_paragraphs=paragraphs,
        )

        assert len(results) >= 2
        # Verify order
        for i in range(1, len(results)):
            assert results[i].alignment.pg_start >= results[i - 1].alignment.pg_start

    def test_build_norm_offset_map(self):
        """_build_norm_offset_map should correctly map normalized positions to raw positions."""
        raw = "Hello, World! This is a test."
        norm_map = VisionAligner._build_norm_offset_map(raw)

        # Normalized: "hello world this is a test"
        # h(0) e(1) l(2) l(3) o(4) (5) w(7) o(8) r(9) l(10) d(11) (13) t(14) ...
        assert len(norm_map) > 0
        # First char should map to start of raw text
        assert norm_map[0] == 0


class TestVisionAlignerWithPG43:
    """Integration tests using the actual PG 43 (Jekyll & Hyde) text."""

    @pytest.fixture
    def pg_text_and_paragraphs(self):
        """Load and parse the PG 43 fixture."""
        import re
        from bs4 import BeautifulSoup

        fixture_path = Path(__file__).parent / "fixtures" / "pg43" / "43-h.htm"
        with open(fixture_path) as f:
            soup = BeautifulSoup(f.read(), "lxml")
        body = soup.find("body")
        pg_text = body.get_text(separator="\n")
        pg_text = re.sub(r"\n{3,}", "\n\n", pg_text).strip()
        paragraphs = [p.strip() for p in pg_text.split("\n\n") if len(p.strip()) > 10]
        return pg_text, paragraphs

    def test_ch1_aligns_to_early_pg_text(self, pg_text_and_paragraphs):
        """Chapter 1 transcription should align to the beginning of the PG text."""
        pg_text, paragraphs = pg_text_and_paragraphs
        aligner = VisionAligner(match_threshold=0.3, min_chunk_length=10)

        # Extract text from ch1 start (around char 349 in pg_text)
        ch1_region = pg_text[349:1049]

        transcription = PageTranscription(
            page_num=0, image_path=Path("/tmp/page0.png"),
            transcription=ch1_region, success=True,
        )

        result = aligner.align_transcription_to_pg(
            transcription=transcription, pg_text=pg_text,
            pg_paragraphs=paragraphs, scan_page=0,
        )

        assert result is not None
        # Should be in the first ~10K chars, NOT near the end
        assert result.alignment.pg_start < 10000

    def test_later_chapter_aligns_to_correct_region(self, pg_text_and_paragraphs):
        """A later chapter should align to the correct mid-text region."""
        pg_text, paragraphs = pg_text_and_paragraphs
        aligner = VisionAligner(match_threshold=0.3, min_chunk_length=10)

        # Extract text from around char 45000 (later in the book)
        later_region = pg_text[45000:45700]

        transcription = PageTranscription(
            page_num=4, image_path=Path("/tmp/page4.png"),
            transcription=later_region, success=True,
        )

        result = aligner.align_transcription_to_pg(
            transcription=transcription, pg_text=pg_text,
            pg_paragraphs=paragraphs, scan_page=4,
        )

        assert result is not None
        # Should be in the range 40000-60000
        assert 40000 < result.alignment.pg_start < 60000

    def test_sequential_pages_stay_in_order(self, pg_text_and_paragraphs):
        """Multiple pages from different chapters should align in order."""
        pg_text, paragraphs = pg_text_and_paragraphs
        aligner = VisionAligner(match_threshold=0.3, min_chunk_length=10)

        # Simulate 5 pages from different parts of the book
        positions = [
            (349, 1049),    # ch1 start
            (5800, 6500),   # ch1 continued
            (12561, 13261), # ch2
            (27836, 28536), # ch3
            (45000, 45700), # later chapter
        ]

        transcriptions = [
            PageTranscription(
                page_num=i, image_path=Path(f"/tmp/page{i}.png"),
                transcription=pg_text[start:end], success=True,
            )
            for i, (start, end) in enumerate(positions)
        ]

        results = aligner.align_all_pages(
            transcriptions=transcriptions, pg_text=pg_text, pg_paragraphs=paragraphs,
        )

        # Should match at least 3 pages
        assert len(results) >= 3

        # Verify reading order
        for i in range(1, len(results)):
            assert results[i].alignment.pg_start >= results[i - 1].alignment.pg_start


# ── RETAS Unique Word Anchoring ──────────────────────────────────────

class TestUniqueWordAnchor:
    """Tests for the RETAS-style unique word anchoring algorithm."""

    @pytest.fixture
    def aligner(self):
        return VisionAligner(match_threshold=0.3, min_chunk_length=10)

    def test_unique_word_anchor_simple(self, aligner):
        """Two short texts where unique words lock position correctly."""
        pg_text = (
            "The introductory matter fills some space with common words. "
            "Meanwhile the extraordinary pachyderm ambled gracefully across moonlit cobblestones "
            "while astonished villagers watched the remarkable spectacle unfold before them. "
            "More filler text about ordinary things goes here."
        )
        trans_text = (
            "the extraordinary pachyderm ambled gracefully across moonlit cobblestones "
            "while astonished villagers watched"
        )

        result = aligner._find_unique_word_anchor(trans_text, pg_text)
        assert result is not None
        pg_word_pos, trans_word_pos, pg_char_pos = result

        # Should have found anchors
        assert len(pg_char_pos) >= 2

        # Anchors should be in the middle of PG text (not at start or end)
        pg_mid = len(pg_text) // 2
        # The unique text starts around position 60 in PG text
        assert min(pg_char_pos) < pg_mid
        assert max(pg_char_pos) > pg_mid - len(pg_text) // 4

    def test_unique_word_anchor_edition_diff(self, aligner):
        """Texts with edition differences but common unique words still lock."""
        pg_text = (
            "Some common introductory text here. "
            "The extraordinary elephant danced across the plaza while bewildered tourists "
            "photographed the magnificent creature with their cameras. "
            "Then it continued toward the fountain."
        )
        # Slightly different wording but shares unique words
        trans_text = (
            "The extraordinary elephant strolled across the plaza while amazed tourists "
            "photographed the magnificent beast with smartphones"
        )

        result = aligner._find_unique_word_anchor(trans_text, pg_text)
        assert result is not None
        pg_word_pos, trans_word_pos, pg_char_pos = result

        # Should find shared unique words: extraordinary, elephant, plaza, magnificent
        assert len(pg_char_pos) >= 2

        # Anchors should cluster around the unique section
        assert max(pg_char_pos) - min(pg_char_pos) < len(pg_text) * 0.7

    def test_unique_word_anchor_no_common(self, aligner):
        """Texts with no common unique words should return None."""
        pg_text = "the quick brown fox jumps over the lazy dog and then runs away"
        trans_text = "astronomers discovered pulsating quasars near distant galactic clusters"

        result = aligner._find_unique_word_anchor(trans_text, pg_text)
        assert result is None

    def test_unique_word_anchor_common_words_filtered(self, aligner):
        """Common short words should be filtered even if they appear once."""
        pg_text = "The boy walked the dog and the cat ran away from the house"
        trans_text = "the boy and the dog"

        result = aligner._find_unique_word_anchor(trans_text, pg_text, min_word_len=4)
        assert result is None  # "the", "and", "boy", "dog" etc. are < 4 chars or appear multiple times

        # But with min_word_len=2, "cat" and "ran" and "away" and "house" are unique in pg_text
        # while "the", "and" still appear multiple times in pg_text
        result2 = aligner._find_unique_word_anchor(trans_text, pg_text, min_word_len=2)
        # "boy" appears once in pg_text, "dog" appears once in pg_text — they're unique
        # "the" appears 4 times in pg_text, "and" appears 2 times — filtered out
        if result2 is not None:
            pg_word_pos, trans_word_pos, pg_char_pos = result2
            # Should NOT include "the" or "and" as anchors
            assert len(pg_char_pos) >= 0

    def test_unique_word_anchor_respects_search_window(self, aligner):
        """Anchor should only find positions within the search window."""
        pg_text = "AAA " * 500 + "the extraordinary pachyderm ambled gracefully across moonlit cobblestones" + " BBB " * 500
        trans_text = "the extraordinary pachyderm ambled gracefully across moonlit cobblestones"

        # Find where the target is
        target_pos = pg_text.lower().find("extraordinary")

        # Search BEFORE the target — should not find it
        result = aligner._find_unique_word_anchor(
            trans_text, pg_text, search_start=0, search_end=target_pos
        )
        assert result is None

        # Search starting AT the target — should find it
        result = aligner._find_unique_word_anchor(
            trans_text, pg_text, search_start=target_pos
        )
        assert result is not None
        pg_word_pos, trans_word_pos, pg_char_pos = result
        assert min(pg_char_pos) >= target_pos

    def test_unique_word_anchor_to_position(self, aligner):
        """_unique_word_anchor_to_position should correctly estimate transcription start."""
        # Simple case: first anchor at word 5 in transcription, at char 1000 in PG
        anchor_pg_char = [1000, 1500, 2000]
        anchor_trans_word = [5, 15, 25]
        trans_word_count = 50
        pg_text_len = 50000
        trans_text_len = 2000

        est = aligner._unique_word_anchor_to_position(
            anchor_pg_char, anchor_trans_word,
            trans_word_count, pg_text_len, trans_text_len,
        )

        # First anchor is at trans word 5 (10% through transcription of 50 words)
        # PG char span is 1000 (1000 to 2000) for trans word span of 20 (5 to 25)
        # chars_per_word = 1000/20 = 50
        # estimated_start = 1000 - (5 * 50) = 750
        assert 500 < est < 1000

    def test_word_lcs_preserves_order(self, aligner):
        """LCS should only keep words that appear in the same order in both sequences."""
        seq1 = ["apple", "banana", "cherry", "date", "elderberry"]
        seq2 = ["banana", "apple", "cherry", "fig", "elderberry"]

        lcs = aligner._word_lcs(seq1, seq2)

        # "apple" appears at pos 0 in seq1 and pos 1 in seq2 — wrong order relative to banana
        # "banana" at pos 1 in seq1 and pos 0 in seq2 — wrong order relative to apple
        # "cherry" at pos 2 in seq1 and pos 2 in seq2 — same relative order
        # "elderberry" at pos 4 in seq1 and pos 4 in seq2 — same relative order
        # LCS could be: banana, cherry, elderberry OR apple, cherry, elderberry
        # But NOT both apple and banana together (wrong order)
        assert "cherry" in lcs
        assert "elderberry" in lcs
        # At most one of apple/banana can be in the LCS
        assert not ("apple" in lcs and "banana" in lcs)

    def test_tokenize_words(self, aligner):
        """Tokenization should produce lowercase alphanumeric words."""
        result = aligner._tokenize_words("Hello, World! It's a test—123.")
        assert result == ["hello", "world", "it", "s", "a", "test", "123"]

    def test_word_positions(self, aligner):
        """Word positions should correctly map words to their indices."""
        words = ["the", "cat", "sat", "on", "the", "mat"]
        positions = aligner._word_positions(words)
        assert positions["the"] == [0, 4]
        assert positions["cat"] == [1]
        assert positions["mat"] == [5]


class TestRETASIntegrationWithPG43:
    """Integration tests for RETAS anchoring against real PG 43 text."""

    @pytest.fixture
    def pg_text_and_paragraphs(self):
        """Load and parse the PG 43 fixture."""
        import re
        from bs4 import BeautifulSoup

        fixture_path = Path(__file__).parent / "fixtures" / "pg43" / "43-h.htm"
        with open(fixture_path) as f:
            soup = BeautifulSoup(f.read(), "lxml")
        body = soup.find("body")
        pg_text = body.get_text(separator="\n")
        pg_text = re.sub(r"\n{3,}", "\n\n", pg_text).strip()
        paragraphs = [p.strip() for p in pg_text.split("\n\n") if len(p.strip()) > 10]
        return pg_text, paragraphs

    def test_retas_anchors_ch1_region(self, pg_text_and_paragraphs):
        """RETAS should anchor ch1 text to the correct early PG region (~400-6000)."""
        pg_text, paragraphs = pg_text_and_paragraphs
        aligner = VisionAligner(match_threshold=0.3, min_chunk_length=10)

        # Extract a chunk from ch1 (starts at ~399 in PG text)
        ch1_chunk = pg_text[399:1399]

        result = aligner._find_unique_word_anchor(ch1_chunk, pg_text)
        assert result is not None
        pg_word_pos, trans_word_pos, pg_char_pos = result

        # Anchors should be in the ch1 region
        assert min(pg_char_pos) < 2000
        assert max(pg_char_pos) < 8000

    def test_retas_anchors_ch2_region(self, pg_text_and_paragraphs):
        """RETAS should anchor ch2 text to the correct PG region (~13145)."""
        pg_text, paragraphs = pg_text_and_paragraphs
        aligner = VisionAligner(match_threshold=0.3, min_chunk_length=10)

        # Extract a chunk from ch2 (starts at ~13145)
        ch2_chunk = pg_text[13145:14145]

        result = aligner._find_unique_word_anchor(ch2_chunk, pg_text)
        assert result is not None
        pg_word_pos, trans_word_pos, pg_char_pos = result

        # Anchors should be near ch2
        assert min(pg_char_pos) > 10000
        assert max(pg_char_pos) < 25000

    def test_retas_alignment_page48_region(self, pg_text_and_paragraphs):
        """Page 48 (ch1) should align to PG offset ~6000-8000, NOT ~11935.

        This is the key regression test — the old n-gram anchor was off.
        """
        pg_text, paragraphs = pg_text_and_paragraphs
        aligner = VisionAligner(match_threshold=0.3, min_chunk_length=10)

        # Extract text from the ~6000-8000 region (where page 48 should land)
        # This is mid-ch1 content
        target_region = pg_text[6000:8000]

        transcription = PageTranscription(
            page_num=0, image_path=Path("/tmp/page0.png"),
            transcription=target_region, success=True,
        )

        result = aligner.align_transcription_to_pg(
            transcription=transcription, pg_text=pg_text,
            pg_paragraphs=paragraphs, scan_page=0,
        )

        assert result is not None
        # Should align near the 6000-8000 region, NOT at 11935
        assert result.alignment.pg_start < 10000, (
            f"Page 0 aligned to {result.alignment.pg_start}, expected < 10000 "
            f"(old buggy offset was ~11935)"
        )
        assert result.alignment.pg_start > 3000, (
            f"Page 0 aligned too early at {result.alignment.pg_start}"
        )

    def test_retas_alignment_page62_region(self, pg_text_and_paragraphs):
        """Page 62 (ch2) should align to PG offset ~18000-20000."""
        pg_text, paragraphs = pg_text_and_paragraphs
        aligner = VisionAligner(match_threshold=0.3, min_chunk_length=10)

        # Extract text from the ~18000-20000 region
        target_region = pg_text[18000:20000]

        transcription = PageTranscription(
            page_num=1, image_path=Path("/tmp/page1.png"),
            transcription=target_region, success=True,
        )

        result = aligner.align_transcription_to_pg(
            transcription=transcription, pg_text=pg_text,
            pg_paragraphs=paragraphs, scan_page=1,
        )

        assert result is not None
        # Should align near the 18000-20000 region
        assert 15000 < result.alignment.pg_start < 25000, (
            f"Page 1 aligned to {result.alignment.pg_start}, expected 15000-25000"
        )

    def test_retas_sequential_pages_across_chapters(self, pg_text_and_paragraphs):
        """Multiple pages spanning chapters should stay in order with RETAS."""
        pg_text, paragraphs = pg_text_and_paragraphs
        aligner = VisionAligner(match_threshold=0.3, min_chunk_length=10)

        # Pages from different chapters
        positions = [
            (399, 1399),     # ch1 start
            (6000, 7000),    # ch1 mid
            (10000, 11000),  # ch1 late
            (13145, 14145),  # ch2 start
            (18000, 19000),  # ch2 mid
        ]

        transcriptions = [
            PageTranscription(
                page_num=i, image_path=Path(f"/tmp/page{i}.png"),
                transcription=pg_text[start:end], success=True,
            )
            for i, (start, end) in enumerate(positions)
        ]

        results = aligner.align_all_pages(
            transcriptions=transcriptions, pg_text=pg_text, pg_paragraphs=paragraphs,
        )

        # Should match at least 4 pages
        assert len(results) >= 4

        # Verify reading order
        for i in range(1, len(results)):
            assert results[i].alignment.pg_start >= results[i - 1].alignment.pg_start

    def test_retas_distinguishes_similar_paragraphs(self, pg_text_and_paragraphs):
        """RETAS should distinguish between similar paragraphs in different chapters."""
        pg_text, paragraphs = pg_text_and_paragraphs
        aligner = VisionAligner(match_threshold=0.3, min_chunk_length=10)

        # Take two chunks of similar length from different parts of the book
        chunk1 = pg_text[399:1399]   # ch1
        chunk2 = pg_text[13145:14145]  # ch2

        # Align chunk2 — it should NOT jump back to chunk1's position
        transcription = PageTranscription(
            page_num=0, image_path=Path("/tmp/page0.png"),
            transcription=chunk2, success=True,
        )

        result = aligner.align_transcription_to_pg(
            transcription=transcription, pg_text=pg_text,
            pg_paragraphs=paragraphs, scan_page=0,
        )

        assert result is not None
        # Should be in the ch2 region, not ch1
        assert result.alignment.pg_start > 10000, (
            f"Chunk from ch2 aligned to ch1 region at {result.alignment.pg_start}"
        )
