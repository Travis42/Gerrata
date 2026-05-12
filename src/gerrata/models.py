"""Data models for gerrata.

All core dataclasses used throughout the pipeline:
Alignment, Error, Report, and supporting types.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional


class ErrorCategory(str, Enum):
    """Categories of discrepancies found between PG text and scan."""

    OCR_SCANNO = "ocr_scanno"
    MISSING_WORD = "missing_word"
    EXTRA_WORD = "extra_word"
    MISSING_PUNCTUATION = "missing_punctuation"
    WRONG_WORD = "wrong_word"
    MISSING_CONTENT = "missing_content"
    ENCODING_ERROR = "encoding_error"
    FORMATTING_ERROR = "formatting_error"
    INTENTIONAL_CHANGE = "intentional_change"
    EDITION_VARIANT = "edition_variant"
    MODERNIZATION = "modernization"
    ALIGNMENT_ARTIFACT = "alignment_artifact"
    AMBIGUOUS = "ambiguous"


class ErrorSeverity(str, Enum):
    """Severity levels for errors."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AlignmentMethod(str, Enum):
    """Methods used for aligning PG text to scan pages."""

    LCS = "lcs"
    OCR_CROSSREF = "ocr_crossref"
    LLM_VISION = "llm_vision"
    MANUAL = "manual"


class Verdict(str, Enum):
    """LLM verdict on a candidate error."""

    PG_CORRECT = "pg_correct"
    SCAN_CORRECT = "scan_correct"
    EDITION_VARIANT = "edition_variant"
    INTENTIONAL_MODERNIZATION = "intentional_modernization"
    AMBIGUOUS = "ambiguous"
    UNABLE_TO_VERIFY = "unable_to_verify"


@dataclass
class Alignment:
    """Maps a passage of PG text to a scan page."""

    pg_start: int  # character offset in PG text
    pg_end: int  # character offset in PG text (exclusive)
    scan_page: int  # page number in scan (0-indexed)
    scan_image_path: Optional[str] = None  # path to page image file
    confidence: float = 1.0  # 0.0-1.0
    method: AlignmentMethod = AlignmentMethod.LCS

    @property
    def pg_length(self) -> int:
        return self.pg_end - self.pg_start

    def to_dict(self) -> dict:
        return {
            "pg_start": self.pg_start,
            "pg_end": self.pg_end,
            "scan_page": self.scan_page,
            "scan_image_path": self.scan_image_path,
            "confidence": self.confidence,
            "method": self.method.value,
        }


@dataclass
class PGMetadata:
    """Metadata extracted from a PG text header."""

    pg_id: int
    title: str = ""
    author: str = ""
    language: str = "en"
    release_date: str = ""
    producer: str = ""
    encoding: str = ""
    source_edition: str = ""
    transcriber_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CandidateError:
    """A potential error found by text diffing (before filtering/verification)."""

    pg_text: str  # The PG text passage containing the issue
    scan_text: str  # The corresponding scan OCR text
    pg_offset: int  # Character offset in PG text
    scan_page: int  # Scan page number (0-indexed)
    diff_description: str = ""  # Human-readable description of the diff
    category: ErrorCategory = ErrorCategory.OCR_SCANNO
    severity: ErrorSeverity = ErrorSeverity.HIGH

    def to_dict(self) -> dict:
        d = asdict(self)
        d["category"] = self.category.value
        d["severity"] = self.severity.value
        return d


@dataclass
class Error:
    """A verified error ready for the report."""

    candidate: CandidateError
    verdict: Verdict = Verdict.UNABLE_TO_VERIFY
    confidence: float = 0.0  # 0.0-1.0
    reasoning: str = ""
    suggested_fix: str = ""
    image_evidence: str = ""  # Text quoted from scan page image by verifier
    scan_image_path: Optional[str] = None
    location_description: str = ""  # e.g. "Chapter 3, paragraph 2"
    pg_file_line: int = 0  # Line number in the PG HTML file
    chapter_title: str = ""  # Chapter title where error occurs

    @property
    def category(self) -> ErrorCategory:
        """Derive category from verdict or candidate category."""
        # Alignment artifacts should always be reported as such, regardless of verdict
        if self.candidate.category == ErrorCategory.ALIGNMENT_ARTIFACT:
            return ErrorCategory.ALIGNMENT_ARTIFACT

        if self.verdict == Verdict.EDITION_VARIANT:
            return ErrorCategory.EDITION_VARIANT
        elif self.verdict == Verdict.INTENTIONAL_MODERNIZATION:
            return ErrorCategory.MODERNIZATION
        elif self.verdict == Verdict.PG_CORRECT:
            return ErrorCategory.INTENTIONAL_CHANGE
        elif self.verdict == Verdict.SCAN_CORRECT:
            return self.candidate.category
        else:
            return ErrorCategory.AMBIGUOUS

    @property
    def severity(self) -> ErrorSeverity:
        """Derive severity from category."""
        cat = self.category
        if cat == ErrorCategory.MISSING_CONTENT:
            return ErrorSeverity.CRITICAL
        elif cat in (ErrorCategory.OCR_SCANNO, ErrorCategory.WRONG_WORD, ErrorCategory.MISSING_WORD):
            return ErrorSeverity.HIGH
        elif cat in (ErrorCategory.EXTRA_WORD, ErrorCategory.MISSING_PUNCTUATION, ErrorCategory.ENCODING_ERROR):
            return ErrorSeverity.MEDIUM
        elif cat == ErrorCategory.FORMATTING_ERROR:
            return ErrorSeverity.LOW
        else:
            return ErrorSeverity.INFO

    def to_dict(self) -> dict:
        return {
            "pg_text": self.candidate.pg_text,
            "scan_text": self.candidate.scan_text,
            "pg_offset": self.candidate.pg_offset,
            "scan_page": self.candidate.scan_page,
            "diff_description": self.candidate.diff_description,
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "suggested_fix": self.suggested_fix,
            "image_evidence": self.image_evidence,
            "scan_image_path": self.scan_image_path,
            "location_description": self.location_description,
            "category": self.category.value,
            "severity": self.severity.value,
            "pg_file_line": self.pg_file_line,
            "chapter_title": self.chapter_title,
        }


@dataclass
class Report:
    """Complete quality audit report."""

    metadata: PGMetadata
    scan_source: str = ""  # e.g. "Internet Archive (identifier: 06-stevenson-jekyll-hyde)"
    date: str = ""
    pages_checked: int = 0
    total_pages: int = 0
    alignment_confidence: float = 0.0  # Text coverage: chars covered / total chars
    avg_page_confidence: float = 0.0  # Per-page confidence: average of per-page match scores
    edition_match_confidence: float = 0.0  # How confident we are editions match
    edition_notes: str = ""  # Notes about edition differences
    errors: list[Error] = field(default_factory=list)
    alignments: list[Alignment] = field(default_factory=list)

    @property
    def total_candidates(self) -> int:
        # Exclude alignment artifacts from the total count
        return len([e for e in self.errors if e.category != ErrorCategory.ALIGNMENT_ARTIFACT])

    @property
    def high_confidence_errors(self) -> list[Error]:
        return [e for e in self.errors
                if e.confidence >= 0.8 and e.category != ErrorCategory.EDITION_VARIANT
                and e.category != ErrorCategory.MODERNIZATION
                and e.category != ErrorCategory.INTENTIONAL_CHANGE
                and e.category != ErrorCategory.ALIGNMENT_ARTIFACT]

    @property
    def medium_confidence_errors(self) -> list[Error]:
        return [e for e in self.errors
                if 0.5 <= e.confidence < 0.8 and e.category != ErrorCategory.EDITION_VARIANT
                and e.category != ErrorCategory.MODERNIZATION
                and e.category != ErrorCategory.INTENTIONAL_CHANGE
                and e.category != ErrorCategory.ALIGNMENT_ARTIFACT]

    @property
    def low_confidence_errors(self) -> list[Error]:
        return [e for e in self.errors
                if e.confidence < 0.5 and e.category != ErrorCategory.EDITION_VARIANT
                and e.category != ErrorCategory.MODERNIZATION
                and e.category != ErrorCategory.INTENTIONAL_CHANGE
                and e.category != ErrorCategory.ALIGNMENT_ARTIFACT]

    @property
    def edition_variants(self) -> list[Error]:
        return [e for e in self.errors if e.category == ErrorCategory.EDITION_VARIANT]

    @property
    def intentional_changes(self) -> list[Error]:
        return [e for e in self.errors if e.category in
                (ErrorCategory.MODERNIZATION, ErrorCategory.INTENTIONAL_CHANGE)]

    def to_dict(self) -> dict:
        # Filter out alignment artifacts from the exported errors
        filtered_errors = [e for e in self.errors if e.category != ErrorCategory.ALIGNMENT_ARTIFACT]
        return {
            "metadata": self.metadata.to_dict(),
            "scan_source": self.scan_source,
            "date": self.date,
            "pages_checked": self.pages_checked,
            "total_pages": self.total_pages,
            "alignment_confidence": self.alignment_confidence,
            "avg_page_confidence": self.avg_page_confidence,
            "edition_match_confidence": self.edition_match_confidence,
            "edition_notes": self.edition_notes,
            "summary": {
                "total_candidates": self.total_candidates,
                "high_confidence_errors": len(self.high_confidence_errors),
                "medium_confidence_errors": len(self.medium_confidence_errors),
                "low_confidence_errors": len(self.low_confidence_errors),
                "edition_variants": len(self.edition_variants),
                "intentional_changes": len(self.intentional_changes),
            },
            "errors": [e.to_dict() for e in filtered_errors],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
