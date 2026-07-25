"""Report generator for gerrata quality audit results.

Generates markdown errata reports, JSON reports, and PG errata email format.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from gerrata.checker.dictionary import DictionaryChecker


def _next_version(path: Path) -> Path:
    """If path exists, return the next available versioned filename.

    Example: if ``report.txt`` exists, returns ``report-2.txt``.
    """
    if not path.exists():
        return path
    stem = path.stem
    ext = path.suffix
    parent = path.parent
    n = 2
    while True:
        versioned = parent / f"{stem}-{n}{ext}"
        if not versioned.exists():
            return versioned
        n += 1


def _slugify(text: str, max_length: int = 60) -> str:
    """Slugify a title for use in filenames.

    Lowercase, strip non-alphanumeric chars, collapse spaces to hyphens,
    truncate to max_length on word boundary.
    """
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    if len(text) > max_length:
        # Truncate on word boundary
        text = text[:max_length].rsplit("-", 1)[0]
    return text

from rich.console import Console
from rich.table import Table

from gerrata.models import Error, ErrorCategory, ErrorSeverity, Report, Verdict
from gerrata.fetcher.pg import PGParsedText


class ReportGenerator:
    """Generate quality audit reports from Report objects."""

    @staticmethod
    def _strip_html(text: str) -> str:
        """Remove HTML tags from text for clean report output."""
        return re.sub(r"<[^>]+>", "", text)

    @staticmethod
    def _trim_shared_edges(pg_text: str, scan_text: str) -> tuple[str, str]:
        """Trim common leading/trailing punctuation from both texts."""
        from gerrata.text_utils import trim_shared_edges
        return trim_shared_edges(pg_text, scan_text)

    def __init__(self, console: Optional[Console] = None, pg_parsed_text: Optional[PGParsedText] = None,
                 pg_file_path: Optional[Path] = None, scan_id: Optional[str] = None,
                 scan_pages: Optional[list] = None, body_text: str = "",
                 alignments: Optional[list] = None, pg_full_text: str = "",
                 global_replacements: Optional[list] = None):
        self.console = console or Console()
        self.pg_parsed_text = pg_parsed_text
        self.pg_file_path = Path(pg_file_path) if pg_file_path else None
        self.scan_id = scan_id
        self.scan_pages = scan_pages or []
        self.body_text = body_text
        self.alignments = alignments or []
        self.pg_full_text = pg_full_text or body_text
        self.global_replacements = global_replacements or []

    def _extract_sentence(self, text: str, offset: int, length: int) -> str:
        """Extract the sentence containing the given offset.

        Args:
            text: Full text to search in
            offset: Character offset of the target text
            length: Length of the target text

        Returns:
            The sentence containing the target text, or empty string if not found
        """
        if not text or offset < 0 or offset >= len(text):
            return ""

        # Find sentence boundaries: look backwards for ., !, ?, or paragraph break
        start = offset
        while start > 0:
            char = text[start - 1]
            if char in '.!?':
                # Include the punctuation and move past it
                start -= 1
                # Skip the punctuation
                while start > 0 and text[start] in '.!? ':
                    start += 1
                break
            if char == '\n' and text[start-1:start] == '\n\n':
                # Paragraph break
                break
            start -= 1

        # Look forward for sentence end
        end = offset + length
        while end < len(text):
            char = text[end]
            if char in '.!?':
                # Include the punctuation
                end += 1
                break
            if char == '\n' and end + 1 < len(text) and text[end+1] == '\n':
                # Paragraph break
                break
            end += 1

        # Extract the sentence and clean it up
        sentence = text[start:end].strip()
        # Replace multiple whitespace with single space
        sentence = ' '.join(sentence.split())
        # Strip underscores (PG italic/formatting markers)
        sentence = sentence.replace('_', '')
        return sentence

    def _get_ia_leaf_number(self, scan_page: int) -> int:
        """Get the IA leaf number for a scan page from its image path.

        IA uses leaf numbers (n0, n1, n2...) for the book's numbered pages.
        Some JP2 zips include a cover image as _0000.jp2 (different aspect
        ratio) that IA doesn't count as a leaf. When present, JP2 filenames
        are offset by +1 from IA leaf numbers: _0001.jp2 = n0, _0002.jp2 = n1.

        Args:
            scan_page: The 0-based index of the page in the scan_pages array

        Returns:
            The IA leaf number to use in URLs like /page/n48/mode/1up
        """
        # Try to get image path from scan_pages
        if self.scan_pages and scan_page < len(self.scan_pages):
            page = self.scan_pages[scan_page]
            if page.image_path:
                # Extract number from filename like 06-Stevenson-JekyllHyde_0048.png
                path_str = str(page.image_path)
                match = re.search(r'_(\d+)\.\w+$', path_str)
                if match:
                    jp2_num = int(match.group(1))
                    # IA book viewer leaf numbering starts at n0 for the first
                    # content page. JP2 zips include a cover image as _0000.jp2
                    # that isn't counted as a leaf, so all JP2 filenames are
                    # offset by +1 from IA leaf numbers.
                    return jp2_num - 1
        # Fallback to scan_page (approximate)
        return scan_page

    def compute_line_number(self, pg_offset: int, pg_text: str) -> int:
        """Compute the line number in the PG HTML file for a given text offset.

        Args:
            pg_offset: Character offset in the extracted body text
            pg_text: The text passage to search for in the HTML file

        Returns:
            Line number (1-indexed) in the PG HTML file, or 0 if not found
        """
        if not self.pg_file_path or not self.pg_file_path.exists():
            return 0

        try:
            html_content = self.pg_file_path.read_text(encoding="utf-8")
            lines = html_content.splitlines()

            # Clean the pg_text for searching: remove extra whitespace and special quotes
            search_text = self._clean_text_for_search(pg_text)

            # Search through the HTML file for the text
            for line_num, line in enumerate(lines, 1):
                # Clean the line for comparison
                cleaned_line = self._clean_html_line(line)

                # Check if our search text appears in this line
                if search_text in cleaned_line:
                    return line_num

            # If not found in a single line, try multi-line search
            # Some errors span multiple lines (e.g., "text1. "Text2")
            return self._multiline_search(lines, search_text)

        except Exception:
            return 0

    def _clean_text_for_search(self, text: str) -> str:
        """Clean text for searching in HTML file."""
        # Normalize whitespace (including multiple spaces)
        text = re.sub(r'\s+', ' ', text)
        # Convert Unicode curly quotes to straight quotes (HTML uses entities)
        # Use Unicode escape sequences to ensure proper character matching
        text = text.replace('\u201c', '"').replace('\u201d', '"')  # Left and right double quotes
        text = text.replace('\u2018', "'").replace('\u2019', "'")  # Left and right single quotes
        # Convert em dashes and en dashes to hyphens
        text = text.replace('\u2014', '-').replace('\u2013', '-')  # Em dash and en dash
        return text.strip()

    def _clean_html_line(self, line: str) -> str:
        """Clean HTML line for text comparison."""
        # Remove HTML tags but keep text content
        line = re.sub(r'<[^>]+>', ' ', line)
        # Decode HTML entities (common ones)
        line = line.replace('&ldquo;', '"').replace('&rdquo;', '"')
        line = line.replace('&lsquo;', "'").replace('&rsquo;', "'")
        line = line.replace('&mdash;', '-').replace('&ndash;', '-')
        # Handle &amp; last to avoid double-decoding
        line = line.replace('&amp;', '&')
        # Normalize whitespace
        line = re.sub(r'\s+', ' ', line)
        return line.strip()

    def _multiline_search(self, lines: list[str], search_text: str) -> int:
        """Search for text that spans multiple lines.

        Args:
            lines: List of HTML lines
            search_text: Text to search for (already cleaned)

        Returns:
            Line number where text starts, or 0 if not found
        """
        # Try combining adjacent lines (up to 6 lines to handle empty paragraphs)
        for window_size in range(2, 7):
            for line_num in range(1, len(lines) - window_size + 2):
                # Combine window_size lines, filtering out empty lines
                combined_lines = lines[line_num-1:line_num-1+window_size]
                cleaned_lines = [self._clean_html_line(line) for line in combined_lines]
                # Filter out empty/whitespace-only lines and join with single space
                non_empty_lines = [line for line in cleaned_lines if line.strip()]
                combined_text = ' '.join(non_empty_lines)

                # Check if search text is in the combined text
                if search_text in combined_text:
                    return line_num

        # If still not found, try fuzzy matching
        if len(search_text.split()) <= 5:
            return self._fuzzy_find_line(lines, search_text)

        return 0

    def _fuzzy_find_line(self, lines: list[str], search_text: str) -> int:
        """Fuzzy match short phrases in HTML file."""
        search_words = search_text.lower().split()

        for line_num, line in enumerate(lines, 1):
            cleaned_line = self._clean_html_line(line).lower()
            line_words = cleaned_line.split()

            # Check if all search words appear near each other
            for i in range(len(line_words)):
                # Try to match a sequence of words
                window = line_words[i:i+len(search_words)]
                if len(window) == len(search_words):
                    matches = sum(1 for s, w in zip(search_words, window)
                                 if s in w or w in s)
                    if matches >= len(search_words) * 0.8:  # 80% match threshold
                        return line_num

        return 0

    def get_line_context(self, pg_file_line: int) -> str:
        """Get surrounding lines from the PG HTML file for a given line number.

        Returns 1 line before and 1 line after the target line, formatted
        with line numbers and a marker on the target line.

        Args:
            pg_file_line: 1-indexed line number in the PG HTML file

        Returns:
            Formatted string with surrounding context, or empty string if unavailable
        """
        if not self.pg_file_path or not self.pg_file_path.exists() or pg_file_line <= 0:
            return ""

        try:
            lines = self.pg_file_path.read_text(encoding="utf-8").splitlines()
            if pg_file_line > len(lines):
                return ""

            context_lines: list[str] = []
            # Previous line (1 before)
            prev_idx = pg_file_line - 2  # 0-indexed
            if prev_idx >= 0 and prev_idx < len(lines):
                prev_line = self._clean_html_line(lines[prev_idx])
                if prev_line:
                    context_lines.append(f"Line {prev_idx + 1}: {prev_line}")

            # Target line with marker
            target_idx = pg_file_line - 1  # 0-indexed
            target_line = self._clean_html_line(lines[target_idx])
            if target_line:
                context_lines.append(f"Line {pg_file_line}: {target_line}  <-- error here")

            # Next line (1 after)
            next_idx = pg_file_line  # 0-indexed
            if next_idx < len(lines):
                next_line = self._clean_html_line(lines[next_idx])
                if next_line:
                    context_lines.append(f"Line {next_idx + 1}: {next_line}")

            return "\n".join(context_lines)
        except Exception:
            return ""

    def get_chapter_context(self, pg_offset: int) -> str:
        """Get the chapter title for a given PG text offset.

        Args:
            pg_offset: Character offset in the PG body text

        Returns:
            Chapter title, or empty string if not found
        """
        if not self.pg_parsed_text or not self.pg_parsed_text.chapters:
            return ""

        for chapter in self.pg_parsed_text.chapters:
            # Handle chapters saved as strings (from save_intermediate fallback)
            if isinstance(chapter, str):
                continue
            if chapter.offset <= pg_offset < chapter.end_offset:
                return chapter.title

        return ""

    def format_arrow_fix(self, error: Error) -> str:
        """Format the suggested fix in PG arrow format.

        Args:
            error: The error object

        Returns:
            String in "erroneous ==> corrected" format, or scan text for comparison
        """
        # For verified scan_correct errors, use arrow format showing PG text vs scan text
        # Prefer image_evidence (what the verifier actually read from the page) over
        # scan_text (the OCR transcription, which is itself unreliable).
        if error.verdict == Verdict.SCAN_CORRECT and error.confidence > 0.0:
            pg_text = error.candidate.pg_text.strip()
            display_text = error.image_evidence.strip() or error.candidate.scan_text.strip()
            source_label = "image" if error.image_evidence.strip() else "OCR"
            pg_trimmed, display_trimmed = self._trim_shared_edges(pg_text, display_text)
            return f"{pg_trimmed} ==> {display_trimmed} [{source_label}]"

        # For other cases with confidence, prefer image_evidence
        if error.confidence > 0.0:
            pg_text = error.candidate.pg_text.strip()
            display_text = error.image_evidence.strip() or error.candidate.scan_text.strip()
            source_label = "image" if error.image_evidence.strip() else "OCR"
            pg_trimmed, display_trimmed = self._trim_shared_edges(pg_text, display_text)
            return f"{pg_trimmed} ==> {display_trimmed} [{source_label}]"
        else:
            # No LLM verification (confidence=0.0), we don't know which text is correct
            pg_text = error.candidate.pg_text.strip()
            scan_text = error.candidate.scan_text.strip()
            return f"{pg_text} (possible error - comparison text: {scan_text})"

    def enrich_errors_with_context(self, report: Report) -> None:
        """Add line numbers and chapter context to all errors in the report.

        This modifies the Error objects in place, always refining line numbers
        from body-text approximations to accurate PG HTML file line numbers.
        """
        for error in report.errors:
            # Always compute line number from PG HTML file (more accurate than body-text)
            line_num = self.compute_line_number(
                error.candidate.pg_offset,
                error.candidate.pg_text
            )
            if line_num > 0:
                error.pg_file_line = line_num
                # Also update the candidate so it stays in sync
                error.candidate.pg_file_line = line_num

            # Get chapter context
            if not error.chapter_title:
                error.chapter_title = self.get_chapter_context(error.candidate.pg_offset)

            # Build location description
            if not error.location_description:
                parts = []
                if error.chapter_title:
                    parts.append(f"Chapter: {error.chapter_title}")
                if error.pg_file_line > 0:
                    parts.append(f"Line {error.pg_file_line}")
                error.location_description = "; ".join(parts) if parts else ""

    def generate_markdown(self, report: Report) -> str:
        """Generate a markdown errata report."""
        lines: list[str] = []

        # Header
        lines.append(f"# Quality Audit Report: {report.metadata.title}")
        lines.append(f"**PG #{report.metadata.pg_id}** by {report.metadata.author}")
        lines.append(f"")
        lines.append(f"**Scan source:** {report.scan_source}")
        lines.append(f"**Date:** {report.date or datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        # Summary
        lines.append("## Summary")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Pages checked | {report.pages_checked} / {report.total_pages} |")
        lines.append(f"| Pages aligned | {len(report.alignments)} |")
        lines.append(f"| Per-page confidence | {report.avg_page_confidence:.2f} |")
        lines.append(f"| Text coverage | {report.alignment_confidence:.0%} |")
        # Only show edition match confidence if it was actually computed (> 0)
        if report.edition_match_confidence > 0.0:
            lines.append(f"| Edition match confidence | {report.edition_match_confidence:.0%} |")
        else:
            lines.append(f"| Edition match confidence | N/A (no LLM comparison) |")
        if report.edition_notes:
            lines.append(f"| Edition notes | {report.edition_notes} |")
        lines.append("")

        # Error summary
        lines.append("## Error Summary")
        lines.append("")
        lines.append("| Category | Count |")
        lines.append("|----------|-------|")
        lines.append(f"| High confidence errors | {len(report.high_confidence_errors)} |")
        lines.append(f"| Medium confidence errors | {len(report.medium_confidence_errors)} |")
        lines.append(f"| Low confidence errors | {len(report.low_confidence_errors)} |")
        lines.append(f"| Edition variants | {len(report.edition_variants)} |")
        lines.append(f"| Intentional changes | {len(report.intentional_changes)} |")
        lines.append(f"| **Total candidates** | {report.total_candidates} |")
        lines.append("")

        if not report.errors:
            lines.append("## Errors")
            lines.append("")
            lines.append("No discrepancies found. The PG text matches the scan well.")
            lines.append("")
            return "\n".join(lines)

        # Edition notes
        if report.edition_variants:
            lines.append("## Edition Variants")
            lines.append("")
            lines.append(
                "These differences are likely due to the PG text and scan being "
                "from different editions, not errors."
            )
            lines.append("")
            for err in report.edition_variants:
                lines.append(f"- **Page {self._get_ia_leaf_number(err.candidate.scan_page)}:** "
                           f"PG has \"{self._strip_html(err.candidate.pg_text)}\" vs scan \"{self._strip_html(err.candidate.scan_text)}\"")
                if err.reasoning:
                    lines.append(f"  - {err.reasoning}")
            lines.append("")

        # High confidence errors (submit-ready: scan_correct with >= 80% confidence)
        submit_ready = [
            e for e in report.errors
            if e.verdict == Verdict.SCAN_CORRECT
            and e.confidence >= 0.8
            and e.category != ErrorCategory.EDITION_VARIANT
            and e.category != ErrorCategory.MODERNIZATION
            and e.category != ErrorCategory.INTENTIONAL_CHANGE
            and e.category != ErrorCategory.ALIGNMENT_ARTIFACT
        ]

        if submit_ready:
            lines.append("## High Confidence Errors")
            lines.append("")
            lines.append(
                "These are the most likely to be real errors in the PG text "
                "that should be reported for correction."
            )
            lines.append("")
            for i, err in enumerate(submit_ready, 1):
                lines.append(f"### Error {i} - {err.category.value} (confidence: {err.confidence:.0%})")
                lines.append(f"")
                location_parts = []
                if err.pg_file_line > 0:
                    location_parts.append(f"**File line:** {err.pg_file_line}")
                if err.chapter_title:
                    location_parts.append(f"**Chapter:** {err.chapter_title}")
                if err.candidate.scan_page is not None:
                    location_parts.append(f"**Scan page:** {self._get_ia_leaf_number(err.candidate.scan_page)}")

                if location_parts:
                    lines.append(f"- {'; '.join(location_parts)}")
                lines.append(f"- **PG text:** \"{self._strip_html(err.candidate.pg_text)}\"")
                lines.append(f"- **Scan text:** \"{self._strip_html(err.candidate.scan_text)}\"")
                lines.append(f"- **Type:** `{err.category.value}`")
                lines.append(f"- **Severity:** {err.severity.value}")
                if err.reasoning:
                    lines.append(f"- **Reasoning:** {err.reasoning}")
                if err.suggested_fix:
                    arrow_fix = self.format_arrow_fix(err)
                    lines.append(f"- **Suggested fix:** `{arrow_fix}`")
                if err.scan_image_path:
                    lines.append(f"- **Scan image:** {err.scan_image_path}")
                # Add scan page link if we have scan_id
                if self.scan_id and err.candidate.scan_page is not None:
                    leaf_num = self._get_ia_leaf_number(err.candidate.scan_page)
                    scan_url = f"https://archive.org/details/{self.scan_id}/page/n{leaf_num}/mode/1up"
                    lines.append(f"- **Scan page:** [{scan_url}]({scan_url})")
                lines.append("")

        # Medium confidence errors
        if report.medium_confidence_errors:
            lines.append("## Medium Confidence Errors")
            lines.append("")
            for i, err in enumerate(report.medium_confidence_errors, 1):
                location_parts = []
                if err.pg_file_line > 0:
                    location_parts.append(f"line {err.pg_file_line}")
                if err.chapter_title:
                    location_parts.append(f"chapter: {err.chapter_title}")

                location_str = f" ({', '.join(location_parts)})" if location_parts else ""

                lines.append(f"{i}. **Page {self._get_ia_leaf_number(err.candidate.scan_page)}{location_str}:** "
                           f"PG has \"{self._strip_html(err.candidate.pg_text)}\" vs scan \"{self._strip_html(err.candidate.scan_text)}\" "
                           f"(confidence: {err.confidence:.0%})")
            lines.append("")

        # Low confidence errors
        if report.low_confidence_errors:
            lines.append("## Low Confidence Errors")
            lines.append("")
            for i, err in enumerate(report.low_confidence_errors, 1):
                location_parts = []
                if err.pg_file_line > 0:
                    location_parts.append(f"line {err.pg_file_line}")
                if err.chapter_title:
                    location_parts.append(f"chapter: {err.chapter_title}")

                location_str = f" ({', '.join(location_parts)})" if location_parts else ""

                lines.append(f"{i}. **Page {self._get_ia_leaf_number(err.candidate.scan_page)}{location_str}:** "
                           f"PG has \"{self._strip_html(err.candidate.pg_text)}\" vs scan \"{self._strip_html(err.candidate.scan_text)}\" "
                           f"(confidence: {err.confidence:.0%})")
            lines.append("")

        # Review needed section
        review_items = [
            e for e in report.errors
            if e.category != ErrorCategory.ALIGNMENT_ARTIFACT
            and not (e.verdict == Verdict.SCAN_CORRECT and e.confidence >= 0.8)
        ]

        if review_items:
            lines.append("## Review Needed")
            lines.append("")
            lines.append(
                "The following items require human judgment before deciding whether to report them:"
            )
            lines.append("")

            for i, err in enumerate(review_items, 1):
                # Determine prefix based on verdict
                if err.verdict == Verdict.UNABLE_TO_VERIFY or err.verdict == Verdict.AMBIGUOUS:
                    prefix = "[?] Ambiguous / unable to verify"
                elif err.verdict == Verdict.EDITION_VARIANT:
                    prefix = "[E] Edition variant"
                elif err.verdict == Verdict.INTENTIONAL_MODERNIZATION:
                    prefix = "[M] Intentional modernization"
                elif err.verdict == Verdict.SCAN_CORRECT and err.confidence < 0.8:
                    prefix = "[~] Low confidence error"
                else:
                    prefix = "[?] Other"

                location_parts = []
                if err.pg_file_line > 0:
                    location_parts.append(f"line {err.pg_file_line}")
                if err.chapter_title:
                    location_parts.append(f"chapter: {err.chapter_title}")
                if err.candidate.scan_page is not None:
                    location_parts.append(f"page {self._get_ia_leaf_number(err.candidate.scan_page)}")

                location_str = f" ({', '.join(location_parts)})" if location_parts else ""

                lines.append(f"{i}. **{prefix}**{location_str}")
                lines.append(f"   - PG text: \"{self._strip_html(err.candidate.pg_text)}\"")
                lines.append(f"   - Scan text: \"{self._strip_html(err.candidate.scan_text)}\"")
                lines.append(f"   - Confidence: {err.confidence:.0%}")
                if err.reasoning:
                    lines.append(f"   - Reasoning: {err.reasoning}")
                # Add scan page link if we have scan_id
                if self.scan_id and err.candidate.scan_page is not None:
                    leaf_num = self._get_ia_leaf_number(err.candidate.scan_page)
                    scan_url = f"https://archive.org/details/{self.scan_id}/page/n{leaf_num}/mode/1up"
                    lines.append(f"   - Scan page: {scan_url}")
                lines.append("")

        return "\n".join(lines)

    def generate_json(self, report: Report) -> str:
        """Generate a JSON report."""
        # Convert report to dict and add display_page and pg_sentence to each error
        report_dict = report.to_dict()
        for error_dict in report_dict.get("errors", []):
            # Add display_page field (1-indexed for human readability)
            error_dict["display_page"] = self._get_ia_leaf_number(error_dict.get("scan_page", 0))

            # Add pg_sentence field if we have body text
            if self.body_text:
                pg_text = error_dict.get("pg_text", "")
                # Find by string search (pg_offset may be inaccurate)
                pos = self.body_text.find(pg_text)
                if pos < 0 and '(absent in PG)' in pg_text:
                    pg_text = error_dict.get("scan_text", "")
                    pos = self.body_text.find(pg_text)
                if pos >= 0:
                    pg_sentence = self._extract_sentence(self.body_text, pos, len(pg_text))
                else:
                    pg_sentence = ""
                error_dict["pg_sentence"] = pg_sentence

        return json.dumps(report_dict, indent=2)

    def generate_review_needed(self, report: Report) -> str:
        """Generate a review file for items that need human decision.

        Includes items that are NOT submit-ready (i.e., anything that doesn't pass
        the errata_email threshold). Format with verdict-based prefixes.
        """
        lines: list[str] = []

        # Header
        title = report.metadata.title
        pg_id = report.metadata.pg_id

        lines.append(f"Quality Audit Review Items: {title} (PG #{pg_id})")
        lines.append("")

        # Filter to items that need review (not submit-ready)
        review_items = [
            e for e in report.errors
            if e.category != ErrorCategory.ALIGNMENT_ARTIFACT  # Skip pipeline noise
            and not (e.verdict == Verdict.SCAN_CORRECT and e.confidence >= 0.8 and e.pg_file_line > 0)
        ]

        # Count by verdict type
        unable_to_verify = [e for e in review_items if e.verdict == Verdict.UNABLE_TO_VERIFY]
        edition_variants = [e for e in review_items if e.verdict == Verdict.EDITION_VARIANT]
        intentional_modernizations = [e for e in review_items if e.verdict == Verdict.INTENTIONAL_MODERNIZATION]
        low_confidence_errors = [e for e in review_items if e.verdict == Verdict.SCAN_CORRECT and e.confidence < 0.8]
        ambiguous = [e for e in review_items if e.verdict == Verdict.AMBIGUOUS]

        # Summary count
        total_review = len(review_items)
        if total_review == 0:
            lines.append("0 items need review - errata_email.txt is ready to submit.")
            return "\n".join(lines)

        lines.append(f"{total_review} items need your decision before submission")
        lines.append("---")
        lines.append("")

        # Generate entries with verdict-based prefixes
        for err in review_items:
            # Determine prefix based on verdict
            if err.verdict == Verdict.UNABLE_TO_VERIFY or err.verdict == Verdict.AMBIGUOUS:
                prefix = "[?]"
                action_needed = "Check scan and decide if PG punctuation is wrong"
            elif err.verdict == Verdict.EDITION_VARIANT:
                prefix = "[E]"
                action_needed = "Classified as edition variant - likely NOT an error"
            elif err.verdict == Verdict.INTENTIONAL_MODERNIZATION:
                prefix = "[M]"
                action_needed = "Classified as intentional modernization - likely NOT an error"
            elif err.verdict == Verdict.PG_CORRECT:
                # PG correct items are intentional changes (not errors)
                prefix = "[M]"
                action_needed = "Classified as intentional change - likely NOT an error"
            elif err.verdict == Verdict.SCAN_CORRECT and err.confidence < 0.8:
                prefix = "[~]"
                action_needed = "Low confidence - verify before submitting"
            else:
                # Skip any other items
                continue

            # Build location info
            location_parts = []
            if err.pg_file_line > 0:
                location_parts.append(f"Line {err.pg_file_line}")
            if err.candidate.scan_page is not None:
                location_parts.append(f"Page {self._get_ia_leaf_number(err.candidate.scan_page)}")
            if err.chapter_title:
                location_parts.append(f"({err.chapter_title})")

            location = ", ".join(location_parts) if location_parts else "Unknown location"

            # Generate entry
            lines.append(f"{prefix} {location}")
            lines.append(f"    PG text: {self._strip_html(err.candidate.pg_text)}")
            lines.append(f"    Scan text: {self._strip_html(err.candidate.scan_text)}")
            lines.append(f"    Verdict: {err.verdict.value} ({err.confidence:.0%})")
            if err.reasoning:
                lines.append(f"    Reasoning: {err.reasoning}")
            lines.append(f"    Action needed: {action_needed}")

            # Add scan page link if we have scan_id
            if self.scan_id and err.candidate.scan_page is not None:
                leaf_num = self._get_ia_leaf_number(err.candidate.scan_page)
                scan_url = f"https://archive.org/details/{self.scan_id}/page/n{leaf_num}/mode/1up"
                lines.append(f"    Scan page: {scan_url}")

            lines.append("")

        return "\n".join(lines)

    def _is_punctuation_only(self, pg_text: str, scan_text: str) -> bool:
        """True if PG and scan text differ only in punctuation/whitespace."""
        pg_words = re.sub(r'[^\w]', '', pg_text)
        scan_words = re.sub(r'[^\w]', '', scan_text)
        if not pg_words or not scan_words:
            return True
        return pg_words == scan_words

    def _is_quote_start_fragment(self, pg_text: str, scan_text: str) -> bool:
        """True if one side starts with a quote and the other doesn't, with significant length difference."""
        s, p = scan_text.strip(), pg_text.strip()
        shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
        if not shorter:
            return False
        if shorter[0] not in '"\u201c\u201c\u00ab':
            return False
        if len(longer) - len(shorter) <= 10:
            return False
        return True

    # -- Stage-1 alignment artifact filters (text-based, no category needed) --

    def _is_absent_entry(self, pg_text: str, scan_text: str) -> bool:
        """True if one side has '(absent in PG)' or '(absent in scan)' marker."""
        return (
            '(absent in pg)' in pg_text.lower()
            or '(absent in scan)' in scan_text.lower()
        )

    def _is_cutoff_artifact(self, pg_text: str, scan_text: str) -> bool:
        """True if one side is a short suffix fragment of the other (≤3 chars)."""
        s, p = scan_text.strip(), pg_text.strip()
        if ' ' in s or ' ' in p:
            return False
        if abs(len(s) - len(p)) != 1:
            return False
        longer, shorter = (s, p) if len(s) > len(p) else (p, s)
        if not (longer.startswith(shorter) or longer.endswith(shorter)):
            return False
        return len(shorter) <= 3

    def _is_long_mismatch(self, pg_text: str, scan_text: str) -> bool:
        """True if either side is >40 chars (alignment spanned too far)."""
        return len(scan_text.strip()) > 40 or len(pg_text.strip()) > 40

    def _is_html_artifact(self, pg_text: str, scan_text: str) -> bool:
        """True if combined text contains HTML/CSS/markup artifacts."""
        combined = scan_text + pg_text
        return any(m in combined for m in [
            '<div', '<span', 'bbox=', '![](', '<img', '</div',
            'margin-', 'text-align', 'page-break', 'font-style', 'font-weight',
        ])

    def _is_all_caps_header(self, scan_text: str) -> bool:
        """True if scan text is an ALL CAPS header (chapter title)."""
        stripped = scan_text.strip()
        return stripped.isupper() and len(stripped) > 5

    def _is_suffix_fragment(self, pg_text: str, scan_text: str) -> bool:
        """True if one side is a tail fragment of the other (≤8 chars, diff ≤3)."""
        s, p = scan_text.strip(), pg_text.strip()
        shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
        if ' ' in shorter:
            return False
        if len(longer) - len(shorter) > 3:
            return False
        if not longer.endswith(shorter):
            return False
        if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
            return False
        if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
            return False
        return len(shorter) <= 8

    def _is_substring_fragment(self, pg_text: str, scan_text: str) -> bool:
        """True if one side is a substring of the other (alignment boundary artifact).

        When the PG and scan windows are offset by a few words, the diff checker
        produces diffs where one side is a word fragment that appears as a
        substring of the other side's full word. E.g. "oment" vs "the moment",
        "airs," vs "the stairs,". These are never real errata - they're just
        alignment window misalignment.
        """
        s, p = scan_text.strip(), pg_text.strip()
        shorter, longer = (s, p) if len(s) <= len(p) else (p, s)

        # Short side must be ≤10 chars and no spaces (single word fragment)
        if len(shorter) > 10:
            return False
        if ' ' in shorter:
            return False

        # Short side must be a substring of the long side
        if shorter.lower() not in longer.lower():
            return False

        # The longer side must be at least 3 chars more (otherwise it's a real diff)
        if len(longer) - len(shorter) < 3:
            return False

        return True

    def _is_quoted_fragment(self, pg_text: str, scan_text: str) -> bool:
        """True if short side starts with quote and length diff > 20."""
        s, p = scan_text.strip(), pg_text.strip()
        shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
        if not shorter:
            return False
        if shorter[0] not in '""\u00ab':
            return False
        return len(longer) - len(shorter) > 20

    def _is_alignment_artifact_text(self, pg_text: str, scan_text: str) -> bool:
        """True if the two texts share no meaningful words - likely a misalignment."""
        import re
        s = scan_text.strip()
        p = pg_text.strip()
        if not s or not p:
            return True
        # Short texts (single words) are likely real diffs, not artifacts
        if len(s.split()) <= 2 and len(p.split()) <= 2:
            return False
        s_words = set(re.findall(r'[a-z]+', s.lower()))
        p_words = set(re.findall(r'[a-z]+', p.lower()))
        if not s_words or not p_words:
            return True
        shared = s_words & p_words
        if not shared:
            return True
        meaningful_shared = {w for w in shared if len(w) > 3}
        return not meaningful_shared

    # -- MISSING CONTENT rendering helpers -----------------------------------

    def _looks_like_header(self, text: str) -> bool:
        """Heuristic: short line that looks like a running header (not prose).

        Used to strip per-page headers and "digit + header" combos when cleaning
        transcriptions for the MISSING CONTENT section.
        """
        if len(text) > 40:
            return False
        if not text.strip():
            return False
        # ALL CAPS title (e.g. "THE YOUNGER EDDA.", "PREFACE.")
        if text.isupper() and any(c.isalpha() for c in text):
            return True
        # Title Case with 2+ words (e.g. "The Younger Edda")
        words = [w for w in text.split() if w]
        if len(words) >= 2 and all(w[0].isupper() for w in words if w[0].isalpha()):
            return True
        return False

    def _detect_running_headers(self) -> set[str]:
        """Find lines that appear (trimmed) on 2+ scan pages.

        Such repetition is the signature of a running header (book title,
        chapter title, author) reprinted on every page.
        """
        line_pages: dict[str, set[int]] = {}
        for sp in self.scan_pages:
            pnum = getattr(sp, "page_num", None)
            if pnum is None:
                continue
            text = getattr(sp, "vision_text", "") or getattr(sp, "ocr_text", "") or ""
            if isinstance(sp, dict):
                text = sp.get("vision_text", "") or sp.get("ocr_text", "") or ""
            seen: set[str] = set()
            for line in text.split("\n"):
                s = line.strip()
                if not s or len(s) > 40:
                    continue
                seen.add(s)
            for s in seen:
                line_pages.setdefault(s, set()).add(pnum)
        return {line for line, pages in line_pages.items() if len(pages) >= 2}

    def _clean_page_transcription(self, text: str, running_headers: set[str]) -> str:
        """Strip page numbers and running headers from a scan transcription.

        Preserves internal blank lines (paragraph breaks) but trims leading and
        trailing whitespace.
        """
        if not text:
            return ""
        kept: list[str] = []
        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped:
                kept.append("")
                continue
            # Standalone page number
            if re.match(r"^\d{1,4}$", stripped):
                continue
            # Exact running-header match
            if stripped in running_headers:
                continue
            # "6 PREFACE." / "PREFACE. 6" style combos
            m = re.match(r"^(\d{1,4})\s+(.+)$", stripped)
            if m and (m.group(2) in running_headers or self._looks_like_header(m.group(2))):
                continue
            m = re.match(r"^(.+?)\s+(\d{1,4})$", stripped)
            if m and (m.group(1) in running_headers or self._looks_like_header(m.group(1))):
                continue
            kept.append(stripped)
        return "\n".join(kept).strip()

    def _get_full_page_text(self, page_num: int) -> str:
        """Return the full transcription (vision_text preferred) for a scan page."""
        for sp in self.scan_pages:
            pnum = getattr(sp, "page_num", None)
            if pnum is None and isinstance(sp, dict):
                pnum = sp.get("page_num")
            if pnum == page_num:
                vt = getattr(sp, "vision_text", "") or ""
                if isinstance(sp, dict):
                    vt = sp.get("vision_text", "") or ""
                ocr = getattr(sp, "ocr_text", "") or ""
                if isinstance(sp, dict):
                    ocr = sp.get("ocr_text", "") or ""
                return vt if vt else ocr
        return ""

    def _group_consecutive_structural_gaps(self, gaps: list) -> list[list]:
        """Group structural gaps whose page numbers are consecutive.

        Each returned group is a list of CoverageGap objects in page order. A
        gap is only grouped with the previous one if its page == previous.page + 1.
        """
        if not gaps:
            return []
        ordered = sorted(gaps, key=lambda g: g.page)
        groups: list[list] = [[ordered[0]]]
        for g in ordered[1:]:
            if g.page == groups[-1][-1].page + 1:
                groups[-1].append(g)
            else:
                groups.append([g])
        return groups

    def _pg_context_for_structural_group(self, group: list) -> str:
        """Build a `PG context: ...before [gap] after...` line for a gap group.

        Locates the neighboring alignments (largest scan_page < first page,
        smallest scan_page > last page) and pulls ~30 words of body text on
        each side. Returns "" if no context could be derived.
        """
        if not self.alignments or not self.body_text:
            return ""
        first_page = group[0].page
        last_page = group[-1].page

        before_offset: int | None = None
        after_offset: int | None = None
        for a in self.alignments:
            sp = a.scan_page if hasattr(a, "scan_page") else a.get("scan_page")
            if sp is None:
                continue
            if sp < first_page:
                end = a.pg_end if hasattr(a, "pg_end") else a["pg_end"]
                if before_offset is None or end > before_offset:
                    before_offset = end
            elif sp > last_page:
                start = a.pg_start if hasattr(a, "pg_start") else a["pg_start"]
                if after_offset is None or start < after_offset:
                    after_offset = start

        def _words_around(offset: int, before: bool) -> str:
            bt = self.body_text
            if before:
                seg_end = min(offset, len(bt))
                while seg_end > 0 and not bt[seg_end - 1].isspace():
                    seg_end -= 1
                words = bt[:seg_end].split()
                return " ".join(words[-30:]).replace("_", "")
            else:
                seg_start = min(offset, len(bt))
                while seg_start < len(bt) and not bt[seg_start].isspace():
                    seg_start += 1
                words = bt[seg_start:].split()
                return " ".join(words[:30]).replace("_", "")

        before_text = _words_around(before_offset, True) if before_offset is not None else ""
        after_text = _words_around(after_offset, False) if after_offset is not None else ""

        if before_text and after_text:
            return f"PG context: ...{before_text} [gap] {after_text}..."
        if after_text:
            return f"PG context: [gap] {after_text}..."
        if before_text:
            return f"PG context: ...{before_text} [gap]"
        return ""

    def _render_content_hole(self, lines: list[str], g, running_headers: set[str]) -> None:
        """Render a content_hole gap in the short format."""
        page = self._get_ia_leaf_number(g.page)
        conf = g.confidence.upper()
        if self.scan_id:
            url = f"https://archive.org/details/{self.scan_id}/page/n{page}/mode/1up"
            lines.append(f"Page {page} ({url}) - {g.word_count} words [{conf}]:")
        else:
            lines.append(f"Page {page} - {g.word_count} words [{conf}]:")
        if g.missing_words:
            text = self._clean_page_transcription(g.missing_words, running_headers)
            if not text:
                text = g.missing_words
            lines.append(f'Missing: "{text}"')
        if g.pg_context_before and g.pg_context_after:
            lines.append(f"PG context: ...{g.pg_context_before} [gap] {g.pg_context_after}...")
        lines.append("")

    def _render_structural_group(self, lines: list[str], group: list,
                                 running_headers: set[str]) -> bool:
        """Render a group of structural gaps (1+ consecutive pages).

        Returns True if the group was rendered as a multi-page long-format entry
        (so the caller knows whether to emit the "Source scan:" header).
        """
        first = group[0]
        conf = first.confidence.upper()
        first_leaf = self._get_ia_leaf_number(first.page)

        # Fetch + clean full text for each page in the group
        text_parts: list[str] = []
        total_words = 0
        for g in group:
            raw = self._get_full_page_text(g.page)
            if not raw:
                raw = g.scan_text_preview
            cleaned = self._clean_page_transcription(raw, running_headers)
            if cleaned:
                text_parts.append(cleaned)
                total_words += len(cleaned.split())
        if not text_parts:
            for g in group:
                text_parts.append(g.scan_text_preview.strip())
                total_words += g.word_count

        is_multi = len(group) > 1
        is_long = is_multi or total_words >= 100

        # Header line (URL always points to the first page)
        if self.scan_id:
            url = f"https://archive.org/details/{self.scan_id}/page/n{first_leaf}/mode/1up"
            if is_multi:
                last_leaf = self._get_ia_leaf_number(group[-1].page)
                wc_label = f"~{total_words} words"
                label = f"Pages {first_leaf}-{last_leaf} ({url})"
            else:
                wc_label = f"{total_words} words" if not is_long else f"~{total_words} words"
                label = f"Page {first_leaf} ({url})"
        else:
            if is_multi:
                last_leaf = self._get_ia_leaf_number(group[-1].page)
                wc_label = f"~{total_words} words"
                label = f"Pages {first_leaf}-{last_leaf}"
            else:
                wc_label = f"{total_words} words" if not is_long else f"~{total_words} words"
                label = f"Page {first_leaf}"
        lines.append(f"{label} - {wc_label} [{conf}]:")

        body = "\n\n".join(text_parts) if is_multi else text_parts[0]
        if is_long:
            lines.append(body)
            lines.append("")
            context = self._pg_context_for_structural_group(group)
            if context:
                lines.append(context)
            lines.append("")
        else:
            lines.append(f'Missing: "{body}"')
            context = self._pg_context_for_structural_group(group)
            if context:
                lines.append(context)
            lines.append("")
        return is_multi

    def generate_errata_email(self, report: Report) -> str:
        """Generate errata report in Project Gutenberg's preferred format.

        Follows PG errata guidance (https://www.gutenberg.org/help/errata.html):
        - Header with title, author, eBook number, file name
        - Scan verification source when available
        - Each error: context line first, then 'erroneous ==> corrected'
        - Enough context to locate the error uniquely
        """
        title = report.metadata.title
        author = report.metadata.author
        pg_id = report.metadata.pg_id

        # Extract filename from path if available
        pg_filename = ""
        if self.pg_file_path:
            pg_filename = self.pg_file_path.name
        else:
            pg_filename = f"{pg_id}.txt"

        # Filter: high-confidence scan_correct errors with real content categories
        # (not formatting/ambiguous/alignment artifacts).
        SUBMIT_CATEGORIES = {
            ErrorCategory.OCR_SCANNO,
            ErrorCategory.WRONG_WORD,
            ErrorCategory.MISSING_WORD,
            ErrorCategory.EXTRA_WORD,
            ErrorCategory.MISSING_CONTENT,
            ErrorCategory.MISSING_PUNCTUATION,
            ErrorCategory.ENCODING_ERROR,
        }
        skip_categories = {
            ErrorCategory.EDITION_VARIANT,
            ErrorCategory.MODERNIZATION,
            ErrorCategory.INTENTIONAL_CHANGE,
            ErrorCategory.ALIGNMENT_ARTIFACT,
            ErrorCategory.FORMATTING_ERROR,
            ErrorCategory.AMBIGUOUS,
            ErrorCategory.PUNCTUATION_DIFF,
        }
        submit_ready = [
            e for e in report.errors
            if e.category not in skip_categories
            and e.confidence >= 0.4  # skip very low confidence (likely artifacts)
            and not self._is_absent_entry(e.candidate.pg_text, e.candidate.scan_text)
            and not self._is_cutoff_artifact(e.candidate.pg_text, e.candidate.scan_text)
            and not self._is_long_mismatch(e.candidate.pg_text, e.candidate.scan_text)
            and not self._is_html_artifact(e.candidate.pg_text, e.candidate.scan_text)
            and not self._is_all_caps_header(e.candidate.scan_text)
            and not self._is_suffix_fragment(e.candidate.pg_text, e.candidate.scan_text)
            and not self._is_quoted_fragment(e.candidate.pg_text, e.candidate.scan_text)
            and not self._is_substring_fragment(e.candidate.pg_text, e.candidate.scan_text)
            and 'FOOTNOTE' not in e.candidate.pg_text.upper()
            and 'FOOTNOTE' not in e.candidate.scan_text.upper()
        ]

        # Deduplicate by offset proximity (within 50 chars)
        sorted_by_offset = sorted(submit_ready, key=lambda e: (e.candidate.pg_offset, -e.confidence))
        seen_offsets: set[int] = set()
        deduplicated: list[Error] = []

        for err in sorted_by_offset:
            # Dedup: skip exact duplicate (same offset OR same text pair within 50 chars)
            is_dup = False
            if err.candidate.pg_offset in seen_offsets:
                is_dup = True
            else:
                # Check if a nearby entry has the same text pair
                for seen in seen_offsets:
                    if 0 < abs(err.candidate.pg_offset - seen) < 50:
                        # Only dedup if it's the same text (alignment variant)
                        for d in deduplicated:
                            if d.candidate.pg_offset == seen:
                                if (d.candidate.pg_text == err.candidate.pg_text
                                    and d.candidate.scan_text == err.candidate.scan_text):
                                    is_dup = True
                                break
                        if is_dup:
                            break
            if is_dup:
                continue
            if self._is_punctuation_only(err.candidate.pg_text, err.candidate.scan_text):
                continue
            if self._is_quote_start_fragment(err.candidate.pg_text, err.candidate.scan_text):
                continue
            deduplicated.append(err)
            seen_offsets.add(err.candidate.pg_offset)

        # Build email in PG's preferred format
        lines: list[str] = []

        lines.append(f"In {title}, by {author}, [EBook #{pg_id}],")
        lines.append(f"File: {pg_filename},")

        # Always include IA source link (mandatory per PG errata guidance)
        if self.scan_id:
            lines.append(f"I verified the following changes against the Internet Archive scan:")
            lines.append(f"https://archive.org/details/{self.scan_id}")
            lines.append(f"NOTE: Page numbers are 'of the scan' not 'of the book.'")
        else:
            lines.append("Source scans: Internet Archive")

        lines.append("")

        # Dictionary validation for global replacements
        dict_checker = DictionaryChecker(pg_text=self.body_text or "")
        gr_validated = []
        gr_flagged = []
        for gr in self.global_replacements:
            if dict_checker.validate_replacement(gr.scan_text, gr.pg_text):
                gr_validated.append(gr)
            else:
                gr_flagged.append(gr)

        # Helper to strip punctuation from global replacement display
        def _clean_gr(text: str) -> str:
            return re.sub(r"^[^\w']+", "", re.sub(r"[^\w']+$", "", text))

        # Deduplicate global replacements after punctuation stripping.
        # The detector may create separate entries for 'word' and 'word.'
        # Combine occurrences and keep the longest pg_text as representative.
        def _dedup_grs(grs):
            seen = {}
            for gr in grs:
                key = (_clean_gr(gr.pg_text).lower(), _clean_gr(gr.scan_text).lower())
                if key in seen:
                    seen[key].occurrences_in_pg = max(seen[key].occurrences_in_pg, gr.occurrences_in_pg)
                    seen[key].caught_by_errata = max(seen[key].caught_by_errata, gr.caught_by_errata)
                else:
                    seen[key] = gr
            return list(seen.values())

        gr_validated = _dedup_grs(gr_validated)
        gr_flagged = _dedup_grs(gr_flagged)

        # Global replacements — validated (dictionary-confirmed)
        lines.append("Global Replacements {")
        if gr_validated:
            for gr in sorted(gr_validated, key=lambda g: _clean_gr(g.pg_text).lower()):
                lines.append(f"{_clean_gr(gr.pg_text)} ==> {_clean_gr(gr.scan_text)}")
            total_v = sum(gr.occurrences_in_pg for gr in gr_validated)
            lines.append(f"({total_v} total occurrences in text)")
        else:
            lines.append("(none found)")
        lines.append("}")
        lines.append("")

        # Global replacements — flagged (scan word not in dictionary)
        if gr_flagged:
            lines.append("Global Replacements (flagged — scan word not in dictionary) {")
            for gr in sorted(gr_flagged, key=lambda g: _clean_gr(g.pg_text).lower()):
                lines.append(f"{_clean_gr(gr.pg_text)} ==> {_clean_gr(gr.scan_text)}")
            total_f = sum(gr.occurrences_in_pg for gr in gr_flagged)
            lines.append(f"({total_f} total occurrences in text)")
            lines.append("}")
            lines.append("")

        # Separate individual errors into those that are instances of
        # global replacements (shown grouped) vs. unique errors
        global_instances: list = []
        unique_errors: list = []
        if self.global_replacements:
            from gerrata.checker.global_replacements import GlobalReplacementDetector
            detector = GlobalReplacementDetector()
            for err in deduplicated:
                if detector.is_global_instance(
                    {"candidate": {"pg_text": err.candidate.pg_text, "scan_text": err.candidate.scan_text}},
                    self.global_replacements,
                ):
                    global_instances.append(err)
                else:
                    unique_errors.append(err)
        else:
            unique_errors = list(deduplicated)

        # Group global replacement instances by their pg_word→scan_word pair
        # so the reviewer can see all caught examples together and decide
        if global_instances:
            # Build a lookup: (pg_text, scan_text) → list of errors
            from gerrata.checker.global_replacements import normalize_possessive
            grouped: dict[tuple[str, str], list] = {}
            for err in global_instances:
                pg_word = err.candidate.pg_text.strip()
                scan_word = err.candidate.scan_text.strip()
                # Normalize to match against global replacement keys
                pg_norm = normalize_possessive(pg_word)
                scan_norm = normalize_possessive(scan_word)
                # Find matching global replacement entry
                for gr in self.global_replacements:
                    if normalize_possessive(gr.pg_text) == pg_norm and normalize_possessive(gr.scan_text) == scan_norm:
                        key = (gr.pg_text, gr.scan_text)
                        grouped.setdefault(key, []).append(err)
                        break
                else:
                    # Didn't match a specific GR entry — use raw values
                    key = (pg_word, scan_word)
                    grouped.setdefault(key, []).append(err)

            # Split grouped instances into validated and flagged
            grouped_validated: dict[tuple[str, str], list] = {}
            grouped_flagged: dict[tuple[str, str], list] = {}
            for key, errs in grouped.items():
                if dict_checker.validate_replacement(key[1], key[0]):
                    grouped_validated[key] = errs
                else:
                    grouped_flagged[key] = errs

            def _emit_grouped(label: str, grouped_items: dict[tuple[str, str], list]):
                if not grouped_items:
                    return
                lines.append(label)
                lines.append("")
                for (pg_word, scan_word), errs in sorted(grouped_items.items(), key=lambda x: x[0][0].lower()):
                    # Find occurrence count from global replacements
                    occ = ""
                    for gr in self.global_replacements:
                        if normalize_possessive(gr.pg_text) == normalize_possessive(pg_word) and normalize_possessive(gr.scan_text) == normalize_possessive(scan_word):
                            occ = f" ({gr.occurrences_in_pg}x in PG text, {len(errs)} caught)"
                            break
                    lines.append(f"  {pg_word} → {scan_word}{occ}")
                    lines.append("")
                    for err in errs:
                        pg_t = re.sub(r"<[^>]+>", "", err.candidate.pg_text.strip())
                        scan_t = re.sub(r"<[^>]+>", "", err.candidate.scan_text.strip())
                        page = self._get_ia_leaf_number(err.candidate.scan_page)
                        if self.scan_id:
                            leaf_num = self._get_ia_leaf_number(err.candidate.scan_page)
                            scan_url = f"https://archive.org/details/{self.scan_id}/page/n{leaf_num}/mode/1up"
                            lines.append(f"  Page {page} ({scan_url}):")
                        else:
                            lines.append(f"  Page {page}:")
                        # PG file line context
                        if err.pg_file_line > 0:
                            line_context = self.get_line_context(err.pg_file_line)
                            if line_context:
                                for lc_line in line_context.split("\n"):
                                    lines.append(f"    {lc_line}")
                        pg_trimmed, scan_trimmed = self._trim_shared_edges(pg_t, scan_t)
                        lines.append(f"    {pg_trimmed} ==> {scan_trimmed}")
                        lines.append("")

            _emit_grouped(
                "GLOBAL REPLACEMENT INSTANCES (individual pages for visual confirmation)",
                grouped_validated,
            )
            _emit_grouped(
                "GLOBAL REPLACEMENT INSTANCES — FLAGGED (scan word not in dictionary)",
                grouped_flagged,
            )
            lines.append("---")
            lines.append("")

        if not unique_errors and not global_instances:
            lines.append("I found no errors requiring correction.")
            return "\n".join(lines)

        if not unique_errors:
            lines.append("(No additional unique errors beyond global replacements above.)")
            return "\n".join(lines)

        # Split unique errors into dictionary-validated and flagged
        unique_validated = []
        unique_flagged = []
        for err in unique_errors:
            scan_clean = re.sub(r"<[^>]+>", "", err.candidate.scan_text.strip())
            pg_clean = re.sub(r"<[^>]+>", "", err.candidate.pg_text.strip())
            if dict_checker.validate_replacement(scan_clean, pg_clean):
                unique_validated.append(err)
            else:
                unique_flagged.append(err)

        def _emit_singular(errs: list):
            for err in errs:
                pg_text = re.sub(r"<[^>]+>", "", err.candidate.pg_text.strip())
                scan_text = re.sub(r"<[^>]+>", "", err.candidate.scan_text.strip())
                page = self._get_ia_leaf_number(err.candidate.scan_page)  # 1-indexed

                # Get context sentence containing the error
                context = ""
                if self.body_text:
                    search_text = pg_text
                    pos = self.body_text.find(search_text)
                    if pos < 0 and '(absent in PG)' in search_text:
                        search_text = scan_text
                        pos = self.body_text.find(search_text)
                    if pos >= 0:
                        context = self._extract_sentence(
                            self.body_text, pos, len(search_text)
                        )

                # PG format: page reference, context line, then fix line
                if self.scan_id:
                    leaf_num = self._get_ia_leaf_number(err.candidate.scan_page)
                    scan_url = f"https://archive.org/details/{self.scan_id}/page/n{leaf_num}/mode/1up"
                    lines.append(f"Page {page} ({scan_url}):")
                else:
                    lines.append(f"Page {page}:")

                # Add PG file line context
                if err.pg_file_line > 0:
                    line_context = self.get_line_context(err.pg_file_line)
                    if line_context:
                        lines.append(line_context)

                if context:
                    lines.append(context)
                pg_trimmed, scan_trimmed = self._trim_shared_edges(pg_text, scan_text)
                lines.append(f"{pg_trimmed} ==> {scan_trimmed}")

                lines.append("")

        lines.append("")
        _emit_singular(unique_validated)

        if unique_flagged:
            lines.append("---")
            lines.append("")
            lines.append("FLAGGED ERRATA (scan word not in dictionary — review before submitting)")
            lines.append("")
            _emit_singular(unique_flagged)

        # Edition variants (informational, not for submission)
        edition_variants = [
            e for e in report.errors
            if e.category == ErrorCategory.EDITION_VARIANT
            and not self._is_alignment_artifact_text(e.candidate.pg_text, e.candidate.scan_text)
        ]

        if edition_variants:
            lines.append("---")
            lines.append("")
            lines.append("EDITION VARIANTS (not for submission - informational only)")
            lines.append("")
            lines.append(
                "The following differences appear to be between editions "
                "rather than errors in either version."
            )
            lines.append("")
            for err in edition_variants:
                pg_text = self._strip_html(err.candidate.pg_text).strip()
                scan_text = self._strip_html(err.candidate.scan_text).strip()
                page = self._get_ia_leaf_number(err.candidate.scan_page)

                if self.scan_id:
                    leaf_num = self._get_ia_leaf_number(err.candidate.scan_page)
                    scan_url = f"https://archive.org/details/{self.scan_id}/page/n{leaf_num}/mode/1up"
                    lines.append(f"Page {page} ({scan_url}):")
                else:
                    lines.append(f"Page {page}:")

                lines.append(f"PG:    {pg_text}")
                lines.append(f"Scan:  {scan_text}")
                lines.append("")

        # Punctuation differences (informational, for downstream use)
        punctuation_diffs = [
            e for e in report.errors
            if e.category == ErrorCategory.PUNCTUATION_DIFF
        ]

        if punctuation_diffs:
            lines.append("---")
            lines.append("")
            lines.append("PUNCTUATION DIFFERENCES")
            lines.append("")

            # Group by type for readability
            quote_diffs = []
            spacing_diffs = []
            other_punct = []

            _all_quote_chars = set('"\'\u201c\u201d\u2018\u2019`')
            # backtick ` is PG's encoding of opening single quote

            # Latin abbreviations where spaced dots are wrong (PG style = compact)
            # PG correctly uses: i.e.  e.g.  s.a.  cf.  ca.  etc.  vs.  et al.
            # Scan incorrectly spaces: i. e.  e. g.  s. a.
            import re as _re
            _latin_pattern = _re.compile(
                r'\b([ievqsf])\.\s+([a-z])\.',  # "i. e." "e. g." "s. a." etc.
                _re.IGNORECASE
            )

            def _is_concat_artifact(pg: str, scan: str) -> bool:
                """Scan has two words smashed together that PG correctly separates."""
                pg_clean = pg.strip().rstrip(',.;:!?)')
                scan_clean = scan.strip().rstrip(',.;:!?)')
                pg_words = pg_clean.split()
                # PG has 2+ words, scan has them concatenated into one
                if len(pg_words) >= 2:
                    joined = ''.join(pg_words)
                    return joined == scan_clean
                return False

            def _is_latin_abbrev_noise(pg: str, scan: str) -> bool:
                """Spaced Latin abbreviation noise: PG has compact form, scan has spaced form.

                Only filter when PG is correct (i.e., e.g., s.a.) and scan is wrong
                (i. e., e. g., s. a.). If PG has the spaced form, the entry is real.
                """
                pg_spaced = bool(_latin_pattern.search(pg))
                scan_spaced = bool(_latin_pattern.search(scan))
                # Filter only when PG is compact (not spaced) and scan is spaced
                return scan_spaced and not pg_spaced

            for err in punctuation_diffs:
                pg_t = err.candidate.pg_text
                scan_t = err.candidate.scan_text
                pg_has_quote = bool(set(pg_t) & _all_quote_chars)
                scan_has_quote = bool(set(scan_t) & _all_quote_chars)

                # Skip em-dash diffs (-- vs —) entirely
                is_em_dash = ('--' in pg_t and '\u2014' in scan_t) or \
                             ('\u2014' in pg_t and '--' in scan_t)
                if is_em_dash:
                    continue

                # Skip OCR concatenation artifacts (scan has words smashed together)
                if _is_concat_artifact(pg_t, scan_t):
                    continue

                # Skip Latin abbreviation spacing noise (i. e. / e. g. / s. a.)
                if _is_latin_abbrev_noise(pg_t, scan_t):
                    continue

                # Quotes: only include one-sided (present on one side, absent on other)
                if pg_has_quote or scan_has_quote:
                    if pg_has_quote != scan_has_quote:
                        quote_diffs.append(err)
                    # else: both have quotes (style swap) — skip
                elif pg_t.replace(' ', '').replace(';', '').replace(':', '').replace(',', '').replace('.', '') == \
                     scan_t.replace(' ', '').replace(';', '').replace(':', '').replace(',', '').replace('.', ''):
                    spacing_diffs.append(err)
                else:
                    other_punct.append(err)

            if quote_diffs:
                lines.append(f"Quotation marks ({len(quote_diffs)}):")
                lines.append("")
                for err in quote_diffs:
                    pg_t = re.sub(r'<[^>]+>', '', err.candidate.pg_text.strip())
                    scan_t = re.sub(r'<[^>]+>', '', err.candidate.scan_text.strip())
                    page = self._get_ia_leaf_number(err.candidate.scan_page)

                    # Get context sentence
                    context = ""
                    if self.body_text:
                        pos = self.body_text.find(pg_t)
                        if pos >= 0:
                            context = self._extract_sentence(self.body_text, pos, len(pg_t))

                    if self.scan_id:
                        leaf_num = self._get_ia_leaf_number(err.candidate.scan_page)
                        scan_url = f"https://archive.org/details/{self.scan_id}/page/n{leaf_num}/mode/1up"
                        lines.append(f"Page {page} ({scan_url}):")
                    else:
                        lines.append(f"Page {page}:")
                    if err.pg_file_line > 0:
                        line_context = self.get_line_context(err.pg_file_line)
                        if line_context:
                            lines.append(line_context)
                    if context:
                        lines.append(context)
                    pg_trimmed, scan_trimmed = self._trim_shared_edges(pg_t, scan_t)
                    lines.append(f"{pg_trimmed} ==> {scan_trimmed}")
                    lines.append("")

            if spacing_diffs:
                lines.append(f"Punctuation spacing ({len(spacing_diffs)}):")
                lines.append("")
                for err in spacing_diffs:
                    pg_t = re.sub(r'<[^>]+>', '', err.candidate.pg_text.strip())
                    scan_t = re.sub(r'<[^>]+>', '', err.candidate.scan_text.strip())
                    page = self._get_ia_leaf_number(err.candidate.scan_page)

                    # Get context sentence
                    context = ""
                    if self.body_text:
                        pos = self.body_text.find(pg_t)
                        if pos >= 0:
                            context = self._extract_sentence(self.body_text, pos, len(pg_t))

                    if self.scan_id:
                        leaf_num = self._get_ia_leaf_number(err.candidate.scan_page)
                        scan_url = f"https://archive.org/details/{self.scan_id}/page/n{leaf_num}/mode/1up"
                        lines.append(f"Page {page} ({scan_url}):")
                    else:
                        lines.append(f"Page {page}:")
                    if err.pg_file_line > 0:
                        line_context = self.get_line_context(err.pg_file_line)
                        if line_context:
                            lines.append(line_context)
                    if context:
                        lines.append(context)
                    pg_trimmed, scan_trimmed = self._trim_shared_edges(pg_t, scan_t)
                    lines.append(f"{pg_trimmed} ==> {scan_trimmed}")
                    lines.append("")

            if other_punct:
                lines.append(f"Other punctuation ({len(other_punct)}):")
                lines.append("")
                for err in other_punct:
                    pg_t = re.sub(r'<[^>]+>', '', err.candidate.pg_text.strip())
                    scan_t = re.sub(r'<[^>]+>', '', err.candidate.scan_text.strip())
                    page = self._get_ia_leaf_number(err.candidate.scan_page)

                    # Get context sentence
                    context = ""
                    if self.body_text:
                        pos = self.body_text.find(pg_t)
                        if pos >= 0:
                            context = self._extract_sentence(self.body_text, pos, len(pg_t))

                    if self.scan_id:
                        leaf_num = self._get_ia_leaf_number(err.candidate.scan_page)
                        scan_url = f"https://archive.org/details/{self.scan_id}/page/n{leaf_num}/mode/1up"
                        lines.append(f"Page {page} ({scan_url}):")
                    else:
                        lines.append(f"Page {page}:")
                    if err.pg_file_line > 0:
                        line_context = self.get_line_context(err.pg_file_line)
                        if line_context:
                            lines.append(line_context)
                    if context:
                        lines.append(context)
                    pg_trimmed, scan_trimmed = self._trim_shared_edges(pg_t, scan_t)
                    lines.append(f"{pg_trimmed} ==> {scan_trimmed}")
                    lines.append("")

        # Coverage gap analysis - missing content detection
        # Note: gaps are already computed in the CLI and included as
        # CandidateError(MISSING_CONTENT) in the report. The section below
        # uses the raw CoverageGap data for richer display.
        if self.alignments and self.scan_pages and self.body_text:
            try:
                from gerrata.checker.gap_detector import detect_scan_gaps, filter_for_report
                all_gaps = detect_scan_gaps(
                    pg_text=self.body_text,
                    alignments=self.alignments,
                    scan_pages=self.scan_pages,
                    pg_full_text=self.pg_full_text,
                )
                report_gaps = filter_for_report(all_gaps)
                # Only show high-confidence gaps in the report
                report_gaps = [g for g in report_gaps if g.confidence == "high"]
                if report_gaps:
                    # Split content holes (short inline gaps) from structural
                    # gaps (whole missing pages / large chunks). Each maps to
                    # the short vs. long MISSING CONTENT formats.
                    content_holes = [g for g in report_gaps if g.strategy == "content_hole"]
                    structural_gaps = [g for g in report_gaps if g.strategy != "content_hole"]

                    structural_groups = self._group_consecutive_structural_gaps(structural_gaps)
                    has_multi_page = any(len(g) > 1 for g in structural_groups)

                    lines.append("---")
                    lines.append("")
                    lines.append("MISSING CONTENT")
                    lines.append("")
                    if has_multi_page and self.scan_id:
                        lines.append(f"Source scan: https://archive.org/details/{self.scan_id}")
                        lines.append("")

                    running_headers = self._detect_running_headers()

                    # Content holes first (short format), sorted by page
                    for g in sorted(content_holes, key=lambda x: x.page):
                        self._render_content_hole(lines, g, running_headers)

                    # Structural gap groups, sorted by first page
                    for group in structural_groups:
                        self._render_structural_group(lines, group, running_headers)
            except Exception:
                pass  # Don't let gap detection failure break report generation

        # Footer
        lines.append("These errata were found using Gerrata (https://github.com/travis42/gerrata) and refined by a human reviewer (me).")
        lines.append("Please reach out if you would like to discuss Gerrata or this report.")

        return "\n".join(lines)

    def save_reports(
        self,
        report: Report,
        output_dir: Path | str,
        base_name: str = "",
        suffix: str = "",
    ) -> tuple[Path, Path]:
        """Save JSON and errata email reports.

        Args:
            report: The report to save
            output_dir: Directory to save reports in
            base_name: Base filename (without extension)
            suffix: Optional suffix to add before file extension (e.g., "-raw")

        Returns:
            Tuple of (json_path, email_path).
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if not base_name:
            title_slug = _slugify(report.metadata.title)
            base_name = f"gutenberg{report.metadata.pg_id}-{title_slug}"

        json_path = _next_version(output_dir / f"{base_name}_errata{suffix}.json")
        email_path = _next_version(output_dir / f"{base_name}_errata_email{suffix}.txt")

        # Enrich errors with line numbers and chapter context
        self.enrich_errors_with_context(report)

        # Generate and save JSON report
        json_content = self.generate_json(report)
        json_path.write_text(json_content, encoding="utf-8")

        # Generate and save errata email
        email_content = self.generate_errata_email(report)
        email_path.write_text(email_content, encoding="utf-8")

        return json_path, email_path

    def print_summary(self, report: Report) -> None:
        """Print a summary to the console using rich."""
        self.console.print()
        self.console.print(
            f"[bold]Quality Audit: {report.metadata.title}[/bold] "
            f"(PG #{report.metadata.pg_id})"
        )
        self.console.print(f"[dim]by {report.metadata.author}[/dim]")
        self.console.print()

        # Summary table
        table = Table(title="Summary")
        table.add_column("Metric", style="bold")
        table.add_column("Value")
        table.add_row("Pages checked", f"{report.pages_checked} / {report.total_pages}")
        if report.skipped_pages > 0:
            table.add_row("Pages skipped", f"{report.skipped_pages} (illustrations/blanks)")
        table.add_row("Pages aligned", f"{len(report.alignments)}")
        table.add_row("Per-page confidence", f"{report.avg_page_confidence:.2f}")
        table.add_row("Text coverage", f"{report.alignment_confidence:.0%}")
        # Only show edition match confidence if it was actually computed (> 0)
        if report.edition_match_confidence > 0.0:
            table.add_row("Edition match", f"{report.edition_match_confidence:.0%}")
        else:
            table.add_row("Edition match", "N/A (no LLM comparison)")
        if report.edition_notes:
            table.add_row("Edition notes", report.edition_notes[:80])
        self.console.print(table)

        # Error table (filter out alignment artifacts)
        display_errors = [e for e in report.errors if e.category != ErrorCategory.ALIGNMENT_ARTIFACT]
        if display_errors:
            error_table = Table(title="Errors Found")
            error_table.add_column("#", style="dim", width=4)
            error_table.add_column("Severity", width=10)
            error_table.add_column("Confidence", width=10)
            error_table.add_column("Page", width=6)
            error_table.add_column("PG Text", max_width=30, no_wrap=True)
            error_table.add_column("Scan Text", max_width=30, no_wrap=True)
            error_table.add_column("Category", width=16)

            for i, err in enumerate(display_errors, 1):
                severity_style = {
                    ErrorSeverity.CRITICAL: "bold red",
                    ErrorSeverity.HIGH: "red",
                    ErrorSeverity.MEDIUM: "yellow",
                    ErrorSeverity.LOW: "dim",
                    ErrorSeverity.INFO: "dim",
                }.get(err.severity, "")

                error_table.add_row(
                    str(i),
                    f"[{severity_style}]{err.severity.value}[/{severity_style}]",
                    f"{err.confidence:.0%}",
                    str(err.candidate.scan_page),
                    self._strip_html(err.candidate.pg_text)[:30],
                    self._strip_html(err.candidate.scan_text)[:30],
                    err.category.value,
                )

            self.console.print()
            self.console.print(error_table)
        else:
            self.console.print()
            self.console.print("[green]No errors found![/green]")

        self.console.print()
