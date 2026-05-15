# Gerrata Refactor Spec

**Date:** 2026-05-13
**Scope:** Code quality, deduplication, structural cleanup
**Constraint:** No behavioral changes — all existing tests must pass before and after each item.

---

## Goals

1. Eliminate code duplication (the biggest source of bugs we hit today)
2. Break up god-files into maintainable pieces
3. Unify the two parallel classification systems (category vs verdict)
4. Remove dead code and stale patterns

---

## Item 1: Extract Shared Filters

**Problem:** Six false-positive filter functions are duplicated between `cli.py` (closures inside `run_pipeline()`, lines 603-829) and `reporter/generator.py` (methods on `ReportGenerator`, lines 706-800). Different implementations, maintained in two places. `checker/rules.py` is a third partial copy.

**TODO:**
- [ ] Create `src/gerrata/checker/filters.py` with module-level filter functions
- [ ] Functions to extract: `is_cutoff_artifact`, `is_long_mismatch`, `is_html_artifact`, `is_all_caps_header`, `is_suffix_fragment`, `is_quoted_fragment`, `is_absent_entry`, `is_punctuation_only`
- [ ] Each function is a pure `fn(scan_text: str, pg_text: str) -> bool` — no class dependency
- [ ] Update `cli.py` to import from `checker/filters.py`, remove inline closures
- [ ] Update `reporter/generator.py` to import from `checker/filters.py`, remove `_is_*` methods
- [ ] Update `checker/rules.py` `FalsePositiveFilter` to delegate to shared functions
- [ ] All 354 tests pass unchanged

**Files affected:**
- `src/gerrata/checker/filters.py` (new)
- `src/gerrata/cli.py` (remove ~250 lines)
- `src/gerrata/reporter/generator.py` (remove ~100 lines)
- `src/gerrata/checker/rules.py` (simplify)

**Lines removed:** ~350

---

## Item 2: Unify Category/Verdict Classification

**Problem:** `CandidateError.category` (set during diff detection in `checker/text_diff.py`) and `Error.verdict` (set during verification in `verifier/vision.py`) classify errors independently. The email reporter filters on `category`, not `verdict`. When the transcription-witness refactor changed `verdict` to `scan_correct`, the email still dropped them because `category` was still `edition_variant`. We had to manually edit the JSON to work around this.

**TODO:**
- [ ] Define a clear contract: `category` is set by the diff checker (mechanical classification of what kind of diff), `verdict` is set by the verifier (which version is correct)
- [ ] In `_derive_verdict_from_transcription()`, also update `category` when promoting to `scan_correct` — if category is `edition_variant` or `modernization` but the transcription proves the scan differs, reclassify category to `ocr_scanno`
- [ ] Alternatively: make the email reporter filter on `verdict` instead of `category`, and use `category` only for display/classification. This is the cleaner long-term fix but requires updating the email template.
- [ ] Add a test: a candidate with `category=edition_variant` and `verdict=scan_correct` (confidence >= 0.85) appears in the email output
- [ ] All tests pass

**Files affected:**
- `src/gerrata/verifier/vision.py` (update `_derive_verdict_from_transcription` or its caller)
- `src/gerrata/reporter/generator.py` (update `generate_errata_email` filter logic)
- `tests/test_verifier.py` (add category-promotion test)

---

## Item 3: Split `vision_aligner.py`

**Problem:** 2329 lines, 6 classes, 57 methods. `align_all_pages()` is a 280-line monolith.

**TODO:**
- [ ] Create `src/gerrata/aligner/tracker.py` — move `SequentialTracker` and constants (`REANCHOR_INTERVAL`, `CHAPTER_THRESHOLD`, `SEQUENTIAL_THRESHOLD`, `GLOBAL_THRESHOLD`) here
- [ ] Create `src/gerrata/aligner/transcriber.py` — move `VisionTranscriber`, `PageTranscription`, `strip_paratext()`, `TRANSCRIPTION_PROMPT`, `MODEL_NAME_MAP`, `find_body_start()` here
- [ ] Keep `vision_aligner.py` with only `VisionAligner`, `VisionAlignmentResult`, and the alignment logic (`align_transcription_to_pg`, `_find_unique_word_anchor`, etc.)
- [ ] Update imports in `cli.py`
- [ ] Remove stale TODO comment at line 2329
- [ ] All tests pass

**Files affected:**
- `src/gerrata/aligner/tracker.py` (new, ~200 lines)
- `src/gerrata/aligner/transcriber.py` (new, ~500 lines)
- `src/gerrata/aligner/vision_aligner.py` (reduced to ~1600 lines)

---

## Item 4: Refactor `run_pipeline()` Step Orchestration

**Problem:** `run_pipeline()` in `cli.py` is 660 lines with deeply nested if/elif for resume logic across 8 steps. Hard to read, hard to modify, easy to introduce bugs.

**TODO:**
- [ ] Extract each pipeline step into its own async function: `step_parse_pg()`, `step_get_pages()`, `step_transcribe()`, `step_align()`, `step_diff()`, `step_filter()`, `step_verify()`, `step_report()`
- [ ] Each step function takes a `PipelineState` dataclass (accumulates `parsed`, `transcriptions`, `alignments`, `candidates`, `verified_errors`, `report`)
- [ ] Each step checks `resume_from` and either loads from cache or runs fresh
- [ ] `run_pipeline()` becomes a ~30-line orchestrator that calls steps in sequence
- [ ] All tests pass

**Files affected:**
- `src/gerrata/cli.py` (restructured, similar line count but much flatter)

---

## Item 5: Delete Dead Code

**Problem:** `ReverseSequentialTracker` is defined (70 lines) but never instantiated. The reverse pass using it is commented out (30 lines). Dead code adds maintenance burden and confusion.

**TODO:**
- [ ] Delete `ReverseSequentialTracker` class from `vision_aligner.py` (or wherever it ends up after Item 3)
- [ ] Delete the commented-out reverse pass block (~30 lines) in `align_all_pages()`
- [ ] If the reverse pass is worth keeping as a future feature, commit it in a branch or document the design decision, but remove from main
- [ ] All tests pass

**Files affected:**
- `src/gerrata/aligner/vision_aligner.py` (or `tracker.py` after Item 3)

**Lines removed:** ~100

---

## Item 6: Unify `MODEL_NAME_MAP`

**Problem:** `MODEL_NAME_MAP` exists in two files with different subsets:
- `vision_aligner.py` line 45: 4 entries (glm-4.6v, glm-4.5v, glm-4.6v-flashx, glm-ocr)
- `verifier/vision.py` line 23: 2 entries (glm-ocr, glm-4.6v)

**TODO:**
- [ ] Move `MODEL_NAME_MAP` to `models.py` (single source of truth, all 4 entries)
- [ ] Import from `models.py` in both `vision_aligner.py` and `verifier/vision.py`
- [ ] All tests pass

**Files affected:**
- `src/gerrata/models.py` (add constant)
- `src/gerrata/aligner/vision_aligner.py` (remove, import)
- `src/gerrata/verifier/vision.py` (remove, import)

---

## Item 7: Fix `alignment_confidence` Scope

**Problem:** Line 938 of `cli.py`: `alignment_confidence=alignment_confidence if 'alignment_confidence' in dir() else 0.0`. Using `dir()` to check local variable scope is a code smell. The variable is set in three conditional branches but not all paths set it.

**TODO:**
- [ ] Initialize `alignment_confidence = 0.0` at the top of `run_pipeline()` (before Step 1)
- [ ] Remove the `in dir()` check
- [ ] All tests pass

**Files affected:**
- `src/gerrata/cli.py` (2 lines changed)

---

## Item 8: Add Named Constants for Verdict Thresholds

**Problem:** `_derive_verdict_from_transcription()` uses magic numbers 0.8 and 0.6 for similarity thresholds, while the alignment system uses named constants (`CHAPTER_THRESHOLD`, etc.).

**TODO:**
- [ ] Add to `models.py` or `verifier/vision.py`:
  ```python
  VERDICT_SCAN_THRESHOLD = 0.8    # Above this: scan_correct
  VERDICT_VARIANT_THRESHOLD = 0.6  # Above this: edition_variant
  ```
- [ ] Replace magic numbers in `_derive_verdict_from_transcription()`
- [ ] All tests pass

**Files affected:**
- `src/gerrata/verifier/vision.py` (or `models.py`)

---

## Item 9: Clean Up Imports

**Problem:** `cli.py` imports `CandidateError` and `ErrorCategory` inside loops (lines 651, 825) instead of at the top of the file.

**TODO:**
- [ ] Move all `from gerrata.models import ...` to the top-level imports
- [ ] All tests pass

**Files affected:**
- `src/gerrata/cli.py` (import cleanup)

---

## Execution Order

Items are ordered by dependency and impact:

```
Item 6 (MODEL_NAME_MAP)     — trivial, no dependencies
Item 7 (alignment_confidence) — trivial, no dependencies
Item 8 (verdict constants)  — trivial, no dependencies
Item 9 (imports)            — trivial, no dependencies
    ↓
Item 1 (shared filters)     — medium, unlocks cleaner Items 3/4
Item 2 (category/verdict)   — medium, independent but logically related to Item 8
Item 5 (dead code)          — small, independent
    ↓
Item 3 (split vision_aligner.py) — large, benefits from Items 1/5
Item 4 (refactor cli.py)    — large, benefits from Items 1/3
```

Items 6-9 can be done in a single commit. Items 1-2 are independent of each other. Items 3-4 depend on 1 being done first.

**Estimated total effort:** ~3-4 hours of focused work, with tests run after each item.

**Verification:** After each item, run `python3 -m pytest tests/ -q` and confirm 354 passed, 8 failed (pre-existing). No new failures.
