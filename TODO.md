# Gerrata TODO

## In Progress

### Add PG line numbers to errata email reports
- [ ] Wire up `compute_line_number()` during pipeline — call it when building Error objects (in `cli.py` after verification, or in `generator.py` during report generation)
- [ ] Include surrounding PG text context (2-3 lines before/after) for each error in the email
- [ ] Format: `Line NNN: <context with the errata text clearly shown>`
- [ ] The `pg_file_line` field on Error model exists but is always 0 — needs to be populated
- [ ] `compute_line_number(pg_offset, pg_text)` method exists in `ReportGenerator` — takes raw offset and text, returns HTML file line number
- [ ] The pipeline reads the PG HTML file and has the raw body text + offsets — line number computation should happen when errors are finalized

## In Progress

### Fix vision aligner brute-force fallback ignoring sequential constraint
- **Status**: Codemaster implementing fix
- **Root cause**: Brute-force chunk loop in `align_transcription_to_pg()` (~line 1390-1428) iterates ALL `pg_chunks` without checking `[search_start, search_end]` bounds
- **The RETAS and n-gram paths correctly enforce the constraint**, but the fallback does not
- **Impact**: Sequential constraint is the mechanism ensuring each page maps to PG text after the previous page. Without it:
  - Short front-matter pages match random chunks anywhere in PG
  - Wrong `search_start` cascades to subsequent pages
  - 87% misalignment rate (Moby Dick: 384/475 candidates wrong)
- **Fix**: Add `effective_start - 200` / `effective_end + 200` bounds check to chunk loop (mirrors existing paragraph loop pattern)
- **After fix**: Re-run Moby Dick pipeline to verify alignment quality
