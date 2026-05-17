#!/usr/bin/env python3
"""Batch queue builder — builds a processing queue from PG Top 100.

Fetches the PG Top 100 (last 30 days), cross-references already-processed books,
filters by language/type, extracts edition info, and searches IA for matching scans.

Usage:
    python3 scripts/batch_prepare.py [--limit N] [--output queue.json]

    --limit N    Only process top N books (default: 30)
    --output     Output queue file (default: cache/batch_queue.json)
"""

import argparse
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

CACHE_DIR = Path(__file__).parent.parent / "cache"
QUEUE_FILE = CACHE_DIR / "batch_queue.json"
ERRATA_QUEUE = Path(__file__).parent.parent / "ERRATA_QUEUE.md"

# Load completed books from completed.json
def load_completed() -> set[int]:
    """Load set of completed PG IDs from cache/completed.json."""
    completed_path = CACHE_DIR / "completed.json"
    if completed_path.exists():
        with open(completed_path) as f:
            return {b["pg_id"] for b in json.load(f)}
    return set()

# Non-English PG IDs to skip
SKIP_NON_ENGLISH = {
    40739, 52206, 55487, 65580, 76471, 23756, 65662,
}

# Reference/anthology/non-fiction PG IDs to skip
SKIP_REFERENCE = {
    27509, 27558, 26849, 22091, 26492, 100, 25851, 22400,
    28428, 12082, 25695, 22153, 25294, 26225, 23321,
    24919, 27430, 28556, 26449, 22210, 22094,
}


def fetch_pg_top_100() -> list[dict]:
    """Fetch PG Top 100 ebooks (all time)."""
    url = "https://www.gutenberg.org/browse/scores/top"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    # Extract PG IDs and titles from the HTML
    # The page has multiple tabs but the HTML contains all of them
    matches = re.findall(r'/ebooks/(\d+).*?>([^<]+)</a>', html)
    books = []
    seen = set()
    for pg_id, title_full in matches:
        pg_id = int(pg_id)
        if pg_id in seen:
            continue
        seen.add(pg_id)

        # Clean title - remove download count suffix like "(4567)"
        title = re.sub(r'\s*\(\d+\)\s*$', '', title_full.strip())
        if not title or len(title) < 3:
            continue
        if title.startswith("Top") or title.startswith("EBooks") or title.startswith("Authors"):
            continue

        # Extract author
        author = ""
        by_match = re.search(r'\bby\s+(.+)$', title)
        if by_match:
            author = by_match.group(1).strip()
            title = title[:by_match.start()].strip().rstrip(",").rstrip()

        books.append({"pg_id": pg_id, "title": title, "author": author})

    return books


def fetch_pg_text(pg_id: int) -> str:
    """Download PG text."""
    url = f"https://www.gutenberg.org/files/{pg_id}/{pg_id}-0.txt"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        # Try without -0 suffix
        url = f"https://www.gutenberg.org/files/{pg_id}/{pg_id}.txt"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            return ""


def detect_language(text: str) -> str:
    """Simple language detection based on character frequency."""
    if not text:
        return "unknown"
    # Check for non-Latin scripts
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04ff')
    german = sum(1 for c in text.lower() if c in 'äöüß')
    
    sample = text[:5000]
    if cjk > 50:
        return "cjk"
    if cyrillic > 50:
        return "cyrillic"
    if german > 20:
        return "german"
    
    # Count common English words
    en_words = sum(1 for w in sample.lower().split() 
                  if w.rstrip(".,;:!?\"'") in {"the", "and", "of", "to", "a", "in", "is", "it", "that", "was"})
    if en_words > 20:
        return "english"
    return "unknown"


def extract_edition_info(text: str) -> dict:
    """Extract edition information from PG text."""
    info = {
        "year": None,
        "publisher": None,
        "edition_note": None,
        "copyright_year": None,
        "transcriber_notes": None,
    }

    # Look for transcriber/produced-by notes (usually at start/end)
    # "Produced by ..." at the beginning
    produced_match = re.search(r'Produced by\s+(.+?)(?:\n|$)', text[:3000])
    if produced_match:
        info["transcriber_notes"] = produced_match.group(1).strip()[:200]

    # "This eBook is for the use of anyone..." header
    # Skip header, look at body text
    start_match = re.search(r'\*\*\* START OF THIS PROJECT GUTENBERG', text)
    body_start = start_match.start() if start_match else 0
    body = text[body_start:body_start + 10000]

    # Edition year patterns
    year_patterns = [
        r'Edition[^.]*?(\d{4})',
        r'published[^.]*?(\d{4})',
        r'Copyright,?\s*(\d{4})',
        r'(\d{4})\s*by\s+',
        r'First (?:edition|published)[^.]*?(\d{4})',
    ]
    for pat in year_patterns:
        m = re.search(pat, body, re.IGNORECASE)
        if m:
            info["year"] = m.group(1)
            break

    # Copyright year at bottom
    end_match = re.search(r'\*\*\* END OF THIS PROJECT GUTENBERG', text)
    if end_match:
        footer = text[max(0, end_match.start() - 2000):end_match.start()]
        copy_match = re.search(r'Copyright,?\s*(\d{4})', footer)
        if copy_match:
            info["copyright_year"] = copy_match.group(1)

    # Publisher
    pub_patterns = [
        r'(?:Published by|Printed by|published for)\s+([^.,\n]+)',
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:\s+(?:&|and)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)*)',
    ]
    for pat in pub_patterns:
        m = re.search(pat, body[:3000])
        if m:
            info["publisher"] = m.group(1).strip()
            break

    return info


def search_ia_scan(title: str, author: str, year: str = None) -> dict:
    """Search Internet Archive for matching scan."""
    # Search IA metadata API
    query = f"{title} {author}"
    if year:
        query += f" {year}"

    search_url = f"https://archive.org/advancedsearch.php?q={urllib.parse.quote(query)}&fl[]=identifier,title,year,publisher&rows=5&output=json"

    try:
        req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        results = []
        for doc in data.get("response", {}).get("docs", []):
            identifier = doc.get("identifier", "")
            # Check if JP2 zip exists
            jp2_url = f"https://archive.org/download/{identifier}/{identifier}_jp2.zip"
            try:
                head_req = urllib.request.Request(jp2_url, method="HEAD",
                    headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(head_req, timeout=10) as head_resp:
                    jp2_available = head_resp.status == 200
            except Exception:
                jp2_available = False

            if jp2_available:
                results.append({
                    "ia_id": identifier,
                    "title": doc.get("title", ""),
                    "year": doc.get("year", ""),
                    "publisher": doc.get("publisher", ""),
                    "jp2_url": jp2_url,
                })

        if results:
            return results[0]  # Best match
    except Exception as e:
        pass

    return None


def build_queue(limit: int = 30) -> list[dict]:
    """Build the processing queue."""
    print("Fetching PG Top 100...")
    books = fetch_pg_top_100()
    print(f"  Found {len(books)} books")

    queue = []
    skipped = []
    PROCESSED = load_completed()
    print(f"  Already completed: {sorted(PROCESSED)}")

    for book in books[:limit]:
        pg_id = book["pg_id"]

        # Skip already processed
        if pg_id in PROCESSED:
            skipped.append((pg_id, book["title"], "already processed"))
            continue

        # Skip non-English
        if pg_id in SKIP_NON_ENGLISH:
            skipped.append((pg_id, book["title"], "non-English"))
            continue

        # Skip reference/anthology
        if pg_id in SKIP_REFERENCE:
            skipped.append((pg_id, book["title"], "reference/anthology"))
            continue

        print(f"\n[{pg_id}] {book['title']} by {book['author']}")

        # Fetch PG text
        text = fetch_pg_text(pg_id)
        if not text:
            skipped.append((pg_id, book["title"], "could not fetch PG text"))
            print(f"  SKIP: Could not fetch PG text")
            continue

        # Detect language
        lang = detect_language(text)
        if lang != "english":
            skipped.append((pg_id, book["title"], f"language={lang}"))
            print(f"  SKIP: Language={lang}")
            continue

        # Extract edition info
        edition = extract_edition_info(text)
        print(f"  Edition: year={edition['year']}, publisher={edition['publisher']}")
        if edition["transcriber_notes"]:
            print(f"  Transcriber: {edition['transcriber_notes'][:80]}")

        # Search IA for matching scan
        print(f"  Searching IA...", end=" ", flush=True)
        ia_result = search_ia_scan(book["title"], book["author"], edition.get("year"))
        if ia_result:
            print(f"FOUND: {ia_result['ia_id']}")
            queue.append({
                "pg_id": pg_id,
                "title": book["title"],
                "author": book["author"],
                "ia_id": ia_result["ia_id"],
                "ia_title": ia_result["title"],
                "ia_year": ia_result["year"],
                "edition_year": edition.get("year"),
                "edition_publisher": edition.get("publisher"),
                "transcriber_notes": edition.get("transcriber_notes"),
            })
        else:
            print("NO MATCH")
            skipped.append((pg_id, book["title"], "no IA scan found"))

        time.sleep(1)  # Rate limit

    print(f"\n{'='*60}")
    print(f"Queue: {len(queue)} books")
    print(f"Skipped: {len(skipped)} books")
    for pg_id, title, reason in skipped:
        print(f"  {pg_id}: {title[:50]} — {reason}")

    return queue


def main():
    parser = argparse.ArgumentParser(description="Build gerrata batch queue")
    parser.add_argument("--limit", type=int, default=30, help="Process top N books")
    parser.add_argument("--output", default=str(QUEUE_FILE), help="Output queue file")
    args = parser.parse_args()

    queue = build_queue(limit=args.limit)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(queue, f, indent=2)

    print(f"\nQueue saved to {out_path}")

    # Also update ERRATA_QUEUE.md
    print(f"\nNext steps:")
    print(f"  1. Review queue: cat {out_path}")
    print(f"  2. Run pipeline: python3 scripts/batch_process.py")
    print(f"  3. Clean up: python3 scripts/batch_cleanup.py --all")


if __name__ == "__main__":
    main()
