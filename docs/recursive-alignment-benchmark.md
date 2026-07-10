# Gerrata Alignment Improvements — Session 2026-07-09

**Fixture:** Saxo Grammaticus, *The Danish History, Books I-IX* (PG #1150)
**Data:** Cached pipeline intermediates in `tests/fixtures/saxo/`

---

## Benchmark Scores

| Metric | Flat (baseline) | Recursive | Stitched |
|--------|----------------|-----------|----------|
| Total candidates | 1,293 | 962 | **635** |
| Change vs flat | — | -26% | **-51%** |
| Pages with errors | 540 | 457 | 215 |
| Avg errors/page | 2.4 | 2.1 | 3.0 |
| Median errors/page | 2 | 2 | 1 |
| Max errors/page | 14 | 13 | 30 |

### Category Breakdown

| Category | Flat | Recursive | Stitched |
|----------|------|-----------|----------|
| EXTRA_WORD | 459 | 300 (-159) | 15 (-444) |
| MISSING_WORD | 245 | 141 (-104) | 13 (-232) |
| WRONG_WORD | 377 | 312 (-65) | 335 (-42) |
| OCR_SCANNO | 212 | 209 (-3) | 272 (+60) |

### Key finding

Stitching nearly eliminated EXTRA_WORD and MISSING_WORD (704 combined → 28).
These were overwhelmingly page-boundary artifacts — split words, truncated
sentences, orphaned fragments. What remains is WRONG_WORD and OCR_SCANNO —
actual character-level differences. The OCR_SCANNO increase (+60) represents
real character errors previously masked by page-boundary noise.

---

## Improvements Made

### 1. `normalize_for_matching` — PG formatting artifact stripping

**Problem:** The aligner's `normalize_for_matching()` used `[^\w\s]` to strip
punctuation, but `\w` matches underscore in regex. PG italic markup
(`_Nostromo_`), HTML tags, entity names, and footnote markers all survived
as literal text, depressing alignment scores.

**Worst case:** Huckleberry Finn (PG #76) — 754 underscore italics instances.
Densest 1000-char windows scored 0.75 instead of 1.0 — a 25% alignment score
drop that could push pages below the 0.50 alignment threshold.

**Fix:** Now strips in order: HTML tags → HTML entities (named and numeric) →
PG footnote refs `[N]` → footnote blocks `[Footnote ...]` → illustration
markers `[Illustration ...]` → sidenote markers `[Sidenote ...]` → punctuation
including underscores → whitespace collapse.

**File:** `src/gerrata/aligner/vision_aligner.py` — `normalize_for_matching()`
**Tests:** 9 new in `tests/test_vision_aligner.py::TestNormalizeForMatching`

### 2. Diacritic/ligature global replacement heuristic

**Problem:** Words like `mediaeval`, `regime`, `Compania`, `Tome` were only
detected as errata once or twice but were wrong in every PG occurrence.
Transcribers systematically strip accents, tildes, umlauts, and expand
ligatures (æ→ae). These should be global replacements but needed ≥2 occurrences
to qualify.

**Fix:** Added `is_diacritic_or_ligature_change()` using Unicode NFD
decomposition. When the only difference between PG and scan forms is diacritics
or ligatures, threshold lowered from ≥2 to ≥1 occurrence.

**Non-diacritic changes** (letter substitutions like `breath→breadth`) keep
the existing ≥2 threshold.

**File:** `src/gerrata/checker/global_replacements.py`
**Tests:** 11 new in `tests/test_global_replacements.py::TestDiacriticLigatureHeuristic`

### 3. Underscore stripping in report context

**Problem:** PG italic markup `_officier superieur_` appeared literally in
email report context sentences, confusing downstream errata processing.

**Fix:** `_extract_sentence()` now strips underscores after extracting the
context sentence.

**File:** `src/gerrata/reporter/generator.py` — `_extract_sentence()`
**Tests:** 1 new in `tests/test_reporter.py`

### 4. Recursive sub-page alignment

**Problem:** The diff checker compared entire page-long passages in one flat
SequenceMatcher pass. Alignment drift within a page produced cascading false
positives.

**Fix:** Find matching blocks (≥5 tokens) between PG and scan text per page.
Diff only the gaps between anchors. Skip gaps where both sides are highly
dissimilar (ratio < 0.3, length > 8) — these are alignment drift, not real
errors.

**Result:** 1,293 → 962 candidates (-26%). Filtered candidates were
predominantly EXTRA_WORD (-159) and MISSING_WORD (-104).

**File:** `src/gerrata/checker/text_diff.py` — `_check_with_recursive_anchors()`, `_diff_gap()`
**Flag:** `use_recursive=True` on `check_all_alignments()`

### 5. Chapter-stitched continuous diff

**Problem:** Page boundaries are physical artifacts, not meaningful text
boundaries. Sentences split across pages, words get hyphenated, and the diff
checker sees truncated passages on both sides.

**Fix:** Concatenate adjacent scan page transcriptions into continuous
segments (53 segments for 554 alignments). Diff each segment against its
PG text span as a unit. Page attribution preserved via `PageMap` that maps
character offsets back to source pages.

**Critical implementation detail:** `stitch_scan_pages()` accepts a
`normalizer` parameter. Passing `normalize_for_diff` ensures the PageMap
offsets align with the normalized text that tokens come from. Without this,
`build_token_page_map()` fails to locate tokens (normalized forms differ from
raw text) and page attribution collapses.

**Page attribution:** Tighter segment grouping (page_gap ≤ 1, pg_gap < 500)
creates manageable segments. Token position lookup via `token_pages` array
remaps each error to its source page. 201 of 215 pages have ≤8 errors;
the 14 outlier pages have genuine high-density character differences.

**Result:** 1,293 → 635 candidates (-51%). EXTRA_WORD and MISSING_WORD
nearly eliminated. Page distribution realistic (median 1/page).

**Files:**
- `src/gerrata/checker/stitch.py` — `PageMap`, `stitch_scan_pages()`, `build_token_page_map()`
- `src/gerrata/checker/text_diff.py` — `check_stitched()`, `_diff_stitched_gap()`
- `tests/saxo_baseline.py` — benchmark harness with `--compare` mode

---

## Test Fixture

Cached Saxo PG#1150 intermediates in `tests/fixtures/saxo/`:
- `03_alignments.json` — 554 page-to-PG alignments
- `03_scan_pages.json` — 648 scan page transcriptions
- `04_candidates_raw.json` — 1,293 original pipeline candidates
- `06_verified_errors.json` — 322 vision-verified errors
- `pg_1150.txt` — PG source text

Benchmark harness: `python3 tests/saxo_baseline.py --compare` runs all three
modes (flat, recursive, stitched) and prints comparison tables.

---

## All Session Commits

1. `81250ae` — Diacritic/ligature global replacement heuristic
2. `3b19881` — Strip underscores from report context sentences
3. `b553f79` — normalize_for_matching strips all PG formatting artifacts
4. `3f3da60` — Benchmark results document
5. `50e6cce` — Chapter-stitched diff mode (initial)
6. `8b7469b` — Page attribution fix (normalized stitch + tighter segments)

All 669 tests pass. Default pipeline path unchanged — all new modes behind flags.
