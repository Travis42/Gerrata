# Spec: Pipeline Resume Point Cleanup and Pre-Report Stage

## Problem

1. The `--resume-from candidates-filtered` flag is misleading — it actually reloads from `04_candidates_raw.json` and re-runs the diff + filter. The name implies it loads pre-filtered results.
2. There is no way to skip directly to verification or report generation without re-running the diff step.
3. Users cannot easily understand which resume point to use.

## Current Flow

```
pg-parsed → transcriptions → alignments → candidates-raw → (filter) → (verify) → (report)
                                                      ↑ resume points
```

Resume points map to: `01_pg_parsed.json`, `02_transcriptions.json`, `03_alignments.json`, `04_candidates_raw.json`. There is no resume point for `05_candidates_filtered.json` (post-filter) or post-verification.

## Proposed Changes

### 1. Rename resume choices

Update `--resume-from` argument choices and the code that handles them:

| Old name | New name | What it does |
|----------|----------|-------------|
| `candidates-raw` | `candidates-raw` | Same — re-runs diff + filter + verify + report |
| `candidates-filtered` | `pre-verify` | Loads from `05_candidates_filtered.json`, runs verify + report |

This is the minimal change. `candidates-filtered` was already misnamed; `pre-verify` accurately describes what it does.

### 2. Add `pre-report` resume point

Add a new resume point that loads from the cached verification results and only generates the report. This requires saving verified errors as an intermediate file.

| Resume point | Loads from | Runs |
|-------------|-----------|------|
| `pre-report` | `06_verified_errors.json` | Report generation only |

This means:
- Add `save_intermediate(intermed_dir, "06_verified_errors", verified_errors)` after the verification step in `cli.py`
- Add handling for `--resume-from pre-report` that loads `06_verified_errors.json` and skips everything before report generation

### 3. Print pipeline stage diagram

At the start of the pipeline (before Step 1), print a simple diagram showing which stages will run:

```
Pipeline: [pg-parse] → [transcribe] → [align] → [diff] → [filter] → [verify] → [report]
                 ✓ cached                    ✓ cached              ← resume from here
```

Where stages are marked:
- `✓ cached` — stage skipped due to cached intermediate
- `← resume from here` — the resume point
- Stages that will execute are shown normally

### 4. Backward compatibility

Keep `candidates-filtered` as an **alias** for `pre-verify` for one release cycle. Print a deprecation warning if used:
```
WARNING: --resume-from=candidates-filtered is deprecated, use --resume-from=pre-verify
```

## Files to Modify

- `/root/projects/gerrata/src/gerrata/cli.py` — resume point handling, pipeline diagram, intermediate save
- `/root/projects/gerrata/tests/test_cli.py` — if it exists, update resume point tests

## Commit Criteria

- [ ] `--resume-from pre-verify` loads from `05_candidates_filtered.json` and runs verify + report only
- [ ] `--resume-from pre-report` loads from `06_verified_errors.json` and runs report only
- [ ] `06_verified_errors.json` is saved after verification step
- [ ] Pipeline stage diagram prints at start showing cached/active stages
- [ ] `--resume-from candidates-filtered` still works with deprecation warning
- [ ] `--help` shows updated resume choices
- [ ] All existing tests pass
- [ ] Running `--resume-from pre-report` on PG#21500 cached data completes in under 5 seconds

## Verification

```bash
# Test pre-verify (should be fast — skips diff)
gerrata 21500 --pg-file cache/21500.txt --jp2-zip cache/memoirsofcourtof00aiki_jp2.zip \
  --cache-dir cache --output reports --resume-from pre-verify

# Test pre-report (should be near-instant — only generates report)
gerrata 21500 --pg-file cache/21500.txt --jp2-zip cache/memoirsofcourtof00aiki_jp2.zip \
  --cache-dir cache --output reports --resume-from pre-report
```
