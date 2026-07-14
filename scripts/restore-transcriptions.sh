#!/usr/bin/env bash
# Restore transcriptions from archive to cache for resuming a book
# Usage: bash scripts/restore-transcriptions.sh <pg_id> <scan_id>
# Example: bash scripts/restore-transcriptions.sh 57723 orkneyingasaga00goudgoog

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TRANSCRIPTIONS_DIR="$ROOT/transcriptions"
CACHE_DIR="$ROOT/cache"

PG_ID="${1:?Usage: restore-transcriptions.sh <pg_id> <scan_id>}"
SCAN_ID="${2:?Usage: restore-transcriptions.sh <pg_id> <scan_id>}"

ARCHIVE_PATH="$TRANSCRIPTIONS_DIR/pg${PG_ID}_${SCAN_ID}"
CACHE_PATH="$CACHE_DIR/$SCAN_ID"

if [ ! -d "$ARCHIVE_PATH" ]; then
    echo "Error: Archive not found: $ARCHIVE_PATH"
    echo "Available archives:"
    ls -1 "$TRANSCRIPTIONS_DIR" 2>/dev/null || echo "  (none)"
    exit 1
fi

echo "Restoring transcriptions: $ARCHIVE_PATH → $CACHE_PATH"
mkdir -p "$CACHE_PATH"

cp -n "$ARCHIVE_PATH/01_pg_parsed.json" "$CACHE_PATH/" 2>/dev/null || true
cp -n "$ARCHIVE_PATH/01b_page_classifications.json" "$CACHE_PATH/" 2>/dev/null || true
cp -n "$ARCHIVE_PATH/02_transcriptions.jsonl" "$CACHE_PATH/" 2>/dev/null || true

echo "Done. Resume with:"
echo "  python3 -m gerrata.cli $PG_ID --scan-id $SCAN_ID --resume-from transcriptions"
