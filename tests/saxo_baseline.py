#!/usr/bin/env python3
"""
Baseline test harness for measuring diff checker performance on cached Saxo data.

Runs the diff checker on cached alignments + scan pages + PG text,
produces JSON output comparable to the pipeline's candidate output,
and prints summary statistics for before/after comparison.

Usage:
    python3 tests/saxo_baseline.py          # run and print stats
    python3 tests/saxo_baseline.py --save   # save baseline JSON for comparison
"""

import json
import sys
import re
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gerrata.checker.text_diff import TextDiffChecker, CandidateError
from gerrata.fetcher.pg import PGFetcher


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "saxo"


def load_fixture():
    """Load cached pipeline intermediates."""
    # PG text
    pg_raw = (FIXTURE_DIR / "pg_1150.txt").read_text(encoding="utf-8")
    parser = PGFetcher()
    parsed = parser.parse_file(str(FIXTURE_DIR / "pg_1150.txt"))
    pg_body = parsed.body_text

    # Alignments
    alignments_raw = json.loads((FIXTURE_DIR / "03_alignments.json").read_text())
    
    # Convert dicts to simple namespace objects (duck-typed)
    class Alignment:
        def __init__(self, d):
            self.pg_start = d["pg_start"]
            self.pg_end = d["pg_end"]
            self.scan_page = d["scan_page"]
            self.confidence = d["confidence"]
            self.method = d.get("method", "")
    
    alignments = [Alignment(a) for a in alignments_raw]

    # Scan pages
    scan_pages_raw = json.loads((FIXTURE_DIR / "03_scan_pages.json").read_text())
    
    class ScanPage:
        def __init__(self, d):
            self.page_num = d["page_num"]
            self.ocr_text = d.get("ocr_text", "")
            self.vision_text = d.get("vision_text", "")
    
    scan_pages = [ScanPage(sp) for sp in scan_pages_raw]

    return pg_body, alignments, scan_pages


def run_diff_check(pg_body, alignments, scan_pages, mode='recursive'):
    """Run the diff checker on cached data.
    
    Modes:
        'flat'       — per-page, no recursive
        'recursive'  — per-page, with recursive sub-page anchors
        'stitched'   — chapter-stitched continuous scan text
    """
    checker = TextDiffChecker()
    if mode == 'stitched':
        return checker.check_stitched(pg_body, alignments, scan_pages)
    else:
        return checker.check_all_alignments(
            pg_body, alignments, scan_pages, use_recursive=(mode == 'recursive')
        )


def candidates_to_json(candidates):
    """Convert CandidateError objects to JSON-serializable dicts."""
    results = []
    for c in candidates:
        results.append({
            "pg_text": c.pg_text,
            "scan_text": c.scan_text,
            "pg_offset": c.pg_offset,
            "scan_page": c.scan_page,
            "diff_description": c.diff_description,
            "category": c.category,
        })
    return results


def print_stats(candidates, label=""):
    """Print summary statistics."""
    from collections import Counter
    
    print(f"\n{'=' * 60}")
    if label:
        print(f"  {label}")
        print(f"{'=' * 60}")
    
    total = len(candidates)
    cats = Counter(c.category for c in candidates)
    
    # Page-level stats
    pages_with_errors = len(set(c.scan_page for c in candidates))
    
    # Errors per page
    page_counts = Counter(c.scan_page for c in candidates)
    if page_counts:
        max_per_page = max(page_counts.values())
        avg_per_page = total / len(page_counts)
        median_per_page = sorted(page_counts.values())[len(page_counts) // 2]
    else:
        max_per_page = avg_per_page = median_per_page = 0
    
    print(f"Total candidates: {total}")
    print(f"Pages with errors: {pages_with_errors}")
    print(f"Avg errors/page: {avg_per_page:.1f}")
    print(f"Median errors/page: {median_per_page}")
    print(f"Max errors/page: {max_per_page}")
    print()
    print("By category:")
    for cat, count in cats.most_common():
        print(f"  {cat}: {count} ({count/total*100:.0f}%)")
    
    # Top 10 noisiest pages
    print()
    print("Top 10 noisiest pages:")
    for page, count in page_counts.most_common(10):
        print(f"  Page {page}: {count} candidates")
    
    return {
        "total": total,
        "pages_with_errors": pages_with_errors,
        "avg_per_page": round(avg_per_page, 1),
        "median_per_page": median_per_page,
        "max_per_page": max_per_page,
        "categories": dict(cats),
    }


def compare_stats(before, after):
    """Print before/after comparison."""
    print(f"\n{'=' * 60}")
    print(f"  BEFORE vs AFTER COMPARISON")
    print(f"{'=' * 60}")
    
    metrics = [
        ("Total candidates", "total", "lower is better"),
        ("Pages with errors", "pages_with_errors", "lower is better"),
        ("Avg errors/page", "avg_per_page", "lower is better"),
        ("Median errors/page", "median_per_page", "lower is better"),
        ("Max errors/page", "max_per_page", "lower is better"),
    ]
    
    for label, key, direction in metrics:
        b = before[key]
        a = after[key]
        delta = a - b
        pct = (delta / b * 100) if b else 0
        arrow = "↓" if delta < 0 else "↑" if delta > 0 else "="
        good = delta < 0 if "lower" in direction else delta > 0
        marker = "✅" if good else "❌" if delta != 0 else "  "
        print(f"  {marker} {label:25s}  {b:>8} → {a:>8}  ({arrow}{abs(delta):>4}, {pct:+.1f}%)")
    
    # Category comparison
    print()
    print("Category changes:")
    all_cats = set(before["categories"]) | set(after["categories"])
    for cat in sorted(all_cats):
        b = before["categories"].get(cat, 0)
        a = after["categories"].get(cat, 0)
        delta = a - b
        if delta != 0:
            arrow = "↓" if delta < 0 else "↑"
            print(f"  {arrow} {cat:25s}  {b:>5} → {a:>5}  ({delta:+d})")


if __name__ == "__main__":
    save = "--save" in sys.argv
    compare = "--compare" in sys.argv
    
    print("Loading cached Saxo intermediates...")
    pg_body, alignments, scan_pages = load_fixture()
    print(f"  PG body: {len(pg_body):,} chars")
    print(f"  Alignments: {len(alignments)}")
    print(f"  Scan pages: {len(scan_pages)}")

    if compare:
        modes = [
            ("flat", "SCORE 1+2: FLAT (current pipeline)"),
            ("recursive", "SCORE 3: RECURSIVE SUB-PAGE"),
            ("stitched", "SCORE 4: CHAPTER-STITCHED"),
        ]
        
        all_stats = {}
        all_candidates = {}
        for mode, label in modes:
            print(f"\nRunning {mode} diff checker...")
            candidates = run_diff_check(pg_body, alignments, scan_pages, mode=mode)
            stats = print_stats(candidates, label)
            all_stats[mode] = stats
            all_candidates[mode] = candidates
            
            # Save run
            out_path = FIXTURE_DIR / f"{mode}_run.json"
            with open(out_path, "w") as f:
                json.dump({"stats": stats, "candidates": candidates_to_json(candidates)}, f, indent=2)
        
        # Compare flat vs stitched
        print()
        compare_stats(all_stats["flat"], all_stats["stitched"])
        print()
        compare_stats(all_stats["recursive"], all_stats["stitched"])
    else:
        mode = "recursive" if not save else "flat"
        print(f"\nRunning diff checker ({mode})...")
        candidates = run_diff_check(pg_body, alignments, scan_pages, recursive=(mode == "recursive"))
        stats = print_stats(candidates, f"{mode.upper()} DIFF CHECKER")

        out_path = FIXTURE_DIR / f"{mode}_run.json"
        with open(out_path, "w") as f:
            json.dump({"stats": stats, "candidates": candidates_to_json(candidates)}, f, indent=2)
        print(f"\nSaved to {out_path}")
