# TASK: Pipeline Improvements — Page Classification, IA Destdir, Alignment Enhancement

Three improvements to the Gerrata pipeline. Read each section carefully and implement all three.

## Overview

1. **Page classification** — detect text-bearing vs. illustration/blank pages before transcription
2. **IA CLI destdir fix** — ensure `ia download` saves into `cache/` not project root
3. **Alignment enhancement** — skip non-text pages during alignment, improve sequential tracker

---

## Spec 1: Page Classification (Pre-Transcription Filter)

### Problem

The pipeline sends every page image to the vision model for transcription, including full-page color illustrations, blank pages, title pages with minimal text, and decorative pages. This wastes API calls (69 pages failed for East of the Sun and West of the Moon, each retried once = 138 wasted calls) and creates noise in downstream stages.

### Solution

Add a new Step 2a between page extraction and transcription. For each extracted page PNG, run a lightweight local classification that determines whether the page likely contains transcribable text. Non-text pages are skipped entirely (not sent to the vision model) and marked in the pipeline intermediates.

### Approach: Pixel Density Heuristic (no API call)

Use Pillow (already a dependency via OpenCV) to analyze the page image:

1. Convert to grayscale
2. Threshold at 128 to binary
3. Count dark pixels (below 128) in the **central 80%** of the page (margins often have noise/bleed)
4. Classify:
   - **text page**: dark pixel density ≥ 3% AND there are "text-like" features (see below)
   - **non-text page**: dark pixel density < 3% OR the page is dominated by large solid blocks (illustration)

5. Text-like feature detection: divide the central region into a grid (e.g., 10×10), count cells with at least some dark pixels. Text pages have dark pixels distributed across many grid cells (because text is spread across lines). Illustration pages have dark pixels clustered in fewer, larger regions.

### API

Create a new module: `src/gerrata/page_classifier.py`

```python
@dataclass
class PageClassification:
    page_num: int
    image_path: Path
    has_text: bool
    dark_density: float  # 0.0-1.0
    grid_coverage: float  # 0.0-1.0, fraction of grid cells with dark pixels
    reason: str  # human-readable reason for classification

def classify_pages(image_paths: list[Path]) -> list[PageClassification]:
    """Classify each page as text-bearing or not. Pure heuristic, no API calls."""
    ...

def filter_text_pages(
    image_paths: list[Path],
    classifications: list[PageClassification],
) -> list[Path]:
    """Return only paths classified as having text."""
    ...
```

### Thresholds (tunable)

These should be configurable via CLI args with sensible defaults:

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `--min-dark-density` | 0.03 | Minimum dark pixel ratio to consider text (3%) |
| `--min-grid-coverage` | 0.25 | Minimum fraction of grid cells with dark pixels (25%) |
| `--grid-size` | 10 | Grid resolution for text feature detection |

A page is classified as text-bearing if BOTH:
- dark_density >= min_dark_density (has enough ink)
- grid_coverage >= min_grid_coverage (ink is distributed like text, not a solid block)

### Integration into CLI pipeline

In `cli.py`, after page extraction and before transcription:

```python
# Step 2a: Classify pages (text vs. illustration)
console.print("[bold blue]Step 2a:[/bold blue] Classifying pages...")
from gerrata.page_classifier import classify_pages, filter_text_pages
classifications = classify_pages(page_images)
text_pages = filter_text_pages(page_images, classifications)
non_text_count = len(page_images) - len(text_pages)
if non_text_count > 0:
    console.print(f"  Classified {len(text_pages)} text pages, {non_text_count} non-text pages (skipped)")
save_intermediate(intermed_dir, "01b_page_classifications",
    [{"page_num": c.page_num, "has_text": c.has_text, "dark_density": c.dark_density,
      "grid_coverage": c.grid_coverage, "reason": c.reason} for c in classifications])
```

Then pass `text_pages` (not `page_images`) to the transcriber.

Non-text pages should also be logged in the final report summary (e.g., "X pages skipped (illustrations/blanks)").

### Tests

Add `tests/test_page_classifier.py` with:
- Test blank image (all white) → non-text
- Test solid block image (single dark rectangle) → non-text (low grid coverage)
- Test image with text-like grid distribution → text
- Test real-world edge cases: title page with large text, page with small illustration in corner
- Test that `filter_text_pages` correctly filters
- Test configurable thresholds

---

## Spec 2: IA CLI Destdir Fix

### Problem

The `ia` CLI (`internetarchive` package) saves downloaded files to `<cwd>/<identifier>/`. When a user runs `ia download <scan_id> <scan_id>_jp2.zip` from the gerrata project root, the zip and extracted files land in `/root/projects/gerrata/eastofsunwestofm00asbj/` instead of `/root/projects/gerrata/cache/eastofsunwestofm00asbj/`.

The code in `ScanFetcher.download_jp2_zip()` uses httpx directly and correctly respects `dest` parameter. But when users follow the README instructions to use the `ia` CLI, they may run it from the wrong directory.

### Solution

Two changes:

#### 2a. Add a helper script: `scripts/ia_download.sh`

A convenience wrapper that ensures downloads go to `cache/`:

```bash
#!/usr/bin/env bash
# Download IA scan to cache/ directory
# Usage: bash scripts/ia_download.sh <scan-id> [file]

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/cache"

mkdir -p "$DEST"
ia download "$1" "${2:-}" --destdir "$DEST"
```

#### 2b. Update README instructions

In the README section "Downloading Scans with the `ia` CLI", update all examples to show `--destdir cache/` or recommend using the helper script:

```bash
# Download the JP2 zip to cache/
ia download volsungasagastor00spariala volsungasagastor00spariala_jp2.zip --destdir cache/

# Or use the helper script
bash scripts/ia_download.sh volsungasagastor00spariala volsungasagastor00spariala_jp2.zip
```

### No code changes needed in ScanFetcher

The `ScanFetcher.download_jp2_zip()` already correctly uses `dest` parameter. The httpx-based download is fine. This is purely a documentation + convenience fix.

---

## Spec 3: Alignment Enhancement — Skip Non-Text Pages

### Problem

When the transcriber processes all pages (current behavior), failed transcriptions create entries in the results list. The aligner then tries to match these empty/failed pages against PG text, wasting time and producing noise.

With Spec 1 implemented, non-text pages won't reach the transcriber. But we should also ensure:

1. The aligner knows which pages were skipped and why (for reporting)
2. The `SequentialTracker` doesn't get confused by gaps in page numbering
3. The final report includes a count of skipped pages

### Solution

#### 3a. Track skipped pages through the pipeline

Add `skipped_pages` field to the pipeline that carries page numbers of non-text pages through to the report. In `cli.py`:

```python
skipped_page_nums = [c.page_num for c in classifications if not c.has_text]
```

Pass this to the report generator so it can include "X pages skipped (illustrations/blanks)" in the summary.

#### 3b. SequentialTracker gap tolerance

The `SequentialTracker` in `vision_aligner.py` already handles gaps (when a page isn't aligned). Review to ensure that when multiple consecutive pages are skipped (e.g., two illustration pages back-to-back), the tracker doesn't produce wildly wrong `chars_per_page` estimates.

Current code in `SequentialTracker.chars_per_page`:
```python
total_chars = recent[-1][1] - recent[0][1]
total_pages = recent[-1][0] - recent[0][0]
return max(500.0, total_chars / max(1, total_pages))
```

This already divides by actual page count difference, so gaps should be handled. **Verify this is correct** by checking that `record()` is only called for successfully aligned pages (it is — it's called in `align_transcription_to_pg` only on success).

No code change needed here — just verify and document that the tracker handles gaps correctly.

#### 3c. Report summary enhancement

In `src/gerrata/reporter/generator.py`, add a `skipped_pages` count to the report summary table. If the pipeline has page classifications, include:

```
│ Pages skipped     │ 69 (illustrations/blanks)  │
```

This requires passing `skipped_page_count` through to `ReportGenerator.generate()`.

### Tests

Add a test to `tests/test_page_classifier.py` or `tests/test_alignment.py`:
- Verify that `SequentialTracker.chars_per_page` correctly handles gaps of 1, 2, 5 consecutive skipped pages
- Verify the tracker's `expected_position` remains reasonable after gaps

---

## Implementation Order

1. **Spec 1** (page_classifier.py + CLI integration) — biggest impact, saves API calls
2. **Spec 2** (ia_download.sh + README) — quick, prevents future mistakes
3. **Spec 3** (report enhancement + tracker verification) — polish, depends on Spec 1

## Testing

Run `python3 -m pytest tests/ -q` after all changes. All existing tests must pass.

## Files to Create/Modify

**Create:**
- `src/gerrata/page_classifier.py` — page classification module
- `tests/test_page_classifier.py` — tests for classifier
- `scripts/ia_download.sh` — IA CLI convenience wrapper

**Modify:**
- `src/gerrata/cli.py` — add Step 2a classification, pass skipped count to reporter
- `src/gerrata/reporter/generator.py` — accept and display skipped page count
- `README.md` — update IA CLI instructions with `--destdir`
