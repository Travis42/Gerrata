#!/usr/bin/env bash
# Sequential errata pipeline runner — OCR mode (no JP2 images needed)
# Uses --no-vision-transcribe to avoid downloading large JP2 zips
# Only needs DJVU text (~500KB per book)
set -uo pipefail

REPO="/root/projects/gerrata"
cd "$REPO"

API_KEY="REDACTED"
WEBHOOK="--channel telegram --to '-1003815086962:3515'"
CONCURRENCY=10
LOG_FILE="/root/clawd/projects/gerrata-queue.log"

# Queue: PG_ID|IA_SCAN_ID|DESCRIPTION
# All scan IDs verified to have metadata + djvu.txt (2026-05-11)
QUEUE=(
  "84|frankenstein00litt|Frankenstein"
  "1184|CountOfMonteCristoV2|Count of Monte Cristo"
  "2554|crimepunishment00dost|Crime and Punishment"
  "2641|roomwithview0000fors_u4k9|A Room with a View"
  "28054|brotherskaramazo00dost|Brothers Karamazov"
  "42486|turnofscrew00jame|Turn of the Screw"
  "67979|bluecastle0000unse_a1b9|The Blue Castle"
)

# Already completed (skip these):
# PG#58169 Hegel's Lectures ✅
# PG#43 Dr. Jekyll ✅
# PG#2701 Moby Dick ✅
# PG#1342 Pride and Prejudice ✅
# PG#1513 Romeo and Juliet ✅
# PG#11 Alice in Wonderland ✅
# PG#145 Middlemarch ✅

run_book() {
  local pg_id="$1"
  local scan_id="$2"
  local desc="$3"
  local start

  # Skip if already completed
  existing=$(ls reports/gutenberg${pg_id}-*_errata_email*.txt 2>/dev/null | sort | tail -1)
  if [ -n "$existing" ]; then
    echo "⏭️  PG #$pg_id ($desc) — already done, skipping"
    return 0
  fi

  echo "========================================" | tee -a "$LOG_FILE"
  echo "Starting: PG #$pg_id — $desc" | tee -a "$LOG_FILE"
  echo "Scan: $scan_id" | tee -a "$LOG_FILE"
  echo "Mode: OCR (no vision)" | tee -a "$LOG_FILE"
  echo "Disk: $(df -h / | tail -1 | awk '{print $4}') free" | tee -a "$LOG_FILE"
  echo "Time: $(date)" | tee -a "$LOG_FILE"
  echo "========================================" | tee -a "$LOG_FILE"

  # Clean any previous cache for this book
  rm -rf cache/pages/ cache/*.zip cache/*.htm 2>/dev/null || true

  start=$(date +%s)

  # Try up to 3 times with exponential backoff
  local attempt=0
  local max_attempts=3
  local success=false

  while [ $attempt -lt $max_attempts ] && [ "$success" = false ]; do
    attempt=$((attempt + 1))
    echo "" | tee -a "$LOG_FILE"
    echo "Attempt $attempt/$max_attempts..." | tee -a "$LOG_FILE"

    ZAI_API_KEY="$API_KEY" gerrata "$pg_id" \
      --scan-id "$scan_id" \
      --concurrency "$CONCURRENCY" \
      --output ./reports \
      --no-vision-transcribe \
      -v 2>&1 | grep -v "DEBUG" | tee -a "$LOG_FILE"

    local exit_code=${PIPESTATUS[0]}
    echo "Exit code: $exit_code" | tee -a "$LOG_FILE"

    # Verify output exists
    email=$(ls reports/gutenberg${pg_id}-*_errata_email*.txt 2>/dev/null | sort | tail -1)
    if [ -n "$email" ]; then
      success=true
      break
    fi

    # Backoff before retry
    if [ $attempt -lt $max_attempts ]; then
      local wait=$((attempt * 120))
      echo "No output found. Waiting ${wait}s before retry..." | tee -a "$LOG_FILE"
      sleep $wait
    fi
  done

  local end=$(date +%s)
  local duration=$(( (end - start) / 60 ))

  if [ "$success" = true ]; then
    email=$(ls reports/gutenberg${pg_id}-*_errata_email*.txt 2>/dev/null | sort | tail -1)
    candidates=$(grep -c "^Page " "$email" 2>/dev/null || echo "0")
    size=$(du -h "$email" | awk '{print $1}')
    echo "" | tee -a "$LOG_FILE"
    echo "✅ PG #$pg_id DONE: $candidates candidates ($size) in ${duration}min (attempt $attempt)" | tee -a "$LOG_FILE"
    /root/clawd/scripts/wake-agent.sh "✅ Gerrata done: $desc (PG #$pg_id) — $candidates candidates in ${duration}min" $WEBHOOK
  else
    echo "" | tee -a "$LOG_FILE"
    echo "❌ PG #$pg_id FAILED after ${duration}min and $max_attempts attempts" | tee -a "$LOG_FILE"
    /root/clawd/scripts/wake-agent.sh "❌ Gerrata FAILED: $desc (PG #$pg_id) after $max_attempts attempts" $WEBHOOK
  fi

  # Cleanup input cache
  rm -rf cache/pages/ cache/*.zip cache/*.htm 2>/dev/null || true
  echo "Cache cleaned. Disk: $(df -h / | tail -1 | awk '{print $4}') free" | tee -a "$LOG_FILE"
  echo "" | tee -a "$LOG_FILE"

  # Brief pause between runs
  sleep 30
}

# Create log file
mkdir -p /root/clawd/projects
echo "Gerrata OCR queue started: $(date)" > "$LOG_FILE"
echo "Queue: ${#QUEUE[@]} books" >> "$LOG_FILE"
echo "Mode: --no-vision-transcribe (OCR only, no JP2 downloads)" >> "$LOG_FILE"

# Run the queue
TOTAL=${#QUEUE[@]}
for i in "${!QUEUE[@]}"; do
  IFS='|' read -r pg_id scan_id desc <<< "${QUEUE[$i]}"
  echo "" | tee -a "$LOG_FILE"
  echo "=== Queue: $((i+1))/$TOTAL ===" | tee -a "$LOG_FILE"
  run_book "$pg_id" "$scan_id" "$desc"
done

echo "========================================" | tee -a "$LOG_FILE"
echo "ALL DONE: $(date)" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

# Final summary
echo "" | tee -a "$LOG_FILE"
echo "Reports generated:" | tee -a "$LOG_FILE"
ls -lh reports/gutenberg*_errata_email*.txt 2>/dev/null | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "Disk: $(df -h / | tail -1 | awk '{print $4}') free" | tee -a "$LOG_FILE"

# Final webhook
/root/clawd/scripts/wake-agent.sh "All ${TOTAL} gerrata OCR queue runs complete" $WEBHOOK
