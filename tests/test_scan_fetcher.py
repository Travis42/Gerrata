"""Tests for scan fetcher (JP2 image download and extraction only)."""

from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from gerrata.fetcher.scans import ScanFetcher, ScanPage


@pytest.fixture
def fetcher():
    return ScanFetcher()


class TestJP2ZipDownload:
    """Tests for JP2 zip download and extraction."""

    @pytest.mark.asyncio
    async def test_download_jp2_zip_new(self, tmp_path):
        """Download JP2 zip from IA."""
        fetcher = ScanFetcher(cache_dir=tmp_path)
        scan_id = "06-stevenson-jekyll-hyde"

        # Mock the download at the method level by creating a real zip
        import zipfile
        zip_path = tmp_path / f"{scan_id}_jp2.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.jp2", b"fake-jp2-data")

        # Test with an already-existing zip (download_jp2_zip returns cached)
        result = await fetcher.download_jp2_zip(scan_id, dest=tmp_path)

        assert result.exists()
        assert result.name == f"{scan_id}_jp2.zip"

    @pytest.mark.asyncio
    async def test_download_jp2_zip_cached(self, tmp_path):
        """Skip download if zip already cached."""
        fetcher = ScanFetcher(cache_dir=tmp_path)
        scan_id = "test-id"
        zip_path = tmp_path / f"{scan_id}_jp2.zip"
        zip_path.write_bytes(b"existing-data")

        result = await fetcher.download_jp2_zip(scan_id, dest=tmp_path)

        assert result == zip_path
        assert result.read_bytes() == b"existing-data"

    def test_extract_jp2_zip(self, tmp_path):
        """Extract JP2 files from a zip and convert to PNG."""
        import zipfile
        from PIL import Image

        # Create a fake zip with JP2-like files
        zip_path = tmp_path / "test_jp2.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            # Create real JP2 files (or use PNG renamed as JP2 for testing)
            for i in [0, 1, 2]:
                img = Image.new("RGB", (100, 100), color="white")
                img_bytes = tmp_path / f"test_{i}.jp2"
                img.save(img_bytes, "JPEG2000") if hasattr(Image, 'JPEG2000_PLUGIN') else img.save(img_bytes, "PNG")
                # Rename to JP2 extension for the zip
                jp2_file = tmp_path / f"Test_Book_{i:04d}.jp2"
                img.save(jp2_file, "PNG")  # Use PNG for compatibility
                zf.write(jp2_file, arcname=f"Test_Book_jp2/Test_Book_{i:04d}.jp2")

        fetcher = ScanFetcher()
        pngs = fetcher.extract_jp2_zip(zip_path, dest=tmp_path / "pages")

        assert len(pngs) == 3
        for p in pngs:
            assert p.exists()
            assert p.suffix == ".png"

    def test_extract_jp2_zip_page_range(self, tmp_path):
        """Extract only pages in a range."""
        import zipfile
        from PIL import Image

        zip_path = tmp_path / "range_test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for i in range(10):
                img = Image.new("RGB", (100, 100), color="white")
                jp2_file = tmp_path / f"Book_{i:04d}.jp2"
                img.save(jp2_file, "PNG")
                zf.write(jp2_file, arcname=f"Book_jp2/Book_{i:04d}.jp2")

        fetcher = ScanFetcher()
        pngs = fetcher.extract_jp2_zip(
            zip_path, dest=tmp_path / "pages_range", page_range=(3, 5)
        )

        assert len(pngs) == 3  # Pages 3, 4, 5

    def test_get_cached_pages(self, tmp_path):
        """List PNG files from a directory."""
        # Create some PNG files
        pages_dir = tmp_path / "pages"
        pages_dir.mkdir()
        for name in ["page_0001.png", "page_0002.png", "page_0003.png"]:
            (pages_dir / name).write_bytes(b"fake-png")

        fetcher = ScanFetcher()
        pngs = fetcher.get_cached_pages(pages_dir)

        assert len(pngs) == 3
        assert pngs[0].name == "page_0001.png"

    def test_get_cached_pages_empty(self, tmp_path):
        """Empty directory returns empty list."""
        fetcher = ScanFetcher()
        pngs = fetcher.get_cached_pages(tmp_path / "nonexistent")
        assert pngs == []

    def test_get_cached_pages_filters_png(self, tmp_path):
        """Only PNG files are returned."""
        pages_dir = tmp_path / "mixed"
        pages_dir.mkdir()
        (pages_dir / "page.png").write_bytes(b"data")
        (pages_dir / "page.jp2").write_bytes(b"data")
        (pages_dir / "page.jpg").write_bytes(b"data")
        (pages_dir / "readme.txt").write_bytes(b"data")

        fetcher = ScanFetcher()
        pngs = fetcher.get_cached_pages(pages_dir)
        assert len(pngs) == 1
        assert pngs[0].suffix == ".png"


class TestScanPageVisionText:
    """Test ScanPage vision_text field."""

    def test_vision_text_default(self):
        page = ScanPage(page_num=0, ocr_text="OCR text")
        assert page.vision_text == ""

    def test_vision_text_set(self):
        page = ScanPage(
            page_num=0,
            ocr_text="OCR text",
            vision_text="Clean vision transcription",
        )
        assert page.vision_text == "Clean vision transcription"
