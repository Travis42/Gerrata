# Spec: Post-Alignment Validation and Offset Correction

**Date:** 2026-05-16
**Component:** `gerrata.aligner.vision_aligner`
**Priority:** High — fixes systematic alignment drift across all books

## Problem

The RETAS alignment produces macroscopically correct progressions (pg_start increases
linearly with scan_page) but has a **systematic offset** of 1-2 pages (typically 1000-3000
chars). This has been observed on Moby Dick (83% misaligned), Hegel (tracker lost at blank
page 161), and Scarlet Letter (consistent ~1 page offset). The offset causes:

1. Wrong scan page URLs in errata reports
2. Diff engine compares PG text from position X against page X+1's transcription
3. Garbled corrections from mismatched comparison

## Root Cause

Front-matter asymmetry between scan (paginated) and PG text (flat) causes the tracker
to start at a slightly wrong position. The re-anchor recovery is self-referential — it
verifies against unconstrained RETAS, which uses the same anchoring that caused the drift.

## Solution: Validation + Correction (no additional LLM calls)

### Overview

After `align_all_pages()` produces alignments, run a **validation pass** that:

1. Samples aligned pages
2. For each, extracts a distinctive phrase from the vision transcription
3. Searches for that phrase in PG text
4. Measures the offset between found position and aligned position
5. If offset is consistent → shift all alignments
6. If offset is inconsistent → re-score with wider windows and drop worst pages

**No LLM calls are needed.** All validation uses cached transcriptions and the PG text
already in memory.

### API

```python
@dataclass
class ValidationResult:
    """Result of post-alignment validation."""
    offset_mean: float          # Mean offset in chars (positive = PG position too high)
    offset_stddev: float        # Standard deviation of offsets
    sample_size: int            # Number of pages sampled
    pages_correct: int          # Pages where offset < 500 chars
    pages_incorrect: int        # Pages where offset >= 500 chars
    corrected: bool             # Whether correction was applied
    dropped: int                # Pages dropped during re-scoring (if applicable)
    verdict: str                # "ok" | "corrected" | "rescored" | "failed"

class VisionAligner:
    def validate_and_correct(
        self,
        alignments: list[Alignment],
        transcriptions: list[PageTranscription],
        pg_text: str,
        sample_size: int = 20,
        offset_tolerance: int = 500,    # Max acceptable per-page offset
    ) -> tuple[list[Alignment], ValidationResult]:
        ...
```

### Algorithm

#### Step 1: Validation Sampling

Select up to `sample_size` aligned pages for validation. Selection strategy:

1. Sort alignments by scan_page
2. Pick every Nth page to spread samples across the book (N = len(alignments) / sample_size)
3. Skip pages with very short transcriptions (<50 chars cleaned)
4. Prefer pages with higher confidence scores

This gives us a representative sample without checking every page.

#### Step 2: Per-Sample Offset Measurement

For each sampled page:

1. Get the vision transcription for the page
2. Extract the first "distinctive phrase": a sentence fragment of 30-80 chars from the
   cleaned transcription (after paratext stripping), starting from position 30-50 chars
   in (to skip chapter titles/running headers)
3. Normalize the phrase: lowercase, strip punctuation, collapse whitespace
4. Search for this phrase in the normalized PG text using `str.find()`
5. If found: `page_offset = found_pg_pos - alignment.pg_start`
6. If not found: try shorter substrings (first 20 chars, then 15) — if still not found,
   skip this sample (page likely has unique text not in PG, e.g., illustration caption)

#### Step 3: Offset Analysis

Compute statistics from all measured offsets:

```python
offset_mean = mean(page_offsets)
offset_stddev = stdev(page_offsets)
pages_correct = count(|offset| < offset_tolerance)
```

#### Step 4: Decision

```
IF pages_correct / sample_size >= 0.8:
    verdict = "ok"          # Alignments are fine, no correction needed
    RETURN (alignments, result)

ELIF offset_stddev < 500:   # Consistent shift
    verdict = "corrected"
    # Shift all alignments by -offset_mean
    for alignment in alignments:
        alignment.pg_start -= round(offset_mean)
        alignment.pg_end -= round(offset_mean)
        alignment.pg_start = max(0, alignment.pg_start)
        alignment.pg_end = max(alignment.pg_start, alignment.pg_end)
    RETURN (corrected_alignments, result)

ELIF offset_stddev < 1500:  # Somewhat noisy shift
    verdict = "rescored"
    # Apply mean shift AND drop bottom 20% by confidence
    # Then re-run RETAS with wider search windows for dropped pages
    # (using cached transcriptions — no LLM calls)
    RETURN (corrected_alignments, result)

ELSE:                        # Random errors, not a shift
    verdict = "failed"
    # Log detailed diagnostics, return unmodified
    # Let downstream filtering handle it (or flag for human review)
    RETURN (alignments, result)
```

### Integration Point

In `cli.py`, after `align_all_pages()` produces alignments and before the text diff step:

```python
# Step 4a: Validate and correct alignment offset
alignments, validation = vision_aligner.validate_and_correct(
    alignments=alignments,
    transcriptions=transcriptions,
    pg_text=parsed.body_text,
)
console.print(f"  Validation: {validation.verdict}")
if validation.corrected:
    console.print(f"  Offset corrected: {validation.offset_mean:+.0f} chars (σ={validation.offset_stddev:.0f})")
    console.print(f"  Accuracy: {validation.pages_correct}/{validation.sample_size} samples")
elif validation.verdict == "rescored":
    console.print(f"  Re-scored: offset={validation.offset_mean:+.0f}±{validation.offset_stddev:.0f}, dropped {validation.dropped}")
elif validation.verdict == "failed":
    console.print(f"  [yellow]Alignment validation failed — offset too inconsistent (σ={validation.offset_stddev:.0f})[/yellow]")
```

### Edge Cases

1. **Very short transcriptions:** Pages with <50 chars of cleaned text can't produce a
   distinctive phrase. Skip these in sampling.

2. **Front matter pages:** TOC entries, illustration lists, etc. may have text that appears
   multiple times in PG. The "first sentence from position 30+" heuristic avoids chapter
   titles. If the extracted phrase appears at multiple PG positions, pick the one closest
   to the aligned position.

3. **Illustration-only pages:** Vision transcription may describe an image rather than
   transcribe text. These won't match PG text. Skip them.

4. **Books with no valid samples:** If <5 samples produce valid measurements, skip
   validation entirely (verdict="ok" by default). This handles very short books or
   books where most transcriptions are illustrations.

5. **Negative pg_start after correction:** Clamp to 0. This may lose the very beginning
   of front matter, which is acceptable since front matter rarely produces errata.

### Tests

Add to `tests/test_vision_aligner.py`:

1. **`test_validation_no_offset`** — Alignments are correct, validation returns "ok"
2. **`test_validation_systematic_shift`** — All alignments are +1500 chars, correction
   shifts them back
3. **`test_validation_partial_shift`** — 70% correct, 30% shifted → "corrected" with mean
4. **`test_validation_inconsistent`** — Random offsets, high stddev → "failed"
5. **`test_validation_short_transcriptions`** — Most pages have <50 chars, validation
   skips gracefully
6. **`test_validation_empty_alignments`** — No alignments → no crash, returns "ok"
7. **`test_phrase_extraction`** — Verifies distinctive phrase selection skips titles,
   works with various transcription formats
8. **`test_offset_clamping`** — Negative pg_start after correction is clamped to 0

### Performance

- Validation pass: ~20 `str.find()` calls on a ~500K string. Sub-second.
- No network calls, no LLM inference, no file I/O beyond what's already in memory.
- Negligible cost compared to the transcription step (which is the bottleneck).

### Success Criteria

On the Scarlet Letter run:
- Validation should detect ~1500 char offset (σ < 500)
- After correction, spot-checking 10 random pages should show text agreement
- Errata email should have correct page references

On Moby Dick (if re-run):
- Validation should detect the front-matter offset
- After correction, misalignment rate should drop from 83% to <20%

On Pride & Prejudice (already working):
- Validation should return "ok" with no correction needed
