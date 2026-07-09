# Recursive Sub-Page Alignment — Benchmark Results

**Date:** 2026-07-09
**Fixture:** Saxo Grammaticus, *The Danish History, Books I-IX* (PG #1150)
**Data:** Cached pipeline intermediates in `tests/fixtures/saxo/`

## Scores

| Metric | Flat (current) | Recursive | Stitched |
|--------|---------------|-----------|----------|
| Total candidates | 1,293 | 962 | 635 |
| Change vs flat | — | -26% | **-51%** |
| Pages with errors | 540 | 457 | 215 |
| Avg errors/page | 2.4 | 2.1 | 3.0 |
| Median errors/page | 2 | 2 | 1 |
| Max errors/page | 14 | 13 | 30 |

## Category Breakdown

| Category | Flat | Recursive | Stitched |
|----------|------|-----------|----------|
| EXTRA_WORD | 459 | 300 (-159) | 15 (-444) |
| MISSING_WORD | 245 | 141 (-104) | 13 (-232) |
| WRONG_WORD | 377 | 312 (-65) | 335 (-42) |
| OCR_SCANNO | 212 | 209 (-3) | 272 (+60) |

## Analysis

### Recursive sub-page alignment (-26%)
Filters alignment drift by diffing between SequenceMatcher anchor blocks.
Skips gaps where both sides are highly dissimilar (ratio < 0.3, length > 8).

### Chapter-stitched alignment (-51%)
Concatenates adjacent scan page transcriptions into continuous segments,
eliminating page-boundary artifacts (split words, truncated sentences,
orphaned fragments). Uses `normalize_for_diff` as the page text normalizer
so the PageMap token-to-page mapping stays aligned.

**Key finding:** Stitching nearly eliminated EXTRA_WORD and MISSING_WORD
(704 combined → 28). These were overwhelmingly page-boundary artifacts.
What remains is WRONG_WORD and OCR_SCANNO — actual character-level
differences. The slight increase in OCR_SCANNO (+60) represents real
character-level errors that were previously masked by page-boundary noise.

### Page attribution
Tighter segment grouping (page_gap ≤ 1, pg_gap < 500) creates 53 segments
instead of 6, keeping page attribution accurate. 201 of 215 pages with
errors have ≤8 candidates. The 14 outlier pages have genuine high-density
character differences (quotation mark style, em-dash formatting).

### Method
- `stitch_scan_pages()` with `normalizer=normalize_for_diff` — normalizes
  each page *before* concatenation so PageMap offsets align with tokens
- `build_token_page_map()` — maps each scan token to its source page
- `_diff_stitched_gap()` — uses `check_aligned_passage` for classification,
  then remaps page attribution via token position lookup

### Related fixes (same session)

1. **`normalize_for_matching`** — strips PG formatting artifacts (underscores,
   HTML tags, entities, footnote markers) that depressed alignment scores.
   Major impact on books with heavy italic markup (Huck Finn: 754 instances).

2. **Diacritic/ligature global replacement heuristic** — replacements that
   only add diacritics/ligatures qualify as global with 1+ occurrence.

3. **Underscore stripping in report context** — `_extract_sentence` removes
   PG italic markup from email report context strings.
