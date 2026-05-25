"""Approach A: Metadata matching between PG and IA scan editions.

Compares edition metadata (publisher, year, title) from PG headers
and IA scan metadata to determine if the scan matches the edition
PG transcribed from.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class PGEditionClues:
    """Edition clues extracted from a PG text header."""

    publisher: str = ""
    year: str = ""
    city: str = ""
    title: str = ""
    is_first_edition: bool = False
    source_scan_id: str = ""  # If PG links to an IA scan directly
    producer: str = ""
    source_edition_raw: str = ""  # Raw source/edition line from header


@dataclass
class IAEditionClues:
    """Edition clues extracted from IA metadata API."""

    title: str = ""
    publisher: str = ""
    date: str = ""
    creator: str = ""
    city: str = ""
    description: str = ""


@dataclass
class MetadataMatchResult:
    """Result of metadata matching."""

    score: int  # 0-100
    result: str  # "match", "possible", "mismatch", "unable_to_determine"
    rationale: str
    pg_clues: dict
    ia_clues: dict
    details: dict = field(default_factory=dict)


class MetadataMatcher:
    """Compare edition metadata between PG text and IA scan."""

    # Patterns for extracting edition info from PG headers
    PUBLISHER_PATTERN = re.compile(
        r"(?:Edition:?\s*)?Published by[:\s]+(.+?)(?:\n|$)",
        re.IGNORECASE,
    )
    PUBLISHER_PATTERN2 = re.compile(
        r"Published by\s+([^\n]+)",
        re.IGNORECASE,
    )
    YEAR_PATTERN = re.compile(
        r"(?:Copyright|Published|Edition|Printed)\s+(?:in\s+)?(\d{4})",
        re.IGNORECASE,
    )
    SOURCE_SCAN_PATTERN = re.compile(
        r"Source\s*:\s*https?://archive\.org/details/([^\s/\"]+)",
        re.IGNORECASE,
    )
    FIRST_EDITION_PATTERN = re.compile(
        r"first\s+edition",
        re.IGNORECASE,
    )
    CITY_PATTERN = re.compile(
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*:\s*(?:Published|Printed|Issued)",
    )

    def __init__(self, cache_dir: str | None = None):
        self.cache_dir = cache_dir

    def extract_pg_clues(self, header_text: str, pg_metadata_title: str = "") -> PGEditionClues:
        """Extract edition clues from PG text header.

        Args:
            header_text: Full text before the START marker.
            pg_metadata_title: Title already parsed from PG metadata.

        Returns:
            PGEditionClues with any edition info found.
        """
        clues = PGEditionClues()

        # Check for direct source scan link
        scan_match = self.SOURCE_SCAN_PATTERN.search(header_text)
        if scan_match:
            clues.source_scan_id = scan_match.group(1).strip()

        # Look for publisher
        pub_match = self.PUBLISHER_PATTERN.search(header_text)
        if not pub_match:
            pub_match = self.PUBLISHER_PATTERN2.search(header_text)
        if pub_match:
            clues.publisher = pub_match.group(1).strip()
            # Try to extract city from publisher line
            # Common format: "City: Publisher Name" or "City, Publisher Name"
            city_in_pub = re.match(
                r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*[,]\s*(.+)",
                clues.publisher,
            )
            if not city_in_pub:
                city_in_pub = re.match(
                    r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*[:]\s*(.+)",
                    clues.publisher,
                )
            if city_in_pub and len(city_in_pub.group(1)) < 20:
                clues.city = city_in_pub.group(1).strip()
                clues.publisher = city_in_pub.group(2).strip()

        # Look for year
        year_match = self.YEAR_PATTERN.search(header_text)
        if year_match:
            clues.year = year_match.group(1)
        else:
            # Fallback: look for a 4-digit year in the publisher/edition context
            if clues.publisher:
                year_in_pub = re.search(r"(\d{4})", clues.publisher)
                if year_in_pub:
                    clues.year = year_in_pub.group(1)
            if not clues.year and clues.source_edition_raw:
                year_in_edition = re.search(r"(\d{4})", clues.source_edition_raw)
                if year_in_edition:
                    clues.year = year_in_edition.group(1)

        # Check for "first edition"
        clues.is_first_edition = bool(self.FIRST_EDITION_PATTERN.search(header_text))

        # Title from metadata
        clues.title = pg_metadata_title

        # Raw source/edition info
        source_match = re.search(
            r"(?:Source|Edition|This.*edition)\s*:\s*(.+?)(?:\n\n|\n[A-Z])",
            header_text, re.IGNORECASE | re.DOTALL,
        )
        if source_match:
            clues.source_edition_raw = source_match.group(1).strip()

        # Producer (from "Produced by" line)
        producer_match = re.search(r"^Produced by[:\s]+(.+)", header_text, re.MULTILINE)
        if producer_match:
            clues.producer = producer_match.group(1).strip()

        return clues

    async def fetch_ia_metadata(self, scan_id: str) -> IAEditionClues:
        """Fetch edition metadata from IA metadata API.

        Args:
            scan_id: Internet Archive identifier.

        Returns:
            IAEditionClues with available metadata.
        """
        url = f"https://archive.org/metadata/{scan_id}"
        clues = IAEditionClues()

        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()

            meta = data.get("metadata", {})

            clues.title = meta.get("title", "")
            clues.publisher = meta.get("publisher", "")
            clues.date = meta.get("date", "")
            clues.creator = meta.get("creator", "")
            clues.city = meta.get("collection-city", "") or ""
            clues.description = meta.get("description", "")

            logger.debug(f"IA metadata for {scan_id}: title={clues.title!r}, "
                         f"publisher={clues.publisher!r}, date={clues.date!r}")

        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch IA metadata for {scan_id}: {e}")

        return clues

    def score_match(
        self,
        pg_clues: PGEditionClues,
        ia_clues: IAEditionClues,
    ) -> MetadataMatchResult:
        """Score the match between PG and IA edition clues.

        Scoring:
          - Publisher match: +40 points
          - Year match (within 2 years): +30 points
          - Title match (after removing subtitle variations): +10 points
          - Same city of publication: +10 points
          - Both are "first edition": +10 points

        Thresholds:
          >=70: MATCH
          40-69: POSSIBLE
          <40: MISMATCH
        """
        score = 0
        details: dict = {}
        pg_dict = asdict(pg_clues)
        ia_dict = asdict(ia_clues)

        # Special case: PG links to this exact scan
        if pg_clues.source_scan_id:
            # source_scan_id comparison is done by the caller
            details["pg_source_scan"] = pg_clues.source_scan_id

        # Publisher match: +40
        pg_pub = pg_clues.publisher.lower().strip()
        ia_pub = ia_clues.publisher.lower().strip()
        if pg_pub and ia_pub:
            # Normalize publisher names for comparison
            pg_pub_norm = self._normalize_publisher(pg_pub)
            ia_pub_norm = self._normalize_publisher(ia_pub)
            if pg_pub_norm == ia_pub_norm or pg_pub_norm in ia_pub_norm or ia_pub_norm in pg_pub_norm:
                score += 40
                details["publisher_match"] = True
            else:
                details["publisher_match"] = False
                details["pg_publisher"] = pg_clues.publisher
                details["ia_publisher"] = ia_clues.publisher
        else:
            details["publisher_match"] = None  # No data

        # Year match: +30
        pg_year = self._extract_year(pg_clues.year)
        ia_year = self._extract_year(ia_clues.date)
        if pg_year and ia_year:
            delta = abs(pg_year - ia_year)
            if delta <= 2:
                score += 30
                details["year_match"] = True
                details["year_delta"] = delta
            else:
                details["year_match"] = False
                details["year_delta"] = delta
        else:
            details["year_match"] = None  # No data

        # Title match: +10
        if pg_clues.title and ia_clues.title:
            title_sim = self._title_similarity(pg_clues.title, ia_clues.title)
            if title_sim > 0.7:
                score += 10
                details["title_match"] = True
                details["title_similarity"] = round(title_sim, 3)
            else:
                details["title_match"] = False
                details["title_similarity"] = round(title_sim, 3)
        else:
            details["title_match"] = None  # No data

        # City match: +10
        pg_city = pg_clues.city.lower().strip()
        ia_city = ia_clues.city.lower().strip()
        if pg_city and ia_city:
            if pg_city == ia_city or pg_city in ia_city or ia_city in pg_city:
                score += 10
                details["city_match"] = True
            else:
                details["city_match"] = False
        else:
            details["city_match"] = None  # No data

        # First edition: +10
        ia_is_first = bool(self.FIRST_EDITION_PATTERN.search(
            ia_clues.description or "")) or "first edition" in (ia_clues.description or "").lower()
        if pg_clues.is_first_edition and ia_is_first:
            score += 10
            details["first_edition_match"] = True
        else:
            details["first_edition_match"] = False

        # Determine result
        has_substantive_data = (
            details.get("publisher_match") is not None
            or details.get("year_match") is not None
        )

        if not any(v is True for k, v in details.items() if k.endswith("_match") and v is not None):
            result = "unable_to_determine"
            rationale = "No edition metadata in PG header"
        elif not has_substantive_data:
            # Only title matches, no publisher/year data — can't determine
            result = "unable_to_determine"
            rationale = f"Only title matched (score {score}/100), no publisher/year data in PG header"
        elif score >= 70:
            result = "match"
            rationale = f"High confidence match (score {score}/100)"
        elif score >= 40:
            result = "possible"
            rationale = f"Ambiguous match (score {score}/100), recommend line-break analysis"
        else:
            result = "mismatch"
            rationale = f"Low confidence (score {score}/100), likely different editions"

        return MetadataMatchResult(
            score=score,
            result=result,
            rationale=rationale,
            pg_clues=pg_dict,
            ia_clues=ia_dict,
            details=details,
        )

    def _normalize_publisher(self, publisher: str) -> str:
        """Normalize publisher name for comparison."""
        # Remove common prefixes/suffixes
        pub = publisher.lower().strip()
        for prefix in ["the ", "and ", "& "]:
            if pub.startswith(prefix):
                pub = pub[len(prefix):]
        # Remove trailing periods, commas
        pub = pub.rstrip(".,")
        # Collapse whitespace
        pub = " ".join(pub.split())
        return pub

    def _extract_year(self, date_str: str) -> int | None:
        """Extract a 4-digit year from a date string."""
        if not date_str:
            return None
        match = re.search(r"(\d{4})", date_str)
        return int(match.group(1)) if match else None

    def _title_similarity(self, title1: str, title2: str) -> float:
        """Compute similarity between two titles, ignoring subtitles and case."""
        # Strip subtitles (after :, ;, --, or ,)
        t1 = re.split(r"[:;]\s*|--|,\s+(?:a |an |the )", title1, 1, flags=re.IGNORECASE)[0].lower().strip()
        t2 = re.split(r"[:;]\s*|--|,\s+(?:a |an |the )", title2, 1, flags=re.IGNORECASE)[0].lower().strip()

        # Remove articles
        for prefix in ["the ", "a ", "an "]:
            if t1.startswith(prefix):
                t1 = t1[len(prefix):]
            if t2.startswith(prefix):
                t2 = t2[len(prefix):]

        # Simple word overlap
        words1 = set(t1.split())
        words2 = set(t2.split())
        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2
        return len(intersection) / len(union) if union else 0.0
