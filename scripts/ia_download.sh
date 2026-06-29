#!/usr/bin/env bash
# Download IA scan to cache/ directory
# Usage: bash scripts/ia_download.sh <scan-id> [file]
#
# Ensures downloads go to cache/ instead of the project root.
# The `ia` CLI (internetarchive package) defaults to <cwd>/<identifier>/.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/cache"

if [ $# -lt 1 ]; then
    echo "Usage: bash scripts/ia_download.sh <scan-id> [file]"
    echo ""
    echo "Examples:"
    echo "  bash scripts/ia_download.sh volsungasagastor00spariala"
    echo "  bash scripts/ia_download.sh volsungasagastor00spariala volsungasagastor00spariala_jp2.zip"
    exit 1
fi

SCAN_ID="$1"
FILE="${2:-}"

mkdir -p "$DEST"

if [ -n "$FILE" ]; then
    echo "Downloading $FILE from $SCAN_ID to $DEST/"
    ia download "$SCAN_ID" "$FILE" --destdir "$DEST"
else
    echo "Downloading all files from $SCAN_ID to $DEST/"
    ia download "$SCAN_ID" --destdir "$DEST"
fi

echo "Done. Files saved to: $DEST/"
