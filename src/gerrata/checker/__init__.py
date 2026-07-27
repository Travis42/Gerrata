"""Checker package — compare PG text against scan OCR to find discrepancies."""

from gerrata.checker.text_diff import TextDiffChecker
from gerrata.checker.gap_detector import (
    CoverageGap,
    detect_scan_gaps,
    filter_for_report,
    gaps_to_candidate_errors,
)

__all__ = [
    "TextDiffChecker",
    "CoverageGap",
    "detect_scan_gaps",
    "filter_for_report",
    "gaps_to_candidate_errors",
]
