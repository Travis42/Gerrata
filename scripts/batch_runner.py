#!/usr/bin/env python3
"""
Gerrata batch runner — processes the PG top 30 errata queue.

Usage:
    python3 batch_runner.py                    # run next unprocessed book
    python3 batch_runner.py --next N            # peek at next N books
    python3 batch_runner.py --run-all            # loop through entire queue
    python3 batch_runner.py --check              # check current pipeline status
    python3 batch_runner.py --cleanup            # free disk by removing input caches

For each book:
1. Find IA scan (requires manual confirmation of scan quality)
2. Download PG HTML + JP2 zip
3. Run gerrata pipeline
4. Verify output exists and report failures
5. Clean up input cache
6. Update ERRATA_QUEUE.md
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

# Timezone
AZORES_TZ_OFFSET = -1  # UTC-1

REPO_DIR = Path(__file__).resolve().parent.parent
QUEUE_FILE = REPO_DIR / "ERRATA_QUEUE.md"
REPORTS_DIR = REPO_DIR / "reports"
CACHE_DIR = REPO_DIR / "cache"
API_KEY = os.environ.get("ZAI_API_KEY", "")


def load_queue() -> list[dict]:
    """Parse ERRATA_QUEUE.md into a list of book entries."""
    if not QUEUE_FILE.exists():
        return []

    entries = []
    header_seen = False
    col_map = {}  # column name -> index

    with open(QUEUE_FILE) as f:
        for line in f:
            line_raw = line.rstrip()
            if not line_raw.startswith("|"):
                # If we've already found data rows, stop at non-table line
                # (avoids confusing the run log table for the queue table)
                if entries:
                    break
                continue
            if line_raw.startswith("|#") or line_raw.startswith("|---"):
                continue

            # Split preserving empty cells to maintain column alignment
            raw_parts = [p.strip() for p in line_raw.split("|")]
            if raw_parts and raw_parts[0] == "":
                raw_parts = raw_parts[1:]
            if raw_parts and raw_parts[-1] == "":
                raw_parts = raw_parts[:-1]

            # Detect header row
            if "PG ID" in raw_parts and "Title" in raw_parts:
                for i, col_name in enumerate(raw_parts):
                    col_name_lower = col_name.lower()
                    if "num" in col_name_lower or col_name_lower == "#":
                        col_map["num"] = i
                    elif "pg" in col_name_lower and "id" in col_name_lower:
                        col_map["pg_id"] = i
                    elif "title" in col_name_lower:
                        col_map["title"] = i
                    elif "author" in col_name_lower:
                        col_map["author"] = i
                    elif "status" in col_name_lower:
                        col_map["status"] = i
                    elif "scan" in col_name_lower and "ia" in col_name_lower:
                        col_map["scan_id"] = i
                    elif "candid" in col_name_lower:
                        col_map["candidates"] = i
                    elif "note" in col_name_lower:
                        col_map["notes"] = i
                header_seen = True
                continue

            if not header_seen or not col_map:
                continue

            try:
                num = int(raw_parts[col_map.get("num", 0)])
                pg_id = int(raw_parts[col_map.get("pg_id", 1)])
                status_cell = raw_parts[col_map.get("status", 4)]
                if "[x]" in status_cell:
                    status = "done"
                elif "[~]" in status_cell:
                    status = "running"
                else:
                    status = "pending"

                scan_col = col_map.get("scan_id", 5)
                scan_id = raw_parts[scan_col] if scan_col < len(raw_parts) else ""
                # Filter out non-scan values
                if scan_id and not re.match(r'^[a-zA-Z0-9._-]+$', scan_id):
                    scan_id = ""

                entries.append({
                    "num": num,
                    "pg_id": pg_id,
                    "title": raw_parts[col_map.get("title", 2)],
                    "author": raw_parts[col_map.get("author", 3)],
                    "status": status,
                    "scan_id": scan_id,
                })
            except (ValueError, IndexError, KeyError):
                pass
    return entries


def check_disk() -> dict:
    """Check available disk space."""
    stat = os.statvfs(REPO_DIR)
    total = stat.f_blocks * stat.f_frsize
    free = stat.f_bavail * stat.f_frsize
    used = total - free
    return {
        "total_gb": total / 1e9,
        "free_gb": free / 1e9,
        "used_gb": used / 1e9,
        "used_pct": (used / total) * 100,
    }


def check_running_pipeline() -> dict | None:
    """Check if a gerrata pipeline is currently running."""
    try:
        result = subprocess.run(
            ["pgrep", "-af", "gerrata"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            for line in lines:
                match = re.search(r"gerrata\s+(\d+)", line)
                if match:
                    pg_id = match.group(1)
                    # Get elapsed time
                    pid_match = re.search(r"^(\d+)", line)
                    pid = pid_match.group(1) if pid_match else "?"
                    try:
                        etime = subprocess.run(
                            ["ps", "-o", "etime=", "-p", pid],
                            capture_output=True, text=True, timeout=5
                        ).stdout.strip()
                    except Exception:
                        etime = "unknown"
                    return {"pg_id": pg_id, "pid": pid, "elapsed": etime}
        return None
    except Exception:
        return None


def cleanup_old_caches(keep_running_pg: str | None = None):
    """Remove input caches for completed pipeline runs."""
    freed = 0
    if not CACHE_DIR.exists():
        return freed

    for entry in CACHE_DIR.iterdir():
        if not entry.is_dir():
            # Check for stray JP2 zips
            if entry.suffix == ".zip" and "jp2" in entry.name:
                size = entry.stat().st_size
                entry.unlink()
                freed += size
                print(f"  Removed stray zip: {entry.name} ({size/1e6:.0f}MB)")
            # Check for stray HTML files
            if entry.suffix == ".htm":
                size = entry.stat().st_size
                entry.unlink()
                freed += size
                print(f"  Removed stray HTML: {entry.name} ({size/1e6:.0f}MB)")
            continue

    print(f"  Freed {freed/1e6:.0f}MB total")
    return freed


def check_pipeline_output(pg_id: int) -> dict:
    """Check if pipeline produced valid output for a PG ID."""
    reports = sorted(REPORTS_DIR.glob(f"gutenberg{pg_id}-*_errata_email*.txt"))
    jsons = sorted(REPORTS_DIR.glob(f"gutenberg{pg_id}-*_errata*.json"))

    email_report = reports[-1] if reports else None
    json_report = jsons[-1] if jsons else None

    # Count candidates from email
    candidate_count = None
    if email_report:
        try:
            text = email_report.read_text()
            candidate_count = text.count("Page ")  # "Page N:" format per error
        except Exception:
            pass

    return {
        "email_path": str(email_report) if email_report else None,
        "json_path": str(json_report) if json_report else None,
        "email_size": email_report.stat().st_size if email_report else 0,
        "candidates": candidate_count,
    }


def run_pipeline(pg_id: int, scan_id: str, concurrency: int = 10) -> subprocess.Popen:
    """Launch a gerrata pipeline run. Returns the process."""
    cmd = [
        sys.executable, "-m", "gerrata",
        str(pg_id),
        "--scan-id", scan_id,
        "--concurrency", str(concurrency),
        "--output", str(REPORTS_DIR),
        "-v",
    ]

    env = os.environ.copy()
    if API_KEY:
        env["ZAI_API_KEY"] = API_KEY

    # Use ; so webhook fires even on non-zero exit
    wake_cmd = [
        "/root/clawd/scripts/wake-agent.sh",
        f"Gerrata pipeline complete for PG #{pg_id}",
        "--channel", "telegram",
        "--to", "-1003815086962:3515",
    ]

    full_cmd = " ".join(cmd) + " ; " + " ".join(wake_cmd)

    log_file = REPO_DIR / f"logs/pipeline-{pg_id}.log"
    log_file.parent.mkdir(exist_ok=True)

    proc = subprocess.Popen(
        f"cd {REPO_DIR} && {full_cmd}",
        shell=True,
        stdout=open(log_file, "w"),
        stderr=subprocess.STDOUT,
        env=env,
    )

    print(f"  Pipeline started: PID {proc.pid}")
    print(f"  Log: {log_file}")
    return proc


def main():
    parser = argparse.ArgumentParser(description="Gerrata batch runner")
    parser.add_argument("--next", type=int, default=0, help="Peek at next N books without running")
    parser.add_argument("--run-all", action="store_true", help="Loop through entire queue")
    parser.add_argument("--check", action="store_true", help="Check pipeline status and disk")
    parser.add_argument("--cleanup", action="store_true", help="Free disk by removing old caches")
    parser.add_argument("--concurrency", type=int, default=10, help="API concurrency (default: 10)")
    args = parser.parse_args()

    queue = load_queue()
    disk = check_disk()

    if args.cleanup:
        print("=== Disk Cleanup ===")
        print(f"  Free: {disk['free_gb']:.1f}GB / {disk['total_gb']:.1f}GB ({disk['used_pct']:.0f}% used)")
        running = check_running_pipeline()
        cleanup_old_caches(keep_running_pg=running["pg_id"] if running else None)
        return

    if args.check:
        print("=== Status Check ===")
        print(f"  Disk: {disk['free_gb']:.1f}GB free / {disk['total_gb']:.1f}GB ({disk['used_pct']:.0f}% used)")
        running = check_running_pipeline()
        if running:
            print(f"  Pipeline running: PG #{running['pg_id']} (PID {running['pid']}, elapsed {running['elapsed']})")
        else:
            print("  No pipeline running")

        # Check recent outputs
        for entry in queue:
            if entry["status"] == "done":
                output = check_pipeline_output(entry["pg_id"])
                if output["email_path"]:
                    print(f"  PG #{entry['pg_id']}: {output['candidates']} candidates ({output['email_size']/1024:.0f}KB)")
                else:
                    print(f"  PG #{entry['pg_id']}: ⚠️  no email output found!")

        # Show next pending
        pending = [e for e in queue if e["status"] == "pending"]
        if pending:
            print(f"\n  Next up: PG #{pending[0]['pg_id']} — {pending[0]['title']}")
        print(f"  Queue: {len([e for e in queue if e['status']=='done'])} done, "
              f"{len([e for e in queue if e['status']=='running'])} running, "
              f"{len([e for e in queue if e['status']=='pending'])} pending")
        return

    if args.next > 0:
        pending = [e for e in queue if e["status"] == "pending"]
        for entry in pending[:args.next]:
            print(f"  #{entry['num']}: PG #{entry['pg_id']} — {entry['title']} ({entry['author']})")
            if entry.get("scan_id"):
                print(f"    Scan: {entry['scan_id']}")
            else:
                print(f"    ⚠️  No scan ID — needs manual source lookup")
        return

    if args.run_all:
        print("=== Batch Run Mode ===")
        print("WARNING: This requires all books to have scan IDs pre-configured.")
        print("Books without scans will be skipped.")
        confirm = input("Proceed? [y/N] ")
        if confirm.lower() != "y":
            print("Aborted.")
            return

        for entry in queue:
            if entry["status"] != "pending":
                print(f"  Skipping PG #{entry['pg_id']} ({entry['status']})")
                continue

            if not entry.get("scan_id"):
                print(f"  ⚠️  PG #{entry['pg_id']} has no scan ID — skipping")
                continue

            # Check disk
            disk = check_disk()
            if disk["free_gb"] < 2:
                print(f"  ⚠️  Only {disk['free_gb']:.1f}GB free — cleaning up...")
                cleanup_old_caches()
                disk = check_disk()
                if disk["free_gb"] < 1:
                    print(f"  ❌ Still only {disk['free_gb']:.1f}GB free — stopping")
                    break

            # Wait for any running pipeline
            running = check_running_pipeline()
            while running:
                print(f"  Waiting for PG #{running['pg_id']} to finish...")
                time.sleep(60)
                running = check_running_pipeline()

            print(f"\n  Starting PG #{entry['pg_id']} — {entry['title']}")
            proc = run_pipeline(entry["pg_id"], entry["scan_id"], args.concurrency)

            # Wait for completion
            proc.wait()
            print(f"  Pipeline exited with code {proc.returncode}")

            # Verify output
            output = check_pipeline_output(entry["pg_id"])
            if output["email_path"]:
                print(f"  ✅ {output['candidates']} candidates ({output['email_size']/1024:.0f}KB)")
            else:
                print(f"  ❌ No output produced!")

            # Cleanup
            print("  Cleaning up input cache...")
            cleanup_old_caches()

        print("\n=== Batch Complete ===")
        return

    # Default: show status and next book
    print("=== Gerrata Queue Status ===")
    print(f"  Disk: {disk['free_gb']:.1f}GB free ({disk['used_pct']:.0f}% used)")
    running = check_running_pipeline()
    if running:
        print(f"  Running: PG #{running['pg_id']} (elapsed {running['elapsed']})")

    pending = [e for e in queue if e["status"] == "pending"]
    done = [e for e in queue if e["status"] == "done"]
    print(f"  Progress: {len(done)}/{len(queue)} done, {len(pending)} pending")

    if pending:
        next_book = pending[0]
        print(f"\n  Next: PG #{next_book['pg_id']} — {next_book['title']}")
        if next_book.get("scan_id"):
            print(f"  Scan: {next_book['scan_id']} (ready to run)")
        else:
            print(f"  ⚠️  Needs scan ID lookup first")


if __name__ == "__main__":
    main()
