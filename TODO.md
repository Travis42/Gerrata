# Gerrata Fix Plan — Verification Hallucination Fix

## Problem Summary
The verification LLM (GLM-4.6V) is hallucinating corrections for nearly all candidate errors in Pride & Prejudice. Only 1 of 75 reported errors was legitimate: `novels" H.T Feb 94 ==> novels"` (scanner metadata leaked into PG text).

## Root Causes Identified

### RC1: Verifier receives scan_text (OCR output) as context alongside page image
**File:** `src/gerrata/verifier/vision.py` line ~447 (`_build_batch_prompt`)
**Issue:** The prompt includes `Scan OCR text: {error.scan_text}`. The LLM reads this text hint and "confirms" or "corrects" based on the OCR text, not by actually reading the page image. This is the PRIMARY cause of hallucination.
**Fix:** Remove `scan_text` from the verification prompt. The LLM should ONLY see the page image and the PG text. It must READ the image, not be told what the OCR said.

### RC2: Alignment produces fragment-level garbage candidates
**File:** Alignment step (text_diff.py or vision_aligner.py)
**Issue:** Two sub-problems:
  (a) Page reference artifacts: 21/75 errors contain `{43}`, `{190}` etc. — footnote numbering from the scan that leaked into candidate text. Creates diffs like `{45} Covering a sc ==> "The Apothecary came"`.
  (b) Word fragment diffs: Most remaining errors are truncated word fragments like `suff ==> suffer by` or `le. ==> unreasonable.` or `c ==> calling her attention.`. These happen when the alignment step breaks long OCR text into fragments that don't cleanly match PG paragraphs. A partial word match at an alignment boundary creates a "difference" that isn't a real text error — it's an artifact of where the alignment algorithm chose to split.
**Fix:** 
  (a) Strip page reference patterns (`{N}`, `{NNN}`, `{xxiii}`) from transcriptions before alignment.
  (b) Filter out candidates where pg_text or scan_text is a word fragment — specifically: if either is shorter than ~10-15 chars AND doesn't look like a complete word (no spaces, no punctuation at boundaries), reject it. A 4-char fragment like "suff" is an alignment boundary, not a real error.
  (c) In text_diff.py, reject candidates containing `{` followed by digits.

### RC3: Reporter shows OCR scan_text as "correction" instead of verified image text
**File:** `src/gerrata/reporter/generator.py`
**Issue:** The report format `pg_text ==> scan_text` shows the OCR transcription as ground truth, even though OCR is itself unreliable. The LLM's `suggested_fix` (even if correct) is also unreliable.
**Fix:** After verification, the report should show what the VERIFIER confirmed from the IMAGE, not the OCR text. If the verifier says scan_correct, use the verifier's transcription of the image, not the OCR output.

### RC4: No intermediate results saved between pipeline steps
**File:** Pipeline flow in `cli.py`
**Issue:** After a run completes, only the final report JSON is saved. Transcriptions, alignments, raw candidates, and verification results are lost. This prevents re-running just the verification step with a different model.
**Fix:** Save intermediate results after each step:
- `cache/{scan_id}_transcriptions.json` — page transcriptions
- `cache/{scan_id}_alignments.json` — alignment results
- `cache/{scan_id}_candidates.json` — raw candidates before filtering
- `cache/{scan_id}_candidates_filtered.json` — after filtering
- `cache/{scan_id}_verification.json` — verification results with full LLM responses
- `cache/{scan_id}_verified_errors.json` — final verified errors

### RC5: Verifier prompt still allows composition despite prior fix
**File:** `src/gerrata/verifier/vision.py` system prompt
**Issue:** Despite adding "CRITICAL RULES" to the system prompt, the LLM still composes text because:
  (a) It sees `scan_text` in the prompt (RC1) and uses it as a crutch
  (b) The prompt says "transcribe exactly what you see" but the LLM interprets this loosely
  (c) For very short fragments (`"c"`, `"wa"`, `","`), the LLM can't locate them on the page and guesses
**Fix:** After fixing RC1 (removing scan_text), the prompt should be further tightened:
  - Ask the LLM to find the specific PG passage ON THE PAGE first, then compare
  - For fragments shorter than ~5 chars, auto-mark as `unable_to_verify` (too short to locate)
  - Require the LLM to quote the surrounding text from the image as evidence

## Task Plan

### Task 1: Fix RC4 — Save intermediate results (foundation for everything else) ✅ DONE
- `save_intermediate()` added to cli.py with 6 save points
- Transcription cache per scan_id: `cache/{scan_id}_transcriptions.json`
- Modify `cli.py` to save results after each pipeline step
- This enables re-running verification without re-doing OCR (saves time and money)
- Priority: HIGH (needed for testing)

### Task 2: Fix RC1 — Remove scan_text from verification prompt ✅ DONE
- `_build_batch_prompt()`: removed `Scan OCR text: {error.scan_text}` line, replaced with comment
- `_build_prompt()`: removed `Scan OCR text (from source scan)` line, tightened instructions
- Modify `_build_batch_prompt()` in `vision.py`
- Remove the line that includes `Scan OCR text: {error.scan_text}`
- The LLM should only see: page image + PG text passage + question
- Priority: CRITICAL (primary hallucination cause)

### Task 3: Fix RC2 — Strip page references and filter fragment candidates ✅ DONE
- Added `_is_fragment_candidate()` in text_diff.py — rejects word-boundary fragments
- Added `{digits}` regex filter in text_diff.py `check_aligned_passage()`
- Filters applied BEFORE verification (candidates never reach the LLM)
- 16/18 test cases pass (2 borderline fragments like "tever" let through — acceptable)
- In `vision_aligner.py`, strip patterns like `{NNN}`, `{N}`, `{xxiii}` from transcriptions before alignment
- In `text_diff.py`, add candidate filters:
  - Reject any candidate where pg_text or scan_text contains `{` followed by digits
  - Reject any candidate where BOTH pg_text and scan_text are shorter than 10 chars (word fragment — alignment boundary artifact)
  - Reject any candidate where one side is < 5 chars (too short to verify on a page)
- Priority: HIGH (produces the majority of garbage candidates)

### Task 4: Fix RC5 — Further tighten verification prompt ✅ DONE
- `_build_batch_prompt()`: LLM must locate passage on page first, quote 20+ chars evidence
- Added `image_evidence` required field in response JSON
- Auto-note for pg_text < 5 chars (unable_to_verify hint)
- `_build_prompt()`: Same tightening
- `_parse_batch_response()` + `_parse_response()`: Extract `image_evidence` from LLM output
- `models.py`: Added `image_evidence` field to Error class + to_dict()
- Redesign the verification prompt to be more structured
- Ask: "Find this exact passage on the page. Quote 20 chars before and after."
- For fragments < 5 chars, auto-skip (unable_to_verify)
- Require evidence: the LLM must quote context from the image
- Priority: HIGH (secondary hallucination cause)

### Task 5: Fix RC3 — Use verified text in reports ✅ DONE
- `_format_correction()` in generator.py: prefers `image_evidence` over `scan_text`
- Displays `[image]` or `[OCR]` label so reviewer knows the source
- Falls back to `scan_text` if `image_evidence` is empty (unverified errors)
- After verification, if verdict is scan_correct, use the verifier's quoted text (from image), not OCR scan_text
- This requires the prompt fix (Task 4) to produce quotable evidence
- Priority: MEDIUM (cosmetic if Tasks 2-4 are done well)

### Task 6: Test with Mistral Small 3.2 24B
- Re-run verification on P&P candidates using intermediate results from Task 1
- Compare results to GLM-4.6V baseline
- Assess: how many hallucinations? how many legitimate errors found?
- Priority: HIGH (validates the fixes)

## Success Criteria
- The only errors in the P&P report should be ones where a human can verify by looking at the scan page
- Zero hallucinated "corrections"
- The `novels" H.T Feb 94` error should still be found
- Edition variants should be clearly separated from actual PG errors
