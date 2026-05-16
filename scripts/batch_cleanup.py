#!/usr/bin/env python3
"""Batch cleanup — remove large files after processing to manage disk space.

Removes PNG page images, JP2 zips, and intermediate files.
Keeps only lightweight cache (PG text, transcriptions, alignments) and reports.

Usage:
    python3 scripts/batch_cleanup.py [--dry-run] [--all] [scan_id ...]

    --dry-run  Show what would be deleted without deleting
    --all      Clean all completed books in cache/
    scan_id    Clean specific scan(s)
"""

import argparse
import shutil
import sys
from pathlib import Path

CACHE_DIR = Path(__file__).parent.parent / "cache"
PAGES_DIRS = ["pages", "pages_screw"]


def get_book_dirs() -> list[Path]:
    """Get all book cache directories."""
    books = []
    for d in CACHE_DIR.iterdir():
        if d.is_dir() and not d.name.startswith(".") and d.name != "pages" and d.name != "pages_screw" and d.name != "pg43":
            books.append(d)
    return books


def get_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    elif path.is_dir():
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return 0


def cleanup_book(book_dir: Path, dry_run: bool = False) -> dict:
    """Remove large files for a processed book. Returns stats."""
    freed = 0
    removed = []
    kept = []

    # 1. Remove PNG files in book cache dir
    for png in book_dir.glob("*.png"):
        size = png.stat().st_size
        freed += size
        removed.append((str(png), size))
        if not dry_run:
            png.unlink()

    # 2. Remove JP2 zip in cache root
    zip_name = book_dir.name + "_jp2.zip"
    zip_path = CACHE_DIR / zip_name
    if zip_path.exists():
        size = zip_path.stat().st_size
        freed += size
        removed.append((str(zip_path), size))
        if not dry_run:
            zip_path.unlink()

    # 3. Remove transcriber cache
    trans_cache = CACHE_DIR / f"{book_dir.name}_transcriptions.json"
    if trans_cache.exists():
        size = trans_cache.stat().st_size
        freed += size
        removed.append((str(trans_cache), size))
        if not dry_run:
            trans_cache.unlink()

    # 4. Remove intermediate heavy files
    heavy_files = [
        "03_scan_pages.json",
        "04_candidates_raw.json",
        "05_candidates_filtered.json",
    ]
    for hf in heavy_files:
        hf_path = book_dir / hf
        if hf_path.exists():
            size = hf_path.stat().st_size
            freed += size
            removed.append((str(hf_path), size))
            if not dry_run:
                hf_path.unlink()

    # 5. Remove extracted JP2 dirs (nested from zip extraction)
    for nested in book_dir.glob(f"{book_dir.name}_jp2"):
        if nested.is_dir():
            size = get_size(nested)
            freed += size
            removed.append((str(nested), size))
            if not dry_run:
                shutil.rmtree(nested)

    # 6. Report what's kept
    light_files = [
        "01_pg_parsed.json",
        "02_transcriptions.json",
        "03_alignments.json",
    ]
    for lf in light_files:
        if (book_dir / lf).exists():
            kept.append(str(book_dir / lf))

    # 7. Clean up top-level pages dirs
    for pd in PAGES_DIRS:
        pages_dir = CACHE_DIR / pd
        if pages_dir.exists() and pages_dir.is_dir():
            size = get_size(pages_dir)
            if size > 0:
                freed += size
                removed.append((str(pages_dir), size))
                if not dry_run:
                    shutil.rmtree(pages_dir)

    return {
        "book": book_dir.name,
        "freed_bytes": freed,
        "removed": removed,
        "kept": kept,
    }


def main():
    parser = argparse.ArgumentParser(description="Clean up large gerrata cache files")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted")
    parser.add_argument("--all", action="store_true", help="Clean all books in cache")
    parser.add_argument("scan_ids", nargs="*", help="Specific scan IDs to clean")
    args = parser.parse_args()

    books_to_clean = []
    if args.scan_ids:
        for sid in args.scan_ids:
            d = CACHE_DIR / sid
            if d.exists():
                books_to_clean.append(d)
            else:
                print(f"Warning: {d} not found", file=sys.stderr)
    else:
        books_to_clean = get_book_dirs()

    if not books_to_clean:
        print("No books to clean.")
        return

    total_freed = 0
    for book_dir in sorted(books_to_clean):
        result = cleanup_book(book_dir, dry_run=args.dry_run)
        freed_mb = result["freed_bytes"] / (1024 * 1024)
        total_freed += result["freed_bytes"]

        action = "WOULD FREE" if args.dry_run else "FREED"
        print(f"\n[{action}] {result['book']}: {freed_mb:.1f} MB")
        for path, size in result["removed"]:
            print(f"  - {Path(path).name} ({size / 1024 / 1024:.1f} MB)")
        if result["kept"]:
            print(f"  Kept: {', '.join(Path(p).name for p in result['kept'])}")

    total_mb = total_freed / (1024 * 1024)
    action = "Would free" if args.dry_run else "Freed"
    print(f"\n{action}: {total_mb:.1f} MB total")

    if not args.dry_run:
        import shutil
        _, _, free = shutil.disk_usage("/")
        free_gb = free / (1024 ** 3)
        total_gb = shutil.disk_usage("/").total / (1024 ** 3)
        print(f"Disk: {free_gb:.1f} GB free of {total_gb:.1f} GB ({100*free/total_gb:.0f}% used)")


if __name__ == "__main__":
    main()
