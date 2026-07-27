"""Cross-edition aligner: align two sets of transcriptions against each other.

Uses the existing GlobalAnchorAligner to map regions of Edition A text
to corresponding regions in Edition B text.

Approach: Edition A's full transcribed text is used as the "base text"
(i.e. PG text equivalent), and Edition B's page transcriptions are used
as the "pages." The global anchor aligner handles this directly.
"""

from __future__ import annotations

import logging
from typing import Optional

from book_projects.gerrata.src.gerrata.aligner.global_anchor import GlobalAnchorAligner
from book_projects.gerrata.src.gerrata.aligner.vision_aligner import PageTranscription
from book_projects.gerrata.src.gerrata.models import Alignment, AlignmentMethod, EditionAlignment

logger = logging.getLogger(__name__)


def _build_synthetic_text(transcriptions: list[PageTranscription]) -> str:
    """Build a full text from a list of page transcriptions.

    Joins transcriptions with newlines, using the cleaned text when available.
    """
    parts = []
    for t in transcriptions:
        if not t.success or not t.transcription:
            continue
        text = t.transcription_cleaned or t.transcription
        parts.append(text)
    return "\n".join(parts)


def _split_text_to_pages(
    text: str, chunk_chars: int = 2000
) -> list[PageTranscription]:
    """Split a plain text into synthetic page transcriptions.

    Splits at paragraph boundaries (~double newlines) trying to keep
    chunks around chunk_chars. These aren't real pages, but the aligner
    works on any chunked text.
    """
    paragraphs = text.split("\n\n")
    pages: list[PageTranscription] = []
    current_parts: list[str] = []
    current_len = 0
    page_num = 0

    for para in paragraphs:
        para_len = len(para)
        if current_len + para_len > chunk_chars and current_parts:
            page_text = "\n\n".join(current_parts)
            pages.append(PageTranscription(
                page_num=page_num,
                image_path=None,
                transcription=page_text,
                transcription_cleaned=page_text,
                success=True,
            ))
            page_num += 1
            current_parts = [para]
            current_len = para_len
        else:
            current_parts.append(para)
            current_len += para_len

    if current_parts:
        page_text = "\n\n".join(current_parts)
        pages.append(PageTranscription(
            page_num=page_num,
            image_path=None,
            transcription=page_text,
            transcription_cleaned=page_text,
            success=True,
        ))

    return pages


def align_editions(
    transcriptions_a: list[PageTranscription],
    transcriptions_b: list[PageTranscription],
    text_a: str | None = None,
    text_b: str | None = None,
    min_phrase_words: int = 8,
    min_score: float = 0.35,
) -> list[EditionAlignment]:
    """Align two editions' transcriptions against each other.

    Uses Edition A's full text as the base and Edition B's page
    transcriptions as "pages" for the global anchor aligner.

    Args:
        transcriptions_a: Page transcriptions for Edition A.
        transcriptions_b: Page transcriptions for Edition B.
        text_a: Pre-built full text for Edition A (if available).
        text_b: Pre-built full text for Edition B (if available).
        min_phrase_words: Minimum words for a distinctive phrase.
        min_score: Minimum alignment score to accept.

    Returns:
        List of EditionAlignment objects mapping A regions to B regions.
    """
    # Build full text for Edition A (the "base")
    base_text = text_a or _build_synthetic_text(transcriptions_a)
    if not base_text.strip():
        logger.warning("Edition A text is empty — nothing to align against")
        return []

    # Use Edition B transcriptions as "pages" to align to A's base text.
    # If we have plain text B instead of page transcriptions, split into
    # synthetic pages.
    b_pages = transcriptions_b
    if not b_pages and text_b:
        b_pages = _split_text_to_pages(text_b)

    if not b_pages:
        logger.warning("No Edition B transcriptions available")
        return []

    # Use GlobalAnchorAligner to find where each B page lands in A's text
    aligner = GlobalAnchorAligner(
        min_phrase_words=min_phrase_words,
        max_phrases_per_page=5,
        min_score=min_score,
    )

    # Build a simple paragraph list from base_text for the aligner
    paragraphs_a = [p.strip() for p in base_text.split("\n\n") if p.strip()]

    alignments_b_in_a = aligner.align_all_pages(
        transcriptions=b_pages,
        pg_text=base_text,
        pg_paragraphs=paragraphs_a,
        chapters=None,
    )

    # Convert Alignment objects to EditionAlignment objects
    # Build a mapping from B page number to A text position
    result: list[EditionAlignment] = []

    for i, align in enumerate(alignments_b_in_a):
        # Find the A page(s) that correspond to this A text region
        a_pages = _find_pages_in_range(
            transcriptions_a, align.pg_start, align.pg_end
        )
        b_page = align.scan_page

        result.append(EditionAlignment(
            edition_a_start=align.pg_start,
            edition_a_end=align.pg_end,
            edition_b_start=0,  # We'll refine this below
            edition_b_end=0,
            edition_a_pages=a_pages,
            edition_b_pages=[b_page],
            confidence=align.confidence,
        ))

    # Now also align A pages into B text for bidirectional offsets
    if text_b or transcriptions_b:
        b_full_text = text_b or _build_synthetic_text(transcriptions_b)
        if b_full_text.strip():
            a_as_pages = transcriptions_a
            if not a_as_pages and text_a:
                a_as_pages = _split_text_to_pages(text_a)

            if a_as_pages:
                b_paragraphs = [p.strip() for p in b_full_text.split("\n\n") if p.strip()]
                alignments_a_in_b = aligner.align_all_pages(
                    transcriptions=a_as_pages,
                    pg_text=b_full_text,
                    pg_paragraphs=b_paragraphs,
                    chapters=None,
                )
                # Build a lookup: A text region → B text region
                a_to_b_map = {}
                for align in alignments_a_in_b:
                    a_to_b_map[(align.pg_start, align.pg_end)] = (align.pg_start, align.pg_end)

                # Refine EditionAlignment B offsets using the B pages alignment
                # For each EditionAlignment, find the corresponding B alignment
                for i, ed_align in enumerate(result):
                    # Find the B page transcription to get its approximate offset in B text
                    b_page_num = ed_align.edition_b_pages[0] if ed_align.edition_b_pages else -1
                    for t in b_pages:
                        if t.page_num == b_page_num and t.success:
                            # Estimate B offset from cumulative page lengths
                            offset = _estimate_offset(b_pages, b_page_num)
                            b_text_len = len(t.transcription_cleaned or t.transcription)
                            ed_align.edition_b_start = offset
                            ed_align.edition_b_end = offset + b_text_len
                            break

    return sorted(result, key=lambda a: a.edition_a_start)


def _find_pages_in_range(
    transcriptions: list[PageTranscription],
    start: int,
    end: int,
) -> list[int]:
    """Find which page numbers overlap with a text character range."""
    pages = []
    offset = 0
    for t in transcriptions:
        if not t.success:
            continue
        text = t.transcription_cleaned or t.transcription or ""
        length = len(text)
        # Check if this page's range [offset, offset+length] overlaps with [start, end]
        if offset < end and offset + length > start:
            pages.append(t.page_num)
        offset += length + 1  # +1 for the joining newline
    return pages


def _estimate_offset(
    pages: list[PageTranscription],
    target_page: int,
) -> int:
    """Estimate the character offset of a page in the concatenated text."""
    offset = 0
    for t in pages:
        if t.page_num == target_page:
            return offset
        if t.success and t.transcription:
            offset += len(t.transcription_cleaned or t.transcription) + 1
    return offset
