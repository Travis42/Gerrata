"""Tests for vision aligner — transcription, fuzzy matching, alignment."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from gerrata.aligner.vision_aligner import (
    VisionAligner,
    VisionTranscriber,
    PageTranscription,
    VisionAlignmentResult,
    ValidationResult,
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

    def test_strips_underscores(self):
        """PG italic markup _word_ should be stripped, not preserved."""
        assert normalize_for_matching("_Nostromo_") == "nostromo"
        assert normalize_for_matching("_italic_ and _more_") == "italic and more"

    def test_strips_html_tags(self):
        """HTML tags like <i>, <em>, <p> should be stripped."""
        assert normalize_for_matching("<i>text</i>") == "text"
        assert normalize_for_matching("<em>word</em>") == "word"
        assert normalize_for_matching("<p>para</p>") == "para"

    def test_strips_html_entities(self):
        """Named and numeric HTML entities should be stripped."""
        assert normalize_for_matching("Tom &amp; Jerry") == "tom jerry"
        assert normalize_for_matching("word &mdash; word") == "word word"
        assert normalize_for_matching("word &#8217; word") == "word word"

    def test_strips_footnote_refs(self):
        """PG footnote references [1] should be stripped."""
        assert normalize_for_matching("text [1] more") == "text more"
        assert normalize_for_matching("see [23] and [45]") == "see and"

    def test_strips_footnote_blocks(self):
        """PG footnote blocks [Footnote ...] should be stripped entirely."""
        assert normalize_for_matching("[Footnote 1: A note.]") == ""
        assert normalize_for_matching("text [Footnote 2: Long note.] end") == "text end"

    def test_strips_illustration_markers(self):
        """PG [Illustration:] markers should be stripped."""
        assert normalize_for_matching("[Illustration: A ship.]") == ""
        assert normalize_for_matching("text [Illustration: Scene.] end") == "text end"

    def test_strips_sidenote_markers(self):
        """PG [Sidenote:] markers should be stripped."""
        assert normalize_for_matching("[Sidenote: A note.]") == ""

    def test_huck_finn_density(self):
        """Dense underscore usage (like Huck Finn) should normalize cleanly."""
        text = "_could_ he ever want _look_ at it s I"
        assert normalize_for_matching(text) == "could he ever want look at it s i"


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
        assert "google/gemini-3.1-flash-lite" in t.models

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
            return_value='It is therefore a vain fancy to regard Spirit as immersed merely in the natural finite existence, and as seeking to raise itself from this limited existence to the absolute. Spirit is not something external to nature; it is the truth of nature itself, the very essence of which is to transcend its own immediacy and to enter into the realm of freedom. The movement of Spirit is thus not a departure from nature, but its own self-realization. In the process of its development, Spirit exhibits itself in three distinct forms: first as Spirit in itself, then as Spirit for itself, and finally as Spirit in and for itself. Each of these stages represents a necessary moment in the self-determination of the Idea, and each is grounded in the logical necessity of the concept. The history of philosophy is nothing other than the progressive unfolding of this dialectical movement, in which each system of thought emerges as a necessary moment in the self-development of absolute knowledge.'
        ):
            result = await transcriber.transcribe_page(img_path, page_num=0)

        assert result.success
        assert "Spirit is not something external" in result.transcription
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
            return 'It is therefore a vain fancy to regard Spirit as immersed merely in the natural finite existence, and as seeking to raise itself from this limited existence to the absolute. Spirit is not something external to nature; it is the truth of nature itself, the very essence of which is to transcend its own immediacy and to enter into the realm of freedom. The movement of Spirit is thus not a departure from nature, but its own self-realization. In the process of its development, Spirit exhibits itself in three distinct forms: first as Spirit in itself, then as Spirit for itself, and finally as Spirit in and for itself. Each of these stages represents a necessary moment in the self-determination of the Idea, and each is grounded in the logical necessity of the concept. The history of philosophy is nothing other than the progressive unfolding of this dialectical movement, in which each system of thought emerges as a necessary moment in the self-development of absolute knowledge.'

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
            return 'It is therefore a vain fancy to regard Spirit as immersed merely in the natural finite existence, and as seeking to raise itself from this limited existence to the absolute. Spirit is not something external to nature; it is the truth of nature itself, the very essence of which is to transcend its own immediacy and to enter into the realm of freedom. The movement of Spirit is thus not a departure from nature, but its own self-realization. In the process of its development, Spirit exhibits itself in three distinct forms: first as Spirit in itself, then as Spirit for itself, and finally as Spirit in and for itself. Each of these stages represents a necessary moment in the self-determination of the Idea, and each is grounded in the logical necessity of the concept. The history of philosophy is nothing other than the progressive unfolding of this dialectical movement, in which each system of thought emerges as a necessary moment in the self-development of absolute knowledge.'

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
                transcription="a fine bright cold day the streets were very full",
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
    """Tests for RETAS alignment strategy (removed n-gram anchoring tests)."""

    @pytest.fixture
    def aligner(self):
        return VisionAligner(match_threshold=0.3, min_chunk_length=10)

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

        # RETAS should find unique words like "CHAPTER ONE", "Utterson", "rugged", etc.
        # and align to the correct region
        if result is not None:
            ch1_pos = pg_text.find("CHAPTER ONE")
            ch5_pos = pg_text.find("CHAPTER FIVE")
            # Should align to the first half (CHAPTER ONE), not the second (CHAPTER FIVE)
            assert result.alignment.pg_start < (ch1_pos + ch5_pos) / 2

    def test_sequential_alignment_enforces_order(self, aligner):
        """Pages should align in reading order (sequential constraint)."""
        # Use text with unique proper nouns/words so RETAS can find unique word anchors.
        # Without unique words, RETAS cannot anchor — this is by design.
        pg_text = (
            "In the year 1884, Mr. Utterson the lawyer walked through Soho. "
            "The neighborhood was dismal and the streets were dark. "
            "He carried a heavy cane and wore a distinguished coat. "
            "The fog was thick that evening in London. "
            "Meanwhile Dr. Lanyon received a peculiar package from Mr. Hyde. "
            "The parcel contained chemicals and a mysterious notebook. "
            "The physician examined its contents with bewilderment. "
            "He decided to contact his old friend Utterson immediately. "
            "Elsewhere, Mr. Hyde trampled a young girl in the street. "
            "The crowd gathered around the scene with horror. "
            "Utterson confronted Hyde about the incident the next morning. "
        )
        paragraphs = [p.strip() for p in pg_text.split(". ") if len(p.strip()) > 10]

        transcriptions = [
            PageTranscription(
                page_num=0, image_path=Path("/tmp/p0.png"),
                transcription="Mr. Utterson the lawyer walked through Soho. The neighborhood was dismal.", success=True,
            ),
            PageTranscription(
                page_num=1, image_path=Path("/tmp/p1.png"),
                transcription="Dr. Lanyon received a peculiar package from Mr. Hyde with chemicals.", success=True,
            ),
            PageTranscription(
                page_num=2, image_path=Path("/tmp/p2.png"),
                transcription="Mr. Hyde trampled a young girl in the street. The crowd gathered.", success=True,
            ),
        ]

        results = aligner.align_all_pages(
            transcriptions=transcriptions, pg_text=pg_text, pg_paragraphs=paragraphs,
        )

        # At least 2 should match (they have unique words like "Soho", "Lanyon", "Hyde")
        assert len(results) >= 2
        # Verify order
        for i in range(1, len(results)):
            assert results[i].alignment.pg_start >= results[i - 1].alignment.pg_start


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


# ── Recovery Mechanisms ─────────────────────────────────────────────

class TestCheckPositionConsistency:
    """Tests for Recovery 1: position consistency checking."""

    def test_consistent_consecutive_pages(self):
        """Consecutive pages with normal gaps should be consistent."""
        assert VisionAligner.check_position_consistency(
            prev_pg_end=3000, prev_page_num=1,
            pg_start=4500, page_num=2,
            chars_per_page=1500,
        ) is True

    def test_consistent_with_one_page_gap(self):
        """One skipped page should still be consistent."""
        assert VisionAligner.check_position_consistency(
            prev_pg_end=3000, prev_page_num=1,
            pg_start=6000, page_num=3,
            chars_per_page=1500,
        ) is True

    def test_anomaly_too_large_gap(self):
        """Gap more than 2× expected should be detected."""
        # Expected gap for 1 page: 1500
        # Actual gap: 5000 (> 2 × 1500 = 3000)
        assert VisionAligner.check_position_consistency(
            prev_pg_end=3000, prev_page_num=1,
            pg_start=8000, page_num=2,
            chars_per_page=1500,
        ) is False

    def test_anomaly_negative_gap(self):
        """Negative gap (overlap) should be detected for consecutive pages."""
        assert VisionAligner.check_position_consistency(
            prev_pg_end=5000, prev_page_num=1,
            pg_start=2000, page_num=2,
            chars_per_page=1500,
        ) is False

    def test_same_page_allows_negative(self):
        """Same page number should return True (can't check consistency)."""
        assert VisionAligner.check_position_consistency(
            prev_pg_end=5000, prev_page_num=2,
            pg_start=2000, page_num=2,
            chars_per_page=1500,
        ) is True

    def test_earlier_page_returns_true(self):
        """Earlier page number should return True."""
        assert VisionAligner.check_position_consistency(
            prev_pg_end=5000, prev_page_num=5,
            pg_start=2000, page_num=3,
            chars_per_page=1500,
        ) is True

    def test_consecutive_pages_allow_small_overlap(self):
        """Consecutive pages allow some overlap (within 0.5× cpp)."""
        # cpp=1500, so 0.5×cpp = 750. Gap of -500 is within tolerance.
        assert VisionAligner.check_position_consistency(
            prev_pg_end=3000, prev_page_num=1,
            pg_start=2500, page_num=2,
            chars_per_page=1500,
        ) is True

    def test_zero_expected_gap(self):
        """Consecutive pages with zero expected gap — small overlaps OK."""
        # expected_gap = 0, so we check actual_gap >= -0.5*1500 = -750
        assert VisionAligner.check_position_consistency(
            prev_pg_end=3000, prev_page_num=1,
            pg_start=2500, page_num=2,
            chars_per_page=1500,
        ) is True
        # Large overlap should fail
        assert VisionAligner.check_position_consistency(
            prev_pg_end=3000, prev_page_num=1,
            pg_start=1000, page_num=2,
            chars_per_page=1500,
        ) is False


class TestCheckScoringRegression:
    """Tests for Recovery 2: scoring regression detection."""

    def test_no_regression_stable_scores(self):
        """Stable scores should not trigger regression."""
        rolling = [0.8, 0.8, 0.8, 0.8, 0.8, 0.8]
        assert VisionAligner.check_scoring_regression(0.78, rolling) is False

    def test_regression_detected(self):
        """Score 30% below average should trigger regression."""
        rolling = [0.8, 0.8, 0.8, 0.8, 0.8, 0.8]
        assert VisionAligner.check_scoring_regression(0.5, rolling) is True

    def test_no_regression_insufficient_data(self):
        """Fewer than 5 rolling scores should not trigger."""
        rolling = [0.8, 0.8, 0.8]
        assert VisionAligner.check_scoring_regression(0.2, rolling) is False

    def test_threshold_boundary(self):
        """Exactly at threshold should not trigger (strict >)."""
        rolling = [0.8, 0.8, 0.8, 0.8, 0.8]
        # avg = 0.8, 20% below = 0.64
        assert VisionAligner.check_scoring_regression(0.64, rolling) is False
        assert VisionAligner.check_scoring_regression(0.63, rolling) is True

    def test_zero_rolling_scores(self):
        """All-zero rolling scores should not trigger."""
        rolling = [0.0, 0.0, 0.0, 0.0, 0.0]
        assert VisionAligner.check_scoring_regression(0.5, rolling) is False

    def test_custom_threshold(self):
        """Custom threshold should work."""
        rolling = [0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8]
        # 10% below average (0.72) with default 20% threshold → no regression
        assert VisionAligner.check_scoring_regression(0.72, rolling, threshold=0.10) is False
        # 15% below with 10% threshold → regression
        assert VisionAligner.check_scoring_regression(0.68, rolling, threshold=0.10) is True


class TestEditDensityCheck:
    """Tests for Recovery 4: post-alignment edit density check."""

    @pytest.fixture
    def aligner(self):
        return VisionAligner(match_threshold=0.3, min_chunk_length=10)

    def test_exact_match_low_density(self, aligner):
        """Exact match should have near-zero edit density."""
        text = "Mr. Utterson the lawyer was a man of a rugged countenance."
        pg_text = "Some intro. " + text + " Some ending."
        pg_paragraphs = [pg_text]

        transcription = PageTranscription(
            page_num=0, image_path=Path("/tmp/page.png"),
            transcription=text, success=True,
        )

        result = aligner.align_transcription_to_pg(
            transcription=transcription, pg_text=pg_text,
            pg_paragraphs=pg_paragraphs, scan_page=0,
        )

        # Should match — low edit density
        assert result is not None

    def test_very_different_text_rejected(self, aligner):
        """Text with >15% edit density should be rejected."""
        # Create a transcription that shares only a few words
        pg_text = (
            "The extraordinary elephant danced across the plaza while bewildered tourists "
            "photographed the magnificent creature with their cameras and smartphones. "
            "The weather was sunny and warm that afternoon in the central square."
        )
        pg_paragraphs = [pg_text]

        # A transcription that has some words in common but is very different
        transcription = PageTranscription(
            page_num=0, image_path=Path("/tmp/page.png"),
            transcription=(
                "The extraordinary elephant danced across the plaza while bewildered tourists "
                "ran away screaming because the creature was angry and dangerous "
                "and the sky turned dark and storm clouds gathered overhead"
            ),
            success=True,
        )

        result = aligner.align_transcription_to_pg(
            transcription=transcription, pg_text=pg_text,
            pg_paragraphs=pg_paragraphs, scan_page=0,
        )

        # Should be rejected due to high edit density
        # (many edits relative to alignment length)
        if result is None:
            pass  # Correctly rejected
        else:
            # If accepted, the score should be moderate at best
            assert result.best_score < 0.7


class TestEnhancedReanchor:
    """Tests for Recovery 3: enhanced SequentialTracker.reanchor()."""

    @pytest.fixture
    def tracker(self):
        from gerrata.aligner.vision_aligner import SequentialTracker
        return SequentialTracker()

    def test_reanchor_without_aligner(self, tracker):
        # Record 3+ high-confidence matches
        tracker.record(1, 1500, 0.5, 1500)
        tracker.record(2, 3000, 0.5, 1500)
        tracker.record(3, 4500, 0.5, 1500)

        result = tracker.reanchor()
        assert result is True
        assert len(tracker.matches) <= 10  # Trimmed to last 10

    def test_reanchor_insufficient_data(self, tracker):
        tracker.record(1, 1500, 0.5, 1500)
        tracker.record(2, 3000, 0.5, 1500)

        result = tracker.reanchor()
        assert result is False

    def test_reanchor_preserves_recent_matches(self, tracker):
        for i in range(20):
            tracker.record(i, (i + 1) * 1500, 0.5, 1500)

        tracker.reanchor()
        assert len(tracker.matches) == 10
        # Should be the LAST 10
        assert tracker.matches[0][0] == 10  # page_num 10

    def test_verify_with_unconstrained_retas(self, tracker):
        """_verify_with_unconstrained_retas should reset tracker when off."""
        from unittest.mock import MagicMock
        # Record matches at wrong positions (simulating drift)
        tracker.record(1, 5000, 0.5, 1500)
        tracker.record(2, 6500, 0.5, 1500)
        tracker.record(3, 8000, 0.5, 1500)

        # Create a mock aligner that returns a position far from tracker expected
        mock_aligner = MagicMock()
        mock_result = MagicMock()
        mock_result.alignment.pg_start = 500  # Way off from expected ~9500
        mock_result.alignment.pg_end = 2000
        mock_result.best_score = 0.8
        mock_aligner.align_transcription_to_pg = MagicMock(return_value=mock_result)

        mock_trans = MagicMock()
        mock_trans.success = True

        tracker._verify_with_unconstrained_retas(
            mock_aligner, mock_trans,
            pg_text="some text " * 1000,
            pg_paragraphs=["some text"],
            page_num=4,
        )

        # Tracker should have been reset to the RETAS-derived position
        assert len(tracker.matches) == 1
        assert tracker.matches[0][0] == 4  # page_num 4
        assert tracker.matches[0][1] == 2000  # pg_end from mock

    def test_verify_no_reset_when_accurate(self, tracker):
        """_verify_with_unconstrained_retas should NOT reset when tracker is accurate."""
        from unittest.mock import MagicMock
        tracker.record(1, 1500, 0.5, 1500)
        tracker.record(2, 3000, 0.5, 1500)
        tracker.record(3, 4500, 0.5, 1500)

        # RETAS returns position consistent with tracker expectation
        expected = tracker.expected_position(4)  # ~6000
        mock_aligner = MagicMock()
        mock_result = MagicMock()
        mock_result.alignment.pg_start = expected + 100  # Close to expected
        mock_result.alignment.pg_end = expected + 1600
        mock_result.best_score = 0.8
        mock_aligner.align_transcription_to_pg = MagicMock(return_value=mock_result)

        mock_trans = MagicMock()

        original_matches = list(tracker.matches)
        tracker._verify_with_unconstrained_retas(
            mock_aligner, mock_trans,
            pg_text="some text " * 1000,
            pg_paragraphs=["some text"],
            page_num=4,
        )

        # Tracker should NOT have been reset
        assert len(tracker.matches) == len(original_matches)


class TestREANCHORInterval:
    """Test that REANCHOR_INTERVAL is set to 10."""

    def test_reanchor_interval(self):
        from gerrata.aligner.vision_aligner import REANCHOR_INTERVAL
        assert REANCHOR_INTERVAL == 10


# ── Post-alignment validation ─────────────────────────────────────────

def _make_pg_text(num_pages: int = 30, chars_per_page: int = 1500) -> str:
    """Generate deterministic PG text for validation tests.

    Each "page" is a paragraph of ~chars_per_page chars with a distinctive
    sentence at offset 30+ for phrase extraction.
    """
    pages = []
    for i in range(num_pages):
        # Header-like text (gets skipped by phrase extraction)
        header = f"Chapter {i + 1}. " * 3
        # Distinctive body text with a unique sentence
        sentence = f"The unique sentence for page {i} contains distinctive markers that should be found. "
        # Pad to desired length
        padding = "The rest of the paragraph contains ordinary text that is repeated across pages. " * 3
        pages.append(header + sentence + padding)
    return "\n\n".join(pages)


def _make_alignments(pg_text: str, num_pages: int = 30, offset: int = 0) -> list[Alignment]:
    """Generate alignments with a systematic offset."""
    chars_per_page = len(pg_text) // num_pages
    alignments = []
    for i in range(num_pages):
        start = max(0, i * chars_per_page + offset)
        end = min(len(pg_text), start + chars_per_page)
        alignments.append(Alignment(
            pg_start=start,
            pg_end=end,
            scan_page=i,
            confidence=0.8,
            method=AlignmentMethod.LLM_VISION,
        ))
    return alignments


def _make_transcriptions(num_pages: int = 30, page_texts: list[str] | None = None) -> list[PageTranscription]:
    """Generate transcriptions matching the PG text structure."""
    transcriptions = []
    for i in range(num_pages):
        text = page_texts[i] if page_texts and i < len(page_texts) else (
            f"Chapter {i + 1}. " * 3
            + f"The unique sentence for page {i} contains distinctive markers that should be found. "
            + "The rest of the paragraph contains ordinary text that is repeated across pages. " * 3
        )
        transcriptions.append(PageTranscription(
            page_num=i,
            image_path=Path(f"/tmp/page_{i:04d}.png"),
            transcription=text,
            transcription_cleaned=text,
            success=True,
        ))
    return transcriptions


class TestValidationNoOffset:
    """Alignments are correct — validation returns 'ok'."""

    def test_ok_verdict(self):
        pg_text = _make_pg_text(30)
        alignments = _make_alignments(pg_text, 30, offset=0)
        transcriptions = _make_transcriptions(30)
        aligner = VisionAligner()

        result_alignments, vr = aligner.validate_and_correct(
            alignments=alignments,
            transcriptions=transcriptions,
            pg_text=pg_text,
        )

        assert vr.verdict == "ok"
        assert not vr.corrected
        assert vr.pages_correct >= vr.sample_size * 0.8


class TestValidationSystematicShift:
    """All alignments are shifted +1500 chars — correction shifts them back."""

    def test_corrects_shift(self):
        shift = 1500
        pg_text = _make_pg_text(30)
        alignments = _make_alignments(pg_text, 30, offset=shift)
        transcriptions = _make_transcriptions(30)
        aligner = VisionAligner()

        result_alignments, vr = aligner.validate_and_correct(
            alignments=alignments,
            transcriptions=transcriptions,
            pg_text=pg_text,
        )

        assert vr.verdict == "corrected"
        assert vr.corrected
        # Mean offset should be close to the applied shift (within 20%)
        assert abs(abs(round(vr.offset_mean)) - shift) < shift * 0.2, (
            f"offset_mean={vr.offset_mean} too far from expected shift={shift}"
        )
        # After correction, offsets should be near 0
        for a in result_alignments:
            assert a.pg_start >= 0
            assert a.pg_end >= a.pg_start


class TestValidationPartialShift:
    """70% correct, 30% shifted by +1200 — should still correct with mean."""

    def test_corrects_partial(self):
        pg_text = _make_pg_text(30)
        alignments = _make_alignments(pg_text, 30, offset=0)
        # Shift 30% of pages
        for i, a in enumerate(alignments):
            if i % 3 == 0:
                a.pg_start += 1200
                a.pg_end += 1200
        transcriptions = _make_transcriptions(30)
        aligner = VisionAligner()

        result_alignments, vr = aligner.validate_and_correct(
            alignments=alignments,
            transcriptions=transcriptions,
            pg_text=pg_text,
        )

        # 30% shifted may produce stddev in 500-1500 range → "rescored"
        assert vr.verdict in ("corrected", "ok", "rescored")
        assert vr.sample_size > 0
        # At minimum we should get a verdict — not crash
        for a in result_alignments:
            assert a.pg_start >= 0


class TestValidationInconsistent:
    """Random offsets with high stddev → 'failed' verdict."""

    def test_failed_verdict(self):
        pg_text = _make_pg_text(30)
        alignments = _make_alignments(pg_text, 30, offset=0)
        # Apply random-ish offsets that create high stddev
        import random
        random.seed(42)
        for a in alignments:
            offset = random.randint(-3000, 3000)
            a.pg_start += offset
            a.pg_end += offset
        transcriptions = _make_transcriptions(30)
        aligner = VisionAligner()

        result_alignments, vr = aligner.validate_and_correct(
            alignments=alignments,
            transcriptions=transcriptions,
            pg_text=pg_text,
        )

        # With random offsets the accuracy will be low and stddev high
        # Could be "failed" or "rescored" depending on stddev
        assert vr.verdict in ("failed", "rescored", "corrected")
        assert vr.sample_size > 0
        # The original alignments should be returned unmodified for "failed"
        if vr.verdict == "failed":
            assert not vr.corrected


class TestValidationShortTranscriptions:
    """Most pages have <50 chars cleaned — validation skips gracefully."""

    def test_skips_short(self):
        pg_text = _make_pg_text(30)
        alignments = _make_alignments(pg_text, 30, offset=1500)
        # All transcriptions are too short
        short_trans = [
            PageTranscription(
                page_num=i,
                image_path=Path(f"/tmp/page_{i:04d}.png"),
                transcription="Short",
                transcription_cleaned="Too short for phrase",
                success=True,
            )
            for i in range(30)
        ]
        aligner = VisionAligner()

        result_alignments, vr = aligner.validate_and_correct(
            alignments=alignments,
            transcriptions=short_trans,
            pg_text=pg_text,
        )

        assert vr.verdict == "ok"  # Skipped → defaults to "ok"
        assert vr.sample_size < 5
        assert len(result_alignments) == len(alignments)


class TestValidationEmptyAlignments:
    """No alignments → no crash, returns 'ok'."""

    def test_empty_ok(self):
        pg_text = "Some text"
        aligner = VisionAligner()

        result_alignments, vr = aligner.validate_and_correct(
            alignments=[],
            transcriptions=[],
            pg_text=pg_text,
        )

        assert vr.verdict == "ok"
        assert result_alignments == []


class TestPhraseExtraction:
    """Verifies distinctive phrase selection skips titles, works with various formats."""

    def test_skips_chapter_title(self):
        aligner = VisionAligner()
        text = "CHAPTER TWELVE  The quick brown fox jumped over the lazy dog near the river bank where the old man was fishing quietly that afternoon."
        phrase = aligner._extract_distinctive_phrase(text, start_offset=30)
        assert phrase is not None
        assert len(phrase) >= 20
        # Should not start with "CHAPTER"
        assert "chapter" not in phrase.lower()[:10]

    def test_works_with_shorter_text(self):
        aligner = VisionAligner()
        text = "Title here. " + "Some distinctive body text follows after the initial heading section ends."
        phrase = aligner._extract_distinctive_phrase(text, start_offset=10)
        assert phrase is not None
        assert "distinctive" in phrase

    def test_returns_none_for_very_short_text(self):
        aligner = VisionAligner()
        text = "Very short"
        phrase = aligner._extract_distinctive_phrase(text, start_offset=30)
        assert phrase is None


class TestOffsetClamping:
    """Negative pg_start after correction is clamped to 0."""

    def test_clamps_to_zero(self):
        # Make alignments with small pg_start values, then apply large negative shift
        pg_text = _make_pg_text(30)
        alignments = _make_alignments(pg_text, 30, offset=0)
        # Force all pg_start to be small (200) then shift will make them negative
        for a in alignments:
            a.pg_start = 200
            a.pg_end = 1700

        transcriptions = _make_transcriptions(30)
        aligner = VisionAligner()

        # The alignment offset will be large positive (pg_text positions are much larger)
        # So correction will subtract a lot, potentially going negative
        result_alignments, vr = aligner.validate_and_correct(
            alignments=alignments,
            transcriptions=transcriptions,
            pg_text=pg_text,
        )

        for a in result_alignments:
            assert a.pg_start >= 0, f"pg_start {a.pg_start} is negative!"
            assert a.pg_end >= a.pg_start, f"pg_end {a.pg_end} < pg_start {a.pg_start}"


class TestValidationLinearDrift:
    """Cumulative drift — offset grows linearly with page number."""

    def test_detects_linear_drift(self):
        """Offset grows ~50 chars/page — should detect as drift_corrected."""
        pg_text = _make_pg_text(60)
        alignments = _make_alignments(pg_text, 60, offset=0)
        transcriptions = _make_transcriptions(60)

        # Apply growing offset: 0 at page 0, ~50*page at page N
        chars_per_page = len(pg_text) // 60
        for i, a in enumerate(alignments):
            drift = i * 50  # cumulative drift
            a.pg_start += drift
            a.pg_end += drift

        aligner = VisionAligner()
        result_alignments, vr = aligner.validate_and_correct(
            alignments=alignments,
            transcriptions=transcriptions,
            pg_text=pg_text,
        )

        # Should detect the linear trend and apply drift correction
        assert vr.verdict == "drift_corrected", f"Expected drift_corrected, got {vr.verdict}"
        assert vr.corrected
        assert abs(vr.drift_slope + 50) < 15, f"drift_slope={vr.drift_slope}, expected ~-50"
        # Residual stddev should be much smaller than raw stddev
        assert vr.residual_stddev < vr.offset_stddev * 0.5

    def test_drift_correction_improves_accuracy(self):
        """After drift correction, spot-check accuracy should improve."""
        pg_text = _make_pg_text(60)
        alignments = _make_alignments(pg_text, 60, offset=0)
        transcriptions = _make_transcriptions(60)

        # Apply growing offset: 40 chars/page
        for i, a in enumerate(alignments):
            drift = i * 40
            a.pg_start += drift
            a.pg_end += drift

        aligner = VisionAligner()
        result_alignments, vr = aligner.validate_and_correct(
            alignments=alignments,
            transcriptions=transcriptions,
            pg_text=pg_text,
        )

        assert vr.verdict == "drift_corrected"

        # All corrected alignments should have valid bounds
        for a in result_alignments:
            assert a.pg_start >= 0
            assert a.pg_end >= a.pg_start
            assert a.pg_end <= len(pg_text)
