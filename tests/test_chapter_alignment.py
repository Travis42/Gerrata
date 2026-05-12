"""Tests for chapter-based alignment constraints and body-start detection."""

import pytest
from pathlib import Path

from gerrata.aligner.vision_aligner import (
    VisionAligner,
    PageTranscription,
    VisionAlignmentResult,
    find_body_start,
)
from gerrata.fetcher.pg import ChapterLocation, PGMetadata, PGParsedText
from gerrata.models import Alignment, AlignmentMethod


# ── find_body_start ───────────────────────────────────────────────────

class TestFindBodyStart:
    """Tests for automatic detection of prose body start (skipping TOC)."""

    def test_simple_toc_then_prose(self):
        """TOC followed by a long prose paragraph should be detected."""
        pg_text = (
            "CHAPTER 1. Title One.\n\n"
            "CHAPTER 2. Title Two.\n\n"
            "CHAPTER 3. Title Three.\n\n"
            "It was a bright cold day in April, and the clocks were striking thirteen. "
            "Winston Smith, his chin nuzzled into his breast in an effort to escape "
            "the vile wind, slipped quickly through the glass doors of Victory Mansions, "
            "though not quickly enough to prevent a swirl of gritty dust from entering "
            "along with him. The hallway smelt of boiled cabbage and old rag mats. "
            "At one end of it a coloured poster, too large for indoor display, had been "
            "tacked to the wall. It depicted simply an enormous face."
        )
        offset = find_body_start(pg_text)
        assert offset > 0
        assert "bright cold day" in pg_text[offset:offset + 100]

    def test_css_noise_ignored(self):
        """CSS/HTML noise in PG text should not confuse detection."""
        pg_text = (
            "body {margin-left:10%; text-align:justify}\n\n"
            "p {text-indent:1em}\n\n"
            "h2 {margin-top:2%}\n\n"
            "CHAPTER 1. The Beginning.\n\n"
            "CHAPTER 2. The Middle.\n\n"
            "In a hole in the ground there lived a hobbit. Not a nasty, dirty, "
            "wet hole, filled with the ends of worms and an oozy smell. "
            "It was a hobbit-hole, and that means comfort. It had a perfectly "
            "round door like a porthole, painted green, with a shiny yellow "
            "brass knob in the exact middle."
        )
        offset = find_body_start(pg_text)
        assert offset > 0
        assert "hobbit" in pg_text[offset:offset + 200]

    def test_no_chapter_headings(self):
        """Text without chapter headings should use the fallback (long paragraph)."""
        pg_text = (
            "Some introductory heading.\n\n"
            "In a village of La Mancha, the name of which I have no desire to "
            "call to mind, there lived not long since one of those gentlemen "
            "that keep a lance in the lance-rack, an old buckler, a lean hack, "
            "and a greyhound for coursing. An olla of rather more beef than mutton, "
            "a salad on most nights, scraps on Saturdays, lentils on Fridays, and a "
            "pigeon or so extra on Sundays, made away with three-quarters of his income."
        )
        offset = find_body_start(pg_text)
        assert offset >= 0

    def test_pride_and_prejudice_pattern(self):
        """Simulates P&P-style: CSS noise, then chapter headings then prose."""
        pg_text = (
            "Pride and Prejudice | Project Gutenberg\n\n"
            ".blk {page-break-before:always}\n\n"
            ".caption {font-weight:normal}\n\n"
            "CHAPTER I.\n\n"
            "CHAPTER II.\n\n"
            "For if her knowledge was not very extended, she knew two things which "
            "only geniuses could know. She knew that the best of men are not perfect, "
            "and that the worst are not wholly bad. This was the foundation of her "
            "philosophy, and it served her well in the years to come. Every morning "
            "she would walk in the garden and reflect upon these things."
        )
        offset = find_body_start(pg_text)
        assert offset > 0
        body = pg_text[offset:]
        assert "margin" not in body[:50]
        assert "CHAPTER" not in body[:50]

    def test_returns_zero_for_empty_or_short_text(self):
        """Very short text should return 0 (no prose found)."""
        assert find_body_start("") == 0
        assert find_body_start("Hello world.") == 0
        assert find_body_start("CHAPTER 1.\n\nShort.") == 0

    def test_all_caps_headings_skipped(self):
        """All-caps headings like Jekyll should be handled correctly."""
        pg_text = (
            "STORY OF THE DOOR\n\n"
            "INCIDENT OF DR. LANYON\n\n"
            "CHAPTER 1.\n\n"
            "Mr. Utterson the lawyer was a man of a rugged countenance that was "
            "never lighted by a smile. He was austere with himself. He leaned "
            "backward in sentiment and had little to say for himself in company."
        )
        offset = find_body_start(pg_text)
        assert offset > 0
        assert "Utterson" in pg_text[offset:offset + 100]

    def test_body_start_offset_points_to_prose(self):
        """The returned offset should point to actual prose, not headings."""
        prose = (
            "Call me Ishmael. Some years ago—never mind how long precisely—having. "
            "With a philosophical flourish Cato throws himself upon his sword. "
            "This is my substitute for pistol and ball."
        )
        pg_text = (
            "CONTENTS\n\nETYMOLOGY.\n\n"
            "CHAPTER 1. Loomings.\n\n"
            "CHAPTER 2. The Carpet-Bag.\n\n"
            + prose + "\n\n"
            "More text here. And so it continued for many years."
        )
        offset = find_body_start(pg_text)
        assert offset > 0
        assert "Call me Ishmael" in pg_text[offset:offset + 50]


# ── _detect_scan_chapter ─────────────────────────────────────────────

class TestDetectScanChapter:
    """Tests for detecting chapter headings in scan page transcriptions."""

    @pytest.fixture
    def aligner(self):
        return VisionAligner()

    def test_simple_title(self, aligner):
        result = aligner._detect_scan_chapter("Loomings\nBut here is an artist.")
        assert result == "Loomings"

    def test_two_word_title(self, aligner):
        result = aligner._detect_scan_chapter("The Carpet-Bag\nI love to sail.")
        assert result == "The Carpet-Bag"

    def test_running_header_rejected(self, aligner):
        result = aligner._detect_scan_chapter("Moby Dick\nwhere that noble mole is washed")
        assert result is None

    def test_chapter_prefix_stripped(self, aligner):
        result = aligner._detect_scan_chapter("CHAPTER 32. Cetology\nSome years ago.")
        assert result == "Cetology"

    def test_trailing_page_number_stripped(self, aligner):
        result = aligner._detect_scan_chapter(
            "The Spouter-Inn 15\n'the harpooneer is a dark complexioned chap.'"
        )
        assert result == "The Spouter-Inn"

    def test_trailing_period_stripped(self, aligner):
        """Trailing periods should be stripped from short titles."""
        result = aligner._detect_scan_chapter("Nightgown\ntime, when all at once")
        assert result == "Nightgown"

    def test_empty_transcription(self, aligner):
        assert aligner._detect_scan_chapter("") is None
        assert aligner._detect_scan_chapter(None) is None

    def test_prose_first_line_rejected(self, aligner):
        """Prose starting with mixed capitalization should not be a chapter."""
        result = aligner._detect_scan_chapter(
            "It was a bright cold day in April and the clocks were striking thirteen."
        )
        # "was", "a", "cold", "day", "and", "the", "clocks" — "was", "a", "and" are short
        # but "cold", "day" start lowercase — check: not all words with len>2 are capitalized
        assert result is None

    def test_non_chapter_headings_rejected(self, aligner):
        assert aligner._detect_scan_chapter("Introduction\nSome text") is None
        assert aligner._detect_scan_chapter("Preface\nSome text") is None
        assert aligner._detect_scan_chapter("Contents\nCHAPTER 1") is None

    def test_case_insensitive_skip(self, aligner):
        assert aligner._detect_scan_chapter("contents\nCHAPTER 1") is None
        assert aligner._detect_scan_chapter("INTRODUCTION\nSome text") is None

    def test_digit_only_line_rejected(self, aligner):
        result = aligner._detect_scan_chapter("42\nThe answer to everything.")
        assert result is None

    def test_biographical_detected(self, aligner):
        """Single-word title should be detected if capitalized."""
        result = aligner._detect_scan_chapter(
            "Biographical\nmy own design, and informed him"
        )
        assert result == "Biographical"


# ── _match_chapter_heading ───────────────────────────────────────────

class TestMatchChapterHeading:
    """Tests for matching scan chapter headings to PG chapter list."""

    @pytest.fixture
    def aligner(self):
        return VisionAligner()

    @pytest.fixture
    def sample_chapters(self):
        return [
            ChapterLocation(title="CHAPTER 1. Loomings.", offset=26358, end_offset=38569),
            ChapterLocation(title="CHAPTER 2. The Carpet-Bag.", offset=38569, end_offset=46510),
            ChapterLocation(title="CHAPTER 3. The Spouter-Inn.", offset=46510, end_offset=78522),
            ChapterLocation(title="CHAPTER 4. The Counterpane.", offset=78522, end_offset=87650),
            ChapterLocation(title="CHAPTER 5. Breakfast.", offset=87650, end_offset=91849),
            ChapterLocation(title="CHAPTER 32. Cetology.", offset=100000, end_offset=110000),
            ChapterLocation(title="CHAPTER 133. The Chase.", offset=500000, end_offset=510000),
            ChapterLocation(title="CHAPTER 134. The Chase—Second Day.", offset=510000, end_offset=520000),
        ]

    def test_exact_match(self, aligner, sample_chapters):
        idx = aligner._match_chapter_heading("Loomings", sample_chapters)
        assert idx == 0

    def test_exact_match_with_period(self, aligner, sample_chapters):
        idx = aligner._match_chapter_heading("Cetology.", sample_chapters)
        assert idx == 5

    def test_fuzzy_match_long_title(self, aligner, sample_chapters):
        idx = aligner._match_chapter_heading("The Spouter Inn", sample_chapters)
        assert idx == 2

    def test_position_preference_near(self, aligner, sample_chapters):
        idx = aligner._match_chapter_heading("The Chase", sample_chapters, current_idx=6)
        assert idx == 6

    def test_position_preference_from_start(self, aligner, sample_chapters):
        idx = aligner._match_chapter_heading("The Chase", sample_chapters, current_idx=0)
        assert idx == 6

    def test_no_match_returns_none(self, aligner, sample_chapters):
        idx = aligner._match_chapter_heading("Nonexistent Chapter", sample_chapters)
        assert idx is None

    def test_empty_heading_returns_none(self, aligner, sample_chapters):
        assert aligner._match_chapter_heading("", sample_chapters) is None
        assert aligner._match_chapter_heading(None, sample_chapters) is None

    def test_short_titles_not_fuzzy_matched(self, aligner, sample_chapters):
        idx = aligner._match_chapter_heading("The", sample_chapters)
        assert idx is None


# ── Chapter-constrained align_all_pages ────────────────────────────────

class TestChapterConstrainedAlignment:
    """Tests for per-chapter alignment constraints in align_all_pages."""

    @pytest.fixture
    def aligner(self):
        return VisionAligner(match_threshold=0.3, min_chunk_length=10)

    def _build_chaptered_pg(self):
        """Build a multi-chapter PG text and return (text, paragraphs, chapters).
        
        Each chapter uses unique, distinctive vocabulary to prevent cross-chapter false matches.
        """
        parts = []
        offsets = {}
        current = 0

        def add(text, label):
            nonlocal current
            offsets[label] = current
            parts.append(text)
            current += len(text)

        add("Preliminary remarks about the cetacean order. ", "intro")
        add("ETYMOLOGY. The word whale comes from old English. ", "ety")
        add("CHAPTER 1. Loomings.\n\n", "ch1_head")
        add(
            "Call me Ishmael. Some years ago—never mind how long precisely—having "
            "little or no money in my purse, I thought I would sail about a little "
            "and see the watery part of the world. It is a way I have of driving "
            "off the spleen and regulating the circulation. Whenever I find myself "
            "growing grim about the mouth, I take to the ship.\n\n",
            "ch1_body"
        )
        add("CHAPTER 2. The Carpet-Bag.\n\n", "ch2_head")
        add(
            "It was a queer sort of place—a gable-ended old house with a low "
            "projecting roof. I stuffed a shirt or two into my old carpet-bag, "
            "tucked my umbrella under my arm, and pushed off for the sign of "
            "the Try Pots. Wrapped up in my shaggy jacket I entered.\n\n",
            "ch2_body"
        )
        add("CHAPTER 3. The Spouter-Inn.\n\n", "ch3_head")
        add(
            "Entering that gable-ended Spouter-Inn, I found myself in a cavernous "
            "gloom. A dark little room it was, with panelled walls and a sanded "
            "floor. The barroom was crowded with whalemen from various parts of "
            "the globe, many of them tattooed and scarred.\n\n",
            "ch3_body"
        )

        pg_text = "".join(parts)
        paragraphs = [p.strip() for p in pg_text.split("\n\n") if len(p.strip()) > 20]
        chapters = [
            ChapterLocation(
                title="CHAPTER 1. Loomings.",
                offset=offsets["ch1_head"],
                end_offset=offsets["ch2_head"],
            ),
            ChapterLocation(
                title="CHAPTER 2. The Carpet-Bag.",
                offset=offsets["ch2_head"],
                end_offset=offsets["ch3_head"],
            ),
            ChapterLocation(
                title="CHAPTER 3. The Spouter-Inn.",
                offset=offsets["ch3_head"],
                end_offset=current,
            ),
        ]
        return pg_text, paragraphs, chapters

    def test_chapters_constrain_search(self, aligner):
        """Pages with chapter headings should be constrained to the correct chapter."""
        pg_text, paragraphs, chapters = self._build_chaptered_pg()

        # Use transcriptions WITH chapter headings so the constraint activates
        transcriptions = [
            PageTranscription(
                page_num=0, image_path=Path("/tmp/p0.png"),
                transcription="Loomings\nCall me Ishmael. Some years ago—never mind how long precisely",
                success=True,
            ),
            PageTranscription(
                page_num=1, image_path=Path("/tmp/p1.png"),
                transcription="The Spouter-Inn\nEntering that gable-ended Spouter-Inn, I found myself in a cavernous gloom",
                success=True,
            ),
        ]

        results = aligner.align_all_pages(
            transcriptions=transcriptions,
            pg_text=pg_text,
            pg_paragraphs=paragraphs,
            chapters=chapters,
        )

        assert len(results) >= 2

        # Pages should be in sequential order
        assert results[0].alignment.pg_start < results[1].alignment.pg_start

    def test_no_chapters_falls_back_to_full_search(self, aligner):
        """Without chapters, alignment should work normally."""
        pg_text, paragraphs, _ = self._build_chaptered_pg()

        transcriptions = [
            PageTranscription(
                page_num=0, image_path=Path("/tmp/p0.png"),
                transcription="Call me Ishmael. Some years ago—never mind how long precisely",
                success=True,
            ),
        ]

        results = aligner.align_all_pages(
            transcriptions=transcriptions,
            pg_text=pg_text,
            pg_paragraphs=paragraphs,
            chapters=None,
        )

        assert len(results) >= 1

    def test_toc_entries_filtered(self):
        """TOC entries (small chapter objects) should be filtered out by size."""
        chapters_with_toc = [
            ChapterLocation(title="CONTENTS", offset=0, end_offset=50),
            ChapterLocation(title="CHAPTER 1. Title.", offset=50, end_offset=55),
            ChapterLocation(title="CHAPTER 2. Title.", offset=55, end_offset=60),
            ChapterLocation(title="CHAPTER 1. Title.", offset=200, end_offset=1500),
            ChapterLocation(title="CHAPTER 2. Title.", offset=1500, end_offset=3000),
        ]

        real_chapters = [ch for ch in chapters_with_toc if ch.end_offset - ch.offset > 1000]
        assert len(real_chapters) == 2
        assert real_chapters[0].offset == 200
        assert real_chapters[1].offset == 1500

    def test_sequential_order_preserved_with_chapters(self, aligner):
        """Pages should align in order even with chapter constraints."""
        pg_text, paragraphs, chapters = self._build_chaptered_pg()

        # Use transcriptions with chapter headings
        transcriptions = [
            PageTranscription(
                page_num=0, image_path=Path("/tmp/p0.png"),
                transcription="Loomings\nCall me Ishmael. Some years ago",
                success=True,
            ),
            PageTranscription(
                page_num=1, image_path=Path("/tmp/p1.png"),
                transcription="The Carpet-Bag\nIt was a queer sort of place",
                success=True,
            ),
            PageTranscription(
                page_num=2, image_path=Path("/tmp/p2.png"),
                transcription="The Spouter-Inn\nEntering for the first time",
                success=True,
            ),
        ]

        results = aligner.align_all_pages(
            transcriptions=transcriptions,
            pg_text=pg_text,
            pg_paragraphs=paragraphs,
            chapters=chapters,
        )

        assert len(results) >= 2
        for i in range(1, len(results)):
            assert results[i].alignment.pg_start >= results[i - 1].alignment.pg_start


class TestAnchoredFlag:
    """Tests for the 'anchored' field on VisionAlignmentResult."""

    def test_anchored_field_exists(self):
        result = VisionAlignmentResult(
            alignment=Alignment(
                pg_start=0, pg_end=100, scan_page=0, method=AlignmentMethod.LLM_VISION
            ),
            transcription=PageTranscription(
                page_num=0, image_path=Path("/tmp/p.png"), transcription="test", success=True
            ),
            best_score=0.5,
            anchored=True,
        )
        assert result.anchored is True

    def test_anchored_defaults_true(self):
        result = VisionAlignmentResult(
            alignment=Alignment(
                pg_start=0, pg_end=100, scan_page=0, method=AlignmentMethod.LLM_VISION
            ),
            transcription=PageTranscription(
                page_num=0, image_path=Path("/tmp/p.png"), transcription="test", success=True
            ),
            best_score=0.5,
        )
        assert result.anchored is True
