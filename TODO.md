# Gerrata TODO

## In Progress

### Add PG line numbers to errata email reports
- [ ] Wire up `compute_line_number()` during pipeline — call it when building Error objects (in `cli.py` after verification, or in `generator.py` during report generation)
- [ ] Include surrounding PG text context (2-3 lines before/after) for each error in the email
- [ ] Format: `Line NNN: <context with the errata text clearly shown>`
- [ ] The `pg_file_line` field on Error model exists but is always 0 — needs to be populated
- [ ] `compute_line_number(pg_offset, pg_text)` method exists in `ReportGenerator` — takes raw offset and text, returns HTML file line number
- [ ] The pipeline reads the PG HTML file and has the raw body text + offsets — line number computation should happen when errors are finalized

## Upcoming

### Fix vision aligner producing 87% misaligned page mappings
- Root cause: LLM-based text matching in `vision_aligner.py` maps wrong PG text to scan pages
- Symptom: PG text from one chapter compared against scan page from completely different chapter
- Impact: nearly all "submit-ready" errors are artifacts of bad alignment, not real errors
- Audit data (Moby Dick): 475 filtered candidates, 384 misaligned (87%), across pages 42-336
- Key observations:
  - First ~20 alignments (scans 29-50) are severely off — front matter scrambled
  - Mid-book has major jumps: alignment 240 has scan gap=233, alignment 10 has scan BACKWARD + PG gap=268K
  - Only 13% of candidates have correctly aligned PG text and scan page content
  - The verifier receives mismatched content and hallucinates corrections
- Needs deep investigation into the vision_aligner.py matching algorithm
