# PG Top 30 Errata Queue

Project Gutenberg's top 30 most-downloaded ebooks.
Goal: run Gerrata pipeline on each to produce errata reports.

## Status Legend
- [x] = pipeline completed
- [~] = in progress
- [ ] = not started
- [!] = scan source verified but not yet run

## Disk Usage
- **Input cache:** `cache/` — JP2 zips and PG HTML (safe to delete after pipeline runs)
- **Output reports:** `reports/` — JSON + email (KEEP these)
- Rule: delete `cache/<book>/` after successful pipeline run to save disk

## IA Infrastructure Note
IA bulk downloads (JP2 zips) are intermittently returning 503/500 errors (2026-05-10).
BookReader pages are still accessible. Scans marked [!] have verified metadata
but need download retry. Re-verify before running pipeline.

## Queue

