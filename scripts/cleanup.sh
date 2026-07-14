#!/usr/bin/env bash
# Gerrata cleanup: archives transcriptions, clears cache, archives reports
# Usage: bash scripts/cleanup.sh [--dry-run]

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE_DIR="$ROOT/cache"
REPORTS_DIR="$ROOT/reports"
ARCHIVE_DIR="$REPORTS_DIR/completed"
TRANSCRIPTIONS_DIR="$ROOT/transcriptions"

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

echo "=== Gerrata Cleanup ==="
echo "Project: $ROOT"
echo "Mode: $([ "$DRY_RUN" = true ] && echo 'DRY RUN (no changes)' || echo 'LIVE')"
echo ""

# --- Transcriptions archive ---
# Save the expensive artifacts (OCR API output) before wiping cache.
# Resume kit: 01_pg_parsed.json, 01b_page_classifications.json, 02_transcriptions.jsonl
# These allow regenerating everything downstream in seconds without API calls.
if [ -d "$CACHE_DIR" ]; then
    mkdir -p "$TRANSCRIPTIONS_DIR"
    for book_dir in "$CACHE_DIR"/*/; do
        [ -d "$book_dir" ] || continue
        scan_id=$(basename "$book_dir")

        # Find PG ID from the pg_parsed file
        pg_id=""
        if [ -f "$book_dir/01_pg_parsed.json" ]; then
            pg_id=$(python3 -c "
import json, sys
with open('$book_dir/01_pg_parsed.json') as f:
    d = json.load(f)
print(d.get('pg_id', d.get('source_id', '')))
" 2>/dev/null || echo "")
        fi

        # Fall back to looking for a cached PG file
        if [ -z "$pg_id" ]; then
            pg_file=$(ls "$CACHE_DIR"/*.txt 2>/dev/null | head -1)
            if [ -n "$pg_file" ]; then
                pg_id=$(basename "$pg_file" .txt)
            fi
        fi

        # Determine archive folder name
        if [ -n "$pg_id" ]; then
            archive_name="pg${pg_id}_${scan_id}"
        else
            archive_name="$scan_id"
        fi

        archive_path="$TRANSCRIPTIONS_DIR/$archive_name"

        # Check if transcriptions exist (the expensive artifact)
        if [ -f "$book_dir/02_transcriptions.jsonl" ]; then
            if [ "$DRY_RUN" = false ]; then
                mkdir -p "$archive_path"
                cp -n "$book_dir/01_pg_parsed.json" "$archive_path/" 2>/dev/null || true
                cp -n "$book_dir/01b_page_classifications.json" "$archive_path/" 2>/dev/null || true
                cp -n "$book_dir/02_transcriptions.jsonl" "$archive_path/" 2>/dev/null || true
                echo "  Archived transcriptions: $archive_name"
            else
                echo "  Would archive: $archive_name"
            fi
        fi
    done
fi

echo ""

# --- Cache ---
if [ -d "$CACHE_DIR" ]; then
    CACHE_SIZE=$(du -sh "$CACHE_DIR" 2>/dev/null | cut -f1)
    CACHE_ITEMS=$(find "$CACHE_DIR" -mindepth 1 -maxdepth 1 | wc -l)
    echo "Cache: $CACHE_ITEMS subdirectories, $CACHE_SIZE"
    if [ "$DRY_RUN" = false ]; then
        rm -rf "${CACHE_DIR:?}/"*
        echo "  → Cleared"
    fi
else
    echo "Cache: does not exist, skipping"
fi

echo ""

# --- Reports ---
if [ -d "$REPORTS_DIR" ]; then
    mkdir -p "$ARCHIVE_DIR"

    # Count report files (excluding the completed/ dir itself)
    REPORT_COUNT=$(find "$REPORTS_DIR" -maxdepth 1 -type f | wc -l)
    echo "Reports: $REPORT_COUNT files to archive"

    if [ "$REPORT_COUNT" -gt 0 ]; then
        if [ "$DRY_RUN" = false ]; then
            # Move all files (not directories) into completed/
            find "$REPORTS_DIR" -maxdepth 1 -type f -exec mv -t "$ARCHIVE_DIR" {} +
            echo "  → Moved to reports/completed/"
        fi
    fi
else
    echo "Reports: does not exist, skipping"
fi

echo ""
echo "=== Done ==="
