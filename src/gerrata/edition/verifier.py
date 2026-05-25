"""Main EditionVerifier orchestrating both verification approaches.

Provides a unified interface for edition verification:
  - Approach A: Metadata matching (fast, deterministic)
  - Approach B: Line-break fingerprinting (slower, structural)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

from gerrata.edition.metadata import (
    MetadataMatcher,
    PGEditionClues,
    IAEditionClues,
    MetadataMatchResult,
)
from gerrata.edition.linebreaks import (
    LineBreakFingerprinter,
    LineBreakDetection,
    LineBreakMatchResult,
)
from gerrata.fetcher.pg import PGFetcher

logger = logging.getLogger(__name__)


class MatchResult(str, Enum):
    """Overall edition verification result."""

    MATCH = "match"
    MISMATCH = "mismatch"
    UNABLE_TO_DETERMINE = "unable_to_determine"


@dataclass
class EditionVerificationResult:
    """Complete result of edition verification."""

    pg_id: int
    scan_id: str
    pg_metadata: dict = field(default_factory=dict)
    scan_metadata: dict = field(default_factory=dict)
    approach_a: dict = field(default_factory=dict)
    approach_b: dict = field(default_factory=dict)
    overall: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, output_dir: str | Path, pg_id: int) -> Path:
        """Save verification result as JSON."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        filepath = output_dir / f"{pg_id}_edition_verification.json"
        filepath.write_text(self.to_json(), encoding="utf-8")
        logger.info(f"Saved verification result to {filepath}")
        return filepath


class EditionVerifier:
    """Orchestrate edition verification using both approaches.

    Usage:
        verifier = EditionVerifier()
        result = await verifier.verify(pg_id=2021, scan_id="nostromotaleofse00conruoft")
        print(result.overall)
    """

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        verbose: bool = False,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else Path("./cache")
        self.verbose = verbose
        self.metadata_matcher = MetadataMatcher(cache_dir=str(self.cache_dir))
        self.linebreak_fingerprinter = LineBreakFingerprinter()
        self.pg_fetcher = PGFetcher(cache_dir=self.cache_dir)

    async def verify(
        self,
        pg_id: int,
        scan_id: str,
        approaches: str = "both",
        pg_file: str | None = None,
        vision_url: str | None = None,
        vision_key: str | None = None,
        vision_model: str | None = None,
    ) -> EditionVerificationResult:
        """Run edition verification.

        Args:
            pg_id: Project Gutenberg ebook ID.
            scan_id: Internet Archive identifier.
            approaches: Which approaches to use: "metadata", "linebreaks", or "both".
            pg_file: Optional local path to PG text file.
            vision_url: Vision model API URL (for future Strategy 2).
            vision_key: Vision model API key.
            vision_model: Vision model name.

        Returns:
            EditionVerificationResult with complete analysis.
        """
        result = EditionVerificationResult(pg_id=pg_id, scan_id=scan_id)

        # Fetch PG text
        if pg_file:
            pg_path = Path(pg_file)
        else:
            try:
                pg_path = await self.pg_fetcher.download(pg_id, dest=self.cache_dir)
            except Exception as e:
                logger.error(f"Failed to download PG #{pg_id}: {e}")
                result.overall = {
                    "result": "unable_to_determine",
                    "confidence": 0.0,
                    "recommendation": f"Failed to fetch PG text: {e}",
                }
                return result

        # Parse PG text
        parsed = self.pg_fetcher.parse_file(pg_path)
        raw_text = pg_path.read_text(encoding="utf-8", errors="replace")

        # For line-break detection, we need the original PG text file that
        # preserves line breaks (typically the -0.txt version). The default
        # download may be a reflowed UTF-8 version without CRLF.
        # Try to download the -0.txt version separately.
        raw_original_text = raw_text
        has_crlf = "\r\n" in raw_text[:50000]
        if not has_crlf and not pg_file:
            try:
                import httpx
                original_url = f"https://www.gutenberg.org/files/{pg_id}/{pg_id}-0.txt"
                original_path = self.cache_dir / f"{pg_id}-0.txt"
                if not original_path.exists():
                    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                        resp = await client.get(original_url)
                        if resp.status_code == 200:
                            original_path.write_bytes(resp.content)
                            logger.info(f"Downloaded original PG text: {original_path}")
                            raw_original_text = resp.content.decode("utf-8", errors="replace")
                        else:
                            logger.debug(f"Original PG text not available: {original_url} (status {resp.status_code})")
                else:
                    raw_original_text = original_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.debug(f"Could not download original PG text: {e}")

        # Extract PG metadata for the result
        result.pg_metadata = {
            "title": parsed.metadata.title,
            "author": parsed.metadata.author,
            "producer": parsed.metadata.producer,
            "source_edition": parsed.metadata.source_edition,
            "release_date": parsed.metadata.release_date,
        }

        # Extract header text for approach A
        from gerrata.fetcher.pg import START_MARKER
        start_match = START_MARKER.search(raw_text)
        header_text = raw_text[:start_match.start()] if start_match else raw_text[:2000]

        # Fetch IA metadata
        ia_clues = await self.metadata_matcher.fetch_ia_metadata(scan_id)
        result.scan_metadata = {
            "title": ia_clues.title,
            "publisher": ia_clues.publisher,
            "date": ia_clues.date,
            "creator": ia_clues.creator,
        }

        # Run Approach A: Metadata matching
        if approaches in ("metadata", "both"):
            result.approach_a = await self._run_approach_a(
                header_text, parsed.metadata.title, ia_clues, scan_id
            )

        # Run Approach B: Line-break fingerprinting
        if approaches in ("linebreaks", "both"):
            result.approach_b = await self._run_approach_b(raw_original_text, scan_id)

        # Compute overall result
        result.overall = self._compute_overall(result)

        return result

    async def _run_approach_a(
        self,
        header_text: str,
        pg_title: str,
        ia_clues: IAEditionClues,
        scan_id: str,
    ) -> dict:
        """Run Approach A: Metadata matching."""
        pg_clues = self.metadata_matcher.extract_pg_clues(header_text, pg_title)
        match_result = self.metadata_matcher.score_match(pg_clues, ia_clues)

        # Special case: PG links to a source scan
        source_scan_note = ""
        if pg_clues.source_scan_id:
            if pg_clues.source_scan_id == scan_id:
                match_result.score = 100
                match_result.result = "match"
                match_result.rationale = (
                    f"PG header directly links to this scan ({scan_id})"
                )
            else:
                match_result.rationale += (
                    f" [PG source scan: {pg_clues.source_scan_id}]"
                )

        return {
            "score": match_result.score,
            "result": match_result.result,
            "rationale": match_result.rationale,
            "pg_clues": match_result.pg_clues,
            "ia_clues": match_result.ia_clues,
            "details": match_result.details,
        }

    async def _run_approach_b(
        self,
        raw_pg_text: str,
        scan_id: str,
    ) -> dict:
        """Run Approach B: Line-break fingerprinting."""
        # Step 1: Detect if PG preserves line breaks
        detection = self.linebreak_fingerprinter.detect_line_break_preservation(raw_pg_text)

        if not detection.preserved:
            return {
                "pg_preserves_breaks": False,
                "pg_column_width": detection.column_width or 0,
                "scan_column_width": 0,
                "line_endings_matched": 0,
                "line_endings_total": 0,
                "sequence_similarity": 0.0,
                "result": "unable_to_determine",
                "confidence": 0.0,
                "rationale": f"PG text does not preserve line breaks ({detection.rationale})",
                "detection": {
                    "confidence": detection.confidence,
                    "rationale": detection.rationale,
                },
            }

        # Step 2: Build PG fingerprint
        pg_fingerprint = self.linebreak_fingerprinter.build_pg_fingerprint(raw_pg_text)

        # Step 3: Fetch scan OCR text
        ocr_text = await self.linebreak_fingerprinter.fetch_ia_ocr_text(scan_id)

        if ocr_text is None:
            return {
                "pg_preserves_breaks": True,
                "pg_column_width": pg_fingerprint.column_width,
                "scan_column_width": 0,
                "line_endings_matched": 0,
                "line_endings_total": pg_fingerprint.long_lines,
                "sequence_similarity": 0.0,
                "result": "unable_to_determine",
                "confidence": 0.0,
                "rationale": (
                    "No OCR text available for scan. "
                    "Vision model transcription needed (future enhancement)."
                ),
                "detection": {
                    "preserved": True,
                    "confidence": detection.confidence,
                    "column_width": detection.column_width,
                },
            }

        # Step 4: Build scan fingerprint
        scan_fingerprint = self.linebreak_fingerprinter.build_scan_fingerprint(ocr_text)

        # Step 5: Compare
        comparison = self.linebreak_fingerprinter.compare_line_breaks(
            pg_fingerprint, scan_fingerprint
        )

        return {
            "pg_preserves_breaks": True,
            "pg_column_width": pg_fingerprint.column_width,
            "scan_column_width": scan_fingerprint.column_width,
            "line_endings_matched": comparison.line_endings_matched,
            "line_endings_total": comparison.line_endings_total,
            "sequence_similarity": comparison.sequence_similarity,
            "result": comparison.result,
            "confidence": comparison.confidence,
            "rationale": comparison.rationale,
            "detection": {
                "preserved": True,
                "confidence": detection.confidence,
                "column_width": detection.column_width,
            },
            "pg_sample": pg_fingerprint.long_lines,
            "scan_sample": scan_fingerprint.long_lines,
        }

    def _compute_overall(self, result: EditionVerificationResult) -> dict:
        """Compute overall result from individual approaches."""
        a_result = result.approach_a.get("result", "unable_to_determine") if result.approach_a else None
        a_score = result.approach_a.get("score", 0) if result.approach_a else 0
        b_result = result.approach_b.get("result", "unable_to_determine") if result.approach_b else None
        b_confidence = result.approach_b.get("confidence", 0.0) if result.approach_b else 0.0

        # If both approaches agree, that's strong
        if a_result and b_result:
            if a_result == "match" and b_result == "match":
                overall_result = "match"
                confidence = max(0.8, (a_score / 100.0 + b_confidence) / 2)
            elif a_result == "mismatch" and b_result == "mismatch":
                overall_result = "mismatch"
                confidence = max(0.6, max(a_score / 100.0, b_confidence))
            elif a_result == "unable_to_determine" and b_result == "unable_to_determine":
                overall_result = "unable_to_determine"
                confidence = 0.0
            elif b_result in ("match", "mismatch") and a_result == "unable_to_determine":
                # Approach B has a signal, A has no data — trust B
                overall_result = b_result
                confidence = b_confidence
            elif a_result in ("match", "mismatch") and b_result == "unable_to_determine":
                # Approach A has a signal, B has no data — trust A
                overall_result = a_result
                confidence = a_score / 100.0
            else:
                # One says something, the other can't tell
                if a_result == "match":
                    overall_result = "match"
                    confidence = 0.6
                elif a_result == "mismatch":
                    overall_result = "mismatch"
                    confidence = 0.6
                elif b_result == "match":
                    overall_result = "match"
                    confidence = 0.6
                elif b_result == "mismatch":
                    overall_result = "mismatch"
                    confidence = 0.6
                else:
                    overall_result = "unable_to_determine"
                    confidence = 0.0
        elif a_result:
            if a_result == "match":
                overall_result = "match"
                confidence = 0.5
            elif a_result == "mismatch":
                overall_result = "mismatch"
                confidence = 0.5
            else:
                overall_result = "unable_to_determine"
                confidence = 0.0
        elif b_result:
            if b_result == "match":
                overall_result = "match"
                confidence = 0.5
            elif b_result == "mismatch":
                overall_result = "mismatch"
                confidence = 0.5
            else:
                overall_result = "unable_to_determine"
                confidence = 0.0
        else:
            overall_result = "unable_to_determine"
            confidence = 0.0

        # Recommendation
        if overall_result == "match":
            recommendation = "Editions likely match. Safe to proceed with the full pipeline."
        elif overall_result == "mismatch":
            recommendation = (
                "Find a scan matching the PG source edition before running the full pipeline"
            )
        else:
            recommendation = (
                "Unable to determine edition match. "
                "Consider finding a scan with OCR text for line-break analysis."
            )

        return {
            "result": overall_result,
            "confidence": round(confidence, 2),
            "recommendation": recommendation,
        }

    @staticmethod
    def format_console(result: EditionVerificationResult) -> str:
        """Format verification result for console output.

        Args:
            result: EditionVerificationResult to format.

        Returns:
            Formatted string for console display.
        """
        lines: list[str] = []

        title = result.pg_metadata.get("title", f"PG #{result.pg_id}")
        lines.append(f"Edition Verification: {title} (PG #{result.pg_id}) vs {result.scan_id}")
        lines.append("")

        # Approach A
        if result.approach_a:
            a = result.approach_a
            pg_producer = result.pg_metadata.get("producer", "unknown")
            ia_pub = result.scan_metadata.get("publisher", "unknown")
            ia_date = result.scan_metadata.get("date", "unknown")

            lines.append("Approach A: Metadata Matching")
            lines.append(f"  PG clues: {pg_producer}")
            if result.pg_metadata.get("source_edition"):
                lines.append(f"  PG edition info: {result.pg_metadata['source_edition']}")
            lines.append(f"  IA clues: {ia_pub}, {ia_date}")
            lines.append(f"  Score: {a['score']}/100")
            lines.append(f"  Result: {a['result'].upper().replace('_', ' ')}")
            if a.get("rationale"):
                lines.append(f"  ({a['rationale']})")
            lines.append("")

        # Approach B
        if result.approach_b:
            b = result.approach_b
            lines.append("Approach B: Line-Break Fingerprinting")

            if b.get("pg_preserves_breaks"):
                lines.append(
                    f"  PG line breaks: PRESERVED "
                    f"(column width: {b.get('pg_column_width', '?')} chars)"
                )
                if b.get("scan_column_width", 0) > 0:
                    col_delta = abs(
                        b.get("pg_column_width", 0) - b.get("scan_column_width", 0)
                    )
                    if col_delta > 0:
                        lines.append(
                            f"  Column widths: PG={b.get('pg_column_width')}, "
                            f"Scan={b.get('scan_column_width')} → "
                            f"{'MATCH' if col_delta <= 2 else 'MISMATCH'} (Δ{col_delta})"
                        )
                    else:
                        lines.append(
                            f"  Column widths: PG={b.get('pg_column_width')}, "
                            f"Scan={b.get('scan_column_width')} → MATCH"
                        )

                matched = b.get("line_endings_matched", 0)
                total = b.get("line_endings_total", 0)
                if total > 0:
                    pct = matched / total * 100
                    lines.append(
                        f"  Line-ending words matched: {matched}/{total} ({pct:.1f}%)"
                    )
                lines.append(
                    f"  Sequence similarity: {b.get('sequence_similarity', 0):.2f}"
                )
            else:
                lines.append("  PG line breaks: NOT PRESERVED (reflowed text)")

            lines.append(f"  Result: {b['result'].upper().replace('_', ' ')}")
            if b.get("confidence"):
                lines.append(f"  Confidence: {b['confidence']:.2f}")
            if b.get("rationale"):
                lines.append(f"  ({b['rationale']})")
            lines.append("")

        # Overall
        overall = result.overall
        lines.append(f"Overall: {overall['result'].upper().replace('_', ' ')}")
        if overall.get("confidence"):
            lines.append(f"Confidence: {overall['confidence']:.2f}")
        if overall.get("recommendation"):
            lines.append(overall["recommendation"])

        return "\n".join(lines)

    @staticmethod
    def exit_code(result: str) -> int:
        """Map result string to exit code."""
        mapping = {
            "match": 0,
            "possible": 2,
            "unable_to_determine": 2,
            "mismatch": 1,
        }
        return mapping.get(result, 2)
