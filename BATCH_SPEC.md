# Gerrata Batch Processing Spec

## Goal
Build an automated pipeline to batch-process PG Top 100 books through Gerrata, producing errata reports with minimal manual intervention.

## Constraints
- **Disk:** 8.5GB free. Each book ≈ 200MB JP2 + 300-500MB PNGs. Can fit ~10 books at a time.
- **API costs:** Gemini Flash Lite via OpenRouter — cheap but not free. ~412 pages × ~$0.0003/page ≈ $0.12/book.
- **Most books will have zero real errata** — the value is in confirming this efficiently.
- **Edition matching is critical** — PG rejected our Moby Dick report because we used the wrong scan edition.

## Pipeline per Book

1. **Fetch PG text** — Download from Gutenberg, parse, extract edition info (transcriber notes, front/back matter)
2. **Find matching IA scan** — Search IA for the edition mentioned in PG text
3. **Download JP2 zip** — From IA (confirmed working from this server)
4. **Extract + convert** — JP2 → PNG via OpenCV
5. **Run Gerrata pipeline** — Transcribe → Align → Diff → Verify → Report
6. **Evaluate results** — Count email entries, flag plausible real errata
7. **Clean up** — Remove PNGs, keep only reports + cached transcriptions

## Batch Orchestration

```python
# config: list of books to process
QUEUE = [
    {"pg_id": 1184, "ia_id": "lecomtedemontecr01dumouoft", "title": "Count of Monte Cristo"},
    ...
]
```

For each book in queue:
1. Check if already processed (report exists)
2. Check disk space (abort if < 2GB free)
3. Run pipeline
4. Save results summary
5. Clean up large files
6. Move to next

## Cleanup Strategy

After each book:
- Delete `cache/<ia_id>/*.png` (page images — largest files)
- Delete `cache/<ia_id>_jp2.zip` (raw scan archive — very large)
- Delete `cache/<ia_id>/03_scan_pages.json`, `04_candidates_raw.json`, `05_candidates_filtered.json`
- Keep: `cache/<ia_id>/01_pg_parsed.json`, `02_transcriptions.json`, `03_alignments.json`
- Keep: `reports/gutenberg<pg_id>-*.json`, `reports/gutenberg<pg_id>-*.txt`
- Delete: `cache/pages/` and `cache/pages_screw/` directories

## Filtering Rules (which books to skip)

Skip automatically:
- Non-English books
- Reference books (Factbooks, dictionaries, phrase books)
- Anthologies / collected works (too hard to match editions)
- Multi-volume works
- Books already processed
- Books where no matching IA scan exists

## Edition Matching Algorithm

1. Parse PG text for edition clues:
   - "First Edition", "Edition of 18XX"
   - Publisher names: "Chapman & Hall", "Macmillan", etc.
   - "Copyright, 18XX"
   - PGDP transcriber notes at start/end
2. Search IA for scans with matching metadata:
   - Author + title + year
   - Publisher + year
   - "This edition published by..."
3. Verify match by checking first few paragraphs

## Scripts Needed

### 1. `scripts/batch_prepare.py` — Queue Builder
- Fetches PG Top 100 list
- Cross-references with ERRATA_QUEUE.md
- Filters by language, type, already-processed
- For each candidate: fetches PG text, extracts edition info
- Searches IA for matching scan
- Outputs queue as JSON

### 2. `scripts/batch_process.py` — Pipeline Runner
- Reads queue JSON
- For each book: checks disk, runs gerrata CLI, collects results
- Respects concurrency (one book at a time)
- Handles failures gracefully
- Outputs summary report

### 3. `scripts/batch_cleanup.py` — Disk Manager
- Removes PNG page images for completed books
- Removes JP2 zips if disk > 80%
- Keeps reports and lightweight cache
- Can be run standalone or after each book

### 4. `scripts/batch_summarize.py` — Results Reporter
- Reads all reports
- Produces summary: X books processed, Y with errata, Z false positives
- Highlights plausible real errata

## Top 100 Candidates (pre-filtered)

Already processed: 2701, 84, 1342, 43, 42486, 58169

High-priority candidates (English fiction with likely IA scans):
| PG # | Title | Author | Notes |
|------|-------|--------|-------|
| 1184 | Count of Monte Cristo | Dumas | Very long, multi-volume risk |
| 11 | Alice's Adventures in Wonderland | Carroll | Short, good first test |
| 2554 | Crime and Punishment | Dostoyevsky | Translation — edition matching harder |
| 76 | Adventures of Huckleberry Finn | Twain | Good candidate |
| 64317 | Great Gatsby | Fitzgerald | 1925, modern — PG may have clean text |
| 37106 | Little Women | Alcott | Good candidate |
| 28054 | Brothers Karamazov | Dostoyevsky | Translation risk |
| 145 | Middlemarch | Eliot | Good candidate |
| 245 | Life on the Mississippi | Twain | Good candidate |
| 22400 | Fox's Book of Martyrs | Foxe | Religious text, many editions |
| 2641 | A Room with a View | Forster | Good candidate |
| 26471 | Spoon River Anthology | Masters | Poetry — may not align well |

Skip (non-English / reference / anthology):
- 27509, 27558: CIA Factbook (reference)
- 26849: George Gillespie (theology)
- 40739: Die Traumdeutung (German)
- 52206: Chinese poetry
- 55487, 65580, 76471: Non-English
- 22091: Short story anthology
- 26492: Fungi studies (reference)
- 100: Complete Shakespeare (anthology)
- 25851: Dickens biography (secondary source)

## Success Criteria
- Process 5+ books in first batch
- Zero manual intervention (after queue is built)
- Disk usage stays under 80%
- Report quality comparable to manual Turn of the Screw run
