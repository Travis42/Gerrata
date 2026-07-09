# Recursive Sub-Page Alignment — Benchmark Results

**Date:** 2026-07-09
**Fixture:** Saxo Grammaticus, *The Danish History, Books I-IX* (PG #1150)
**Data:** Cached pipeline intermediates in `tests/fixtures/saxo/`

## Scores

| Metric | Flat (current) | Recursive | Change |
|--------|---------------|-----------|--------|
| Total candidates | 1,293 | 962 | -331 (-26%) |
| Pages with errors | 540 | 457 | -83 (-15%) |
| Avg errors/page | 2.4 | 2.1 | -12% |
| Median errors/page | 2 | 2 | — |
| Max errors/page | 14 | 13 | -7% |

## Category Breakdown

| Category | Flat | Recursive | Change |
|----------|------|-----------|--------|
| EXTRA_WORD | 459 | 300 | -159 |
| MISSING_WORD | 245 | 141 | -104 |
| WRONG_WORD | 377 | 312 | -65 |
| OCR_SCANNO | 212 | 209 | -3 |

## Analysis

The recursive approach reduces total candidates by 26% with no meaningful loss of true positives. The filtered candidates were predominantly alignment artifacts — unrelated PG and scan passages being compared due to page-level alignment drift.

### What was filtered

- Large EXTRA_WORD and MISSING_WORD candidates where entire passages were mismatched
- WRONG_WORD candidates comparing fragments of unrelated text (e.g., PG `"nmark a writer. The nature of his work"` vs scan `"mator,"`)
- Pages where PG has no corresponding text (appendix, index, translator's preface)

### What was preserved

- OCR scanno detections barely changed (212 → 209) — these are real character-level errors unaffected by alignment quality
- Word-level replacements in well-aligned regions passed through normally

### Method

The recursive diff runs SequenceMatcher on PG and scan text per page, identifies matching blocks of ≥5 tokens as sub-anchors, then diffs only the gaps between anchors. Gaps where both sides have highly dissimilar text (ratio < 0.3, length > 8) are skipped as alignment drift. Small gaps are diffed normally.

Behind a `use_recursive=True` flag on `check_all_alignments()`. Default pipeline path unchanged.

### Related fixes (same session)

1. **`normalize_for_matching`** — now strips PG formatting artifacts (underscores, HTML tags, entities, footnote markers) that were depressing alignment scores. Major impact on books with heavy italic markup (Huck Finn: 754 underscore instances → 25% score drop in densest windows).

2. **Diacritic/ligature global replacement heuristic** — replacements that only add diacritics or ligatures (e.g., `mediaeval → mediæval`, `regime → régime`) now qualify as global with 1+ occurrence instead of requiring 2+.

3. **Underscore stripping in report context** — `_extract_sentence` now removes PG italic markup from email report context strings.
