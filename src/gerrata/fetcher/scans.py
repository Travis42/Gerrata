"""Download and manage source page scans from Internet Archive.

Handles:
- Downloading IA OCR text
- Downloading JP2 zip archives and extracting page images
- Converting JP2 to PNG for LLM vision processing
- Splitting OCR text into per-page segments
"""

from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ScanPage:
    """A single page from a scanned book."""

    page_num: int  # 0-indexed
    ocr_text: str = ""  # OCR text for this page
    vision_text: str = ""  # Vision model transcription of this page
    image_path: Path | None = None  # Path to downloaded/converted image


@dataclass
class ScanData:
    """Complete scan data for a book."""

    identifier: str  # IA identifier
    ocr_text: str = ""  # Full OCR text
    pages: list[ScanPage] = field(default_factory=list)
    total_pages: int = 0
    page_image_url_pattern: str = ""  # URL pattern with NNNN placeholder
    source_url: str = ""


class ScanFetcher:
    """Fetch page scans from Internet Archive."""

    DOWNLOAD_BASE = "https://archive.org/download/{identifier}/"

    def __init__(self, cache_dir: Path | str | None = None):
        if cache_dir:
            self.cache_dir = Path(cache_dir)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.cache_dir = None

    async def fetch_ocr_text(self, identifier: str) -> str:
        """Download OCR text from Internet Archive.

        Tries multiple OCR file patterns (DjVu XML, DjVu text, ABBYY XML).
        """
        import httpx

        # Common OCR text file patterns on IA
        ocr_patterns = [
            f"{identifier}_djvu.txt",  # DjVu OCR text (most common)
            f"{identifier}_djvu.xml",  # DjVu XML
            f"{identifier}_abbyy.xml",  # ABBYY XML
            f"{identifier}_abbyy.txt",  # ABBYY text
        ]

        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
            for pattern in ocr_patterns:
                url = f"{self.DOWNLOAD_BASE.format(identifier=identifier)}{pattern}"
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        content = resp.text
                        if len(content) > 100:  # Sanity check
                            logger.info(f"Downloaded OCR text: {url}")
                            return content
                except httpx.HTTPError as e:
                    logger.debug(f"Failed to fetch {url}: {e}")

        raise FileNotFoundError(f"No OCR text found for identifier '{identifier}'")

    async def download_page_image(
        self,
        identifier: str,
        page_num: int,
        jp2_pattern: str = "",
        dest: Path | None = None,
    ) -> Path | None:
        """Download a single page image as JP2, convert to PNG.

        Args:
            identifier: IA identifier.
            page_num: 0-indexed page number.
            jp2_pattern: URL pattern for JP2 files. Uses NNNN as page number placeholder.
            dest: Destination directory. Defaults to cache_dir.

        Returns:
            Path to the PNG file, or None if download fails.
        """
        import httpx

        if jp2_pattern:
            url = jp2_pattern.replace("NNNN", f"{page_num:04d}")
        else:
            # Default IA JP2 URL pattern
            url = (
                f"{self.DOWNLOAD_BASE.format(identifier=identifier)}"
                f"{identifier}_jp2/{identifier}_{page_num:04d}.jp2"
            )

        dest = dest or self.cache_dir
        if dest and not dest.exists():
            dest.mkdir(parents=True, exist_ok=True)

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"Failed to download page {page_num}: HTTP {resp.status_code}")
                    return None

                # Save JP2
                jp2_path = dest / f"page_{page_num:04d}.jp2" if dest else Path(f"page_{page_num:04d}.jp2")
                jp2_path.write_bytes(resp.content)

                # Convert to PNG
                png_path = self._jp2_to_png(jp2_path)
                return png_path

        except httpx.HTTPError as e:
            logger.warning(f"Failed to download page {page_num}: {e}")
            return None

    def _jp2_to_png(self, jp2_path: Path) -> Path:
        """Convert a JP2 image to PNG using Pillow."""
        from PIL import Image

        png_path = jp2_path.with_suffix(".png")
        img = Image.open(jp2_path)
        img.save(png_path, "PNG")
        logger.info(f"Converted {jp2_path.name} to {png_path.name}")
        return png_path

    async def download_jp2_zip(
        self,
        identifier: str,
        dest: Path | None = None,
    ) -> Path:
        """Download the JP2 zip archive for a scan from Internet Archive.

        The zip contains all JP2 page images for the scan.
        Individual JP2 URLs often 404 — the zip is the reliable source.

        Args:
            identifier: IA identifier (e.g. "06-stevenson-jekyll-hyde").
            dest: Directory to save the zip. Defaults to cache_dir.

        Returns:
            Path to the downloaded zip file.
        """
        import httpx

        dest = dest or self.cache_dir
        if dest and not dest.exists():
            dest.mkdir(parents=True, exist_ok=True)

        zip_url = f"https://archive.org/download/{identifier}/{identifier}_jp2.zip"
        zip_path = (dest / f"{identifier}_jp2.zip") if dest else Path(f"{identifier}_jp2.zip")

        if zip_path.exists():
            logger.info(f"JP2 zip already cached: {zip_path}")
            return zip_path

        logger.info(f"Downloading JP2 zip: {zip_url}")
        async with httpx.AsyncClient(follow_redirects=True, timeout=300) as client:
            async with client.stream("GET", zip_url) as resp:
                if resp.status_code != 200:
                    raise FileNotFoundError(
                        f"JP2 zip not found: HTTP {resp.status_code} from {zip_url}"
                    )
                with open(zip_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)

        logger.info(f"Downloaded JP2 zip: {zip_path} ({zip_path.stat().st_size:,} bytes)")
        return zip_path

    def extract_jp2_zip(
        self,
        zip_path: Path,
        dest: Path | None = None,
        page_range: tuple[int, int] | None = None,
    ) -> list[Path]:
        """Extract JP2 files from a zip and convert to PNG.

        Args:
            zip_path: Path to the JP2 zip file.
            dest: Directory for extracted/converted files. Defaults to cache_dir.
            page_range: Optional (start, end) page numbers to extract (inclusive).

        Returns:
            Sorted list of paths to extracted PNG files.
        """
        from PIL import Image

        dest = dest or self.cache_dir or zip_path.parent
        if not dest.exists():
            dest.mkdir(parents=True, exist_ok=True)

        png_files: list[Path] = []

        with zipfile.ZipFile(zip_path, "r") as zf:
            jp2_names = sorted(
                n for n in zf.namelist() if n.endswith(".jp2")
            )

            for jp2_name in jp2_names:
                # Extract page number from filename
                # Pattern: {base}_NNNN.jp2
                match = re.search(r"(\d{4})\.jp2$", jp2_name)
                if not match:
                    continue
                page_num = int(match.group(1))

                if page_range is not None:
                    if page_num < page_range[0] or page_num > page_range[1]:
                        continue

                # Check if PNG already exists
                png_name = Path(jp2_name).stem + ".png"
                png_path = dest / png_name
                if png_path.exists():
                    png_files.append(png_path)
                    continue

                # Extract JP2 from zip to temp, then convert
                jp2_data = zf.read(jp2_name)
                jp2_temp = dest / f"_temp_{page_num}.jp2"
                jp2_temp.write_bytes(jp2_data)

                try:
                    img = Image.open(jp2_temp)
                    img.save(png_path, "PNG")
                    png_files.append(png_path)
                    logger.debug(f"Extracted page {page_num}: {png_path.name}")
                finally:
                    jp2_temp.unlink(missing_ok=True)

        png_files.sort(key=lambda p: p.name)
        logger.info(f"Extracted {len(png_files)} pages from JP2 zip")
        return png_files

    def get_cached_pages(
        self,
        pages_dir: Path,
    ) -> list[Path]:
        """Get sorted list of PNG page images from a directory.

        Args:
            pages_dir: Directory containing PNG files.

        Returns:
            Sorted list of PNG paths.
        """
        if not pages_dir.exists():
            return []
        pngs = sorted(pages_dir.glob("*.png"))
        return pngs

    def load_local_ocr_text(self, path: Path | str) -> str:
        """Load OCR text from a local file."""
        path = Path(path)
        return path.read_text(encoding="utf-8", errors="replace")

    def split_ocr_into_pages(self, ocr_text: str, known_pages: int = 0) -> list[ScanPage]:
        """Split full OCR text into per-page segments.

        Internet Archive OCR text may contain page number markers
        like 'Page 5' or 'Page 88' on their own lines. If found, we
        split on these. Otherwise, if a known page count is given,
        we split evenly. If neither, return the entire text as one page.
        """
        # Try to find page markers
        page_marker_pattern = re.compile(r"^Page\s+(\d+)\s*$", re.MULTILINE)
        matches = list(page_marker_pattern.finditer(ocr_text))

        pages: list[ScanPage] = []

        if len(matches) >= 3:
            # Enough page markers to split on
            for i, match in enumerate(matches):
                page_num = int(match.group(1)) - 1  # Convert to 0-indexed
                start = match.end()

                # Find end (start of next page marker)
                if i + 1 < len(matches):
                    end = matches[i + 1].start()
                else:
                    end = len(ocr_text)

                page_text = ocr_text[start:end].strip()
                if page_text:
                    pages.append(ScanPage(page_num=page_num, ocr_text=page_text))
        elif known_pages > 0:
            # Split evenly into known_pages chunks
            total_len = len(ocr_text)
            chunk_size = total_len // known_pages
            for i in range(known_pages):
                start = i * chunk_size
                end = (i + 1) * chunk_size if i < known_pages - 1 else total_len
                page_text = ocr_text[start:end].strip()
                if page_text:
                    pages.append(ScanPage(page_num=i, ocr_text=page_text))
        else:
            # No page markers, no known count — treat entire text as one page
            pages.append(ScanPage(page_num=0, ocr_text=ocr_text.strip()))

        return pages

    async def prepare_scan(
        self,
        identifier: str,
        ocr_text: str | None = None,
        ocr_file: Path | str | None = None,
        jp2_pattern: str = "",
        known_pages: int = 0,
    ) -> ScanData:
        """Prepare complete scan data.

        Args:
            identifier: IA identifier.
            ocr_text: Pre-loaded OCR text (skip download).
            ocr_file: Local OCR text file to load.
            jp2_pattern: URL pattern for JP2 page images.
            known_pages: Known total page count (for splitting without markers).

        Returns:
            ScanData with pages split from OCR text.
        """
        if ocr_file:
            ocr_text = self.load_local_ocr_text(ocr_file)
        elif ocr_text is None:
            ocr_text = await self.fetch_ocr_text(identifier)

        pages = self.split_ocr_into_pages(ocr_text, known_pages=known_pages)

        return ScanData(
            identifier=identifier,
            ocr_text=ocr_text,
            pages=pages,
            total_pages=len(pages),
            page_image_url_pattern=jp2_pattern,
            source_url=f"https://archive.org/details/{identifier}",
        )
