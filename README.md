![Gerrata](Gerrata.png)

# Gerrata

**Automated errata detection for Project Gutenberg.** Compares published PG texts against original page scans from the Internet Archive to find transcription errors, producing errata-submission-ready reports.

**DISCLAIMER** Although every effort has been made to reduce false positives, errata from this tool should never be sent as-is.  Always review the results, delete false positives, and then send your human curated errata email.  This project is not responsible for irresponsible errata reports, and has intentionally made the choice to not allow automated reporting to keep you, the human, in the loop.  **Read and heed!**

## How It Works

1. **Fetch** the PG text (HTML or plain text) and the corresponding source scan from the Internet Archive
2. **Transcribe** each scan page using a vision model to get clean text from the physical book pages
3. **Align** transcriptions to the PG text using global word-sequence matching (distinctive phrase anchors → position lock)
4. **Diff** aligned passages to find candidate errors
5. **Filter** false positives using rule-based heuristics (alignment artifacts, typography variants, US/UK spelling, modernization, hyphenation, cutoff fragments)
6. **Verify** remaining candidates with programmatic confidence scoring (context match, edit distance, alignment quality, diff characteristics)
7. **Gap analysis** detects scan pages with text that has no corresponding passage in the PG text — verified via fuzzy search to avoid false positives from alignment failures
8. **Report** produces two files:
   - `errata_email.txt` — ready to send to PG, with only confirmed errors in arrow format
   - `review_needed.txt` — items that need human judgment before submitting
9. **Substantive errata report** (optional) — sends the full report to an LLM for intelligent classification, separating meaning-changing errors from noise (diacritics, spelling variants, OCR errors, pipeline artifacts)

## Quick Start

```bash
# Install
pip install gerrata

# Set your API key (vision model is needed for page transcription)
export OPENROUTER_API_KEY="your-api-key-here"
# Or store it in ~/.secrets/openrouter.key

# Run on a PG book
gerrata 43 \
  --scan-id "06-stevenson-jekyll-hyde" \
  --vision-key "$OPENROUTER_API_KEY" \
  -o reports

# With substantive errata analysis (LLM-curated report)
gerrata 43 \
  --scan-id "06-stevenson-jekyll-hyde" \
  --vision-key "$OPENROUTER_API_KEY" \
  --substantive-report \
  -o reports
```

This will:
- Download PG #43 (Dr. Jekyll and Mr. Hyde) from Project Gutenberg
- Download the scan pages from the Internet Archive
- Transcribe each page with a vision model (Gemini 3.1 Flash Lite via OpenRouter)
- Align, diff, filter, and verify
- Generate reports in `./reports/`

## Using Local Files

If you already have the files, skip the downloads:

```bash
gerrata 43 \
  --pg-file /path/to/43-h.htm \
  --jp2-zip /path/to/06-stevenson-jekyll-hyde_jp2.zip \
  --vision-key "$OPENROUTER_API_KEY" \
  -o reports
```

You can also point to a directory of pre-extracted PNG page images:

```bash
gerrata 43 \
  --pg-file /path/to/43-h.htm \
  --pages-dir /path/to/extracted-pages \
  --vision-key "$OPENROUTER_API_KEY" \
  -o reports
```

## Page Range

To process a subset of pages (useful for testing or large books):

```bash
gerrata 43 \
  --scan-id "06-stevenson-jekyll-hyde" \
  --page-range "40-100" \
  --vision-key "$OPENROUTER_API_KEY" \
  -o reports
```

## Pipeline Resume

The pipeline saves intermediate results at each step. If a run crashes or you want to re-run from a specific step (e.g., after fixing a bug in filtering), use `--resume-from`:

```bash
# Re-run from verification (skips PG parsing, page extraction, transcription, alignment, and diff)
gerrata 2701 \
  --scan-id mobydick0001herm \
  --resume-from pre-verify \
  --vision-key "$OPENROUTER_API_KEY" \
  -o reports
```

### Available Resume Points

→ pg-parsed → transcribe → align → diff → filter → verify → report

| Resume point | Skips these steps | When to use |
|---|---|---|
| `pg-parsed` | Download + parse PG text | Testing alignment or later |
| `transcriptions` | + page images + transcription | Testing alignment, diff, or later |
| `alignments` | + alignment computation | Testing diff, filtering, or later |
| `candidates-raw` | + text diff | Testing filtering, verification, or later |
| `pre-verify` | + false positive filtering | Testing verification or reporting |
| `pre-report` | + programmatic verification | Testing report generation |

Intermediate files are saved in `cache/{scan-id}/` (e.g., `cache/mobydick0001herm/01_pg_parsed.json`, `02_transcriptions.json`, etc.).

Crash-resilient transcription is built in: if the process dies mid-transcription, a JSONL log tracks completed pages. Resuming will skip already-transcribed pages automatically.

## Output Files

After a pipeline run, the output directory contains:

| File | Purpose |
|------|---------|
| `gutenberg{ID}-*_errata.md` | Full markdown report with all errors and context |
| `gutenberg{ID}-*_errata.json` | Machine-readable JSON with all error data |
| `gutenberg{ID}-*_errata_email.txt` | Submit-ready errata report in PG's preferred format |
| `gutenberg{ID}-*_review_needed.txt` | Items needing human review before submission |
| `gutenberg{ID}-*_substantive_errata.md` | LLM-curated report: only meaning-changing errors with false positives documented |

### substantive_errata.md (optional)

When `--substantive-report` is enabled, the pipeline sends the full errata report to an LLM for intelligent classification. This produces a curated markdown document that separates genuine errors from noise:

**What gets excluded (with explanations):**
- Diacritic-only changes (Senor→Señor, etc.)
- Spelling variants (corredor→corridor, among→amongst)
- Scan OCR errors where PG is correct
- Repeated detections of the same pattern
- Pipeline alignment artifacts
- British/American spelling differences

**What gets included as substantive:**
- Wrong words that change meaning (e.g., "Gefe" for "Jefe")
- Clear PG typos (e.g., "superintendendence")
- Typesetting errors (l/I confusion, rn/m confusion)
- Missing or extra words

Every entry includes a clickable Internet Archive scan page link for verification.

```bash
gerrata 43 \
  --scan-id "06-stevenson-jekyll-hyde" \
  --vision-key "$OPENROUTER_API_KEY" \
  --substantive-report \
  -o reports
```

The substantive analysis uses a separate model call (default: `google/glm-5.1` via OpenRouter). It reuses the same `OPENROUTER_API_KEY` env var, but you can override it with `--substantive-key` if needed.

### errata_email.txt

Generated by the pipeline. Uses page numbers, scan links, and arrow format:

```
In Alice's Adventures in Wonderland, by Lewis Carroll, [EBook #11],
File: 11.txt,
I verified the following changes against the Internet Archive scan:
https://archive.org/details/alicesadventur00carr
NOTE: Page numbers are 'of the scan' not 'of the book.'

Page 24 (https://archive.org/details/alicesadventur00carr/page/n24/mode/1up):
How funny it'll seem to come out among the people that walk with their heads downward!
downward! ==> downwards!

Page 105 (https://archive.org/details/alicesadventur00carr/page/n105/mode/1up):
The cook threw a frying-pan after her as she went out, but it just missed her.
went out, ==> went,

---

These errata were found using Gerrata (https://github.com/travis42/gerrata) and refined by a human reviewer (me).
Please reach out if you would like to discuss Gerrata or this report.
```

### review_needed.txt

Items the tool couldn't confidently classify. Each includes the verdict, a scan page link for manual verification, and a prompt for what to decide:

```
Quality Audit Review Items: The Strange Case Of Dr. Jekyll And Mr. Hyde (PG #43)

2 items need your decision before submission
---

[?] Line 335, Page 48, (STORY OF THE DOOR)
    PG text: returned Enfield. "But
    Scan text: " But
    Verdict: unable_to_verify (50%)
    Reasoning: Low context match suggests possible alignment issue
    Action needed: Check scan and decide if PG punctuation is wrong
    Scan page: https://archive.org/details/06-stevenson-jekyll-hyde/page/n48/mode/1up
```

Verdict prefixes:
- `[?]` — ambiguous, needs your judgment
- `[E]` — edition variant, likely not an error
- `[M]` — intentional modernization, likely not an error
- `[~]` — low confidence confirmed error

## Filtering

The pipeline applies filters at multiple stages:

### Stage 1: False Positive Filter (programmatic)

Deterministic rules applied before verification:

→ Alignment artifacts (absent in PG/scan, long mismatches, HTML leaks)
→ Typography conversions (smart quotes, em dashes, ligatures)
→ Intentional modernizations (e.g., `someone` ↔ `some one`, `tonight` ↔ `to-night`)
→ US/UK spelling variants (e.g., `favour` ↔ `favor`, `colour` ↔ `color`)
→ Edition variants (e.g., `;` ↔ `:`, spelling differences)
→ Punctuation/whitespace-only changes
→ Line-breaking hyphenation artifacts (e.g., `some- thing` ↔ `something`)

### Stage 2: Alignment Artifact Filters (pipeline)

Applied to raw candidates before verification:

→ Absent text: `(absent in PG)` or `(absent in scan)` markers from window overshoot
→ Word-boundary cutoff: Short fragments like `hen` → `when` (≤3 char suffix)
→ Long mismatch: One side >40 chars (alignment spanned too far)
→ HTML artifact: `<div>`, `<span>`, `bbox=` leaked from scan OCR
→ ALL CAPS header: Chapter titles matched as errors
→ Suffix fragment: `terson` → `Utterson` (≤8 char tail fragment)
→ Quoted fragment: Opening quote grabbed alone, rest of sentence on other side

### Stage 3: Report Filters (email generation)

Applied when building the errata email:

→ Low confidence (< 0.4) — likely artifacts
→ Alignment artifacts, edition variants, modernization — already classified
→ Deduplication (within 50 char offset proximity)
→ Punctuation-only and quote-start fragment filters (post-dedup)

## Programmatic Verification

Gerrata uses deterministic confidence scoring instead of LLM verification. Each candidate gets a confidence score (0.0–1.0) based on:

1. **Context match ratio** — how well the surrounding words match between PG and scan
2. **Edit distance** — how similar the PG and scan words are
3. **Alignment quality** — the confidence of the page alignment
4. **Diff characteristics** — word lengths, single-vs-multi-word diffs

Scores above 0.8 are considered high-confidence. The errata email includes items with confidence ≥ 0.4 (lowered threshold catches more candidates; the review file shows lower-confidence items for manual inspection).

## Gap Analysis (Missing Content)

In addition to finding transcription errors, Gerrata detects **coverage gaps** — scan pages that contain text with no corresponding passage in the PG text. These may indicate paragraphs or sentences that were omitted during transcription.

Two strategies are used:

1. **Uncovered pages** — Scan pages with text but no alignment at all (the aligner couldn't match them)
2. **Partial coverage** — Within aligned pages, portions of scan text that don't correspond to any PG passage

Each gap is **verified against the full PG text** using fuzzy matching. If the gap text is found elsewhere in PG (just misaligned), it's filtered out. Only gaps that genuinely don't appear in PG are reported.

Gaps are included in the errata email under a `MISSING CONTENT` section with page links, word counts, and confidence levels.

## Filing an Errata Report with Project Gutenberg

### How to Submit

1. **Review** the errata email output (`reports/gutenberg{ID}-*_errata_email.txt`)
2. **Verify** errors by checking context and the scan page links
3. **Check** `review_needed.txt` for items that need your judgment
4. **Remove** any items you disagree with from the email
5. **Email** the final content to:

   **errata2026@pglaf.org** (remove spaces)

   Change `2026` to the current year if needed.

### What PG Expects

PG's errata page (<https://www.gutenberg.org/help/errata.html>) specifies:
- Include the **full title, author, and eBook number**
- Include the **file name** (e.g., `43-h.htm`)
- Use **line numbers** or **page numbers** to pinpoint errors
- Give **enough context** to find the error (not just a single common word)
- Use the **arrow format**: `erroneous ==> corrected`
- **Do NOT** send full corrected paragraphs — it buries the error
- When you've verified against a scan, **mention the source used**

## Finding a Source Scan

To audit a PG book, you need a corresponding scan on the Internet Archive. Look for:
- The same title and author
- Preferably the same edition (matching publication year and publisher)
- A scan with JP2 page images available for download

Browse the Internet Archive at <https://archive.org> or search:
```
site:archive.org "Robert Louis Stevenson" "Jekyll" "pdf" OR "jp2"
```

The `--scan-id` argument is the IA identifier (the part after `/details/` in the URL).

### Source Scan for Testing

The test fixture uses IA identifier `06-stevenson-jekyll-hyde`:
- Scan: <https://archive.org/details/06-stevenson-jekyll-hyde>
- PDF: <https://archive.org/download/06-stevenson-jekyll-hyde/06-Stevenson-JekyllHyde.pdf>
- JP2 zip: <https://archive.org/download/06-stevenson-jekyll-hyde/06-stevenson-jekyll-hyde_jp2.zip>

## Batch Processing

Gerrata includes scripts for auditing multiple books in sequence.

### Build a Queue

```bash
# Build queue from PG Top 100, search IA for matching scans
python3 scripts/batch_prepare.py --limit 30
```

This creates `cache/batch_queue.json` with books that have matching IA scans and haven't been processed yet.

### Process a Queue

```bash
# Process the full queue
python3 scripts/batch_process.py

# Process just the next 5 books
python3 scripts/batch_process.py --limit 5

# Resume from book 10 in the queue
python3 scripts/batch_process.py --start 10
```

Each book runs the full pipeline, generates reports, and cleans up disk space afterward. Completed books are tracked in `cache/completed.json`.

## Requirements

- Python 3.10+
- A vision-capable LLM API key (any OpenAI-compatible provider)
- Internet connection (for downloading PG texts and IA scans)

### Setting Up an API Key

The tool needs access to a vision-capable LLM for page transcription. It works with **any OpenAI-compatible API** — just set the URL and key.

**Default provider: OpenRouter** (Gemini 3.1 Flash Lite)

**Option 1: Environment variable (simplest)**

```bash
export OPENROUTER_API_KEY="your-key-here"
gerrata 43 --scan-id "some-scan-id" -o reports
```

The `OPENROUTER_API_KEY` env var is picked up automatically. You can skip `--vision-key` when it's set.

**Option 2: Key file**

```bash
# Store once
echo "your-key-here" > ~/.secrets/openrouter.key

# Run (key loaded automatically)
gerrata 43 --scan-id "some-scan-id" -o reports
```

**Option 3: CLI flags**

```bash
gerrata 43 \
  --scan-id "some-scan-id" \
  --vision-key "your-key-here" \
  -o reports
```

**Option 4: Using a different provider (OpenAI, Anthropic, etc.)**

Any OpenAI-compatible endpoint works:

```bash
gerrata 43 \
  --scan-id "some-scan-id" \
  --vision-url "https://api.openai.com/v1/chat/completions" \
  --vision-key "$OPENAI_API_KEY" \
  --vision-model "gpt-4o" \
  -o reports
```

The default is OpenRouter with Gemini 3.1 Flash Lite, but this is not a hard dependency — change `--vision-url` and `--vision-model` to use whichever provider you prefer.

### Configuration Reference

| Flag | Default | Purpose |
|------|---------|---------|
| `--vision-url` | `https://openrouter.ai/api/v1/chat/completions` | API endpoint for page transcription |
| `--vision-key` | `$OPENROUTER_API_KEY` or `~/.secrets/openrouter.key` | API key for transcription |
| `--vision-model` | `gemini-3.1-flash-lite` | Model name for transcription |
| `--page-range` | all pages | Page range to process, e.g. `40-100` |
| `--concurrency` | `10` | Number of concurrent API calls |
| `--strict` | off | Use strict filtering (keep more candidates) |
| `--resume-from` | — | Resume from an intermediate save point |
| `--output`, `-o` | `./reports` | Output directory for reports |
| `--substantive-report` | off | Generate substantive errata report via LLM analysis |
| `--substantive-model` | `google/glm-5.1` | Model for substantive errata analysis |
| `--substantive-key` | `$OPENROUTER_API_KEY` | API key for substantive analysis (separate from vision key) |
| `--verbose`, `-v` | off | Enable verbose logging |

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -q

# Run with verbose output
gerrata 43 --scan-id "06-stevenson-jekyll-hyde" --vision-key "$OPENROUTER_API_KEY" -v
```

## License

MIT
