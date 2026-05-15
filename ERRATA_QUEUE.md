# Gerrata Errata Queue — PG Top Downloads

Updated: 2026-05-15

## Provenance Policy
After PG rejected our Moby Dick errata (wrong edition), we now require **verified source edition**
before running the pipeline. Only books where the PG text explicitly states its source edition
are eligible. See `research/2026-05-15-pg-feedback-lessons.md` for the full rejection analysis.

## Tier 1 — Ready to Run (verified edition + scan needed)

### PG #58169 — Hegel Lectures Vol 3 (1896 Kegan Paul)
- **PG Source:** Kegan Paul, Trench, Trubner & Co., 1896. Trans. Haldane & Simson
- **Scan needed:** [dli.bengal.10689.2429](https://archive.org/details/dli.bengal.10689.2429)
- **Status:** Previous run used WRONG scan (in.ernet.dli.2015.190961) and OpenRouter 400 errors.
  Starting fresh with correct scan.
- **Note:** DLI scan — may need manual download if IA blocks bulk fetch.

### PG #42324 — Frankenstein (1831 Bentley)
- **PG Source:** Photo-reprint of 1831 Henry Colburn & Richard Bentley, London (Standard Novels)
- **Scan:** [frankenstein00shel](https://archive.org/details/frankenstein00shel) — JP2 ✅
- **Match:** Exact — 1831 Colburn & Bentley

### PG #41445 — Frankenstein (1818 first edition, 3 vols)
- **PG Source:** "Photo-reprint of the 1818 edition" — Lackington, Hughes, Harding, Mavor & Jones
- **Scan (Vol I):** [maryshelleyfrankenstein1](https://archive.org/details/maryshelleyfrankenstein1)
- **Scan (Vol II):** [shelleyfrankenstein02](https://archive.org/details/shelleyfrankenstein02)
- **Scan (Vol III):** needs lookup on IA
- **Match:** Exact 1818 first edition. **Note:** PG has TWO Frankenstein texts — #84 (unknown edition),
  #42324 (1831 Bentley), #41445 (1818 first). Use #41445 for the 1818 text.

### PG #84 — Frankenstein (inferred 1818 first edition)
- **PG Source:** Not stated, but header lists "published 1818" and text matches 1818 patterns
- **Scan (Vol I):** [maryshelleyfrankenstein1](https://archive.org/details/maryshelleyfrankenstein1)
- **Scan (Vol II):** [shelleyfrankenstein02](https://archive.org/details/shelleyfrankenstein02)
- **Match:** INFERRED — likely 1818 edition based on PG header. Less certain than #41445.
- **Note:** This is the popular PG Frankenstein. If #84 text matches #41445, they're the same work.

### PG #25344 — The Scarlet Letter (1878 Osgood)
- **PG Source:** J.R. Osgood & Co., Boston, 1878. Illustrated by Mary Hallock Foote
- **Scan:** [scarletletter00hawt](https://archive.org/details/scarletletter00hawt) — JP2 ✅
- **Match:** Exact — Library of Congress scan

### PG #1342 — Pride and Prejudice (1894 George Allen)
- **PG Source:** George Allen, Ruskin House, 1894. Illustrated by Hugh Thomson
- **Scan:** [prideprejudice00aust](https://archive.org/details/prideprejudice00aust) — JP2 ✅
- **Match:** Exact — NYPL scan

### PG #42486 — The Turn of the Screw (1898 Macmillan)
- **PG Source:** The Macmillan Company, 1898. From "The Two Magics"
- **Scan:** [twomagicsturnofs00jameiala](https://archive.org/details/twomagicsturnofs00jameiala) — JP2 ✅
- **Match:** Exact — 1898 Macmillan

## Tier 2 — Needs Attention

### PG #1260 — Jane Eyre (1897 Service & Paton) — NO JP2
- **PG Source:** Service & Paton, 1897. Ill. F. H. Townsend
- **Scan:** [janeeyreanautob03brongoog](https://archive.org/details/janeeyreanautob03brongoog) — JP2 ❌
- **Issue:** Google Books scan — only DAISY/EPUB, no raw page images.

### PG #1400 — Great Expectations (1867?) — PARTIAL MATCH
- **PG Source:** "[1867 Edition]" Chapman & Hall
- **Scan:** [greatexpectation03dick](https://archive.org/details/greatexpectation03dick) — JP2 ✅
- **Issue:** Best scan is 1861 first edition (3 vols), not 1867. Low risk — Dickens' revisions
  between 1861/1867 were mostly preface additions.

### PG #67979 — Blue Castle (1926 Stokes) — NO SCAN FOUND
- **PG Source:** Frederick A. Stokes Company, 1926
- **Issue:** No Stokes 1926 on IA. Only A.L. Burt reprint and McClelland & Stewart Canadian.

## Tier 3 — Skipped (unverified source)

| PG # | Book | Reason |
|------|------|--------|
| 2701 | Moby Dick | "Combination of etexts", unknown source |
| 1184 | Count of Monte Cristo | Unknown translation, unknown edition |
| 2554 | Crime and Punishment | Garnett translation, unknown print edition |
| 28054 | Brothers Karamazov | Garnett translation, unknown print edition |
| 11 | Alice in Wonderland | Millennium Fulcrum 1991 digital edition |
| 16 | Peter Pan | Millennium Fulcrum 1991 digital edition |
| 1513 | Romeo and Juliet | Unknown edition |
| 1661 | Sherlock Holmes | Unknown edition |
| 35 | Time Machine | Unknown edition |
| 36 | War of the Worlds | Unknown edition |
| 2641 | Room with a View | Unknown edition |
| 76 | Huckleberry Finn | Unknown (US vs UK first edition) |
| 408 | Souls of Black Folk | Pre-1995, unknown edition |
| 1080 | A Modest Proposal | Unknown edition |

## Previously Processed

| PG # | Book | Status | Notes |
|------|------|--------|-------|
| 58169 | Hegel Vol 3 | ⚠️ Needs redo | Wrong scan + OpenRouter 400 errors. 444 candidates, 0 verified |
| 2701 | Moby Dick | ❌ Rejected by PG | Wrong edition |
| 1342 | Pride and Prejudice | ✅ Done | 83 candidates |

## Already Done via OCR Queue (low confidence, no LLM verification)

| PG # | Book | Candidates | Notes |
|------|------|-----------|-------|
| 84 | Frankenstein | ~883 | Wrong edition scan used |
| 1184 | Count of Monte Cristo | ~1865 | Unknown translation |
| 2554 | Crime and Punishment | queued | |
| 2641 | Room with a View | queued | |
| 28054 | Brothers Karamazov | queued | |
| 42486 | Turn of the Screw | queued | |
| 67979 | Blue Castle | queued | |

See `gerrata-queue.log` for full OCR-only queue output.
