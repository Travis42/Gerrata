"""Page classification: detect text-bearing vs. illustration/blank pages.

Uses pixel density heuristics to classify page images without any API calls.
This runs before transcription to skip illustration, blank, and decorative pages,
saving vision API calls and reducing downstream noise.

Approach:
1. Convert page to grayscale
2. Threshold to binary (dark vs. light pixels)
3. Measure dark pixel density in the central 80% of the page
4. Divide central region into a grid and count cells with dark pixels
5. Text pages have distributed dark pixels across many grid cells;
   illustrations/blanks have either very few dark pixels or clustered blocks
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PageClassification:
    """Result of classifying a single page image."""

    page_num: int
    image_path: Path
    has_text: bool
    dark_density: float  # 0.0-1.0, fraction of dark pixels in central region
    grid_coverage: float  # 0.0-1.0, fraction of grid cells with dark pixels
    reason: str  # human-readable explanation


def _analyze_image(
    image_path: Path,
    grid_size: int = 10,
) -> tuple[float, float]:
    """Analyze a page image and return (dark_density, grid_coverage).

    Uses Pillow for image processing (lighter than OpenCV for this task).
    """
    from PIL import Image
    import numpy as np

    img = Image.open(image_path).convert("L")  # Grayscale
    arr = np.array(img)

    h, w = arr.shape

    # Central 80% region (skip margins)
    margin_y = int(h * 0.10)
    margin_x = int(w * 0.10)
    central = arr[margin_y : h - margin_y, margin_x : w - margin_x]

    if central.size == 0:
        return 0.0, 0.0

    # Binary threshold: pixels below 128 are "dark" (text ink)
    binary = central < 128

    # Dark pixel density
    dark_density = float(binary.sum()) / binary.size

    # Grid coverage: divide into grid_size x grid_size cells
    ch, cw = central.shape
    cell_h = ch // grid_size
    cell_w = cw // grid_size

    if cell_h == 0 or cell_w == 0:
        return dark_density, 0.0

    covered_cells = 0
    total_cells = grid_size * grid_size

    for gy in range(grid_size):
        for gx in range(grid_size):
            y0 = gy * cell_h
            y1 = (gy + 1) * cell_h if gy < grid_size - 1 else ch - margin_y * 0
            x0 = gx * cell_w
            x1 = (gx + 1) * cell_w if gx < grid_size - 1 else cw - margin_x * 0
            # Use the same binary array (already cropped to central)
            cy0 = gy * cell_h
            cy1 = (gy + 1) * cell_h
            cx0 = gx * cell_w
            cx1 = (gx + 1) * cell_w
            cell = binary[cy0:cy1, cx0:cx1]
            if cell.size > 0:
                cell_density = float(cell.sum()) / cell.size
                # A cell "has content" if it has at least 1% dark pixels
                if cell_density >= 0.01:
                    covered_cells += 1

    grid_coverage = covered_cells / total_cells

    return dark_density, grid_coverage


def classify_page(
    image_path: Path,
    page_num: int = 0,
    min_dark_density: float = 0.03,
    min_grid_coverage: float = 0.25,
    grid_size: int = 10,
) -> PageClassification:
    """Classify a single page as text-bearing or not.

    A page is classified as text-bearing if BOTH:
    - dark_density >= min_dark_density (has enough ink)
    - grid_coverage >= min_grid_coverage (ink distributed like text)
    """
    try:
        dark_density, grid_coverage = _analyze_image(image_path, grid_size)
    except Exception as e:
        logger.warning(f"Failed to analyze {image_path.name}: {e}")
        # On error, assume text (don't skip — let transcription try)
        return PageClassification(
            page_num=page_num,
            image_path=image_path,
            has_text=True,
            dark_density=0.0,
            grid_coverage=0.0,
            reason=f"analysis failed ({e}), assuming text",
        )

    has_text = dark_density >= min_dark_density and grid_coverage >= min_grid_coverage

    if has_text:
        reason = f"text page (density={dark_density:.3f}, coverage={grid_coverage:.3f})"
    elif dark_density < min_dark_density:
        reason = f"low ink density ({dark_density:.3f} < {min_dark_density})"
    else:
        reason = f"concentrated ink / illustration ({grid_coverage:.3f} < {min_grid_coverage})"

    return PageClassification(
        page_num=page_num,
        image_path=image_path,
        has_text=has_text,
        dark_density=dark_density,
        grid_coverage=grid_coverage,
        reason=reason,
    )


def classify_pages(
    image_paths: list[Path],
    min_dark_density: float = 0.03,
    min_grid_coverage: float = 0.25,
    grid_size: int = 10,
) -> list[PageClassification]:
    """Classify each page as text-bearing or not. Pure heuristic, no API calls.

    Args:
        image_paths: List of page image paths (PNG).
        min_dark_density: Minimum dark pixel ratio (default 3%).
        min_grid_coverage: Minimum grid cell coverage (default 25%).
        grid_size: Grid resolution for text feature detection (default 10x10).

    Returns:
        List of PageClassification, one per image path, in order.
    """
    results = []
    for i, path in enumerate(image_paths):
        c = classify_page(
            path,
            page_num=i,
            min_dark_density=min_dark_density,
            min_grid_coverage=min_grid_coverage,
            grid_size=grid_size,
        )
        results.append(c)
        if not c.has_text:
            logger.info(f"  Skip page {i}: {c.reason}")

    text_count = sum(1 for c in results if c.has_text)
    non_text_count = len(results) - text_count
    logger.info(f"Classified {text_count} text pages, {non_text_count} non-text pages")

    return results


def filter_text_pages(
    image_paths: list[Path],
    classifications: list[PageClassification],
) -> list[Path]:
    """Return only paths classified as having text.

    Args:
        image_paths: Original list of page image paths.
        classifications: Classifications from classify_pages().

    Returns:
        Filtered list of paths (only text-bearing pages).
    """
    return [
        path
        for path, c in zip(image_paths, classifications)
        if c.has_text
    ]
