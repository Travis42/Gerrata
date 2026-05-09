# Gerrata

**Automated errata detection for Project Gutenberg.** Compares published PG texts against original page scans from the Internet Archive to find transcription errors, producing errata-submission-ready reports.

## How It Works

1. **Fetch** the PG text (HTML or plain text) and the corresponding source scan from the Internet Archive
2. **Transcribe** each scan page using a vision model (GLM-4.6V) to get clean text from the physical book pages
3. **Align** transcriptions to the PG text using the RETAS algorithm (unique word anchors → LCS ordering → position lock → local diff)
4. **Diff** aligned passages to find candidate errors
5. **Filter** false positives using rule-based heuristics (alignment artifacts, typography variants, punctuation normalization)
6. **Verify** remaining candidates by showing them to a vision model that looks at the actual scan page and classifies each difference: real error, edition variant, or intentional modernization
7. **Report** produces two files:
   - `errata_email.txt` — ready to send to PG, with only confirmed errors in arrow format
   - `review_needed.txt` — items that need human judgment before submitting

## Quick Start

```bash
# Install
pip install gerrata

# Set your vision model API key (Z.AI)
export ZAI_API_KEY="your-api-key-here"

# Run on a PG book
gerrata 43 \
  --scan-id "06-stevenson-jekyll-hyde" \
  --vision-key "$ZAI_API_KEY" \
  -o reports
```

This will:
- Download PG #43 (Dr. Jekyll and Mr. Hyde) from Project Gutenberg
- Download the scan pages from the Internet Archive
- Transcribe each page with GLM-4.6V
- Align, diff, filter, and verify
- Generate reports in `./reports/`

### Using Local Files

If you already have the files, skip the downloads:

```bash
gerrata 43 \
  --pg-file /path/to/43-h.htm \
  --jp2-zip /path/to/06-stevenson-jekyll-hyde_jp2.zip \
  --vision-key "$ZAI_API_KEY" \
  -o reports
```

### Page Range

To process a subset of pages (useful for testing or large books):

```bash
gerrata 43 \
  --scan-id "06-stevenson-jekyll-hyde" \
  --page-range "40-100" \
  --vision-key "$ZAI_API_KEY" \
  -o reports
```

### Skip Verification (Fast/Free)

Omit LLM verification if you just want raw diffs:

```bash
gerrata 43 \
  --scan-id "06-stevenson-jekyll-hyde" \
  --no-verify \
  --vision-key "$ZAI_API_KEY" \
  -o reports
```

## Output Files

After running, the output directory contains:

| File | Purpose |
|------|---------|
| `pg{ID}_errata_email.txt` | **Submit-ready** errata report in PG's preferred format |
| `pg{ID}_review_needed.txt` | Items needing human review before submission |
| `pg{ID}_errata.md` | Full markdown report with all errors and context |
| `pg{ID}_errata.json` | Machine-readable JSON with all error data |

### errata_email.txt

This file is formatted for direct submission to Project Gutenberg. It contains only confirmed errors (≥80% confidence, `scan_correct` verdict) with line numbers and arrow-format fixes:

```
The Strange Case Of Dr. Jekyll And Mr. Hyde, by Robert Louis Stevenson
 [EBook #43]
 File: 43-h.htm
 Verified against Internet Archive scan: https://archive.org/details/06-stevenson-jekyll-hyde

 1 errors ready for submission
 2 items need your review (see review_needed.txt)

 Line 130:
 assed
 assed ==> passed
```

### review_needed.txt

Items the tool couldn't confidently classify. Each includes the verdict, a scan page link for manual verification, and a prompt for what to decide:

```
2 items need your decision before submission
---

[?] Line 335, Page 0, (STORY OF THE DOOR)
    PG text: returned Enfield. "But
    Scan text: " But
    Verdict: unable_to_verify (50%)
    Action needed: Check scan and decide if PG punctuation is wrong
    Scan page: https://archive.org/details/06-stevenson-jekyll-hyde/page/n48/mode/1up
```

Verdict prefixes:
- `[?]` — ambiguous, needs your judgment
- `[E]` — edition variant, likely not an error
- `[M]` — intentional modernization, likely not an error
- `[~]` — low confidence confirmed error

## Filing an Errata Report with Project Gutenberg

### How to Submit

1. **Review** `review_needed.txt` (if any items need your decision)
2. **Verify** confirmed errors by clicking the scan page links
3. **Edit** `errata_email.txt` to remove any items you disagree with, or to add items from the review file
4. **Email** the final `errata_email.txt` content to:

   **errata2026@pglaf.org** (remove spaces)

   Change `2026` to the current year if needed.

### What PG Expects

PG's errata page (<https://www.gutenberg.org/help/errata.html>) specifies:
- Include the **full title, author, and eBook number**
- Include the **file name** (e.g., `43-h.htm`)
- Use **line numbers** to pinpoint errors
- Give **enough context** to find the error (not just a single common word)
- Use the **arrow format**: `erroneous ==> corrected`
- **Do NOT** send full corrected paragraphs — it buries the error
- When you've verified against a scan, **mention the source used**

### Example Errata Report

```
In Stevenson's "The Strange Case Of Dr. Jekyll And Mr. Hyde," EBook #43, File: 43-h.htm,

Verified against Internet Archive scan: https://archive.org/details/06-stevenson-jekyll-hyde

 Line 130, in the chapter "SEARCH FOR MR. HYDE":
 assed
 assed ==> passed
```

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

## Requirements

- Python 3.10+
- A vision-capable LLM API key (any OpenAI-compatible provider)
- Internet connection (for downloading PG texts and IA scans)

### Setting Up an API Key

The tool needs access to a vision-capable LLM (for page transcription and error verification). It works with **any OpenAI-compatible API** — just set the URL and key.

**Option 1: Environment variable (simplest)**

```bash
export ZAI_API_KEY="your-key-here"
gerrata 43 --scan-id "some-scan-id" -o reports
```

The `ZAI_API_KEY` env var is picked up automatically. You can skip `--vision-key` when it's set.

**Option 2: CLI flags**

```bash
gerrata 43 \
  --scan-id "some-scan-id" \
  --vision-key "your-key-here" \
  -o reports
```

**Option 3: Using a different provider (OpenAI, Anthropic, etc.)**

Any OpenAI-compatible endpoint works. For example, with OpenAI:

```bash
gerrata 43 \
  --scan-id "some-scan-id" \
  --vision-url "https://api.openai.com/v1/chat/completions" \
  --vision-key "$OPENAI_API_KEY" \
  --vision-model "gpt-4o" \
  -o reports
```

With a local Ollama instance:

```bash
gerrata 43 \
  --scan-id "some-scan-id" \
  --vision-url "http://localhost:11434/v1/chat/completions" \
  --vision-model "llama3.2-vision" \
  -o reports
```

The default is Z.AI (`api.z.ai`) with GLM-4.6V, but this is not a hard dependency — change `--vision-url` and `--vision-model` to use whichever provider you prefer.

### Split Endpoint Configuration

You can use different API endpoints for transcription and verification. This is useful when you want to use GLM-OCR for transcription (specialized for document layout parsing) and GLM-4.6V for verification (better at understanding context and edition variants).

```bash
gerrata 43 \
  --scan-id "some-scan-id" \
  --vision-url "https://api.z.ai/api/paas/v4/layout_parsing" \
  --vision-model "glm-ocr" \
  --vision-key "$ZAI_API_KEY" \
  --verify-url "https://api.z.ai/api/paas/v4/chat/completions" \
  --verify-model "glm-4.6v" \
  -o reports
```

If you only specify `--vision-*` flags, the same endpoint will be used for both transcription and verification (backward compatible).

| Flag | Default | Purpose |
|------|---------|---------|
| `--vision-url` | `https://api.z.ai/api/paas/v4/chat/completions` | API endpoint for page transcription (Step 3) |
| `--vision-key` | `$ZAI_API_KEY` | API key for transcription |
| `--vision-model` | auto-select | Model name for transcription (e.g., `glm-ocr`, `glm-4.6v`) |
| `--verify-url` | same as `--vision-url` | API endpoint for verification (Step 7) |
| `--verify-key` | same as `--vision-key` | API key for verification |
| `--verify-model` | same as `--vision-model` | Model name for verification |

**Note:** Page transcription and verification both require a model that can read images. Text-only models won't work for these steps. Use `--no-verify` to skip verification (you'll still need vision for transcription unless you also use `--no-vision-transcribe`).

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -q

# Run with verbose output
gerrata 43 --scan-id "06-stevenson-jekyll-hyde" --vision-key "$ZAI_API_KEY" -v
```

## License

MIT
