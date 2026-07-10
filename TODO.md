# Gerrata TODO — 2026-07-10

## 1. Missing Content: Wire In All 3 Detection Types

**Problem:** The MISSING CONTENT section only shows results from `detect_scan_gaps` (whole unaligned pages). The other two detection methods exist in the codebase but aren't wired into the pipeline.

**Three types of missing content:**
1. **Scan gap** (whole pages) — `detect_scan_gaps()` — WIRED IN ✓
2. **Content hole** (gaps within aligned passages) — `detect_content_holes()` — NOT WIRED ✗
3. **Missing/extra word** (individual token diffs) — from `check_stitched()` — produced but heavily filtered

**Fix:**
- Call `detect_content_holes()` in the CLI after `detect_scan_gaps()` (step 5a)
- Merge results into the MISSING CONTENT section
- Ensure missing_word/extra_word candidates that survive filtering appear in the errata section (they already do)

**Status:** TODO

---

## 2. PUNCTUATION DIFFERENCES Section Nearly Empty

**Problem:** Raw candidates contain 452 PUNCTUATION_DIFF entries, but only 5 survive into the filtered report. The false positive filter (`rules.py` line 194) strips punctuation, compares word-only versions, and if they match, marks them as false positives with reason "Only punctuation/whitespace difference" — deleting them before they reach the reporter.

**Root cause:** The PUNCTUATION_DIFF category was added to `_categorize_replacement()` in `text_diff.py`, and the reporter has a section for it, but the false positive filter doesn't know that PUNCTUATION_DIFF should be RETAINED (not filtered). The filter rule `pg_words == scan_words` catches exactly the same cases.

**Fix:**
- In `rules.py`, add a check: if `error.category == ErrorCategory.PUNCTUATION_DIFF`, do NOT filter as false positive
- The reporter already skips PUNCTUATION_DIFF in the main errata section and puts them in their own section
- Verify: after fix, the ~447 filtered-out punctuation diffs should appear in the report section

**Impact:** The false positive filter's "Only punctuation/whitespace" rule becomes a no-op for pre-classified PUNCTUATION_DIFF candidates. The rule can still fire for other categories where punctuation-only differences indicate alignment noise.

**Status:** TODO

---

## 3. Dictionary-Based Errata Ordering

**Problem:** Errata should be ordered by whether the replacement word is found in a dictionary. Old English needs coverage too, so we need either a comprehensive dictionary or two dictionaries.

**Requirements:**
- For **singular errata**: entries where the replacement word IS in the dictionary come first (higher confidence). Entries where it ISN'T go in a separate flagged section below.
- For **global replacements**: two alphabetical groups — dictionary-validated first, then flagged/uncertain.
- Accompanying individual findings for each global replacement group follow after their respective group.

**Dictionary options to investigate:**
- **NLTK words** (`nltk.corpus.words`) — ~236K English words, modern
- **PyEnchant** — wraps various dictionaries, supports en_US/en_GB
- **Old English dictionary** — Bosworth-Toller API or export, or a wordlist file
- **aspell** — has en_US, en_GB, and can potentially handle archaic forms
- ** Combined approach:** Modern English dictionary + Old English wordlist

**Implementation plan:**
1. Research dictionary options (speed, coverage, archaic/old English support)
2. Build a `DictionaryChecker` class with fast lookup
3. Add `dictionary_validated` flag to candidates during step 6c (additional filtering)
4. Update reporter:
   - Singular errata: dictionary-validated first, then flagged section
   - Global replacements: two alphabetical groups + their findings

**Status:** TODO — needs dictionary research first
