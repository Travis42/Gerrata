#!/usr/bin/env python3
"""Combine multiple IA scan volumes into a single pages directory.

Extracts JP2 pages from multiple volume zips, converts to PNG,
and saves with sequential numbering for use with --pages-dir.

Usage:
    python3 scripts/combine_volumes.py <pg_id> <scan_id_1> [<scan_id_2> ...]

Example:
    python3 scripts/combine_volumes.py 1150 \\
        ninebooksofdanis01saxouoft \\
        ninebooksofdanis02saxo_202107
"""

import gc
import sys
import zipfile
from pathlib import Path

import cv2

CACHE = Path(__file__).resolve().parent.parent / "cache"


def extract_volume(zip_path: Path, pages_dir: Path, start_index: int) -> int:
    """Extract pages from a JP2 zip into pages_dir with sequential numbering.

    Returns the number of pages extracted.
    """
    with zipfile.ZipFile(zip_path) as zf:
        jp2_names = sorted(n for n in zf.namelist() if n.endswith(".jp2"))
        count = len(jp2_names)
        print(f"  {count} pages in {zip_path.parent.name}")

        for i, jp2_name in enumerate(jp2_names):
            page_idx = start_index + i
            png_path = pages_dir / f"page_{page_idx:04d}.png"

            if png_path.exists():
                continue

            # Read JP2 from zip, convert to PNG
            jp2_data = zf.read(jp2_name)
            temp_path = pages_dir / "_temp.jp2"
            temp_path.write_bytes(jp2_data)
            del jp2_data

            img = cv2.imread(str(temp_path), cv2.IMREAD_ANYCOLOR)
            if img is None:
                print(f"  WARNING: could not read {jp2_name}", file=sys.stderr)
                temp_path.unlink(missing_ok=True)
                continue
            cv2.imwrite(str(png_path), img)
            del img
            temp_path.unlink(missing_ok=True)

            if (i + 1) % 50 == 0:
                gc.collect()
                print(f"  ...{i + 1}/{count}")

        return count


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    pg_id = sys.argv[1]
    scan_ids = sys.argv[2:]
    pages_dir = CACHE / f"{pg_id}_pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    global_index = 0

    for vol_num, scan_id in enumerate(scan_ids, 1):
        # Find the JP2 zip (try multiple naming patterns)
        candidates = [
            CACHE / scan_id / f"{scan_id}_jp2.zip",
            CACHE / f"{scan_id}_jp2.zip",
        ]
        zip_path = None
        for c in candidates:
            if c.exists():
                zip_path = c
                break

        if zip_path is None:
            print(f"Downloading {scan_id}...")
            import subprocess
            subprocess.run(
                ["ia", "download", scan_id, f"{scan_id}_jp2.zip", "--destdir", str(CACHE)],
                check=True,
            )
            # Find where it landed
            for c in candidates:
                if c.exists():
                    zip_path = c
                    break
            if zip_path is None:
                print(f"ERROR: could not find downloaded zip for {scan_id}", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Cached: {zip_path}")

        print(f"Extracting volume {vol_num} ({scan_id})...")
        count = extract_volume(zip_path, pages_dir, global_index)
        print(f"  Volume {vol_num}: pages {global_index}–{global_index + count - 1}")
        global_index += count
        gc.collect()

    total = len(list(pages_dir.glob("page_*.png")))
    print(f"\n=== Done: {total} pages in {pages_dir} ===")
    print(f"\nRun: gerrata {pg_id} --pages-dir {pages_dir} --pg-file cache/{pg_id}.txt ...")


if __name__ == "__main__":
    main()
