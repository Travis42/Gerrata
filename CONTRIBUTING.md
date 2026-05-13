# Gerrata — Contributing Guidelines

## No Standalone Hack Scripts

**Never create standalone scripts that duplicate pipeline code.** This has caused repeated problems in this project (e.g., `rerun_from_candidates.py`, `gen_report_from_filtered.py` — both deleted).

When you want to test a variation (different model, different filter, different parameter), use one of these approaches:

1. **Add a CLI flag** to `gerrata` — e.g., `--verify-provider`, `--resume-from`, `--strict`
2. **Import from the main codebase** — scripts must import functions, not copy them
3. **Extract shared logic into a library module** before writing anything that would duplicate it

The test: "Can I express this as a flag on the existing pipeline?" If yes, add the flag. If no, extract a shared module.

## Pipeline Resume Points

The pipeline caches intermediate results and can resume from any stage:

```
--resume-from pg-parsed           Step 1 done, skip download
--resume-from transcriptions      Step 3 done, skip API transcription
--resume-from alignments          Step 4 done, skip alignment
--resume-from candidates-raw      Step 5 done, re-run filtering + verification
--resume-from candidates-filtered Step 6 done, re-run verification only
```

If you're tempted to create a script that "starts midway," use `--resume-from` instead.

## Provider Presets

Use `--verify-provider` to switch verification API without hardcoding URLs:

```
--verify-provider zai          Z.AI native API (default)
--verify-provider openrouter   openrouter.ai (OPENROUTER_API_KEY or ~/.secrets/openrouter.key)
--verify-provider openai       OpenAI (OPENAI_API_KEY)
--verify-provider anthropic    Anthropic (ANTHROPIC_API_KEY)
```

Override with `--verify-url` and `--verify-key` for custom endpoints.
