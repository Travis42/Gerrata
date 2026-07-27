"""Download and parse Project Gutenberg texts.

Handles HTML and plain text formats, extracts metadata and clean body text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from gerrata.models import PGMetadata

logger = logging.getLogger(__name__)

# Patterns for PG header/footer markers
START_MARKER = re.compile(r"\*\*\* START OF (?:THE )?PROJECT GUTENBERG", re.IGNORECASE)
END_MARKER = re.compile(r"\*\*\* END OF (?:THE )?PROJECT GUTENBERG", re.IGNORECASE)

# Metadata field patterns (from plain-text PG headers)
META_PATTERNS = {
    "title": re.compile(r"^Title:\s*(.+)", re.MULTILINE),
    "author": re.compile(r"^Author:\s*(.+)", re.MULTILINE),
    "language": re.compile(r"^Language:\s*(.+)", re.MULTILINE),
    "release_date": re.compile(r"^Release Date:\s*(.+?)(?:\s*\[|$)", re.MULTILINE),
    "producer": re.compile(r"^Produced by:\s*(.+)", re.MULTILINE),
    "encoding": re.compile(r"^Character set encoding:\s*(.+)", re.MULTILINE),
}


@dataclass
class PGParsedText:
    """Result of parsing a PG text file."""

    metadata: PGMetadata
    body_text: str  # Clean body text (between START/END markers)
    full_text: str  # Full file text (for reference)
    chapters: list[ChapterLocation] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)


@dataclass
class ChapterLocation:
    """Location of a chapter heading in the text."""

    title: str
    offset: int  # character offset in body_text
    end_offset: int = 0  # start of next chapter or end of text


class PGFetcher:
    """Download and parse Project Gutenberg texts."""

    BASE_URL = "https://www.gutenberg.org/files/{pg_id}/"

    def __init__(self, cache_dir: Path | str | None = None):
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = None

    async def download(self, pg_id: int, dest: Path | str | None = None) -> Path:
        """Download a PG text, preferring plain text UTF-8 format.

        Plain text is preferred over HTML because:
        - No CSS/style block noise that confuses paragraph detection
        - No HTML entities to unescape
        - Smaller files (faster downloads, less memory)
        - Same prose content, just cleaner representation

        Args:
            pg_id: Project Gutenberg ebook ID.
            dest: Optional destination directory. Defaults to cache_dir.

        Returns:
            Path to the downloaded file.
        """
        import httpx

        dest = Path(dest) if dest else self.cache_dir
        if dest and not dest.exists():
            dest.mkdir(parents=True, exist_ok=True)

        # Prefer plain text (no CSS noise), fall back to HTML
        # PG file structure:
        #   Plain text UTF-8: /files/{pg_id}/{pg_id}-0.txt
        #   Plain text ISO:   /files/{pg_id}/{pg_id}.txt or {pg_id}-8.txt
        #   HTML:             /files/{pg_id}/{pg_id}-h/{pg_id}-h.htm
        for fmt, filename, ext in [
            ("txt-utf8", f"{pg_id}-0", ".txt"),
            ("txt", f"{pg_id}", ".txt"),
            ("txt-iso", f"{pg_id}-8", ".txt"),
            ("html", f"{pg_id}-h/{pg_id}-h", ".htm"),
        ]:
            try:
                url = f"{self.BASE_URL.format(pg_id=pg_id)}{filename}{ext}"
                logger.info(f"Trying {fmt}: {url}")
                async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        content = resp.text
                        # Verify it's not a 404 page
                        if "Not Found" not in content[:500]:
                            filename = f"{pg_id}{ext}"
                            filepath = dest / filename if dest else Path(filename)
                            filepath.write_text(content, encoding="utf-8", errors="replace")
                            logger.info(f"Downloaded {filepath}")
                            return filepath
            except httpx.HTTPError as e:
                logger.debug(f"Failed to fetch {url}: {e}")
                continue

        raise FileNotFoundError(f"Could not download PG #{pg_id} from any format URL")

    def parse_file(self, filepath: Path | str) -> PGParsedText:
        """Parse a local PG text file.

        Handles both HTML (.htm, .html) and plain text formats.
        """
        filepath = Path(filepath)
        content = filepath.read_text(encoding="utf-8", errors="replace")

        if filepath.suffix in (".htm", ".html"):
            return self._parse_html(content, filepath)
        else:
            return self._parse_plaintext(content, filepath)

    def _parse_html(self, content: str, filepath: Path) -> PGParsedText:
        """Parse PG HTML format."""
        soup = BeautifulSoup(content, "lxml")

        # Extract PG ID from filename or content
        pg_id = self._extract_pg_id(filepath, content)

        # Extract metadata from HTML
        metadata = self._extract_html_metadata(soup, pg_id, content)

        # Find body text between START and END markers
        body_text = self._extract_body_from_html(soup, content)

        # Extract chapters
        chapters = self._extract_chapters(body_text)

        # Extract paragraphs
        paragraphs = self._extract_paragraphs(body_text)

        return PGParsedText(
            metadata=metadata,
            body_text=body_text,
            full_text=content,
            chapters=chapters,
            paragraphs=paragraphs,
        )

    def _parse_plaintext(self, content: str, filepath: Path) -> PGParsedText:
        """Parse PG plain text format."""
        pg_id = self._extract_pg_id(filepath, content)
        metadata = self._extract_plaintext_metadata(content, pg_id)

        # Extract body between START and END markers
        body_text = self._extract_body_text(content)

        # Extract chapters
        chapters = self._extract_chapters(body_text)

        # Extract paragraphs
        paragraphs = self._extract_paragraphs(body_text)

        return PGParsedText(
            metadata=metadata,
            body_text=body_text,
            full_text=content,
            chapters=chapters,
            paragraphs=paragraphs,
        )

    def _extract_pg_id(self, filepath: Path, content: str) -> int:
        """Extract PG ID from filename or content."""
        # Try filename first
        match = re.search(r"(\d+)", filepath.stem)
        if match:
            return int(match.group(1))

        # Try content
        match = re.search(r"EBook #?(\d+)", content)
        if match:
            return int(match.group(1))

        # Try from START marker
        match = re.search(r"GUTENBERG EBOOK\s+(\d+)", content, re.IGNORECASE)
        if match:
            return int(match.group(1))

        return 0

    def _extract_html_metadata(self, soup: BeautifulSoup, pg_id: int, content: str) -> PGMetadata:
        """Extract metadata from PG HTML."""
        title = ""
        author = ""

        # Try h1 for title
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

        # Try h2 with "by" for author
        for h2 in soup.find_all("h2"):
            text = h2.get_text(strip=True).lower()
            if text.startswith("by "):
                author = h2.get_text(strip=True).removeprefix("by ").strip()
                break

        # Try to find more metadata from the text content
        # Some PG HTMLs have metadata in the body
        body_divs = soup.find_all(string=START_MARKER)
        if body_divs:
            # Look in text before the start marker for metadata
            for sibling in body_divs[0].find_next_siblings():
                if hasattr(sibling, "get_text"):
                    text = sibling.get_text()
                    for key, pattern in META_PATTERNS.items():
                        if key == "title" and not title:
                            m = pattern.search(text)
                            if m:
                                title = m.group(1).strip()
                        elif key == "author" and not author:
                            m = pattern.search(text)
                            if m:
                                author = m.group(1).strip()

        # Also check title tag
        if not title:
            title_tag = soup.find("title")
            if title_tag:
                t = title_tag.get_text(strip=True)
                # Remove " | Project Gutenberg" suffix
                title = re.sub(r"\s*\|\s*Project Gutenberg\s*$", "", t)

        return PGMetadata(
            pg_id=pg_id,
            title=title,
            author=author,
        )

    def _extract_plaintext_metadata(self, content: str, pg_id: int) -> PGMetadata:
        """Extract metadata from PG plain text header."""
        # The header is everything before START marker
        start_match = START_MARKER.search(content)
        header_text = content[:start_match.start()] if start_match else content[:2000]

        metadata = PGMetadata(pg_id=pg_id)

        for key, pattern in META_PATTERNS.items():
            m = pattern.search(header_text)
            if m:
                value = m.group(1).strip()
                if key == "title":
                    metadata.title = value
                elif key == "author":
                    metadata.author = value
                elif key == "language":
                    metadata.language = value
                elif key == "release_date":
                    metadata.release_date = value
                elif key == "producer":
                    metadata.producer = value
                elif key == "encoding":
                    metadata.encoding = value

        # If no structured metadata found (common in modern PG plain texts),
        # extract title and author from the first lines after START marker
        if not metadata.title and start_match:
            body_start = start_match.end()
            # Skip past the marker line
            nl = content.find("\n", body_start)
            if nl != -1:
                body_start = nl + 1
            # Read first few non-blank lines as title/author
            lines = []
            for line in content[body_start:body_start + 2000].splitlines():
                stripped = line.strip()
                if stripped:
                    lines.append(stripped)
                if len(lines) >= 10:
                    break

            if lines:
                # Title is usually the first substantial line
                metadata.title = lines[0] if lines else ""
                # Author is on a "By ..." line
                for line in lines[:6]:
                    if line.lower().startswith("by "):
                        metadata.author = line[3:].strip()
                        break

        # Look for source edition info
        source_match = re.search(
            r"(?:Source|Edition|This.*edition|Published by)\s*:\s*(.+?)(?:\n\n|\n[A-Z])",
            header_text, re.IGNORECASE | re.DOTALL
        )
        if source_match:
            metadata.source_edition = source_match.group(1).strip()

        return metadata

    def _extract_body_from_html(self, soup: BeautifulSoup, content: str) -> str:
        """Extract clean body text from HTML between START/END markers."""
        # Use the raw content to find START/END markers
        start_match = START_MARKER.search(content)
        end_match = END_MARKER.search(content)

        if not start_match:
            logger.warning("No START marker found in HTML")
            return self._html_to_text(soup.body) if soup.body else ""

        # Skip past the full marker line
        start = start_match.end()
        nl_pos = content.find("\n", start)
        if nl_pos != -1:
            start = nl_pos + 1

        # Extract everything between markers
        if end_match:
            body_html = content[start:end_match.start()]
        else:
            body_html = content[start:]

        # Parse the body HTML segment and extract text
        body_soup = BeautifulSoup(body_html, "lxml")

        # Remove images and figures
        for tag in body_soup.find_all(["img", "br"]):
            tag.decompose()

        # Convert block-level tags to paragraph separators
        for tag in body_soup.find_all(["p", "h1", "h2", "h3", "h4", "div", "hr"]):
            tag.insert_before("\n\n")

        text = body_soup.get_text()

        # Clean up whitespace
        lines = text.splitlines()
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped:
                cleaned_lines.append(stripped)

        return "\n\n".join(cleaned_lines)

    def _html_to_text(self, element: Tag | BeautifulSoup | None) -> str:
        """Convert HTML element to plain text."""
        if element is None:
            return ""
        text = element.get_text(separator="\n")
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return "\n".join(lines)

    def _extract_body_text(self, content: str) -> str:
        """Extract body text between START/END markers from plain text."""
        start_match = START_MARKER.search(content)
        end_match = END_MARKER.search(content)

        if not start_match:
            logger.warning("No START marker found in plain text")
            return content

        # Skip past the full marker line
        start = start_match.end()
        # Skip any remaining text on the start marker line (e.g., "***" closing)
        nl_pos = content.find("\n", start)
        if nl_pos != -1:
            start = nl_pos + 1

        end = end_match.start() if end_match else len(content)
        body = content[start:end]

        # Normalize: blank lines become paragraph separators,
        # wrapped lines (single newline between non-blank lines) are joined
        lines = body.splitlines()
        result_parts: list[str] = []
        current_paragraph: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped == "":
                if current_paragraph:
                    result_parts.append(" ".join(current_paragraph))
                    current_paragraph = []
            else:
                current_paragraph.append(stripped)
        if current_paragraph:
            result_parts.append(" ".join(current_paragraph))

        return "\n\n".join(result_parts)

    def _extract_chapters(self, body_text: str) -> list[ChapterLocation]:
        """Extract chapter headings and their positions."""
        chapters = []
        # Common chapter patterns
        chapter_patterns = [
            re.compile(
                r"^(?:CHAPTER|Chapter)\s+(?:[IVXLCDM]+|\d+)\.?\s*:?\s*(.*)",
                re.MULTILINE,
            ),
            re.compile(
                r"^([A-Z][A-Z\s&'.]+)$",
                re.MULTILINE,  # All-caps headings (like PG #43)
            ),
        ]

        found: list[tuple[int, str]] = []

        for pattern in chapter_patterns:
            for match in pattern.finditer(body_text):
                offset = match.start()
                title = match.group(1).strip() if match.lastindex and match.group(1) else match.group(0).strip()
                if not title:
                    continue
                # Skip common non-chapter all-caps lines
                if pattern == chapter_patterns[1]:
                    # Clean title - remove trailing periods
                    clean_title = title.rstrip(".")
                    if len(clean_title) < 5:
                        continue
                    # Skip lines that look like page markers or trivial text
                    skip_words = {"page", "end", "the", "of", "and", "a", "to", "in", "for"}
                    words = clean_title.lower().split()
                    if all(w in skip_words for w in words):
                        continue
                found.append((offset, title))

        # Sort and deduplicate by position (allow chapters from different patterns
        # at nearby offsets — only skip exact duplicates)
        found.sort(key=lambda x: x[0])
        seen_offsets: set[int] = set()
        for offset, title in found:
            # Skip exact offset duplicates
            if offset in seen_offsets:
                continue
            seen_offsets.add(offset)
            chapters.append(ChapterLocation(title=title, offset=offset))

        # Set end offsets
        for i, ch in enumerate(chapters):
            if i + 1 < len(chapters):
                ch.end_offset = chapters[i + 1].offset
            else:
                ch.end_offset = len(body_text)

        return chapters

    def _extract_paragraphs(self, body_text: str) -> list[str]:
        """Split body text into paragraphs."""
        # Split on blank lines
        raw_paragraphs = re.split(r"\n{2,}", body_text)
        paragraphs = [p.strip() for p in raw_paragraphs if p.strip()]
        return paragraphs
