# Gerrata TODO — 2026-07-10

## Completed

### ✅ 1. Missing Content: Wire In All 3 Detection Types
- `detect_content_holes()` wired into CLI at step 5a
- Saxo: no content holes found, but detection runs

### ✅ 2. PUNCTUATION DIFFERENCES Section Nearly Empty
- Fixed false positive filter in `rules.py` — PUNCTUATION_DIFF candidates no longer deleted
- Report now shows ~300 punctuation entries instead of ~5

### ✅ 3. Dictionary-Based Errata Ordering
- DictionaryChecker: NLTK words + system dict (~301K words)
- Global replacements split validated/flagged, both alphabetical
- Singular errata split validated/flagged
- Global replacement instances follow same split

### ✅ 4. Diacritic/Ligature Auto-Validation
- Words with diacritics/ligatures skip dictionary, auto-validated

---

## In Progress

### 5. Promote Singular Diacritic/Ligature Errata to Global Replacements

**Requirement:** Singular errata that are diacritic/ligature additions should be promoted to the global replacements list, since they're almost certainly systematic.

**Plan:**
- In the reporter or CLI step 8a, after detecting global replacements, scan remaining singular errata for diacritic/ligature entries
- Check if the pg_word appears multiple times in the PG text (even if only 1 was caught by diff)
- If the scan has a diacritic version and the word appears N+ times in PG, add as a global replacement
- Use the existing diacritic detection from DictionaryChecker

**Status:** TODO

---

### 6. Numbers Should Not Be Flagged

**Requirement:** If the replacement word is purely numeric (or numbers with punctuation), it should not be flagged.

**Fix:** In `DictionaryChecker.validate_replacement()`, add a check: if the cleaned word matches `^\d+([.,]\d+)*$` or similar, return True (auto-validate).

**Status:** TODO

---

### 7. Fix Punctuation Contamination in Dictionary Lookup

**Problem:** `validate_replacement()` receives scan_text with trailing punctuation (e.g., `doors?`, `boot.`, `grain,`). These fail dictionary lookup because `doors?` ≠ `doors`.

**Root cause:** The function strips HTML markup but not trailing punctuation before lookup.

**Fix:** Strip leading/trailing punctuation in `validate_replacement()` before dictionary lookup. Keep internal punctuation (hyphens, apostrophes).

**Status:** TODO

---

### 8. Handle Inflected Forms (plurals, -ing, -ed)

**Problem:** Words like `harbouring` fail lookup even though `harbour` is in the dictionary. UK spellings and inflected forms need handling.

**Fix:** In `is_in_dictionary()`, try stripping common English inflections:
- `-ing` → check base (`harbouring` → `harbour`)
- `-ed` → check base (`handed` → `hand`)
- `-s`/`-es` → check singular (`doors` → `door`)
- `-ly` → check base
- `-er`/`-est` → check base

**Status:** TODO

---

### 9. Supplemental Dictionary File (Human-Editable)

**Requirement:** A human-readable/editable file where users can add words to the dictionary. Always in the same location, documented as a feature.

**Plan:**
- File: `/root/projects/gerrata/dictionary_supplement.txt` (one word per line)
- Loaded by `DictionaryChecker` at init, merged into word_set
- Documented in README and DOCS.md
- Support comments (lines starting with `#`)

**Status:** TODO
