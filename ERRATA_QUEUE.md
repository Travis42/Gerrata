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

| # | PG ID | Title | Author | Status | IA Scan | Verified | Notes |
|---|-------|-------|--------|--------|---------|----------|-------|
| 1 | 58169 | Hegel's Lectures on the History of Philosophy, Vol 3 | Hegel | [x] | dli.bengal.10689.2429 | ✅ | 175 candidates, 10 blank pages |
| 2 | 43 | The Strange Case of Dr. Jekyll and Mr. Hyde | Stevenson | [x] | 06-stevenson-jekyll-hyde | ✅ | Test data |
| 3 | 2701 | Moby Dick; Or, The Whale | Melville | [~] | mobydickorwhale01melv | ✅ | Running now |
| 4 | 84 | Frankenstein; or, the Modern Prometheus | Shelley | [ ] | frankensteinorthe00shel | ❌ | 503 on JP2 zip; retry needed |
| 5 | 1184 | The Count of Monte Cristo | Dumas | [ ] | comtedemontecris00dumuoft | ❌ | 503 on JP2 zip; retry needed |
| 6 | 1342 | Pride and Prejudice | Austen | [!] | prideprejudice00aust | ✅ | JP2 zip downloads (201MB) |
| 7 | 1513 | Romeo and Juliet | Shakespeare | [ ] | romeojuliet01shak | ❌ | 500 on JP2 zip; retry needed |
| 8 | 11 | Alice's Adventures in Wonderland | Carroll | [ ] | aliceinwonderland00carr | ❌ | 403 on JP2 zip; need alt scan |
| 9 | 145 | Middlemarch | Eliot | [!] | middlemarch01elio | ✅ | JP2 zip downloads OK |
| 10 | 2554 | Crime and Punishment | Dostoevsky | [ ] | crimepunishment00dost | ❌ | 403 on JP2 zip; retry needed |
| 11 | 37106 | Little Women | Alcott | [ ] | — | — | Need to find scan |
| 12 | 2641 | A Room with a View | Forster | [ ] | roomwithaview0000for | ❌ | 503 on JP2 zip; retry needed |
| 13 | 28054 | The Brothers Karamazov | Dostoevsky | [ ] | brotherskaramazo00dost | ❌ | 500 on JP2 zip; retry needed |
| 14 | 42486 | The Two Magics: Turn of the Screw, Covering End | James | [ ] | turnofscrew00jame | ❌ | 403 on JP2 zip; retry needed |
| 15 | 67979 | The Blue Castle | Montgomery | [ ] | bluecastle00mont | ❌ | 403 on JP2 zip; retry needed |
| 16 | 40739 | Die Traumdeutung | Freud | [ ] | — | — | German text, deprioritize |
| 17 | 45304 | The City of God, Volume I | Augustine | [ ] | — | — | Need to find scan |
| 18 | 26716 | The Crown of Wild Olive | Ruskin | [ ] | — | — | Need to find scan |
| 19 | 26471 | Spoon River Anthology | Masters | [ ] | — | — | Need to find scan |
| 20 | 24950 | Bradford's History of 'Plimoth Plantation' | Bradford | [ ] | — | — | Need to find scan |
| 21 | 25049 | My Reminiscences of the Anglo-Boer War | Conan Doyle | [ ] | — | — | Need to find scan |
| 22 | 100 | The Complete Works of William Shakespeare | Shakespeare | [ ] | — | — | Very large, deprioritize |
| 23 | 27558 | The 2003 CIA World Factbook | CIA | [ ] | — | — | Reference, low priority |
| 24 | 25810 | In Connection with the De Willoughby Claim | various | [ ] | — | — | Need to find scan |
| 25 | 21053 | An Anthology of German Literature | various | [ ] | — | — | German, deprioritize |
| 26 | 22542 | Jesus the Christ | Talmage | [ ] | — | — | Need to find scan |
| 27 | 26849 | The Works of Mr. George Gillespie (Vol. 1) | Gillespie | [ ] | — | — | Need to find scan |
| 28 | 25851 | The Life of Charles Dickens | various | [ ] | — | — | Need to find scan |
| 29 | 21436 | Poems Every Child Should Know | various | [ ] | — | — | Poetry, different patterns |
| 30 | 23403 | Special Report on Diseases of the Horse | various | [ ] | — | — | Reference, low priority |
| 31 | 28046 | Critical and Historical Essays, Vol. III | Macaulay | [ ] | — | — | Need to find scan |

## Pipeline Run Log

| Date | PG ID | Title | Scan | Candidates | Duration | Issues |
|------|------|-------|------|------------|----------|--------|
| 2026-05-10 | 58169 | Hegel Vol 3 | dli.bengal.10689.2429 | 175 | ~110 min | 10 blank JP2 pages; exit code 2 |
| 2026-05-10 | 43 | Dr Jekyll | 06-stevenson-jekyll-hyde | 49 (post-filter) | — | Test data, replay mode |
| 2026-05-10 | 2701 | Moby Dick | mobydickorwhale01melv | — | — | In progress |
