# PG Top 30 Errata Queue

Project Gutenberg's top 30 most-downloaded ebooks.
Goal: run Gerrata pipeline on each to produce errata reports.

## Status Legend
- [x] = pipeline completed
- [~] = in progress
- [ ] = not started

## Disk Usage
- **Input cache:** `cache/` — JP2 zips and PG HTML (safe to delete after pipeline runs)
- **Output reports:** `reports/` — JSON + email (KEEP these)
- Rule: delete `cache/<book>/` after successful pipeline run to save disk

## Queue

| # | PG ID | Title | Author | Status | IA Scan | Candidates | Notes |
|---|-------|-------|--------|--------|---------|------------|-------|
| 1 | 58169 | Hegel's Lectures on the History of Philosophy, Vol 3 | Hegel | [x] | dli.bengal.10689.2429 | 175 | 10 blank pages skipped |
| 2 | 2701 | Moby Dick; Or, The Whale | Melville | [~] | mobydickorwhale01melv | — | Running now |
| 3 | 84 | Frankenstein; or, the Modern Prometheus | Shelley | [ ] | | | |
| 4 | 1184 | The Count of Monte Cristo | Dumas | [ ] | | | |
| 5 | 40739 | Die Traumdeutung | Freud | [ ] | | | German text |
| 6 | 1342 | Pride and Prejudice | Austen | [ ] | | | |
| 7 | 1513 | Romeo and Juliet | Shakespeare | [ ] | | | |
| 8 | 45304 | The City of God, Volume I | Augustine | [ ] | | | |
| 9 | 26716 | The Crown of Wild Olive | Ruskin | [ ] | | | |
| 10 | 11 | Alice's Adventures in Wonderland | Carroll | [ ] | | | |
| 11 | 26471 | Spoon River Anthology | Masters | [ ] | | | |
| 12 | 24950 | Bradford's History of 'Plimoth Plantation' | Bradford | [ ] | | | |
| 13 | 25049 | My Reminiscences of the Anglo-Boer War | Conan Doyle | [ ] | | | |
| 14 | 100 | The Complete Works of William Shakespeare | Shakespeare | [ ] | | | Very large |
| 15 | 2641 | A Room with a View | Forster | [ ] | | | |
| 16 | 27558 | The 2003 CIA World Factbook | CIA | [ ] | | | Reference, low priority |
| 17 | 43 | The Strange Case of Dr. Jekyll and Mr. Hyde | Stevenson | [x] | 06-stevenson-jekyll-hyde | — | Test data |
| 18 | 145 | Middlemarch | Eliot | [ ] | | | |
| 19 | 2554 | Crime and Punishment | Dostoevsky | [ ] | | | |
| 20 | 25810 | In Connection with the De Willoughby Claim | various | [ ] | | | |
| 21 | 37106 | Little Women | Alcott | [ ] | | | |
| 22 | 21053 | An Anthology of German Literature | various | [ ] | | | |
| 23 | 22542 | Jesus the Christ | Talmage | [ ] | | | |
| 24 | 42486 | The Two Magics: Turn of the Screw, Covering End | James | [ ] | | | |
| 25 | 28054 | The Brothers Karamazov | Dostoevsky | [ ] | | | |
| 26 | 26849 | The Works of Mr. George Gillespie (Vol. 1) | Gillespie | [ ] | | | |
| 27 | 25851 | The Life of Charles Dickens | various | [ ] | | | |
| 28 | 21436 | Poems Every Child Should Know | various | [ ] | | | Poetry |
| 29 | 67979 | The Blue Castle | Montgomery | [ ] | | | |
| 30 | 23403 | Special Report on Diseases of the Horse | various | [ ] | | | Reference |
| 31 | 28046 | Critical and Historical Essays, Vol. III | Macaulay | [ ] | | | |

## Pipeline Run Log

| Date | PG ID | Title | Scan | Candidates | Duration | Issues |
|------|------|-------|------|------------|----------|--------|
| 2026-05-10 | 58169 | Hegel Vol 3 | dli.bengal.10689.2429 | 175 | ~110 min | 10 blank JP2 pages skipped; exit code 2 (non-fatal) |
| 2026-05-10 | 43 | Dr Jekyll | 06-stevenson-jekyll-hyde | 49 (post-filter) | — | Test data, replay mode |
| 2026-05-10 | 2701 | Moby Dick | mobydickorwhale01melv | — | — | In progress |
