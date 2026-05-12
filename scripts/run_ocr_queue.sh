#!/usr/bin/env bash
# OCR-mode errata queue runner
# Uses local OCR text files (no IA download needed)
# Each report will note OCR-only source mode
set -uo pipefail

REPO="/root/projects/gerrata"
cd "$REPO"

API_KEY="REDACTED"
WEBHOOK="--channel telegram --to '-1003815086962:3515'"
CONCURRENCY=10
LOG_FILE="/root/clawd/projects/gerrata-queue.log"

# Queue: PG_ID|OCR_FILE|SCAN_ID|DESCRIPTION
QUEUE=(
  "84|cache/shelley-1888-frankenstein_djvu.txt|shelley-1888-frankenstein|Frankenstein"
  "1184|cache/Count_of_Monte_Cristo_v2_djvu.txt|CountOfMonteCristoV2|Count of Monte Cristo"
  "2554|cache/crimepunishment1917dost_djvu.txt|crimepunishment1917dost|Crime and Punishment"
  "2641|cache/roomwithview00fors_4_djvu.txt|roomwithview00fors_4|A Room with a View"
  "28054|cache/brotherskaramazo00dost_djvu.txt|brotherskaramazo00dost|Brothers Karamazov"
  "42486|cache/in.ernet.dli.2015.459091_djvu.txt|in.ernet.dli.2015.459091|Turn of the Screw"
  "67979|cache/bluecastle0000lucy_djvu.txt|bluecastle0000lucy|The Blue Castle"
)

run_book() {
  local pg_id="$1"
  local ocr_file="$2"
  local scan_id="$3"
  local desc="$4"
  local start

  # Skip if already completed
  existing=$(ls reports/gutenberg${pg_id}-*_errata_email*.txt 2>/dev/null | sort | tail -1)
  if [ -n "$existing" ]; then
    echo "⏭️  PG #$pg_id ($desc) — already done, skipping"
    return 0
  fi

  echo "========================================" | tee -a "$LOG_FILE"
  echo "Starting: PG #$pg_id — $desc" | tee -a "$LOG_FILE"
  echo "OCR file: $ocr_file" | tee -a "$LOG_FILE"
  echo "Scan ID: $scan_id" | tee -a "$LOG_FILE"
  echo "Mode: OCR-only (no vision verification)" | tee -a "$LOG_FILE"
  echo "Time: $(date)" | tee -a "$LOG_FILE"
  echo "========================================" | tee -a "$LOG_FILE"

  start=$(date +%s)

  # Run gerrata with OCR file, no vision
  ZAI_API_KEY="$API_KEY" gerrata "$pg_id" \
    --scan-id "$scan_id" \
    --ocr-file "$ocr_file" \
    --concurrency "$CONCURRENCY" \
    --output ./reports \
    --no-vision-transcribe \
    --no-verify \
    -v 2>&1 | grep -v "DEBUG" | tee -a "$LOG_FILE"

  local exit_code=${PIPESTATUS[0]}
  local end=$(date +%s)
  local duration=$(( (end - start) / 60 ))

  # Check for output
  email=$(ls reports/gutenberg${pg_id}-*_errata_email*.txt 2>/dev/null | sort | tail -1)

  if [ -n "$email" ]; then
    # Append OCR source disclaimer to the report
    disclaimer="\n---\nNOTE: This errata report was generated using OCR text comparison only (no vision/visual verification against original page scans). Results may include false positives from OCR errors in the source scan. Source scan: https://archive.org/details/${scan_id}\n"

    # Only append if not already present
    if ! grep -q "OCR text comparison only" "$email" 2>/dev/null; then
      echo -e "$disclaimer" >> "$email"
    fi

    candidates=$(grep -c "^Page " "$email" 2>/dev/null || echo "0")
    size=$(du -h "$email" | awk '{print $1}')
    echo "" | tee -a "$LOG_FILE"
    echo "✅ PG #$pg_id DONE: $candidates candidates ($size) in ${duration}min" | tee -a "$LOG_FILE"
    /root/clawd/scripts/wake-agent.sh "✅ Gerrata OCR done: $desc (PG #$pg_id) — $candidates candidates" $WEBHOOK
  else
    echo "" | tee -a "$LOG_FILE"
    echo "❌ PG #$pg_id FAILED (${duration}min, exit $exit_code)" | tee -a "$LOG_FILE"
    /root/clawd/scripts/wake-agent.sh "❌ Gerrata FAILED: $desc (PG #$pg_id)" $WEBHOOK
  fi

  echo "" | tee -a "$LOG_FILE"
  sleep 15
}

mkdir -p /root/clawd/projects
echo "Gerrata OCR queue started: $(date)" > "$LOG_FILE"
echo "Mode: --ocr-file + --no-vision-transcribe + --no-verify" >> "$LOG_FILE"
echo "Queue: ${#QUEUE[@]} books" >> "$LOG_FILE"

TOTAL=${#QUEUE[@]}
for i in "${!QUEUE[@]}"; do
  IFS='|' read -r pg_id ocr_file scan_id desc <<< "${QUEUE[$i]}"
  echo "" | tee -a "$LOG_FILE"
  echo "=== Queue: $((i+1))/$TOTAL ===" | tee -a "$LOG_FILE"
  run_book "$pg_id" "$ocr_file" "$scan_id" "$desc"
done

echo "========================================" | tee -a "$LOG_FILE"
echo "ALL DONE: $(date)" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"
echo "Final reports:" | tee -a "$LOG_FILE"
ls -lh reports/gutenberg*_errata_email*.txt 2>/dev/null | tee -a "$LOG_FILE"

/root/clawd/scripts/wake-agent.sh "All ${TOTAL} gerrata OCR runs complete" $WEBHOOK
