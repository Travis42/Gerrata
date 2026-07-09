#!/usr/bin/env python3
"""
regenerate_report.py — Regenerate an errata email report from an existing
errata.json file, applying the latest format (global replacements detection,
grouped instances, alphabetical sorting).

Usage:
    python3 scripts/regenerate_report.py <errata.json> [--pg-file <path>] [--scan-id <id>] [-o <dir>]
"""

import argparse
import json
import re
import sys
from pathlib import Path
from collections import defaultdict


def parse_errata_entry(pg_text: str, scan_text: str) -> tuple[str, str] | None:
    """Extract a clean find/replace pair from pg_text/scan_text."""
    find = re.sub(r"<[^>]+>", "", pg_text).strip()
    replace = re.sub(r"<[^>]+>", "", scan_text).strip()
    if not find or not replace or find == replace:
        return None
    return (find, replace)


def detect_global_replacements(errors: list[dict]) -> tuple[list[dict], list[dict]]:
    """Detect global replacement patterns from verified errors.

    Returns (global_replacements, remaining_errors) where remaining_errors
    has global instances removed.
    """
    # Build word pairs from errors
    pair_counts: dict[tuple[str, str], list[int]] = defaultdict(list)
    word_to_pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for i, err in enumerate(errors):
        pair = parse_errata_entry(err.get("pg_text", ""), err.get("scan_text", ""))
        if pair is None:
            continue
        find, replace = pair

        # Skip multi-word phrases
        if " " in find.strip() or " " in replace.strip():
            continue
        # Skip very different lengths
        if abs(len(find) - len(replace)) > 5:
            continue
        # Skip pairs that are just punctuation differences
        find_clean = re.sub(r"[^\w]", "", find.lower())
        replace_clean = re.sub(r"[^\w]", "", replace.lower())
        if find_clean == replace_clean:
            continue

        pair_counts[(find, replace)].append(i)
        word_to_pairs[find].append((find, replace))

    # Detect global patterns: same find word appearing in ≥2 errors
    # with different replace values (indicating systematic error)
    global_pairs: dict[tuple[str, str], list[int]] = {}

    for (find, replace), indices in pair_counts.items():
        # Count how many times the find word appears across ALL pairs
        all_find_indices = []
        for (f, r), idxs in pair_counts.items():
            if f == find:
                all_find_indices.extend(idxs)

        if len(all_find_indices) >= 2:
            global_pairs[(find, replace)] = indices

    # Also detect single-occurrence words that are clearly systematic
    # (diacritics, common OCR patterns)
    for (find, replace), indices in pair_counts.items():
        if len(indices) >= 1 and (find, replace) not in global_pairs:
            # Check if it's a diacritic addition (e.g., Senor → Señor)
            if normalize_for_comparison(find) == normalize_for_comparison(replace):
                # Only include if there are ≥2 total occurrences across pairs
                total_for_word = sum(len(idxs) for (f, r), idxs in pair_counts.items() if f == find)
                if total_for_word >= 2:
                    global_pairs[(find, replace)] = indices

    # Count occurrences of each find word in PG text (from errors)
    pg_occurrences: dict[str, int] = {}
    for err in errors:
        pair = parse_errata_entry(err.get("pg_text", ""), err.get("scan_text", ""))
        if pair:
            find = pair[0]
            pg_occurrences[find] = pg_occurrences.get(find, 0) + 1

    # Build global replacement list
    global_list = []
    global_error_indices = set()
    for (find, replace), indices in sorted(global_pairs.items()):
        total_occ = sum(
            len(idxs)
            for (f, r), idxs in global_pairs.items()
            if f == find
        )
        global_list.append({
            "pg_text": find,
            "scan_text": replace,
            "occurrences_in_pg": total_occ,
            "caught_by_errata": len(indices),
            "examples": indices[:5],
        })
        global_error_indices.update(indices)

    # Remaining errors (not global instances)
    remaining = [err for i, err in enumerate(errors) if i not in global_error_indices]

    return global_list, remaining


def normalize_for_comparison(word: str) -> str:
    """Normalize for diacritic-insensitive comparison."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", word)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def trim_shared_edges(pg: str, scan: str) -> tuple[str, str]:
    """Trim shared leading/trailing characters."""
    # Leading
    i = 0
    while i < len(pg) and i < len(scan) and pg[i] == scan[i]:
        i += 1
    if i > 3:
        pg = pg[i:]
        scan = scan[i:]
    # Trailing
    j = 0
    while j < len(pg) and j < len(scan) and pg[-(j+1)] == scan[-(j+1)]:
        j += 1
    if j > 3:
        pg = pg[:-j]
        scan = scan[:-j]
    return pg.strip(), scan.strip()


def generate_email(
    errors: list[dict],
    metadata: dict,
    scan_source: str,
    pg_file_path: Path | None,
    global_replacements: list[dict],
    scan_id: str | None,
    pg_file_lines: dict | None,
) -> str:
    """Generate the errata email text."""
    title = metadata.get("title", "Unknown")
    author = metadata.get("author", "Unknown")
    pg_id = metadata.get("pg_id", "")

    # Determine PG filename
    pg_filename = pg_file_path.name if pg_file_path else f"{pg_id}.txt"

    # Load PG file lines for context
    pg_lines = []
    if pg_file_path and pg_file_path.exists():
        pg_lines = pg_file_path.read_text(encoding="utf-8", errors="replace").splitlines()

    lines = []
    lines.append(f"In {title}, by {author}, [EBook #{pg_id}],")
    lines.append(f"File: {pg_filename},")
    lines.append(f"I verified the following changes against the Internet Archive scan:")
    lines.append(f"{scan_source}")
    lines.append(f"NOTE: Page numbers are 'of the scan' not 'of the book.'")
    lines.append("")

    # Global replacements
    lines.append("Global Replacements {")
    if global_replacements:
        for gr in sorted(global_replacements, key=lambda g: g["pg_text"].lower()):
            lines.append(f"{gr['pg_text']} ==> {gr['scan_text']}")
        total = sum(gr["occurrences_in_pg"] for gr in global_replacements)
        lines.append(f"({total} total occurrences in text)")
    else:
        lines.append("(none found)")
    lines.append("}")
    lines.append("")

    # Group errors into global instances vs unique
    global_find_words = {gr["pg_text"] for gr in global_replacements}
    global_instances = []
    unique_errors = []

    for err in errors:
        pair = parse_errata_entry(err.get("pg_text", ""), err.get("scan_text", ""))
        if pair and pair[0] in global_find_words:
            global_instances.append(err)
        else:
            unique_errors.append(err)

    # Global replacement instances section
    if global_instances:
        lines.append("GLOBAL REPLACEMENT INSTANCES (individual pages for visual confirmation)")
        lines.append("")
        lines.append("These are individual page-level occurrences of words also listed")
        lines.append("above as global replacements. Review each before deciding whether")
        lines.append("to apply as a global find/replace or reject.")
        lines.append("")

        # Group by find word
        grouped: dict[str, list] = defaultdict(list)
        for err in global_instances:
            pair = parse_errata_entry(err.get("pg_text", ""), err.get("scan_text", ""))
            if pair:
                grouped[pair[0]].append(err)

        for find_word in sorted(grouped.keys(), key=lambda w: -len(grouped[w])):
            errs = grouped[find_word]
            # Find occurrence count
            occ = sum(gr["occurrences_in_pg"] for gr in global_replacements if gr["pg_text"] == find_word)
            # Get the replace word from first instance
            replace_word = parse_errata_entry(errs[0].get("pg_text", ""), errs[0].get("scan_text", ""))
            replace_word = replace_word[1] if replace_word else ""

            lines.append(f"  {find_word} → {replace_word} ({occ}x in PG text, {len(errs)} caught)")
            lines.append("")

            for err in errs:
                pg_t = re.sub(r"<[^>]+>", "", err.get("pg_text", "").strip())
                scan_t = re.sub(r"<[^>]+>", "", err.get("scan_text", "").strip())
                page = err.get("scan_page", 0)

                if scan_id:
                    scan_url = f"https://archive.org/details/{scan_id}/page/n{page}/mode/1up"
                    lines.append(f"  Page {page} ({scan_url}):")
                else:
                    lines.append(f"  Page {page}:")

                # PG file line context
                pg_file_line = err.get("pg_file_line", 0)
                if pg_file_line > 0 and pg_lines:
                    for offset in [-1, 0, 1]:
                        idx = pg_file_line - 1 + offset
                        if 0 <= idx < len(pg_lines):
                            clean = re.sub(r"<[^>]+>", "", pg_lines[idx]).strip()
                            if clean:
                                if offset == 0:
                                    lines.append(f"    Line {pg_file_line}: {clean}  <-- error here")
                                else:
                                    lines.append(f"    Line {idx + 1}: {clean}")

                pg_trimmed, scan_trimmed = trim_shared_edges(pg_t, scan_t)
                lines.append(f"    {pg_trimmed} ==> {scan_trimmed}")
                lines.append("")
        lines.append("---")
        lines.append("")

    # Unique errors
    if not unique_errors and not global_instances:
        lines.append("I found no errors requiring correction.")
        return "\n".join(lines)

    if not unique_errors:
        lines.append("(No additional unique errors beyond global replacements above.)")
        return "\n".join(lines)

    lines.append("")

    for err in unique_errors:
        pg_t = re.sub(r"<[^>]+>", "", err.get("pg_text", "").strip())
        scan_t = re.sub(r"<[^>]+>", "", err.get("scan_text", "").strip())
        page = err.get("scan_page", 0)

        if scan_id:
            scan_url = f"https://archive.org/details/{scan_id}/page/n{page}/mode/1up"
            lines.append(f"Page {page} ({scan_url}):")
        else:
            lines.append(f"Page {page}:")

        # PG file line context
        pg_file_line = err.get("pg_file_line", 0)
        if pg_file_line > 0 and pg_lines:
            for offset in [-1, 0, 1]:
                idx = pg_file_line - 1 + offset
                if 0 <= idx < len(pg_lines):
                    clean = re.sub(r"<[^>]+>", "", pg_lines[idx]).strip()
                    if clean:
                        if offset == 0:
                            lines.append(f"Line {pg_file_line}: {clean}  <-- error here")
                        else:
                            lines.append(f"Line {idx + 1}: {clean}")

        # Context sentence
        context = err.get("pg_sentence", "")
        if context:
            lines.append(context)

        pg_trimmed, scan_trimmed = trim_shared_edges(pg_t, scan_t)
        lines.append(f"{pg_trimmed} ==> {scan_trimmed}")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Regenerate errata email from JSON with latest format")
    parser.add_argument("errata_json", type=Path, help="Path to existing errata.json")
    parser.add_argument("--pg-file", type=Path, default=None, help="PG text file for line context")
    parser.add_argument("--scan-id", type=str, default=None, help="IA scan identifier")
    parser.add_argument("-o", "--output", type=Path, default=Path("reports"), help="Output directory")
    parser.add_argument("--suffix", type=str, default="2", help="Filename suffix (e.g., -2)")
    args = parser.parse_args()

    # Load JSON
    data = json.loads(args.errata_json.read_text(encoding="utf-8"))
    errors = data.get("errors", [])
    metadata = data.get("metadata", {})
    scan_source = data.get("scan_source", "")

    # Extract scan_id from scan_source URL
    scan_id = args.scan_id
    if not scan_id and scan_source:
        m = re.search(r"archive\.org/details/(\S+)", scan_source)
        if m:
            scan_id = m.group(1)

    print(f"Loaded {len(errors)} errors from {args.errata_json.name}")
    print(f"Scan: {scan_id or 'unknown'}")

    # Detect global replacements
    global_replacements, remaining = detect_global_replacements(errors)
    print(f"Detected {len(global_replacements)} global replacements")
    print(f"  {len(errors) - len(remaining)} errors are global instances")
    print(f"  {len(remaining)} unique errors remain")

    # Generate email
    email = generate_email(
        errors=errors,
        metadata=metadata,
        scan_source=scan_source,
        pg_file_path=args.pg_file,
        global_replacements=global_replacements,
        scan_id=scan_id,
        pg_file_lines=None,
    )

    # Build output filename
    stem = args.errata_json.stem.replace("_errata", "")
    # Remove existing suffix if present
    stem = re.sub(r"-\d+$", "", stem)
    out_name = f"{stem}_errata_email-{args.suffix}.txt"
    out_path = args.output / out_name

    args.output.mkdir(parents=True, exist_ok=True)
    out_path.write_text(email, encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
