"""Comparison reporter: generate JSON and Markdown reports for edition comparisons.

Produces chapter-level grouped reports with summary statistics,
mirroring the structure used in the main errata reporter.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from gerrata.models import (
    ComparisonReport,
    TextualVariant,
    VariantCategory,
    VariantSignificance,
)

logger = __import__("logging").getLogger(__name__)


def _detect_chapters(variants: list[TextualVariant]) -> list[dict]:
    """Group variants into chapters based on chapter_title field.

    Returns list of dicts with chapter info and variant counts.
    """
    chapters: dict[str, dict] = {}
    order: list[str] = []

    for v in variants:
        chapter = v.chapter_title or "Unclassified"
        if chapter not in chapters:
            chapters[chapter] = {
                "title": chapter,
                "a_pages": set(),
                "b_pages": set(),
                "variants": [],
                "counts": defaultdict(int),
            }
            order.append(chapter)

        ch = chapters[chapter]
        if v.edition_a_page:
            ch["a_pages"].add(v.edition_a_page)
        if v.edition_b_page:
            ch["b_pages"].add(v.edition_b_page)
        ch["variants"].append(v)
        ch["counts"][v.category.value] += 1
        ch["counts"][v.significance.value] += 1

    result = []
    for title in order:
        ch = chapters[title]
        a_pages = sorted(ch["a_pages"])
        b_pages = sorted(ch["b_pages"])
        a_range = f"{a_pages[0]}-{a_pages[-1]}" if len(a_pages) > 1 else str(a_pages[0]) if a_pages else "—"
        b_range = f"{b_pages[0]}-{b_pages[-1]}" if len(b_pages) > 1 else str(b_pages[0]) if b_pages else "—"
        result.append({
            "title": title,
            "a_pages": a_range,
            "b_pages": b_range,
            "total_variants": len(ch["variants"]),
            "major": ch["counts"].get("major", 0),
            "moderate": ch["counts"].get("moderate", 0),
            "minor": ch["counts"].get("minor", 0),
            "trivial": ch["counts"].get("trivial", 0),
        })

    return result


def _format_number(n: int) -> str:
    """Format a number with commas."""
    return f"{n:,}"


class ComparisonReporter:
    """Generate edition comparison reports in JSON and Markdown formats."""

    def __init__(
        self,
        significance_filter: str = "all",
        report: ComparisonReport | None = None,
    ):
        """
        Args:
            significance_filter: Only include variants at or above this level.
                "all" includes everything. "major" includes major+moderate+minor+trivial.
                "moderate" includes moderate+minor+trivial.
            report: Optional pre-built ComparisonReport to use.
        """
        self.significance_filter = significance_filter
        self._report = report

        # Significance hierarchy for filtering
        _hierarchy = {
            "major": 0,
            "moderate": 1,
            "minor": 2,
            "trivial": 3,
            "all": 4,
        }
        self._filter_level = _hierarchy.get(significance_filter, 4)

    def _filter_variants(self, variants: list[TextualVariant]) -> list[TextualVariant]:
        """Filter variants based on significance level."""
        if self.significance_filter == "all":
            return variants

        _sig_order = {
            VariantSignificance.MAJOR: 0,
            VariantSignificance.MODERATE: 1,
            VariantSignificance.MINOR: 2,
            VariantSignificance.TRIVIAL: 3,
        }
        _sig_filter = {
            "major": 0,
            "moderate": 1,
            "minor": 2,
            "all": 3,
        }
        cutoff = _sig_filter.get(self.significance_filter, 3)

        return [
            v for v in variants
            if _sig_order.get(v.significance, 3) <= cutoff
        ]

    def generate_json(
        self, report: ComparisonReport, output_path: Path
    ) -> Path:
        """Generate a JSON comparison report.

        Args:
            report: The comparison report to serialize.
            output_path: Path to write the JSON file.

        Returns:
            Path to the written JSON file.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        logger.info(f"JSON report written to {output_path}")
        return output_path

    def generate_markdown(
        self, report: ComparisonReport, output_path: Path
    ) -> Path:
        """Generate a Markdown comparison report.

        Args:
            report: The comparison report to render.
            output_path: Path to write the Markdown file.

        Returns:
            Path to the written Markdown file.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        lines = self._render_markdown(report)

        with open(output_path, "w") as f:
            f.write("\n".join(lines))

        logger.info(f"Markdown report written to {output_path}")
        return output_path

    def generate(
        self,
        report: ComparisonReport,
        output_dir: Path,
        formats: list[str] | None = None,
    ) -> dict[str, Path]:
        """Generate comparison reports in multiple formats.

        Args:
            report: The comparison report.
            output_dir: Directory to write reports into.
            formats: List of formats to generate ("json", "markdown").
                Defaults to ["json", "markdown"].

        Returns:
            Dict mapping format name to output file path.
        """
        if formats is None:
            formats = ["json", "markdown"]

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        results: dict[str, Path] = {}

        if "json" in formats:
            json_path = output_dir / "edition_comparison.json"
            results["json"] = self.generate_json(report, json_path)

        if "markdown" in formats:
            md_path = output_dir / "edition_comparison.md"
            results["markdown"] = self.generate_markdown(report, md_path)

        return results

    def _render_markdown(self, report: ComparisonReport) -> list[str]:
        """Render the report as Markdown lines."""
        lines: list[str] = []

        # Title
        a_label = report.edition_a.label or report.edition_a.source
        b_label = report.edition_b.label or report.edition_b.source
        title = report.edition_a.title or "Untitled"
        lines.append(f"# Edition Comparison: {title}")
        lines.append("")

        # Edition info table
        lines.append("## Editions")
        lines.append("")
        lines.append("| | Edition A | Edition B |")
        lines.append("|---|---|---|")
        lines.append(f"| Source | {report.edition_a.source} | {report.edition_b.source} |")
        if report.edition_a.year or report.edition_b.year:
            lines.append(f"| Year | {report.edition_a.year or '—'} | {report.edition_b.year or '—'} |")
        if report.edition_a.publisher or report.edition_b.publisher:
            lines.append(f"| Publisher | {report.edition_a.publisher or '—'} | {report.edition_b.publisher or '—'} |")
        lines.append(
            f"| Pages | {_format_number(report.edition_a.total_pages)} | "
            f"{_format_number(report.edition_b.total_pages)} |"
        )
        lines.append(
            f"| Chars | {_format_number(report.edition_a.total_chars)} | "
            f"{_format_number(report.edition_b.total_chars)} |"
        )
        lines.append("")

        # Alignment summary
        alignment = report.alignment_summary
        lines.append("## Alignment")
        lines.append("")
        total_b = max(report.edition_b.total_pages, 1)
        lines.append(
            f"- Pages matched: {_format_number(alignment['pages_matched'])}/"
            f"{_format_number(total_b)} ({alignment['coverage_pct']}%)"
        )
        lines.append(f"- Average confidence: {alignment['avg_confidence']:.0%}")
        lines.append("")

        # Summary
        by_cat = report.by_category
        by_sig = report.by_significance
        major = by_sig.get("major", 0)
        moderate = by_sig.get("moderate", 0)
        minor = by_sig.get("minor", 0)
        trivial = by_sig.get("trivial", 0)

        lines.append("## Summary")
        lines.append("")
        lines.append(
            f"- **{_format_number(report.total_variants)} total variants** found"
        )
        lines.append(
            f"- **{_format_number(major)} major** | {_format_number(moderate)} moderate "
            f"| {_format_number(minor)} minor | {_format_number(trivial)} trivial"
        )
        lines.append("")

        # Category breakdown
        if by_cat:
            lines.append("### By Category")
            lines.append("")
            lines.append("| Category | Count |")
            lines.append("|---|---|")
            cat_order = [
                "textual_variant", "punctuation_variant", "spelling_change",
                "normalization", "missing_content", "added_content",
                "formatting_variant", "linebreak_variant",
            ]
            for cat in cat_order:
                count = by_cat.get(cat, 0)
                if count > 0:
                    label = cat.replace("_", " ").title()
                    lines.append(f"| {label} | {_format_number(count)} |")
            lines.append("")

        # Major variants section
        major_variants = [
            v for v in report.variants
            if v.significance == VariantSignificance.MAJOR
        ]
        if major_variants:
            lines.append("## Major Variants")
            lines.append("")
            for i, v in enumerate(major_variants, 1):
                lines.append(f"### {i}. Page {v.edition_a_page} → {v.edition_b_page} (confidence: {v.confidence:.0%})")
                lines.append(f"- **A:** \"{v.edition_a_text}\"")
                lines.append(f"- **B:** \"{v.edition_b_text}\"")
                if v.context:
                    lines.append(f"- Context: \"...{v.context}...\"")
                if v.chapter_title:
                    lines.append(f"- Chapter: {v.chapter_title}")
                lines.append("")

        # Moderate variants section
        moderate_variants = [
            v for v in report.variants
            if v.significance == VariantSignificance.MODERATE
        ]
        if moderate_variants:
            lines.append("## Moderate Variants")
            lines.append("")
            lines.append(f"({len(moderate_variants)} moderate variants)")
            lines.append("")
            for i, v in enumerate(moderate_variants[:50], 1):  # Limit display
                a_page = v.edition_a_page if v.edition_a_page else "?"
                b_page = v.edition_b_page if v.edition_b_page else "?"
                lines.append(f"**{i}.** Page {a_page} → {b_page}: \"{v.edition_a_text}\" → \"{v.edition_b_text}\"")
            if len(moderate_variants) > 50:
                lines.append(f"\n... and {len(moderate_variants) - 50} more (see JSON for full list)")
            lines.append("")

        # Chapter-level summary table
        chapters = _detect_chapters(report.variants)
        if chapters and len(chapters) > 1:
            lines.append("## All Variants by Chapter")
            lines.append("")
            lines.append("| Chapter | A Pages | B Pages | Major | Moderate | Minor | Trivial |")
            lines.append("|---|---|---|---|---|---|---|")
            for ch in chapters:
                lines.append(
                    f"| {ch['title']} | {ch['a_pages']} | {ch['b_pages']} | "
                    f"{ch['major']} | {ch['moderate']} | {ch['minor']} | {ch['trivial']} |"
                )
            lines.append("")

        # Footer
        lines.append("---")
        lines.append(f"*Generated by Gerrata edition comparison on {report.date or datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*")

        return lines
