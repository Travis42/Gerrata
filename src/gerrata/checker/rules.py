"""Rule-based false positive filter.

Removes candidate errors that are likely false positives due to:
- Typography changes (em dashes, curly quotes)
- Paragraph reflowing
- Known PG conventions
- Edition variants that look like errors
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from gerrata.models import CandidateError, ErrorCategory

logger = logging.getLogger(__name__)

# Patterns for known typography conversions (intentional PG changes)
TYPOGRAPHY_PATTERNS = [
    # Em dash variants
    (re.compile(r"^--$"), re.compile(r"^—$")),  # -- → —
    (re.compile(r" -+ "), re.compile(r" — ")),  # spaced dashes → em dash
    # Quotation marks
    (re.compile(r'^"$'), re.compile(r'^[\u201c\u201d]$')),  # straight → curly
    (re.compile(r"^'$"), re.compile(r'^[\u2018\u2019]$')),  # straight → curly
    # Apostrophe variants
    (re.compile(r"’"), re.compile(r"'")),  # curly → straight apostrophe
]

# Words that are commonly modernized (not errors)
MODERNIZATION_PATTERNS = [
    (r"\bshan't\b", r"\bshall not\b"),
    (r"\bcan't\b", r"\bcannot\b"),
    (r"\bdon't\b", r"\bdo not\b"),
    (r"\bdidn't\b", r"\bdid not\b"),
    (r"\bwon't\b", r"\bwill not\b"),
    (r"'twas", r"it was"),
    (r"'tis", r"it is"),
    (r"\bsome one\b", r"\bsomeone\b"),
    (r"\bany one\b", r"\banyone\b"),
    (r"\bevery one\b", r"\beveryone\b"),
    (r"\bto-morrow\b", r"\btomorrow\b"),
    (r"\bto-night\b", r"\btonight\b"),
    (r"\bto-day\b", r"\btoday\b"),
    (r"\balways\b", r"\bal ways\b"),  # hyphenation variant
    (r"\bupon\b", r"\bon\b"),
]

# Edition variant patterns (legitimate editorial differences)
EDITION_VARIANT_PATTERNS = [
    # Spelling variants (British vs American, archaic vs modern)
    (r"\bcolour\b", r"\bcolor\b"),
    (r"\bfavour\b", r"\bfavor\b"),
    (r"\b honour\b", r"\bhonor\b"),
    (r"\b rumour\b", r"\brumor\b"),
    (r"\bbehaviour\b", r"\bbehavior\b"),
    (r"\bcentre\b", r"\bcenter\b"),
    (r"\b defence\b", r"\bdefense\b"),
    (r"\btravelling\b", r"\btraveling\b"),
    (r"\b amongst\b", r"\b among\b"),
    (r"\b whilst\b", r"\b while\b"),
    (r"\b towards\b", r"\b toward\b"),
    (r"\b forwards\b", r"\b forward\b"),
    # Compound word variants (downright vs down-right, etc.)
    (r"\bdownright\b", r"\bdown-right\b"),
    (r"\baback\b", r"\ba-back\b"),
    (r"\bsomehow\b", r"\bsome-how\b"),
    (r"\banyhow\b", r"\bany-how\b"),
    (r"\beverywhere\b", r"\bevery-where\b"),
    (r"\bsomething\b", r"\bsome-thing\b"),
    (r"\bnothing\b", r"\bno-thing\b"),
    # Punctuation style differences
    (r":", r";"),
    (r";", r":"),
]


@dataclass
class FilterResult:
    """Result of filtering a candidate error."""

    error: CandidateError
    is_false_positive: bool
    reason: str
    new_category: ErrorCategory | None = None  # If category should be changed


class FalsePositiveFilter:
    """Filter out candidate errors that are likely false positives."""

    def __init__(self, strict: bool = False):
        """Initialize filter.

        Args:
            strict: If True, fewer false positives are filtered out (keep more candidates).
        """
        self.strict = strict

    def filter(self, errors: list[CandidateError]) -> list[CandidateError]:
        """Filter a list of candidate errors.

        Returns the list with false positives removed (or reclassified).
        """
        filtered: list[CandidateError] = []
        for error in errors:
            result = self._check_error(error)
            if result.is_false_positive:
                if not self.strict:
                    logger.debug(f"Filtered: {result.reason}")
                    continue
            if result.new_category:
                error.category = result.new_category
            filtered.append(error)
        return filtered

    def _check_error(self, error: CandidateError) -> FilterResult:
        """Check if a single error is a false positive."""
        pg = error.pg_text.lower().strip()
        scan = error.scan_text.lower().strip()

        # Check for alignment artifacts (absent in PG/scan with short other side)
        if self._is_alignment_artifact(error):
            return FilterResult(
                error=error,
                is_false_positive=False,
                reason="Alignment artifact (window overshoot)",
                new_category=ErrorCategory.ALIGNMENT_ARTIFACT,
            )

        # Check typography conversions
        for pg_pat, scan_pat in TYPOGRAPHY_PATTERNS:
            if pg_pat.match(pg) or scan_pat.match(scan):
                return FilterResult(
                    error=error,
                    is_false_positive=True,
                    reason="Typography conversion (intentional)",
                )

        # Check modernization patterns
        for mod_pat, orig_pat in MODERNIZATION_PATTERNS:
            if re.search(mod_pat, pg) and re.search(orig_pat, scan):
                return FilterResult(
                    error=error,
                    is_false_positive=True,
                    reason="Intentional modernization",
                )
            if re.search(orig_pat, pg) and re.search(mod_pat, scan):
                return FilterResult(
                    error=error,
                    is_false_positive=True,
                    reason="Intentional modernization",
                )

        # Check edition variants
        for var1, var2 in EDITION_VARIANT_PATTERNS:
            if re.search(var1, pg) and re.search(var2, scan):
                return FilterResult(
                    error=error,
                    is_false_positive=False,
                    reason="Possible edition variant",
                    new_category=ErrorCategory.EDITION_VARIANT,
                )
            if re.search(var2, pg) and re.search(var1, scan):
                return FilterResult(
                    error=error,
                    is_false_positive=False,
                    reason="Possible edition variant",
                    new_category=ErrorCategory.EDITION_VARIANT,
                )

        # Check for pure whitespace/punctuation differences
        # Exception: text containing single-letter initials with dots (e.g.,
        # "H.T", "J.B.") indicates abbreviated content like scanner metadata
        # that leaked into PG text — don't filter these as "punctuation only".
        # Note: pg and scan are lowercased here.
        combined_lower = pg + ' ' + scan
        has_initials = bool(re.search(r'[a-z]\.[a-z]', combined_lower))
        pg_words = re.sub(r"[^\w]", "", pg)
        scan_words = re.sub(r"[^\w]", "", scan)
        if pg_words == scan_words and not has_initials:
            return FilterResult(
                error=error,
                is_false_positive=True,
                reason="Only punctuation/whitespace difference",
            )

        # Check for line-breaking hyphenation artifacts
        if self._is_hyphenation_artifact(pg, scan):
            return FilterResult(
                error=error,
                is_false_positive=True,
                reason="Line-breaking hyphenation artifact",
            )

        return FilterResult(
            error=error,
            is_false_positive=False,
            reason="No rule matches",
        )

    def _is_hyphenation_artifact(self, pg: str, scan: str) -> bool:
        """Check if the difference is due to hyphenation at line breaks."""
        # Pattern: PG has "some-\nthing" or "some- thing" vs scan has "something"
        # Or vice versa
        pg_unhyphenated = re.sub(r"(\w)-\s*", r"\1", pg)
        scan_unhyphenated = re.sub(r"(\w)-\s*", r"\1", scan)
        return pg_unhyphenated == scan_unhyphenated

    def _is_alignment_artifact(self, error: CandidateError) -> bool:
        """Check if this is an alignment artifact (absent in PG/scan with short other side).

        These occur when the alignment window is wider than the actual page content,
        causing "(absent in PG)" or "(absent in scan)" markers to appear.

        Any "(absent in ...)" marker is considered an alignment artifact, regardless of
        the length of the other side, as these represent window overshoot issues.
        """
        scan_text = error.scan_text.strip()

        # Check if scan text contains "(absent in ...)" marker
        # Any such marker is considered an alignment artifact
        is_absent_in_pg = "(absent in pg)" in scan_text.lower()
        is_absent_in_scan = "(absent in scan)" in scan_text.lower()

        return is_absent_in_pg or is_absent_in_scan


# Need to import logger
import logging
logger = logging.getLogger(__name__)
