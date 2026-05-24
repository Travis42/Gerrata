#!/usr/bin/env python3
"""Rerun gap detection on cached Nostromo pipeline data."""

import json
import sys
sys.path.insert(0, "/root/projects/gerrata/src")

from pathlib import Path
from gerrata.checker.gap_detector import detect_scan_gaps

cache_dir = Path("/root/projects/gerrata/cache/nostromotaleofse00conruoft")

# Load PG parsed text
with open(cache_dir / "01_pg_parsed.json") as f:
    parsed = json.load(f)
pg_text = parsed.get("body_text", "")

# Load alignments
with open(cache_dir / "03_alignments.json") as f:
    alignments_raw = json.load(f)

from gerrata.models import Alignment, AlignmentMethod
alignments = []
for a in alignments_raw:
    method = a.get("method", "lcs")
    alignments.append(Alignment(
        pg_start=a["pg_start"],
        pg_end=a["pg_end"],
        scan_page=a["scan_page"],
        confidence=a.get("confidence", 1.0),
        method=AlignmentMethod(method) if method else AlignmentMethod.LCS,
    ))

# Load scan pages
from gerrata.fetcher.scans import ScanPage
with open(cache_dir / "03_scan_pages.json") as f:
    pages_raw = json.load(f)

scan_pages = []
for p in pages_raw:
    scan_pages.append(ScanPage(
        page_num=p["page_num"],
        ocr_text=p.get("ocr_text", ""),
        vision_text=p.get("vision_text", ""),
        image_path=Path(p["image_path"]) if p.get("image_path") else None,
    ))

# Run gap detection
gaps = detect_scan_gaps(
    pg_text=pg_text,
    alignments=alignments,
    scan_pages=scan_pages,
    min_gap_words=15,
    book_title="Nostromo A Tale of the Seaboard",
)

print(f"\n{'='*60}")
print(f"Gap Detection Results for Nostromo (PG #2021)")
print(f"{'='*60}")
print(f"Total alignments: {len(alignments)}")
print(f"Total scan pages: {len(scan_pages)}")
print(f"Coverage gaps found: {len(gaps)}")
print()

if gaps:
    # Count which pages have gaps
    gap_pages = set(g.scan_page for g in gaps)
    covered_pages = set(a.scan_page for a in alignments)
    uncovered_pages = [p for p in scan_pages if p.page_num not in covered_pages]

    print(f"Pages with alignment coverage: {len(covered_pages)}/{len(scan_pages)}")
    print(f"Completely uncovered pages: {len(uncovered_pages)}")
    print(f"Pages with partial coverage gaps: {len(gap_pages - set(p.page_num for p in uncovered_pages))}")
    print()

    for i, gap in enumerate(gaps, 1):
        print(f"--- Gap #{i} (page {gap.scan_page}) ---")
        print(f"  Description: {gap.diff_description}")
        print(f"  PG text: {gap.pg_text[:100]}...")
        print(f"  Scan text: {gap.scan_text[:200]}...")
        word_count = len(gap.scan_text.split())
        print(f"  Word count: ~{word_count}")
        print()
else:
    print("No coverage gaps detected — all scan pages are accounted for.")

# Summary of scan page coverage
print(f"\n{'='*60}")
print("Scan Page Coverage Summary")
print(f"{'='*60}")
covered_set = set(a.scan_page for a in alignments)
for sp in sorted(scan_pages, key=lambda p: p.page_num):
    status = "✅ covered" if sp.page_num in covered_set else "❌ UNCOVERED"
    text = sp.vision_text or sp.ocr_text or "(empty)"
    wc = len(text.split())
    print(f"  Page {sp.page_num:4d}: {status} ({wc} words)")
