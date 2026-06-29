#!/usr/bin/env bash
# Gerrata cleanup: clears cache, archives reports
# Usage: bash scripts/cleanup.sh [--dry-run]

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE_DIR="$ROOT/cache"
REPORTS_DIR="$ROOT/reports"
ARCHIVE_DIR="$REPORTS_DIR/completed"

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

echo "=== Gerrata Cleanup ==="
echo "Project: $ROOT"
echo "Mode: $([ "$DRY_RUN" = true ] && echo 'DRY RUN (no changes)' || echo 'LIVE')"
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
