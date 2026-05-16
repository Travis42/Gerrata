# Alignment Diagnosis: Scarlet Letter (PG #25344)

**Date:** 2026-05-16
**Scan:** scarletletter00hawt (1878 Osgood edition)
**Model:** OpenRouter Gemma 4 31B paid

## Symptom

The errata email has correct PG text snippets but wrong scan page references and garbled corrections. Example: "inmost ⇒ innermost" attributed to scan page 19, but the actual "inmost Me behind its veil" text is on scan page 20's content, while scan page 19 starts the Custom-House intro.

## Root Cause: Systematic 1-2 Page Offset

The RETAS alignment produces **macroscopically correct** progressions (pg_start increases linearly with scan_page at ~1378 chars/page) but has a **systematic 1-2 page offset** throughout the book. This means:

1. Every errata entry cites the wrong scan page URL
2. The diff engine compares PG text from position X against vision transcription from page X+1 or X+2
3. The "corrections" are artifacts of mismatched comparison — comparing text from one page against a completely different page's transcription

### Evidence

| Text landmark | PG position | Aligned scan page | Actual scan page | Offset |
|---|---|---|---|---|
| "circumstances that lie around" | pg[8083] | 21 | 20 | +1 |
| "inmost Me behind its veil" | pg[8103] | 21 | not found in any vision | N/A |
| "naughty baggage" | pg[100172] | not covered | — | N/A |
| "I. THE PRISON-DOOR" | pg[2649] | not covered | — | N/A |

### Why This Book Is Worse Than Others

1. **Front matter asymmetry:** The scan has ~10 pages of front matter (copyright, preface, TOC, illustrations list, blank/ghost pages 17-18) before the Custom-House intro. The PG text has all this content but in a different layout (flat paragraphs, no page breaks). The `find_body_start()` returns offset 507 (the preface), but the actual Custom-House prose doesn't start until pg[6138].

2. **Missing pages:** Pages 17 (blank) and 18 (ghost/bleed-through) have no transcriptions. This creates a gap in the sequential tracker right at the start of the Custom-House intro.

3. **TOC anchoring pollution:** The PG text has "THE CUSTOM-HOUSE" at position 4207 (as a TOC entry with page numbers), 5977, and 6060 (running headers/body). The scan page 19 starts with "THE CUSTOM-HOUSE" as a chapter title. While RETAS correctly filters non-unique words, the TOC-to-body transition creates ambiguity in anchor selection.

4. **The interpolation overshoot:** Page 19's vision transcription includes both the title and opening paragraphs. The unique anchors ("inexcusably", "indulgent", "deserts", "button") land at pg[6448-6770], which is correct for the body text. But the direct interpolation estimates pg_start at 5361 — overshooting backwards into the illustration list (pg[5000-6000]). This sets the tracker's initial position ~1000 chars too early.

5. **Drift compounds from the start:** Once the tracker starts ~1 page early, every subsequent page's search window is shifted, and the re-anchor recovery mechanism (every 10 pages) confirms the wrong position because the unconstrained RETAS also anchors to the shifted region.

## Historical Context

This is a **recurrent pattern**, not a new bug:

- **Moby Dick (2026-05-12):** 83% misaligned (2 aligned, 10 misaligned) in the first report. Fixed with `find_body_start()` to skip TOC. Still 50% misaligned after chapter-constrained alignment.
- **Hegel (2026-05-15):** Tracker lost the thread at page 161 (blank JP2). ~425 pages got no alignment. Fixed with RETAS-only recovery mechanisms.
- **Pride & Prejudice (2026-05-10):** Worked — but that's a simpler text with fewer front-matter issues.

The pattern: **whenever there's front-matter asymmetry between scan and PG text, the tracker drifts and never fully recovers.** The re-anchor mechanism verifies against RETAS, which has the same anchoring biases.

## The Deeper Problem

The alignment code has **no ground truth validation**. Every step trusts the LLM transcription and the RETAS anchors without independent verification. There's no "does scan page N actually contain the text at pg[X]?" check. The confidence is computed from SequenceMatcher ratio and is always clamped to 1.0.

The recovery mechanisms (position consistency, scoring regression, periodic re-anchor) are all **self-referential** — they compare the tracker's estimate against RETAS, which uses the same flawed anchoring. There's no external signal.

## Possible Fixes

### Short-term: Post-alignment validation

After alignment, spot-check a random sample of 10-20 pages by comparing the first sentence of the vision transcription against the PG text at the aligned position. If >50% fail, reject the entire alignment and try different parameters.

### Medium-term: Anchor verification

When RETAS finds an anchor word at position X in PG, verify by checking that the surrounding 50 chars of PG text match the surrounding text of the anchor word in the transcription. This catches anchors that land on homographs or coincidental matches.

### Long-term: Dual-source alignment

Use both the vision transcription AND the OCR text (from IA's `_ocr.txt` file) as independent alignment sources. If they agree on the page position, trust it. If they disagree, flag for manual review. The OCR is noisy but available as a structural signal (page breaks, paragraph breaks) that the vision transcription doesn't preserve.

### Specific to this book: Front-matter skip

The `find_body_start()` should skip past ALL front matter including TOC entries, illustration lists, and prefaces, not just to the first prose paragraph. For the Scarlet Letter, this means skipping to pg[6138] (the Custom-House prose) rather than pg[507] (the preface).
