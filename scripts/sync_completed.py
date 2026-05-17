#!/usr/bin/env python3
"""Sync completed.json with reports/completed/ directory.

Any book with report files in reports/completed/ gets marked as submitted_to_pg: true.
New books found in reports/completed/ that aren't in completed.json get added.

Usage:
    python3 scripts/sync_completed.py [--dry-run]
"""

import argparse
import glob
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
COMPLETED_FILE = PROJECT_ROOT / "cache" / "completed.json"
REPORTS_DIR = PROJECT_ROOT / "reports" / "completed"


def extract_pg_id(filename: str) -> int | None:
    """Extract PG ID from a report filename like 'gutenberg1234-title_errata.json'."""
    match = re.search(r'gutenberg(\d+)', filename)
    return int(match.group(1)) if match else None


def main():
    parser = argparse.ArgumentParser(description="Sync completed.json with reports/completed/")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()

    if not COMPLETED_FILE.exists():
        print(f"Error: {COMPLETED_FILE} not found")
        return

    completed = json.load(open(COMPLETED_FILE))
    existing_ids = {c["pg_id"] for c in completed}

    # Scan reports/completed/ for all report files
    completed_files = sorted(glob.glob(str(REPORTS_DIR / "gutenberg*")))
    if not completed_files:
        print("No files in reports/completed/")
        return

    # Group by PG ID
    books_in_dir: dict[int, list[str]] = {}
    for f in completed_files:
        pg_id = extract_pg_id(f)
        if pg_id:
            books_in_dir.setdefault(pg_id, []).append(Path(f).name)

    changes = []

    # Mark existing entries as submitted if their reports are in completed/
    for c in completed:
        pg_id = c["pg_id"]
        if pg_id in books_in_dir and not c.get("submitted_to_pg", False):
            c["submitted_to_pg"] = True
            c["report_files"] = sorted(books_in_dir[pg_id])
            changes.append(f"  ✓ PG#{pg_id}: marked submitted_to_pg (files: {len(books_in_dir[pg_id])})")
        elif pg_id in books_in_dir and c.get("submitted_to_pg", False):
            # Already marked, just update file list
            c["report_files"] = sorted(books_in_dir[pg_id])

    # Add new entries not yet in completed.json
    for pg_id, files in sorted(books_in_dir.items()):
        if pg_id not in existing_ids:
            completed.append({
                "pg_id": pg_id,
                "title": "",
                "author": "",
                "ia_id": "",
                "completed_at": "",
                "report_files": sorted(files),
                "submitted_to_pg": True,
            })
            changes.append(f"  + PG#{pg_id}: added (files: {len(files)})")

    if not changes:
        print("Nothing to sync — already up to date.")
        return

    print(f"Changes ({len(changes)}):")
    for c in changes:
        print(c)

    if not args.dry_run:
        json.dump(completed, open(COMPLETED_FILE, "w"), indent=2)
        print(f"\nUpdated {COMPLETED_FILE}")

    # Summary
    submitted = sum(1 for c in completed if c.get("submitted_to_pg"))
    pending = sum(1 for c in completed if not c.get("submitted_to_pg"))
    print(f"\nSummary: {submitted} submitted, {pending} pending ({len(completed)} total)")


if __name__ == "__main__":
    main()
