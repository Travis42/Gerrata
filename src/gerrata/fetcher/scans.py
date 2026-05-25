"""Download and manage source page scans from Internet Archive.

Handles:
- Downloading JP2 zip archives and extracting page images
- Converting JP2 to PNG for LLM vision processing

Text is produced by LLM vision transcription, not legacy OCR.
"""

from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass

# Browser-like user agent to avoid IA anti-bot blocking
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ScanPage:
    """A single page from a scanned book."""

    page_num: int  # 0-indexed
    ocr_text: str = ""  # OCR text for this page
    vision_text: str = ""  # Vision model transcription of this page
    image_path: Path | None = None  # Path to downloaded/converted image


class ScanFetcher:
    """Fetch page scans from Internet Archive."""

    DOWNLOAD_BASE = "https://archive.org/download/{identifier}/"
    METADATA_URL = "https://archive.org/metadata/{identifier}"

    def __init__(self, cache_dir: Path | str | None = None):
        if cache_dir:
            self.cache_dir = Path(cache_dir)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.cache_dir = None

    async def _get_direct_server(self, identifier: str) -> str | None:
        """Resolve IA identifier to direct server URL via metadata API.

        The /download/ endpoint intermittently returns 503/500 errors.
        The metadata API (https://archive.org/metadata/{id}) returns the
        actual storage server hostname and directory path, allowing direct
        downloads that bypass the congested /download/ endpoint.
        """
        import httpx

        try:
            url = self.METADATA_URL.format(identifier=identifier)
            async with httpx.AsyncClient(follow_redirects=True, timeout=15, headers={"user-agent": _BROWSER_UA}) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    server = data.get("server")
                    dir_path = data.get("dir")
                    if server and dir_path:
                        return f"https://{server}{dir_path}"
                    elif server:
                        # Some items omit dir, construct it
                        return f"https://{server}/{identifier}"
        except Exception as e:
            logger.debug(f"Metadata lookup failed for {identifier}: {e}")
        return None

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
            async with httpx.AsyncClient(follow_redirects=True, timeout=120, headers={"user-agent": _BROWSER_UA}) as client:
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
        """Convert a JP2 image to PNG using OpenCV (Pillow lacks JP2 support)."""
        import cv2

        png_path = jp2_path.with_suffix(".png")
        img = cv2.imread(str(jp2_path), cv2.IMREAD_ANYCOLOR)
        if img is None:
            raise ValueError(f"OpenCV could not read {jp2_path}")
        cv2.imwrite(str(png_path), img)
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

        Features:
        - Retry with exponential backoff on 503/429/500 errors
        - Resume partial downloads using HTTP Range header
        - Fallback to direct server via metadata API

        Args:
            identifier: IA identifier (e.g. "06-stevenson-jekyll-hyde").
            dest: Directory to save the zip. Defaults to cache_dir.

        Returns:
            Path to the downloaded zip file.
        """
        import asyncio
        import httpx

        dest = dest or self.cache_dir
        if dest and not dest.exists():
            dest.mkdir(parents=True, exist_ok=True)

        zip_url = f"https://archive.org/download/{identifier}/{identifier}_jp2.zip"
        zip_path = (dest / f"{identifier}_jp2.zip") if dest else Path(f"{identifier}_jp2.zip")

        if zip_path.exists():
            # Check if the file looks complete (non-zero size)
            if zip_path.stat().st_size > 0:
                logger.info(f"JP2 zip already cached: {zip_path}")
                return zip_path
            else:
                logger.info(f"JP2 zip exists but is empty, re-downloading: {zip_path}")
                zip_path.unlink()

        max_retries = 8
        base_delay = 30  # seconds
        partial_path = zip_path.with_suffix(".zip.partial")

        for attempt in range(max_retries + 1):
            existing_size = 0
            if partial_path.exists():
                existing_size = partial_path.stat().st_size
                if existing_size > 0:
                    logger.info(f"Resuming partial download from {existing_size:,} bytes (attempt {attempt + 1}/{max_retries + 1})")
                else:
                    partial_path.unlink()

            headers = {"user-agent": _BROWSER_UA}
            if existing_size > 0:
                headers["Range"] = f"bytes={existing_size}-"

            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=600, headers=headers) as client:
                    async with client.stream("GET", zip_url) as resp:
                        if resp.status_code in (503, 429, 500, 502):
                            delay = base_delay * (2 ** attempt) + (0.5 * attempt)
                            logger.warning(f"IA returned HTTP {resp.status_code}, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries + 1})")
                            await resp.aclose()
                            await asyncio.sleep(delay)
                            continue

                        if resp.status_code == 416:
                            # Range not satisfiable — file is already complete or too large
                            logger.info(f"Range request returned 416, file may be complete")
                            if existing_size > 0:
                                partial_path.rename(zip_path)
                                logger.info(f"JP2 zip complete (resumed): {zip_path} ({zip_path.stat().st_size:,} bytes)")
                                return zip_path
                            continue

                        if resp.status_code not in (200, 206):
                            # Non-retryable error — try direct server fallback
                            http_code = resp.status_code
                            logger.warning(f"Standard /download/ returned {http_code}, trying direct server...")
                            await resp.aclose()
                            direct_path = await self._try_direct_server_download(
                                client, identifier, zip_path, partial_path, existing_size
                            )
                            if direct_path:
                                return direct_path
                            raise FileNotFoundError(
                                f"JP2 zip not found: HTTP {http_code} from {zip_url} (direct server also failed)"
                            )

                        # Stream the response to file
                        mode = "ab" if (existing_size > 0 and resp.status_code == 206) else "wb"
                        downloaded = existing_size
                        with open(partial_path, mode) as f:
                            async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                                f.write(chunk)
                                downloaded += len(chunk)

                # Verify the download looks valid
                partial_path.rename(zip_path)
                logger.info(f"Downloaded JP2 zip: {zip_path} ({zip_path.stat().st_size:,} bytes)")
                return zip_path

            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as e:
                delay = base_delay * (2 ** attempt) + (0.5 * attempt)
                logger.warning(f"Connection error during download: {e}, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries + 1})")
                await asyncio.sleep(delay)
                continue

        # All retries exhausted
        if partial_path.exists():
            partial_path.unlink()
        raise FileNotFoundError(
            f"JP2 zip download failed after {max_retries + 1} attempts: {zip_url}"
        )

    async def _try_direct_server_download(
        self,
        client: httpx.AsyncClient,
        identifier: str,
        zip_path: Path,
        partial_path: Path,
        existing_size: int = 0,
    ) -> Path | None:
        """Try downloading via direct server as fallback. Returns path on success, None on failure."""
        direct_base = await self._get_direct_server(identifier)
        if not direct_base:
            return None

        direct_url = f"{direct_base}/{identifier}_jp2.zip"
        logger.info(f"Trying direct server: {direct_url}")

        try:
            headers = {"user-agent": _BROWSER_UA}
            if existing_size > 0:
                headers["Range"] = f"bytes={existing_size}-"

            async with client.stream("GET", direct_url) as resp:
                if resp.status_code not in (200, 206):
                    logger.warning(f"Direct server returned HTTP {resp.status_code}")
                    return None

                mode = "ab" if (existing_size > 0 and resp.status_code == 206) else "wb"
                with open(partial_path, mode) as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)

            partial_path.rename(zip_path)
            logger.info(f"Downloaded JP2 zip via direct server: {zip_path} ({zip_path.stat().st_size:,} bytes)")
            return zip_path
        except Exception as e:
            logger.warning(f"Direct server download failed: {e}")
            return None

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
        import cv2

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

                # Extract JP2 from zip to temp, then convert with OpenCV
                jp2_data = zf.read(jp2_name)
                jp2_temp = dest / f"_temp_{page_num}.jp2"
                jp2_temp.write_bytes(jp2_data)

                try:
                    img = cv2.imread(str(jp2_temp), cv2.IMREAD_ANYCOLOR)
                    if img is None:
                        logger.warning(f"Failed to read {jp2_name}")
                        continue
                    cv2.imwrite(str(png_path), img)
                    png_files.append(png_path)
                    logger.debug(f"Extracted page {page_num}: {png_path.name}")
                except Exception as e:
                    logger.warning(f"Skipping corrupted page {page_num} ({jp2_name}): {e}")
                    png_path.unlink(missing_ok=True)
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
        # Also support page_NNNN.png pattern (from pdftoppm)
        if not pngs:
            pngs = sorted(pages_dir.glob("page_*.png"))
        return pngs
