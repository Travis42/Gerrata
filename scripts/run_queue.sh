#!/usr/bin/env bash
# Sequential errata pipeline runner
# Runs each book in order: download → pipeline → verify → cleanup → next
set -euo pipefail

REPO="/root/projects/gerrata"
cd "$REPO"

API_KEY="REDACTED"
WEBHOOK="--channel telegram --to '-1003815086962:3515'"
CONCURRENCY=10

# Queue: PG_ID|IA_SCAN_ID|DESCRIPTION
QUEUE=(
  "1342|prideprejudice00aust|Pride and Prejudice"
  "145|middlemarch01elio|Middlemarch"
  "11|aliceinwonderlan0000unse_v9s1|Alice in Wonderland"
  "1513|2tragedyofromeoj00shakuoft|Romeo and Juliet"
)

run_book() {
  local pg_id="$1"
  local scan_id="$2"
  local desc="$3"
  local start

  echo "========================================"
  echo "Starting: PG #$pg_id — $desc"
  echo "Scan: $scan_id"
  echo "Disk: $(df -h / | tail -1 | awk '{print $4}') free"
  echo "========================================"

  # Clean any previous cache for this book
  rm -rf cache/pages/ cache/*.zip cache/*.htm 2>/dev/null || true

  start=$(date +%s)

  ZAI_API_KEY="$API_KEY" gerrata "$pg_id" \
    --scan-id "$scan_id" \
    --concurrency "$CONCURRENCY" \
    --output ./reports \
    -v \
    ; /root/clawd/scripts/wake-agent.sh "Pipeline done: PG #$pg_id ($desc)" "$WEBHOOK"

  local exit_code=$?
  local end=$(date +%s)
  local duration=$(( (end - start) / 60 ))

  # Verify output
  email=$(ls reports/gutenberg${pg_id}-*_errata_email*.txt 2>/dev/null | sort | tail -1)
  if [ -n "$email" ]; then
    candidates=$(grep -c "Page " "$email" 2>/dev/null || echo "?")
    size=$(du -h "$email" | awk '{print $1}')
    echo "✅ PG #$pg_id DONE: $candidates candidates ($size) in ${duration}min"
  else
    echo "❌ PG #$pg_id FAILED (exit $exit_code) after ${duration}min"
  fi

  # Cleanup input cache
  rm -rf cache/pages/ cache/*.zip cache/*.htm 2>/dev/null || true
  echo "Cache cleaned. Disk: $(df -h / | tail -1 | awk '{print $4}') free"
  echo ""

  # Brief pause between runs
  sleep 10
}

# Run the queue
TOTAL=${#QUEUE[@]}
for i in "${!QUEUE[@]}"; do
  IFS='|' read -r pg_id scan_id desc <<< "${QUEUE[$i]}"
  echo "=== Queue: $((i+1))/$TOTAL ==="
  run_book "$pg_id" "$scan_id" "$desc"
done

echo "========================================"
echo "ALL DONE"
echo "========================================"

# Final summary
echo ""
echo "Reports generated:"
ls -lh reports/gutenberg*_errata_email*.txt 2>/dev/null
echo ""
echo "Disk: $(df -h / | tail -1 | awk '{print $4}') free"

# Final webhook
/root/clawd/scripts/wake-agent.sh "All errata pipeline runs complete" "$WEBHOOK"
