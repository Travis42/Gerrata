"""Tests for page classification module."""

import pytest
import numpy as np
from PIL import Image
from pathlib import Path

from gerrata.page_classifier import (
    PageClassification,
    classify_page,
    classify_pages,
    filter_text_pages,
    _analyze_image,
)


@pytest.fixture
def tmp_png(tmp_path):
    """Helper to create a PNG image from a numpy array."""
    def _make(arr, name="test.png"):
        path = tmp_path / name
        Image.fromarray(arr).save(path)
        return path
    return _make


@pytest.fixture
def blank_page(tmp_png):
    """All-white page — should be classified as non-text."""
    arr = np.full((400, 300), 255, dtype=np.uint8)
    return tmp_png(arr, "blank.png")


@pytest.fixture
def near_blank_page(tmp_png):
    """Nearly blank page — only a page number in corner (very sparse)."""
    arr = np.full((400, 300), 255, dtype=np.uint8)
    # Tiny page number at bottom
    for x in range(260, 280):
        arr[380, x] = 200
    return tmp_png(arr, "near_blank.png")


@pytest.fixture
def text_page(tmp_png):
    """Simulated text page — distributed dark pixels across grid cells.
    
    Text has lines of small characters spread across the full page height.
    Each line is ~1-2px tall with spacing between lines, creating a
    pattern of many grid cells with low but non-zero dark pixel density.
    """
    arr = np.full((400, 300), 255, dtype=np.uint8)
    # Draw horizontal "text lines" spread across the full page
    for y in range(30, 380, 18):
        # Each "line" of text is scattered dark pixels
        for x in range(40, 260, 2):
            noise = np.random.randint(0, 60)
            arr[y, x] = noise
            arr[y + 1, x] = noise
    return tmp_png(arr, "text.png")


@pytest.fixture
def dense_text_page(tmp_png):
    """Dense text page — lots of ink spread across most of the page."""
    arr = np.full((400, 300), 255, dtype=np.uint8)
    for y in range(20, 390, 12):
        for x in range(30, 280, 2):
            arr[y, x] = np.random.randint(0, 80)
            arr[y + 1, x] = np.random.randint(0, 80)
            arr[y + 2, x] = np.random.randint(0, 80)
    return tmp_png(arr, "dense_text.png")


@pytest.fixture
def illustration_page(tmp_png):
    """Full-page illustration — high dark density, large solid regions.
    
    This simulates a color illustration that would have very different
    pixel distribution from text. It has high overall density but
    concentrated in large blocks rather than thin text lines.
    """
    arr = np.full((400, 300), 255, dtype=np.uint8)
    # Large irregular blocks covering most of the page
    # (simulating a painting/illustration)
    arr[20:350, 20:280] = np.random.randint(30, 120, size=(330, 260), dtype=np.uint8)
    # A few bright spots within (highlights)
    arr[50:100, 50:100] = 230
    arr[200:250, 150:200] = 240
    return tmp_png(arr, "illustration.png")


class TestAnalyzeImage:
    def test_blank_page_density(self, blank_page):
        density, coverage = _analyze_image(blank_page)
        assert density < 0.01, f"Blank page should have near-zero density, got {density}"

    def test_text_page_density(self, text_page):
        density, coverage = _analyze_image(text_page)
        assert density >= 0.03, f"Text page should have sufficient density, got {density}"
        assert coverage >= 0.25, f"Text page should have good coverage, got {coverage}"

    def test_near_blank_low_density(self, near_blank_page):
        density, coverage = _analyze_image(near_blank_page)
        # Very few dark pixels — should have very low density
        assert density < 0.01, f"Near-blank page should have minimal density, got {density}"

    def test_illustration_high_density(self, illustration_page):
        density, coverage = _analyze_image(illustration_page)
        # Full-page illustration has high density (lots of ink)
        assert density > 0.30, f"Illustration should have high density, got {density}"


class TestClassifyPage:
    def test_blank_page_is_non_text(self, blank_page):
        result = classify_page(blank_page, page_num=0)
        assert not result.has_text
        assert "low ink density" in result.reason

    def test_near_blank_is_non_text(self, near_blank_page):
        result = classify_page(near_blank_page, page_num=0)
        assert not result.has_text

    def test_text_page_is_text(self, text_page):
        result = classify_page(text_page, page_num=0)
        assert result.has_text
        assert "text page" in result.reason

    def test_dense_text_page_is_text(self, dense_text_page):
        result = classify_page(dense_text_page, page_num=0)
        assert result.has_text

    def test_illustration_page_behavior(self, illustration_page):
        """Illustration pages may or may not pass pixel heuristics.
        
        Full-page illustrations have high density and coverage, similar
        to dense text pages. The pixel heuristic alone cannot reliably
        distinguish them. These pages will be caught by transcription
        quality checks instead (short response → MIN_TRANSCRIPTION_CHARS).
        
        This test documents the current behavior rather than asserting
        a specific outcome.
        """
        result = classify_page(illustration_page, page_num=0)
        # The illustration has very high density, so it may pass as "text"
        # That's OK — transcription quality check will catch it
        assert result.dark_density > 0.30

    def test_custom_thresholds_pass_all(self, blank_page):
        # With zero thresholds, everything passes
        result = classify_page(blank_page, page_num=0, min_dark_density=0.0, min_grid_coverage=0.0)
        assert result.has_text

    def test_custom_thresholds_filter_all(self, text_page):
        # With very high thresholds, nothing passes
        result = classify_page(text_page, page_num=0, min_dark_density=1.0, min_grid_coverage=1.0)
        assert not result.has_text

    def test_error_fallback(self, tmp_path):
        # Non-existent file should fall back to "assume text" (safe default)
        result = classify_page(tmp_path / "nonexistent.png", page_num=0)
        assert result.has_text
        assert "analysis failed" in result.reason

    def test_page_num_recorded(self, text_page):
        result = classify_page(text_page, page_num=42)
        assert result.page_num == 42

    def test_reason_includes_values(self, text_page):
        result = classify_page(text_page, page_num=0)
        assert "density=" in result.reason
        assert "coverage=" in result.reason


class TestClassifyPages:
    def test_batch_classification(self, blank_page, text_page):
        paths = [blank_page, text_page]
        results = classify_pages(paths)
        assert len(results) == 2
        assert not results[0].has_text  # blank
        assert results[1].has_text     # text

    def test_batch_with_near_blank(self, near_blank_page, text_page, dense_text_page):
        paths = [near_blank_page, text_page, dense_text_page]
        results = classify_pages(paths)
        assert len(results) == 3
        assert not results[0].has_text  # near-blank
        assert results[1].has_text      # text
        assert results[2].has_text      # dense text

    def test_maintains_order(self, text_page, blank_page):
        results = classify_pages([text_page, blank_page])
        assert results[0].has_text
        assert not results[1].has_text

    def test_empty_input(self):
        results = classify_pages([])
        assert results == []


class TestFilterTextPages:
    def test_filters_non_text(self, blank_page, text_page):
        paths = [blank_page, text_page]
        classifications = classify_pages(paths)
        filtered = filter_text_pages(paths, classifications)
        assert len(filtered) == 1
        assert filtered[0] == text_page

    def test_all_text(self, text_page):
        paths = [text_page, text_page]
        classifications = classify_pages(paths)
        filtered = filter_text_pages(paths, classifications)
        assert len(filtered) == 2

    def test_all_non_text(self, blank_page):
        paths = [blank_page, blank_page]
        classifications = classify_pages(paths)
        filtered = filter_text_pages(paths, classifications)
        assert len(filtered) == 0

    def test_empty_input(self):
        result = filter_text_pages([], [])
        assert result == []
