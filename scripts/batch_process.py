#!/usr/bin/env python3
"""Batch processor — run gerrata pipeline on a queue of books.

Usage:
    python3 scripts/batch_process.py [--queue queue.json] [--limit N] [--no-cleanup]

    --queue      Queue file (default: cache/batch_queue.json)
    --limit N    Process at most N books
    --no-cleanup Don't clean up after each book
    --start N    Start from book N in queue (resume)
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

CACHE_DIR = Path(__file__).parent.parent / "cache"
REPORTS_DIR = Path(__file__).parent.parent / "reports"
SCRIPTS_DIR = Path(__file__).parent
MIN_DISK_GB = 2


def check_disk() -> bool:
    """Check if enough disk space."""
    _, _, free = shutil.disk_usage("/")
    free_gb = free / (1024 ** 3)
    return free_gb >= MIN_DISK_GB


def cleanup_book(ia_id: str, dry_run: bool = False):
    """Clean up large files for a book."""
    import subprocess
    script = SCRIPTS_DIR / "batch_cleanup.py"
    cmd = [sys.executable, str(script), ia_id]
    if dry_run:
        cmd.append("--dry-run")
    subprocess.run(cmd, capture_output=True, timeout=60)


def run_gerrata(pg_id: int, ia_id: str) -> dict:
    """Run gerrata pipeline for a book. Returns result stats."""
    start = time.time()

    cmd = [
        sys.executable, "-m", "gerrata",
        str(pg_id),
        "--scan-id", ia_id,
        "--concurrency", "10",
    ]

    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,  # 10 min max per book
        cwd=SCRIPTS_DIR.parent,
    )

    elapsed = time.time() - start
    success = result.returncode == 0

    # Count errors from report
    error_count = 0
    email_entries = 0
    report_path = REPORTS_DIR / f"gutenberg{pg_id}-*_errata_email.txt"

    for rp in REPORTS_DIR.glob(f"gutenberg{pg_id}-*_errata_email.txt"):
        with open(rp) as f:
            email_entries += f.read().count("\n\nPage ")

    for rp in REPORTS_DIR.glob(f"gutenberg{pg_id}-*_errata.json"):
        with open(rp) as f:
            data = json.load(f)
            error_count = len(data.get("errors", []))

    return {
        "success": success,
        "elapsed_seconds": elapsed,
        "total_errors": error_count,
        "email_entries": email_entries,
        "stdout_tail": result.stdout[-500:] if result.stdout else "",
        "stderr_tail": result.stderr[-500:] if result.stderr else "",
    }


def main():
    parser = argparse.ArgumentParser(description="Run gerrata on batch queue")
    parser.add_argument("--queue", default=str(CACHE_DIR / "batch_queue.json"))
    parser.add_argument("--limit", type=int, default=0, help="Max books to process")
    parser.add_argument("--no-cleanup", action="store_true")
    parser.add_argument("--start", type=int, default=0, help="Start from book N")
    args = parser.parse_args()

    queue_path = Path(args.queue)
    if not queue_path.exists():
        print(f"Queue not found: {queue_path}")
        print("Run python3 scripts/batch_prepare.py first")
        sys.exit(1)

    with open(queue_path) as f:
        queue = json.load(f)

    if args.start:
        queue = queue[args.start:]
    if args.limit:
        queue = queue[:args.limit]

    if not queue:
        print("No books in queue to process")
        return

    print(f"Processing {len(queue)} books...")
    print(f"{'='*60}")

    results = []
    for i, book in enumerate(queue):
        pg_id = book["pg_id"]
        ia_id = book["ia_id"]
        title = book["title"]

        print(f"\n[{i+1}/{len(queue)}] PG #{pg_id}: {title}")
        print(f"  IA: {ia_id}")

        # Check disk
        if not check_disk():
            print(f"  WARNING: Low disk space, running cleanup...")
            cleanup_book(ia_id)
            if not check_disk():
                print(f"  FATAL: Still low disk space after cleanup. Stopping.")
                break

        # Run gerrata
        result = run_gerrata(pg_id, ia_id)

        status = "OK" if result["success"] else "FAILED"
        print(f"  {status} in {result['elapsed_seconds']:.0f}s")
        print(f"  Errors: {result['total_errors']} total, {result['email_entries']} email entries")

        if not result["success"]:
            print(f"  STDERR: {result['stderr_tail'][:200]}")

        result["pg_id"] = pg_id
        result["ia_id"] = ia_id
        result["title"] = title
        results.append(result)

        # Clean up
        if not args.no_cleanup:
            print(f"  Cleaning up...")
            cleanup_book(ia_id)

        time.sleep(2)  # Rate limit between books

    # Summary
    print(f"\n{'='*60}")
    print(f"BATCH SUMMARY")
    print(f"{'='*60}")

    success_count = sum(1 for r in results if r["success"])
    fail_count = len(results) - success_count
    total_email = sum(r["email_entries"] for r in results)
    total_time = sum(r["elapsed_seconds"] for r in results)

    print(f"  Books processed: {len(results)}")
    print(f"  Successful: {success_count}")
    print(f"  Failed: {fail_count}")
    print(f"  Total email entries: {total_email}")
    print(f"  Total time: {total_time/60:.1f} minutes")

    for r in results:
        status = "OK" if r["success"] else "FAIL"
        print(f"  [{status}] PG #{r['pg_id']}: {r['title'][:40]} — "
              f"{r['email_entries']} email entries ({r['elapsed_seconds']:.0f}s)")

    # Save results
    results_path = CACHE_DIR / "batch_results.json"
    # Load existing results if any
    existing = []
    if results_path.exists():
        with open(results_path) as f:
            existing = json.load(f)

    # Merge (update by pg_id)
    existing_ids = {r["pg_id"] for r in existing}
    for r in results:
        if r["pg_id"] in existing_ids:
            existing = [e for e in existing if e["pg_id"] != r["pg_id"]]
        existing.append(r)

    with open(results_path, "w") as f:
        json.dump(existing, f, indent=2)

    # Disk status
    _, _, free = shutil.disk_usage("/")
    print(f"\n  Disk: {free / (1024**3):.1f} GB free")


if __name__ == "__main__":
    main()
