#!/usr/bin/env python3
"""Replay filtering on saved Gerrata results and regenerate reports."""

import json
import re
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from gerrata.models import CandidateError, Error, Verdict, ErrorCategory
from gerrata.reporter.generator import ReportGenerator


# ── Filter functions (mirrors from cli.py) ──

def is_cutoff_artifact(scan_text, pg_text):
    s, p = scan_text.strip(), pg_text.strip()
    if ' ' in s or ' ' in p: return False
    if abs(len(s) - len(p)) != 1: return False
    longer, shorter = (s, p) if len(s) > len(p) else (p, s)
    if not (longer.startswith(shorter) or longer.endswith(shorter)): return False
    return len(shorter) <= 3

def is_absent_entry(scan_text, pg_text):
    return '(absent in PG)' in pg_text or '(absent in scan)' in scan_text

def is_long_mismatch(scan_text, pg_text):
    return len(scan_text.strip()) > 40 or len(pg_text.strip()) > 40

def is_html_artifact(scan_text, pg_text):
    combined = scan_text + pg_text
    return any(m in combined for m in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])

def is_all_caps_header(scan_text):
    stripped = scan_text.strip()
    return stripped.isupper() and len(stripped) > 5

def is_suffix_fragment(scan_text, pg_text):
    s, p = scan_text.strip(), pg_text.strip()
    shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
    if ' ' in shorter: return False
    if len(longer) - len(shorter) > 3: return False
    if not longer.endswith(shorter): return False
    # Singular/plural exception
    if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4: return False
    if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4: return False
    if len(shorter) > 8: return False
    return True

def is_quoted_fragment(scan_text, pg_text):
    s, p = scan_text.strip(), pg_text.strip()
    shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
    if not shorter: return False
    starts_with_quote = shorter[0] in '""\u00ab'
    if not starts_with_quote: return False
    return len(longer) - len(shorter) > 20

def is_punctuation_only(pg_text, scan_text):
    """True if PG and scan text differ only in punctuation/whitespace."""
    pg_words = re.sub(r'[^\w]', '', pg_text)
    scan_words = re.sub(r'[^\w]', '', scan_text)
    if not pg_words or not scan_words:
        return True
    return pg_words == scan_words

def is_quote_start_mismatch(pg_text, scan_text):
    """True if one side starts with a quote and the other doesn't, with significant length difference.
    Catches dialogue reassembly artifacts like 'erson?" "Only' -> '"Only'."""
    s, p = scan_text.strip(), pg_text.strip()
    shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
    if not shorter:
        return False
    if shorter[0] not in '"\u201c\u201c\u00ab':
        return False
    if len(longer) - len(shorter) <= 10:
        return False
    return True


# ── Main ──

def main():
    input_json = sys.argv[1] if len(sys.argv) > 1 else None
    if not input_json:
        # Find the saved report
        candidates = [
            Path("reports/finished_full_drjekyl/gutenberg43-the-strange-case-of-dr-jekyll-and-mr-hyde_errata.json"),
        ]
        for c in candidates:
            if c.exists():
                input_json = str(c)
                break
        if not input_json:
            print("No input JSON found", file=sys.stderr)
            sys.exit(1)

    # Load saved report
    report = json.load(open(input_json))
    errors = report['errors']
    print(f"Loaded {len(errors)} errors from {input_json}")

    # Apply filters
    filters = [
        ("Absent text", is_absent_entry),
        ("Word-boundary cutoff (≤3 chars)", is_cutoff_artifact),
        ("Long mismatch (>40 chars)", is_long_mismatch),
        ("HTML artifact", is_html_artifact),
        ("ALL CAPS header", lambda s, p: is_all_caps_header(s)),
        ("Suffix fragment", is_suffix_fragment),
        ("Quoted fragment", is_quoted_fragment),
    ]

    filtered = []
    counts = {}
    for e in errors:
        scan = e.get('scan_text', '')
        pg = e.get('pg_text', '')
        
        caught = False
        for name, fn in filters:
            if fn(scan, pg):
                counts[name] = counts.get(name, 0) + 1
                caught = True
                break
        if not caught:
            filtered.append(e)

    total_filtered = len(errors) - len(filtered)
    print(f"\nFilters applied:")
    for name, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {name}: {count}")
    print(f"Total filtered: {total_filtered}")
    print(f"Remaining: {len(filtered)}")

    # Show breakdown of remaining
    from collections import Counter
    verdicts = Counter(e.get('verdict') for e in filtered)
    print(f"\nRemaining by verdict:")
    for v, c in verdicts.most_common():
        print(f"  {v}: {c}")

    # Only scan_correct high-confidence are email-worthy
    scan_correct = [e for e in filtered if e.get('verdict') == 'scan_correct' and e.get('confidence', 0) >= 0.85]
    print(f"\nscan_correct + high confidence ≥0.85 (email candidates): {len(scan_correct)}")

    # Load PG text for sentence extraction
    body_text = ""
    html_path = Path("cache/43.htm")
    if html_path.exists():
        html = html_path.read_text(encoding='utf-8')
        body_text = re.sub(r'<[^>]+>', ' ', html)
        body_text = re.sub(r'\s+', ' ', body_text).strip()

    # Extract sentences and add display_page
    def extract_sentence(text, offset, length):
        if not text or offset < 0:
            return ""
        start = max(0, offset - 1)
        while start > 0:
            if text[start] in '.!?\n':
                start += 1
                break
            start -= 1
        end = min(len(text), offset + length)
        while end < len(text):
            if text[end] in '.!?':
                end += 1
                break
            end += 1
        return text[start:end].strip()

    # Add computed fields to all filtered
    for e in filtered:
        e['display_page'] = e.get('scan_page', 0) + 1

        # Find PG text by string search (pg_offset is unreliable)
        pg_text = e.get('pg_text', '')
        pos = body_text.find(pg_text)
        if pos < 0 and '(absent in PG)' in pg_text:
            pg_text = e.get('scan_text', '')
            pos = body_text.find(pg_text)
        if pos >= 0:
            e['pg_file_line'] = body_text[:pos].count('\n') + 1
            e['pg_sentence'] = extract_sentence(body_text, pos, len(pg_text))
        else:
            e['pg_file_line'] = 0
            e['pg_sentence'] = ""

    # Deduplicate by pg_offset proximity (within 50 chars): keep highest confidence
    deduped = sorted(scan_correct, key=lambda x: x.get('pg_offset', 0))
    final = []
    last_offset = -100
    for e in deduped:
        if e.get('pg_offset', 0) - last_offset < 50:
            continue  # Too close to previous — likely same error
        final.append(e)
        last_offset = e.get('pg_offset', 0)

    # ── Post-dedup filters ──
    post_filters = [
        ("Punctuation-only change", lambda e: is_punctuation_only(e.get('pg_text', ''), e.get('scan_text', ''))),
        ("Quote-start fragment", lambda e: is_quote_start_mismatch(e.get('pg_text', ''), e.get('scan_text', ''))),
    ]

    cleaned = []
    post_counts = {}
    for e in final:
        caught = False
        for name, fn in post_filters:
            if fn(e):
                post_counts[name] = post_counts.get(name, 0) + 1
                caught = True
                break
        if not caught:
            cleaned.append(e)

    total_post = len(final) - len(cleaned)
    if total_post > 0:
        print(f"\nPost-dedup filters:")
        for name, count in sorted(post_counts.items(), key=lambda x: -x[1]):
            print(f"  {name}: {count}")
        print(f"Total removed: {total_post}")
        print(f"Final email entries: {len(cleaned)}")
    else:
        print(f"\nFinal email entries: {len(final)}")

    # Build the email — PG Format 2 (arrow fix)
    lines = []
    lines.append("The Strange Case Of Dr. Jekyll And Mr. Hyde, by Robert Louis Stevenson")
    lines.append(" [EBook #43]")
    lines.append(" File: 43-h.htm")
    lines.append("")
    lines.append(f" {len(cleaned)} errors ready for submission")
    lines.append("")

    if not cleaned:
        lines.append("No errors found requiring correction.")
    else:
        for e in cleaned:
            scan = e.get('scan_text', '').strip()
            pg = e.get('pg_text', '').strip()
            page = e.get('display_page', '?')
            line = e.get('pg_file_line', '?')
            ctx = e.get('pg_sentence', '')

            lines.append(f"Page {page}: {pg} -> {scan}")
            if ctx:
                lines.append(f"  Context: {ctx}")
            lines.append("")

    # Save the email
    output_dir = Path("reports/replay_output")
    output_dir.mkdir(parents=True, exist_ok=True)
    email_path = output_dir / "gutenberg43-errata_email.txt"
    email_path.write_text('\n'.join(lines), encoding='utf-8')
    print(f"\nEmail saved to: {email_path}")

    # Also save filtered JSON
    report['errors'] = filtered
    report['filter_stats'] = counts
    report['total_filtered'] = total_filtered
    json_path = output_dir / "gutenberg43-errata_filtered.json"
    json.dump(report, json_path.open('w'), indent=2, ensure_ascii=False)
    print(f"Filtered JSON saved to: {json_path}")


if __name__ == '__main__':
    main()
