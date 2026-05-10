#!/usr/bin/env python3
"""
Find Internet Archive scans for Project Gutenberg ebooks.

For each PG ID:
1. Fetch PG metadata (title, author)
2. Determine the edition PG transcribed from (title page, publisher info)
3. Search IA for matching scans
4. Verify JP2 zip is downloadable
5. Output results in ERRATA_QUEUE.md format

Usage:
    python3 find_scans.py 84 1342 11 145 2554 2701
    python3 find_scans.py --from-queue          # process all unscanned books in queue
    python3 find_scans.py --retry-failed        # retry books with failed scans
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

REPO_DIR = Path(__file__).resolve().parent.parent
QUEUE_FILE = REPO_DIR / "ERRATA_QUEUE.md"


def get_pg_metadata(pg_id: int) -> dict:
    """Fetch PG metadata from RDF and HTML."""
    import urllib.request

    result = {"pg_id": pg_id, "title": "", "author": "", "edition": "", "credits": ""}

    # RDF for title and author
    try:
        rdf_url = f"https://www.gutenberg.org/cache/epub/{pg_id}/pg{pg_id}.rdf"
        with urllib.request.urlopen(rdf_url, timeout=10) as resp:
            rdf = resp.read().decode("utf-8", errors="replace")
        m = re.search(r"<dcterms:title>(.*?)</dcterms:title>", rdf)
        if m:
            result["title"] = m.group(1).strip()
        # Try multiple author extraction patterns
        for pattern in [
            r"<pgterms:name>(.*?)</pgterms:name>",
            r"<dcterms:creator>\s*<rdf:Description[^>]*>\s*<dc:title>(.*?)</dc:title>",
            r"<marcrel:aut>\s*<pgterms:agent>\s*<pgterms:name>(.*?)</pgterms:name>",
        ]:
            m = re.search(pattern, rdf, re.DOTALL)
            if m:
                result["author"] = m.group(1).strip()
                break
    except Exception:
        pass

    # HTML for edition info and credits
    for htm_path in [f"{pg_id}-h/{pg_id}-h.htm", f"{pg_id}/{pg_id}-h.htm", f"{pg_id}-h.htm"]:
        try:
            html_url = f"https://www.gutenberg.org/files/{pg_id}/{htm_path}"
            with urllib.request.urlopen(html_url, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            # Extract text between START and END markers
            m = re.search(r"\*\*\* START OF.*?\*\*\*(.*?)\*\*\* END OF", html, re.DOTALL)
            if m:
                body = m.group(1)
                # Look for credits near the END of the body (not narrative "produced by")
                last_section = body[-5000:]
                for credit_pattern in [
                    r"(?:<br\s*/?>\s*)?(?:Produced by|Transcribed by)\s+([^.<]{5,100}?)(?:\.|<|$)",
                ]:
                    for credit_match in re.finditer(credit_pattern, last_section, re.IGNORECASE):
                        credit_text = credit_match.group(1).strip()
                        narrative_words = ["the ", "his ", "her ", "its ", "their ",
                                           "a ", "an ", "this ", "that ", "such ",
                                           "each ", "which ", "all ", "no ", "our "]
                        if not any(credit_text.lower().startswith(w) for w in narrative_words):
                            if len(credit_text) > 10:
                                result["credits"] = credit_text[:100]
                                break
                    if result["credits"]:
                        break

                # Look for edition info in the FIRST 2000 chars (title page)
                intro = re.sub(r"<[^>]+>", " ", body[:2000])
                intro = re.sub(r"\s+", " ", intro)
                edition_lines = []
                for pattern in [
                    r"((?:First |Re-?)printed \d{4}[^.]{0,60})",
                    r"(Published[^.]{0,80}\d{4}[^.]{0,60})",
                    r"((?:LONDON|NEW YORK|BOSTON|CHICAGO|PHILADELPHIA)[^.]{0,80})",
                    r"((?:Oxford|Cambridge|Harper|Macmillan|Routledge|Dodd|Scribner|Houghton)[^.]{0,80})",
                ]:
                    for match in re.finditer(pattern, intro, re.IGNORECASE):
                        clean = match.group(1).strip()
                        if 10 < len(clean) < 80:
                            edition_lines.append(clean)
                result["edition"] = "; ".join(dict.fromkeys(edition_lines)[:3])[:200]
            break
        except Exception:
            continue

    return result


def search_ia(title: str, author: str, max_results: int = 5) -> list[dict]:
    """Search Internet Archive for matching scans."""
    import urllib.request
    import urllib.parse

    query = f"{re.split(r'[;:]', title)[0].strip()} {author}"
    results = []

    try:
        # Build URL manually to handle array params correctly
        # IA advancedsearch needs fl[] repeated, sort[] repeated
        from urllib.parse import quote
        q_encoded = quote(query)
        url = (
            f"https://archive.org/advancedsearch.php"
            f"?q={q_encoded}"
            f"&fl[]=identifier"
            f"&fl[]=title,publisher,year,page_count"
            f"&fl[]=downloads"
            f"&sort[]=downloads+desc"
            f"&rows={max_results}"
            f"&output=json"
        )
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        for doc in data.get("response", {}).get("docs", []):
            identifier = doc.get("identifier", "")
            # Filter out non-scans (PDFs, epubs without page images)
            if not identifier:
                continue
            results.append({
                "identifier": identifier,
                "title": doc.get("title", ""),
                "publisher": doc.get("publisher", ""),
                "year": doc.get("year", ""),
                "pages": doc.get("page_count", ""),
                "downloads": doc.get("downloads", 0),
            })
    except Exception as e:
        print(f"  IA search error: {e}", file=sys.stderr)

    return results


def verify_scan(identifier: str) -> dict:
    """Check if a scan's JP2 zip is downloadable."""
    import urllib.request

    result = {"identifier": identifier, "jp2_available": False, "jp2_size": 0, "error": ""}

    # Check metadata for JP2 zip
    try:
        meta_url = f"https://archive.org/metadata/{identifier}"
        with urllib.request.urlopen(meta_url, timeout=10) as resp:
            meta = json.loads(resp.read().decode())

        files = meta.get("files", [])
        jp2_zips = [f for f in files if "jp2.zip" in f["name"].lower()]
        if jp2_zips:
            result["jp2_available"] = True
            result["jp2_size"] = jp2_zips[0].get("size", 0)
        else:
            result["error"] = "No JP2 zip in metadata"
    except Exception as e:
        result["error"] = str(e)

    # Verify actual download (HEAD request)
    if result["jp2_available"]:
        try:
            zip_name = jp2_zips[0]["name"] if jp2_zips else f"{identifier}_jp2.zip"
            dl_url = f"https://archive.org/download/{identifier}/{zip_name}"
            req = urllib.request.Request(dl_url, method="HEAD")
            with urllib.request.urlopen(req, timeout=15) as resp:
                result["jp2_size"] = resp.headers.get("Content-Length", result["jp2_size"])
        except Exception as e:
            code = getattr(e, "code", 0)
            result["jp2_available"] = False
            result["error"] = f"Download failed: HTTP {code}" if code else f"Download failed: {e}"

    return result


def find_scan_for_book(pg_id: int) -> dict:
    """Find the best IA scan for a PG ebook."""
    print(f"\n{'='*60}")
    print(f"PG #{pg_id}")
    print(f"{'='*60}")

    # Step 1: Get PG metadata
    meta = get_pg_metadata(pg_id)
    print(f"  Title:    {meta['title']}")
    print(f"  Author:   {meta['author']}")
    print(f"  Edition:  {meta['edition']}")
    print(f"  Credits:  {meta['credits']}")

    # Step 2: Search IA
    print(f"  Searching IA...")
    results = search_ia(meta["title"], meta["author"])

    if not results:
        print(f"  ⚠️  No IA results found")
        return {"pg_id": pg_id, "scan": None, "meta": meta}

    # Step 3: Check top results for JP2 zip
    best = None
    for i, r in enumerate(results):
        print(f"  [{i+1}] {r['identifier']}: {r['title'][:50]} ({r.get('year','?')})")
        verified = verify_scan(r["identifier"])
        status = "✅" if verified["jp2_available"] else f"❌ {verified['error']}"
        size_mb = int(verified.get("jp2_size", 0)) / 1e6
        print(f"      JP2 zip: {status} ({size_mb:.0f}MB)")

        if verified["jp2_available"] and best is None:
            size_mb = int(verified.get("jp2_size", 0)) / 1e6
            # Skip tiny JP2 zips (< 5MB) — likely derivative works, not book scans
            if size_mb < 5:
                print(f"      ⚠️  Skipping ({size_mb:.0f}MB — too small for a book scan)")
                continue
            best = {
                "identifier": r["identifier"],
                "title": r["title"],
                "jp2_size_mb": size_mb,
                "year": r.get("year", ""),
            }

    if best:
        print(f"  ✅ Best match: {best['identifier']} ({best['jp2_size_mb']:.0f}MB)")
    else:
        print(f"  ❌ No downloadable JP2 zip found")

    return {"pg_id": pg_id, "scan": best, "meta": meta}


def main():
    parser = argparse.ArgumentParser(description="Find IA scans for PG ebooks")
    parser.add_argument("pg_ids", nargs="*", type=int, help="PG IDs to look up")
    parser.add_argument("--from-queue", action="store_true", help="Process unscanned books from queue")
    parser.add_argument("--retry-failed", action="store_true", help="Retry books with failed scans")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    pg_ids = args.pg_ids

    if args.from_queue or args.retry_failed:
        # Parse queue for books needing scans
        if QUEUE_FILE.exists():
            import re
            with open(QUEUE_FILE) as f:
                for line in f:
                    if not line.startswith("|") or line.startswith("|#") or line.startswith("|---"):
                        continue
                    parts = [p.strip() for p in line.split("|") if p.strip()]
                    if len(parts) < 5:
                        continue
                    try:
                        pg_id = int(parts[1])
                        scan_id = parts[5] if len(parts) > 5 else ""
                        verified = parts[6] if len(parts) > 6 else ""

                        need_scan = False
                        if args.from_queue and (not scan_id or scan_id == "—"):
                            need_scan = True
                        if args.retry_failed and verified in ("❌", "❌"):
                            need_scan = True

                        if need_scan and pg_id not in pg_ids:
                            pg_ids.append(pg_id)
                    except (ValueError, IndexError):
                        pass

    if not pg_ids:
        print("No PG IDs specified. Use --from-queue or provide IDs as arguments.")
        return

    all_results = []
    for pg_id in pg_ids:
        result = find_scan_for_book(pg_id)
        all_results.append(result)
        time.sleep(1)  # Be polite to PG and IA

    if args.json:
        print(json.dumps(all_results, indent=2, default=str))
    else:
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        found = [r for r in all_results if r["scan"]]
        not_found = [r for r in all_results if not r["scan"]]
        print(f"  ✅ Found:  {len(found)}")
        for r in found:
            print(f"     PG #{r['pg_id']}: {r['scan']['identifier']} ({r['scan']['jp2_size_mb']:.0f}MB)")
        print(f"  ❌ Not found: {len(not_found)}")
        for r in not_found:
            print(f"     PG #{r['pg_id']}: {r['meta']['title']}")


if __name__ == "__main__":
    main()
