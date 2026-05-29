"""Tests for the batch runner."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# batch_runner module was removed; skip all tests in this file
pytestmark = pytest.mark.skip(reason="batch_runner module has been removed")

try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    from batch_runner import load_queue, check_disk, cleanup_old_caches, check_pipeline_output
except ImportError:
    pass


@pytest.fixture
def temp_queue(tmp_path):
    """Create a temporary ERRATA_QUEUE.md for testing."""
    queue_content = """\
# PG Top 30 Errata Queue

## Queue

| # | PG ID | Title | Author | Status | IA Scan | Candidates | Notes |
|---|-------|-------|--------|--------|---------|------------|-------|
| 1 | 58169 | Hegel's Lectures Vol 3 | Hegel | [x] | dli.bengal.10689.2429 | 175 | |
| 2 | 2701 | Moby Dick | Melville | [~] | mobydickorwhale01melv | — | Running |
| 3 | 84 | Frankenstein | Shelley | [ ] | | | |
| 4 | 1184 | Count of Monte Cristo | Dumas | [ ] | | | |
| 5 | 40739 | Die Traumdeutung | Freud | [ ] | | German | |
| 6 | 43 | Dr Jekyll | Stevenson | [x] | 06-stevenson-jekyll-hyde | — | Test |

## Pipeline Run Log

| Date | PG ID | Title | Scan | Candidates | Duration | Issues |
|------|------|-------|------|------------|----------|--------|
| 2026-05-10 | 58169 | Hegel Vol 3 | dli.bengal.10689.2429 | 175 | 110 min | None |
"""
    queue_file = tmp_path / "ERRATA_QUEUE.md"
    queue_file.write_text(queue_content)
    return queue_file


class TestLoadQueue:
    def test_parses_all_entries(self, temp_queue):
        with patch("batch_runner.QUEUE_FILE", temp_queue):
            entries = load_queue()
        assert len(entries) == 6

    def test_extracts_pg_ids(self, temp_queue):
        with patch("batch_runner.QUEUE_FILE", temp_queue):
            entries = load_queue()
        pg_ids = [e["pg_id"] for e in entries]
        assert pg_ids == [58169, 2701, 84, 1184, 40739, 43]

    def test_extracts_status_done(self, temp_queue):
        with patch("batch_runner.QUEUE_FILE", temp_queue):
            entries = load_queue()
        assert entries[0]["status"] == "done"
        assert entries[5]["status"] == "done"

    def test_extracts_status_running(self, temp_queue):
        with patch("batch_runner.QUEUE_FILE", temp_queue):
            entries = load_queue()
        assert entries[1]["status"] == "running"

    def test_extracts_status_pending(self, temp_queue):
        with patch("batch_runner.QUEUE_FILE", temp_queue):
            entries = load_queue()
        assert entries[2]["status"] == "pending"
        assert entries[3]["status"] == "pending"

    def test_extracts_scan_id(self, temp_queue):
        with patch("batch_runner.QUEUE_FILE", temp_queue):
            entries = load_queue()
        assert entries[0]["scan_id"] == "dli.bengal.10689.2429"
        assert entries[1]["scan_id"] == "mobydickorwhale01melv"
        assert entries[5]["scan_id"] == "06-stevenson-jekyll-hyde"

    def test_filters_non_scan_notes(self, temp_queue):
        """Notes like 'German text' should not appear as scan IDs."""
        with patch("batch_runner.QUEUE_FILE", temp_queue):
            entries = load_queue()
        # Freud has empty scan column, 'German' is in Notes column
        assert entries[4]["scan_id"] == ""

    def test_stops_at_second_table(self, temp_queue):
        """Should not parse the Pipeline Run Log table."""
        with patch("batch_runner.QUEUE_FILE", temp_queue):
            entries = load_queue()
        # All entries should have integer pg_ids, not date strings
        for e in entries:
            assert isinstance(e["pg_id"], int)

    def test_empty_file(self, tmp_path):
        queue_file = tmp_path / "EMPTY.md"
        queue_file.write_text("")
        with patch("batch_runner.QUEUE_FILE", queue_file):
            entries = load_queue()
        assert entries == []

    def test_missing_file(self):
        with patch("batch_runner.QUEUE_FILE", Path("/nonexistent")):
            entries = load_queue()
        assert entries == []

    def test_preserves_order(self, temp_queue):
        with patch("batch_runner.QUEUE_FILE", temp_queue):
            entries = load_queue()
        nums = [e["num"] for e in entries]
        assert nums == [1, 2, 3, 4, 5, 6]

    def test_extracts_titles(self, temp_queue):
        with patch("batch_runner.QUEUE_FILE", temp_queue):
            entries = load_queue()
        assert "Hegel" in entries[0]["title"]
        assert "Moby" in entries[1]["title"]
        assert "Frankenstein" in entries[2]["title"]


class TestCheckDisk:
    def test_returns_dict_with_required_keys(self):
        result = check_disk()
        assert "total_gb" in result
        assert "free_gb" in result
        assert "used_gb" in result
        assert "used_pct" in result

    def test_values_are_positive(self):
        result = check_disk()
        assert result["total_gb"] > 0
        assert result["free_gb"] > 0
        assert 0 <= result["used_pct"] <= 100


class TestCheckPipelineOutput:
    def test_finds_email_report(self, tmp_path):
        email = tmp_path / "gutenberg2701-moby-dick_errata_email.txt"
        email.write_text("\nPage 1:\nfoo ==> bar\n\nPage 2:\nbaz ==> qux\n")
        with patch("batch_runner.REPORTS_DIR", tmp_path):
            result = check_pipeline_output(2701)
        assert result["email_path"] is not None
        assert result["candidates"] == 2

    def test_no_output(self, tmp_path):
        with patch("batch_runner.REPORTS_DIR", tmp_path):
            result = check_pipeline_output(9999)
        assert result["email_path"] is None
        assert result["candidates"] is None

    def test_finds_latest_version(self, tmp_path):
        """Should pick the highest version number."""
        old = tmp_path / "gutenberg2701-moby-dick_errata_email-2.txt"
        old.write_text("\nPage 1:\nold ==> new\n")
        new = tmp_path / "gutenberg2701-moby-dick_errata_email-3.txt"
        new.write_text("\nPage 1:\nold ==> new\n\nPage 2:\nold ==> new\n\nPage 3:\nold ==> new\n")
        with patch("batch_runner.REPORTS_DIR", tmp_path):
            result = check_pipeline_output(2701)
        assert result["candidates"] == 3


class TestCleanupOldCaches:
    def test_removes_stray_jp2_zips(self, tmp_path):
        jp2 = tmp_path / "book_jp2.zip"
        jp2.write_bytes(b"x" * 1000)
        with patch("batch_runner.CACHE_DIR", tmp_path):
            freed = cleanup_old_caches()
        assert not jp2.exists()
        assert freed >= 1000

    def test_removes_stray_html_files(self, tmp_path):
        htm = tmp_path / "2701.htm"
        htm.write_bytes(b"<html></html>")
        with patch("batch_runner.CACHE_DIR", tmp_path):
            freed = cleanup_old_caches()
        assert not htm.exists()

    def test_preserves_non_cache_files(self, tmp_path):
        keep = tmp_path / "important.json"
        keep.write_bytes(b"{}")
        with patch("batch_runner.CACHE_DIR", tmp_path):
            cleanup_old_caches()
        assert keep.exists()

    def test_empty_cache(self, tmp_path):
        with patch("batch_runner.CACHE_DIR", tmp_path):
            freed = cleanup_old_caches()
        assert freed == 0
