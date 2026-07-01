#!/usr/bin/env bash
# Extract multiple IA scan volumes into a single pages directory
# for multi-volume works.
#
# Usage: bash scripts/extract_multi_volume.sh <pg_id> <scan_id_1> [scan_id_2] [scan_id_3] ...
#
# Downloads JP2 zips for each volume, extracts pages sequentially
# (vol1 pages first, then vol2, etc.), and places them in cache/<pg_id>_pages/
#
# Then run gerrata with: --pages-dir cache/<pg_id>_pages

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="$ROOT/cache"

if [ $# -lt 2 ]; then
    echo "Usage: bash scripts/extract_multi_volume.sh <pg_id> <scan_id_1> [scan_id_2] ..."
    echo ""
    echo "Example: bash scripts/extract_multi_volume.sh 1150 \\"
    echo "    ninebooksofdanis01saxouoft \\"
    echo "    ninebooksofdanis02saxo_202107"
    exit 1
fi

PG_ID="$1"
shift
SCAN_IDS=("$@")

PAGES_DIR="$CACHE/${PG_ID}_pages"
mkdir -p "$PAGES_DIR"

VOL_NUM=0
GLOBAL_PAGE=0

for SCAN_ID in "${SCAN_IDS[@]}"; do
    VOL_NUM=$((VOL_NUM + 1))
    ZIP_PATH="$CACHE/${SCAN_ID}/${SCAN_ID}_jp2.zip"

    # Download if not cached
    if [ ! -f "$ZIP_PATH" ]; then
        echo "Downloading $SCAN_ID JP2 zip..."
        mkdir -p "$CACHE/$SCAN_ID"
        ia download "$SCAN_ID" "${SCAN_ID}_jp2.zip" --destdir "$CACHE"
    else
        echo "Cached: $ZIP_PATH"
    fi

    # Extract JP2s to temp, convert to sequentially-numbered PNGs
    TEMP_DIR="$CACHE/_vol_temp"
    mkdir -p "$TEMP_DIR"

    echo "Extracting volume $VOL_NUM ($SCAN_ID)..."
    python3 -c "
import zipfile, cv2, re, sys, os
from pathlib import Path

zip_path = '$ZIP_PATH'
temp_dir = Path('$TEMP_DIR')
pages_dir = Path('$PAGES_DIR')
global_start = $GLOBAL_PAGE

with zipfile.ZipFile(zip_path) as zf:
    jp2_names = sorted(n for n in zf.namelist() if n.endswith('.jp2'))
    print(f'  {len(jp2_names)} pages in volume $VOL_NUM')

    for i, jp2_name in enumerate(jp2_names):
        page_idx = global_start + i
        png_name = f'page_{page_idx:04d}.png'
        png_path = pages_dir / png_name

        if png_path.exists():
            continue

        # Extract and convert
        jp2_data = zf.read(jp2_name)
        jp2_temp = temp_dir / f'_temp.jp2'
        jp2_temp.write_bytes(jp2_data)
        del jp2_data

        img = cv2.imread(str(jp2_temp), cv2.IMREAD_ANYCOLOR)
        if img is None:
            print(f'  WARNING: could not read {jp2_name}', file=sys.stderr)
            continue
        cv2.imwrite(str(png_path), img)
        del img
        jp2_temp.unlink(missing_ok=True)

        if (i + 1) % 50 == 0:
            import gc; gc.collect()
            print(f'  ...{i+1}/{len(jp2_names)} pages')

    print(f'  Volume $VOL_NUM: pages {global_start}–{global_start + len(jp2_names) - 1}')
" 2>&1

    # Update global page counter
    PAGE_COUNT=$(python3 -c "
import zipfile
with zipfile.ZipFile('$ZIP_PATH') as zf:
    print(sum(1 for n in zf.namelist() if n.endswith('.jp2')))
")
    GLOBAL_PAGE=$((GLOBAL_PAGE + PAGE_COUNT))

    # Cleanup
    rm -rf "$TEMP_DIR"
    echo ""
done

# Count total pages
TOTAL=$(ls "$PAGES_DIR"/page_*.png 2>/dev/null | wc -l)
echo "=== Done ==="
echo "Total pages: $TOTAL in $PAGES_DIR"
echo ""
echo "Run gerrata with:"
echo "  gerrata $PG_ID --pages-dir $PAGES_DIR --pg-file cache/$PG_ID.txt ..."
