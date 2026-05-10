# Processing a Book

Step-by-step instructions for running the Gerrata pipeline on a new PG ebook.

## Prerequisites

- `ZAI_API_KEY` environment variable set (Z.AI coding plan)
- `gerrata` CLI installed (`pip install -e .` from repo root)
- Internet Archive scan identifier for the source edition

## Steps

### 1. Get PG Book Details

Visit `https://www.gutenberg.org/ebooks/<pg_id>` to get:
- Title
- Author
- HTML file name (e.g., `pg58169-h.htm`)
- Download link for HTML zip: `https://www.gutenberg.org/cache/epub/<pg_id>/pg<pg_id>-h.zip`

### 2. Find the Internet Archive Scan

Search IA for the original edition that PG transcribed from:
```
site:archive.org "<title>" "<author>" "<original publisher>"
```

The scan should be:
- The same edition PG used (check PG's "Credits" section)
- A physical book scan with page images (not a derived PDF)
- Have a downloadable PDF or JP2 page images

Verify the scan has page images by checking:
- `https://archive.org/details/<identifier>/page/n1/mode/1up`

Record the IA identifier (e.g., `in.ernet.dli.2015.190961`).

### 3. Run the Pipeline

```bash
ZAI_API_KEY=<key> gerrata <pg_id> \
  --scan-id <ia_identifier> \
  --concurrency 10 \
  --output ./reports \
  -v
```

**Options:**
- `--scan-id`: IA identifier for source scans (required for scan page links in email)
- `--concurrency 10`: 10 parallel API calls (adjust for rate limits)
- `--no-verify`: Skip LLM vision verification (fast, free, OCR-only)
- `--page-range 48-100`: Only process a range of pages
- `--pg-file /path/to/pg58169-h.htm`: Use local PG file instead of downloading
- `--pages-dir /path/to/pages/`: Use pre-extracted page images
- `--cache-dir ./cache`: Cache downloads for reuse

### 4. With Completion Webhook

To get notified when the pipeline finishes:

```bash
ZAI_API_KEY=<key> gerrata <pg_id> \
  --scan-id <ia_identifier> \
  --concurrency 10 \
  && /root/clawd/scripts/wake-agent.sh "Gerrata pipeline complete for PG #<pg_id>" \
    --channel telegram --to "-1003815086962:3515"
```

### 5. Review and Submit

After completion, the output files are in `./reports/`:
- `<title-slug>_errata.json` — Full JSON report with all errors
- `<title-slug>_errata_email.txt` — PG-formatted email ready to send

The email follows PG errata guidelines:
- Context line with the error, then `erroneous ==> corrected`
- Per-error scan page links
- IA source scan link at top
- Gerrata attribution footer

**Manual review before sending:**
- Remove any remaining false positives
- Add/remove entries as needed
- Send to: errata2026@pglaf.org

## File Naming

Output files are unique per book:
- `gutenberg<pg_id>-<title-slug>_errata.json`
- `gutenberg<pg_id>-<title-slug>_errata_email.txt`

Reruns for the same book are versioned: `-2.txt`, `-3.txt`, etc.

## Current Book: PG #58169

- **Title:** Hegel's Lectures on the History of Philosophy: Volume 3 (of 3)
- **Author:** Georg Wilhelm Friedrich Hegel (trans. Haldane & Simson)
- **PG File:** pg58169-images.html (zip: `pg58169-h.zip`)
- **IA Scan:** https://archive.org/details/dli.bengal.10689.2429
  - Kegan Paul, Trench, Trübner edition (1895), Routledge & Kegan Paul reprint
  - JP2 zip: `10689.2429_jp2.zip` (167MB)
  - First-choice scan (in.ernet.dli.2015.190961) returns 500 errors for downloads

### Notes
- DLI scans (in.ernet.dli.*, dli.bengal.*) often have different JP2 zip naming than the standard `<identifier>_jp2.zip` pattern
- Use `--jp2-zip` with the explicit local path when the auto-naming fails
- PG HTML filenames vary: check the zip contents (`unzip -l`) before running
