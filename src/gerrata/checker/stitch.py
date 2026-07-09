"""Stitch scan page transcriptions into continuous text for chapter-level diffing.

Instead of diffing each page in isolation (which creates artifacts at page
boundaries — split words, truncated sentences, orphaned fragments), we
concatenate all scan page transcriptions into a single continuous text
and diff it against PG text as one unit.

A PageMap tracks which page each character range came from, so errors
can be attributed back to source pages.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PageMap:
    """Maps character offsets in concatenated text back to source page numbers.
    
    Built as a list of (start_offset, end_offset, page_num) entries.
    Lookup is binary search for efficiency.
    """
    entries: list[tuple[int, int, int]] = field(default_factory=list)
    
    def add(self, start: int, end: int, page_num: int) -> None:
        """Add a character range mapping."""
        self.entries.append((start, end, page_num))
    
    def lookup(self, offset: int) -> int | None:
        """Find which page a character offset belongs to.
        
        Returns page number, or None if offset is out of range.
        """
        for start, end, page_num in self.entries:
            if start <= offset < end:
                return page_num
        return None
    
    def lookup_token(self, token_offset: int, token_lengths: list[int]) -> int | None:
        """Find which page a token offset belongs to.
        
        Args:
            token_offset: Index into the token list.
            token_lengths: List of character lengths for each token
                (including separator spaces).
        
        Returns page number.
        """
        char_offset = sum(token_lengths[:token_offset]) if token_offset > 0 else 0
        return self.lookup(char_offset)


def stitch_scan_pages(
    scan_pages: list,
    normalizer=None,
) -> tuple[str, PageMap]:
    """Concatenate scan page transcriptions into continuous text.
    
    Joins page texts with a single space separator. Strips leading/trailing
    whitespace from each page. Skips empty pages.
    
    If a normalizer function is provided, it is applied to each page's text
    before concatenation. This ensures the page map offsets align with the
    normalized text that tokens are derived from. This is critical for
    correct page attribution — if the text is normalized after stitching,
    character offsets shift and the page map breaks.
    
    Args:
        scan_pages: List of ScanPage objects (or dicts) with page_num and
            vision_text/ocr_text fields.
        normalizer: Optional function str -> str to normalize each page
            (e.g., normalize_for_diff from text_diff module).
    
    Returns:
        Tuple of (concatenated_text, page_map).
    """
    parts: list[str] = []
    page_map = PageMap()
    current_offset = 0
    
    for sp in scan_pages:
        # Get page number
        if hasattr(sp, 'page_num'):
            page_num = sp.page_num
            text = (getattr(sp, 'vision_text', '') or '') or (getattr(sp, 'ocr_text', '') or '')
        elif isinstance(sp, dict):
            page_num = sp.get('page_num')
            text = sp.get('vision_text', '') or sp.get('ocr_text', '') or ''
        else:
            continue
        
        text = text.strip()
        if not text:
            continue
        
        if normalizer:
            text = normalizer(text)
        
        start = current_offset
        parts.append(text)
        current_offset += len(text)
        
        page_map.add(start, current_offset, page_num)
        current_offset += 1  # for the space separator
    
    concatenated = ' '.join(parts)
    return concatenated, page_map


def build_token_page_map(tokens: list[str], concatenated_text: str, page_map: PageMap) -> list[int]:
    """Build a mapping from token index to page number.
    
    For each token in the list, find its position in the concatenated text
    and look up the page number.
    
    Args:
        tokens: List of tokens (words).
        concatenated_text: The concatenated text (should be the SAME text
            that tokens were derived from — use the normalizer parameter
            in stitch_scan_pages for correct alignment).
        page_map: The PageMap for the concatenated text.
    
    Returns:
        List where index i gives the page number for token i.
    """
    token_pages = []
    search_start = 0
    
    for token in tokens:
        pos = concatenated_text.find(token, search_start)
        if pos == -1:
            # Token not found — inherit previous token's page
            if token_pages:
                token_pages.append(token_pages[-1])
            else:
                token_pages.append(-1)
        else:
            page = page_map.lookup(pos)
            token_pages.append(page if page is not None else -1)
            search_start = pos + len(token)
    
    return token_pages
