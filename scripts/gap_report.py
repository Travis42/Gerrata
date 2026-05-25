#!/usr/bin/env python3
"""Run gap detection on cached pipeline data and output a markdown report.

Usage:
    python3 scripts/gap_report.py [--scan-id SCAN_ID] [--pg-file FILE] [-o OUTPUT]

If --scan-id is given, reads cached pipeline intermediates from cache/<scan_id>/.
Otherwise reads from the default cache/ directory based on existing data.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gerrata.checker.gap_detector import detect_scan_gaps, filter_for_report


def find_cache_dir(scan_id: str | None = None) -> Path:
    """Find the cache directory with pipeline intermediates."""
    cache = Path("cache")
    if scan_id:
        d = cache / scan_id
        if d.exists() and (d / "03_alignments.json").exists():
            return d
        raise FileNotFoundError(f"No pipeline cache found at {d}")

    # Try to find any directory with alignments
    for d in sorted(cache.iterdir()):
        if d.is_dir() and (d / "03_alignments.json").exists():
            return d

    raise FileNotFoundError("No pipeline cache found in cache/")


def load_pipeline_data(cache_dir: Path, pg_file: Path | None = None) -> dict:
    """Load all cached pipeline data needed for gap detection."""
    data = {}

    # Alignments
    with open(cache_dir / "03_alignments.json") as f:
        data["alignments"] = json.load(f)

    # Scan pages
    with open(cache_dir / "03_scan_pages.json") as f:
        data["scan_pages"] = json.load(f)

    # PG parsed text
    pg_path = cache_dir / "01_pg_parsed.json"
    if not pg_path.exists():
        pg_path = None

    if pg_path:
        with open(pg_path) as f:
            pg_parsed = json.load(f)
        data["pg_body_text"] = pg_parsed.get("body_text", "")
        data["pg_title"] = pg_parsed.get("title", "Unknown")
        data["pg_author"] = pg_parsed.get("author", "Unknown")

        # Extract body between START/END markers for alignment-aware detection
        full = data["pg_body_text"]
        start_marker = re.search(r"\*\*\* START OF.*?\*\*\*", full)
        end_marker = re.search(r"\*\*\* END OF.*?\*\*\*", full)
        if start_marker and end_marker:
            data["pg_body_text"] = full[start_marker.end():end_marker.start()]
    else:
        data["pg_body_text"] = ""
        data["pg_title"] = "Unknown"
        data["pg_author"] = "Unknown"

    # Full PG text (with header/footer) for content hole verification.
    # Defaults to body_text if no separate full text is provided.
    if pg_file and pg_file.exists():
        data["pg_full_text"] = pg_file.read_text(encoding="utf-8", errors="replace")
    else:
        data["pg_full_text"] = data["pg_body_text"]

    # Existing gaps (from previous run, if any)
    old_gaps_path = cache_dir / "05_gaps.json"
    if old_gaps_path.exists():
        with open(old_gaps_path) as f:
            data["old_gaps"] = json.load(f)
    else:
        data["old_gaps"] = []

    return data


def format_gap(gap, idx: int) -> str:
    """Format a single gap as a markdown section."""
    lines = []
    lines.append(f"### Gap {idx}: Page {gap['page']} — {gap['strategy']}")

    # Confidence badge
    conf = gap.get("confidence", "unknown")
    badge = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(conf, "❓")
    lines.append(f"**Confidence:** {badge} {conf} · **{gap.get('word_count', 0)} words**")

    if gap.get("coverage_ratio") is not None:
        lines.append(f"**Coverage ratio:** {gap['coverage_ratio']:.1%}")

    if gap.get("pg_verified"):
        lines.append("**PG verification:** ✅ Text absent from PG — likely real omission")
    elif gap.get("pg_verified") is False:
        lines.append("**PG verification:** ⚠️ Text found elsewhere in PG — possible alignment artifact")

    # Missing words (content holes)
    if gap.get("missing_words"):
        lines.append(f"\n**Missing from PG:** _\"{gap['missing_words']}\"_")
        if gap.get("pg_context_before"):
            lines.append(f"**PG before:** ...{gap['pg_context_before']} ▸ _[gap]_ ▸ {gap['pg_context_after']}...")

    # Scan text preview
    preview = gap.get("scan_text_preview", "")
    if preview:
        # Truncate for readability
        if len(preview) > 300:
            preview = preview[:300] + "..."
        lines.append(f"\n**Scan text:**\n> {preview}")

    lines.append("")
    return "\n".join(lines)


def write_report(data: dict, gaps: list, report_gaps: list, output_path: Path):
    """Write the gap detection report as markdown."""
    scan_id = data.get("scan_id", "unknown")
    title = data["pg_title"]
    author = data["pg_author"]

    # Count by strategy
    by_strategy = {}
    by_confidence = {"high": 0, "medium": 0, "low": 0}
    content_holes = [g for g in gaps if g.get("strategy") == "content_hole"]
    verified_holes = [g for g in content_holes if g.get("pg_verified")]

    for g in gaps:
        s = g.get("strategy", "unknown")
        by_strategy[s] = by_strategy.get(s, 0) + 1
        c = g.get("confidence", "low")
        by_confidence[c] = by_confidence.get(c, 0) + 1

    old_count = len(data.get("old_gaps", []))
    lines = []

    lines.append(f"# Gap Detection Report: {title}")
    lines.append(f"**Author:** {author} · **Scan:** `{scan_id}`\n")
    lines.append("---\n")
    lines.append("## Summary\n")

    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total gaps detected | {len(gaps)} |")
    lines.append(f"| Previous run (old gaps) | {old_count} |")
    lines.append(f"| High-confidence for report | {len(report_gaps)} |")
    lines.append(f"| Content holes (missing words) | {len(content_holes)} |")
    lines.append(f"| Content holes verified absent | {len(verified_holes)} |")
    lines.append("")

    # Strategy breakdown
    lines.append("### By Strategy\n")
    for s, count in sorted(by_strategy.items()):
        label = {
            "uncovered": "Uncovered pages (no alignment)",
            "partial": "Partial coverage (<60% of page aligned)",
            "content_hole": "Content holes (words missing within aligned passage)",
        }.get(s, s)
        lines.append(f"- **{s}**: {count} — {label}")
    lines.append("")

    # Confidence breakdown
    lines.append("### By Confidence\n")
    lines.append(f"- 🔴 High: {by_confidence['high']}")
    lines.append(f"- 🟡 Medium: {by_confidence['medium']}")
    lines.append(f"- ⚪ Low: {by_confidence['low']}")
    lines.append("")

    # Content holes section (always interesting)
    if content_holes:
        lines.append("---\n")
        lines.append("## Content Holes (Words Missing Within Aligned Passages)\n")
        lines.append("These are passages where the scan has text between two well-matched regions,")
        lines.append("but PG has nothing in between — indicating words deleted from the PG transcription.\n")

        for i, g in enumerate(content_holes, 1):
            lines.append(format_gap(g, i))

    # Report-quality gaps (high-confidence, large)
    if report_gaps:
        non_hole_report = [g for g in report_gaps if g.get("strategy") != "content_hole"]
        if non_hole_report:
            lines.append("---\n")
            lines.append("## High-Confidence Structural Gaps\n")
            lines.append("These are pages or page regions where significant scan text has no PG alignment.\n")
            for i, g in enumerate(non_hole_report, 1):
                lines.append(format_gap(g, i))

    # All gaps (collapsed detail)
    all_non_hole = [g for g in gaps if g.get("strategy") != "content_hole"]
    if all_non_hole:
        lines.append("---\n")
        lines.append("## All Structural Gaps (Full Detail)\n")
        for i, g in enumerate(all_non_hole, 1):
            lines.append(format_gap(g, i))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Generate gap detection report from cached pipeline data")
    parser.add_argument("--scan-id", help="IA scan identifier (e.g. 2015.148755.Nostromo)")
    parser.add_argument("--pg-file", type=Path, help="Full PG text file (with header/footer) for content hole verification")
    parser.add_argument("-o", "--output", type=Path, help="Output markdown file path")
    args = parser.parse_args()

    # Find cache directory
    cache_dir = find_cache_dir(args.scan_id)
    scan_id = args.scan_id or cache_dir.name
    print(f"Using cache: {cache_dir}")

    # Load data
    data = load_pipeline_data(cache_dir, args.pg_file)
    data["scan_id"] = scan_id
    print(f"Loaded {len(data['alignments'])} alignments, {len(data['scan_pages'])} scan pages")
    print(f"PG text: {len(data['pg_body_text']):,} chars")
    print(f"Full PG text: {len(data['pg_full_text']):,} chars")

    # Run gap detection
    print("\nRunning gap detection...")
    gaps = detect_scan_gaps(
        pg_text=data["pg_body_text"],
        alignments=data["alignments"],
        scan_pages=data["scan_pages"],
        pg_full_text=data["pg_full_text"],
    )
    print(f"Total gaps: {len(gaps)}")

    # Filter for report
    report_gaps = filter_for_report(gaps)
    print(f"High-confidence for report: {len(report_gaps)}")

    # Content holes
    content_holes = [g for g in gaps if g.strategy == "content_hole"]
    print(f"Content holes: {len(content_holes)}")
    for h in content_holes:
        print(f"  Page {h.page}: {h.word_count}w ({h.confidence}) — {h.missing_words[:60]}")

    # Write report
    output = args.output or Path(f"reports/gap-detection-{scan_id}.md")
    path = write_report(data, [g.to_dict() for g in gaps], [g.to_dict() for g in report_gaps], output)
    print(f"\nReport written to: {path}")


if __name__ == "__main__":
    main()
