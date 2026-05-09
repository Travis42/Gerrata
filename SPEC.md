# Gerrata — Specification & Roadmap

## Pipeline Steps

1. **Parse** — Fetch PG text from Gutenberg HTML
2. **Scan Pages** — Get page images (JP2 zip from IA, or local `--pages-dir`)
3. **Transcribe** — OCR via vision model (GLM-OCR, or any OpenAI-compatible endpoint)
4. **Align** — Match transcribed pages to PG text offsets
5. **Diff** — Compare aligned scan text vs PG text, find differences
6. **Filter** — Remove false positives (known PG formatting patterns)
7. **Verify** — LLM vision review of candidates (batch per-page, classifies verdicts)
8. **Report** — Generate markdown, JSON, email, and review-needed outputs

## Current Capabilities

- Multi-provider via `--vision-url`/`--vision-model` (Z.AI, OpenAI, Ollama)
- Split endpoints: `--vision-*` for transcription, `--verify-*` for verification
- GLM-OCR adapter (auto-detected, routes to `/v4/layout_parsing`)
- Batch per-page verification with retry/exponential backoff
- Transcription cache (`--transcription-cache`, keyed by page hash + model)
- Raw findings report (`*-raw.*`) saved before verification for comparison
- `--pages-dir` for local page images (fallback when IA JP2 unavailable)
- `--page-range` for partial runs

---

## TODO — Future Work

### Performance

- [x] **Concurrent API calls** — Parallelize transcription and verification steps
  - Both are embarrassingly parallel (per-page independence)
  - Use `asyncio.Semaphore(n) + asyncio.gather()` pattern
  - Add `--concurrency N` flag (default: 1, recommended: 5-10)
  - Need exponential backoff for rate limit handling at higher concurrency
  - Transcription: 266 pages at 1/sec sequential → ~20s at 10 concurrent
  - Verification: ~100 pages at 2/sec sequential → ~20s at 10 concurrent
  - Sort results by page index after gather to maintain ordering

### Verification Resume

- [ ] **Verification checkpoint** — Save per-page verification progress to cache
  - Same pattern as transcription cache
  - On restart, skip pages already verified
  - Key by (page_index, candidate_hashes)

### Input Flexibility

- [ ] **PDF input** — Accept PDF files directly via `--scan-pdf` flag
  - Extract pages with `pdf2image`/`pdftoppm`
  - Currently requires pre-extracted PNGs via `--pages-dir`

### Output

- [ ] **Diff comparison mode** — Diff raw vs verified reports to quantify LLM review value
  - Show: added verdicts, confidence changes, false positives eliminated
  - Could be a `--compare-raw` flag or separate `gerrata compare` subcommand

### Accuracy & Filtering

- [ ] **Word-boundary cutoff filter** — Items like "hen"→"when" are alignment artifacts where a word was cut at the edge of a text block. These are program errors, not source material errors.
  - When scan_text is a suffix of a longer word (or vice versa) with ≤3 extra chars, flag as likely boundary artifact
  - Consider requiring both words to be complete words (space-bounded) to be reported
  - Alternative: check if the diff spans a line break or column boundary in the transcription

- [ ] **Page numbering off-by-one** — Reported `scan_page` is consistently +1 from the actual book page. Fix the offset (likely 0-indexed internally but displayed as 1-indexed without adjustment, or vice versa).

- [ ] **Context sentences in JSON output** — When a single-word difference is found (e.g. "the" vs "that"), include the surrounding sentence in the JSON for human readability.
  - Add `pg_sentence` field: the full sentence containing the PG text from the HTML source
  - Add `scan_sentence` field: the surrounding transcribed text from the same page region
  - Extract by splitting on sentence boundaries (`.`, `!`, `?`) and including the one containing the offset

### Output

- [ ] **Errata email draft** — Generate a human-readable draft email as part of standard output
  - Currently the `*_errata_email.txt` file exists but appears empty or missing for runs where findings were filtered
  - Should include: greeting, book title, PG number, list of confirmed errata with page numbers and context, closing
  - Filter to only high-confidence items that pass the cutoff filters above
  - Include the contextual sentence for each finding

### Testing

- [ ] **End-to-end test with mocked API** — Currently relies on live API for integration tests
- [ ] **Transcription cache unit tests** — Test hash validation, model mismatch, corruption recovery
