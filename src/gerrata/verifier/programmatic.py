"""Programmatic error verifier — no LLM, pure scoring.

Replaces the LLM vision verifier with deterministic confidence scoring.
Scores are based on:
1. Context match ratio: how well the surrounding words match
2. Edit distance: how similar the PG and scan words are
3. Alignment quality: the confidence of the page alignment
4. Diff characteristics: word lengths, single-vs-multi-word diffs
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from pathlib import Path

from gerrata.models import CandidateError, Error, ErrorCategory, Verdict

logger = logging.getLogger(__name__)


class ProgrammaticVerifier:
    """Score candidate errors programmatically without LLM calls.

    Each candidate gets a confidence score (0.0-1.0) based on how likely
    it is to be a real error rather than an alignment artifact.

    The key insight: real errors have high context match (the words around
    the diff match perfectly) but the diff itself is a clear substitution.
    Alignment artifacts have low context match (surrounding words don't
    align either, because the whole window is misaligned).
    """

    CONTEXT_WINDOW = 10  # words on each side to check
    HIGH_CONFIDENCE = 0.8
    MEDIUM_CONFIDENCE = 0.5

    def __init__(self, pg_text: str, alignments: list):
        """
        Args:
            pg_text: Full PG body text.
            alignments: List of Alignment objects for page-level confidence.
        """
        self.pg_text = pg_text
        # Build page→alignment lookup for alignment confidence
        self.page_alignment = {}
        for a in alignments:
            self.page_alignment[a.scan_page] = a

    def verify(self, candidate: CandidateError) -> Error:
        """Score a single candidate error programmatically."""
        score, reasoning = self._score_candidate(candidate)

        # Determine verdict based on confidence
        if score >= self.HIGH_CONFIDENCE:
            verdict = Verdict.SCAN_CORRECT
        elif score >= self.MEDIUM_CONFIDENCE:
            verdict = Verdict.AMBIGUOUS
        else:
            verdict = Verdict.UNABLE_TO_VERIFY

        return Error(
            candidate=candidate,
            verdict=verdict,
            confidence=score,
            reasoning=reasoning,
        )

    def verify_batch(self, candidates: list[CandidateError]) -> list[Error]:
        """Score all candidates."""
        results = []
        for c in candidates:
            results.append(self.verify(c))
        return results

    def _score_candidate(self, candidate: CandidateError) -> tuple[float, str]:
        """Compute confidence score and reasoning for a candidate.

        Returns (score, reasoning) where score is 0.0-1.0.
        """
        factors = []

        # 1. Context match ratio (most important factor)
        ctx_score, ctx_reason = self._context_match_score(candidate)
        factors.append(("context", ctx_score, ctx_reason))

        # 2. Edit distance similarity
        edit_score, edit_reason = self._edit_distance_score(candidate)
        factors.append(("edit_dist", edit_score, edit_reason))

        # 3. Alignment confidence for this page
        align_score, align_reason = self._alignment_score(candidate)
        factors.append(("alignment", align_score, align_reason))

        # 4. Word length sanity check
        length_score, length_reason = self._length_score(candidate)
        factors.append(("length", length_score, length_reason))

        # 5. Is it an absent-text artifact? (zero confidence)
        if "(absent" in candidate.pg_text or "(absent" in candidate.scan_text:
            return 0.0, "Absent text marker — alignment boundary artifact"

        # Weighted combination: context is king
        weights = {"context": 0.45, "edit_dist": 0.20, "alignment": 0.20, "length": 0.15}
        total = sum(weights[name] * score for name, score, _ in factors)

        # Build reasoning string
        reasons = []
        for name, score, reason in factors:
            if score is not None:
                reasons.append(f"{name}={score:.2f} ({reason})")
        reasoning = "; ".join(reasons)

        return round(total, 3), reasoning

    def _context_match_score(
        self, candidate: CandidateError
    ) -> tuple[float, str]:
        """Check how well the surrounding context words match.

        High context match = the diff is isolated, not part of a
        misaligned region. This is the strongest signal for real errors.
        """
        pg_text = candidate.pg_text
        scan_text = candidate.scan_text

        # Skip absent markers
        if "(absent" in pg_text or "(absent" in scan_text:
            return 0.0, "absent text"

        # Extract context from PG text at the offset
        offset = candidate.pg_offset
        # Find the actual pg_text in the full body to get surrounding context
        search_pos = self._find_in_pg(pg_text, offset)
        if search_pos < 0:
            # Fallback: use offset directly
            search_pos = offset

        context_start = max(0, search_pos - 300)
        context_end = min(len(self.pg_text), search_pos + len(pg_text) + 300)
        pg_context = self.pg_text[context_start:context_end]

        pg_words = pg_context.lower().split()
        scan_words = scan_text.lower().split()

        if len(scan_words) == 0:
            return 0.0, "no scan words"

        # Count how many scan words appear in the PG context
        matches = 0
        total = 0
        for w in scan_words:
            if len(w) < 3:
                continue  # skip short words
            total += 1
            # Check if this word appears anywhere in pg_context
            if w in pg_words or w.rstrip(".,;:!?\"'") in pg_words:
                matches += 1

        if total == 0:
            return 0.0, "no significant scan words"

        ratio = matches / total

        if ratio >= 0.8:
            return 1.0, f"{matches}/{total} scan words found in PG context"
        elif ratio >= 0.6:
            return 0.8, f"{matches}/{total} scan words found in PG context"
        elif ratio >= 0.4:
            return 0.5, f"{matches}/{total} scan words found in PG context"
        elif ratio >= 0.2:
            return 0.3, f"{matches}/{total} scan words found in PG context"
        else:
            return 0.0, f"{matches}/{total} scan words found — misaligned"

    def _edit_distance_score(
        self, candidate: CandidateError
    ) -> tuple[float, str]:
        """Score based on edit distance between PG and scan words.

        Real typos tend to be 1-2 edit distance apart.
        Completely different words suggest misalignment.
        """
        pg = candidate.pg_text.strip().lower()
        scan = candidate.scan_text.strip().lower()

        if "(absent" in pg or "(absent" in scan:
            return 0.0, "absent text"

        # Strip punctuation for comparison
        pg_clean = re.sub(r'[.,;:!?"""\'()\[\]{}]', '', pg)
        scan_clean = re.sub(r'[.,;:!?"""\'()\[\]{}]', '', scan)

        if not pg_clean or not scan_clean:
            return 0.0, "empty after cleaning"

        # For multi-word segments, use token-level comparison
        pg_tokens = pg_clean.split()
        scan_tokens = scan_clean.split()

        if len(pg_tokens) == 1 and len(scan_tokens) == 1:
            ed = _levenshtein(pg_tokens[0], scan_tokens[0])
            max_len = max(len(pg_tokens[0]), len(scan_tokens[0]))
            if max_len == 0:
                return 0.0, "empty"
            ratio = ed / max_len

            if ratio == 0:
                return 1.0, "identical"
            elif ratio <= 0.2:
                return 0.9, f"edit_dist={ed}/{max_len}"
            elif ratio <= 0.4:
                return 0.7, f"edit_dist={ed}/{max_len}"
            elif ratio <= 0.6:
                return 0.4, f"edit_dist={ed}/{max_len}"
            else:
                return 0.1, f"edit_dist={ed}/{max_len} — very different"

        # Multi-word: use SequenceMatcher ratio
        sm = SequenceMatcher(None, pg_tokens, scan_tokens)
        ratio = sm.ratio()

        if ratio >= 0.7:
            return 0.9, f"seq_match={ratio:.2f}"
        elif ratio >= 0.5:
            return 0.6, f"seq_match={ratio:.2f}"
        elif ratio >= 0.3:
            return 0.3, f"seq_match={ratio:.2f}"
        else:
            return 0.1, f"seq_match={ratio:.2f} — very different"

    def _alignment_score(
        self, candidate: CandidateError
    ) -> tuple[float, str]:
        """Use the page alignment confidence as a factor."""
        align = self.page_alignment.get(candidate.scan_page)
        if align is None:
            return 0.3, "no alignment data"

        conf = align.confidence
        if conf >= 0.9:
            return 1.0, f"page_align={conf:.3f}"
        elif conf >= 0.7:
            return 0.8, f"page_align={conf:.3f}"
        elif conf >= 0.5:
            return 0.5, f"page_align={conf:.3f}"
        else:
            return 0.2, f"page_align={conf:.3f} — low alignment quality"

    def _length_score(
        self, candidate: CandidateError
    ) -> tuple[float, str]:
        """Check word length sanity.

        Real errors tend to be similar length (1-2 char difference).
        Very different lengths suggest misaligned text.
        """
        pg = candidate.pg_text.strip()
        scan = candidate.scan_text.strip()

        if "(absent" in pg or "(absent" in scan:
            return 0.0, "absent text"

        pg_len = len(pg)
        scan_len = len(scan)

        # If lengths are very similar, more likely real
        if pg_len == 0 or scan_len == 0:
            return 0.3, f"pg_len={pg_len} scan_len={scan_len}"

        ratio = min(pg_len, scan_len) / max(pg_len, scan_len)

        if ratio >= 0.8:
            return 1.0, f"len_ratio={ratio:.2f}"
        elif ratio >= 0.6:
            return 0.7, f"len_ratio={ratio:.2f}"
        elif ratio >= 0.4:
            return 0.4, f"len_ratio={ratio:.2f}"
        else:
            return 0.1, f"len_ratio={ratio:.2f} — very different lengths"

    def _find_in_pg(self, text: str, offset: int) -> int:
        """Find the text in PG body, preferring offset-based position."""
        if not text or "(absent" in text:
            return -1
        # Try exact find near the offset
        search_start = max(0, offset - 50)
        search_end = min(len(self.pg_text), offset + len(text) + 50)
        region = self.pg_text[search_start:search_end]
        pos = region.find(text)
        if pos >= 0:
            return search_start + pos

        # Try normalized find
        clean = text.lower().strip()
        region_lower = region.lower()
        pos = region_lower.find(clean)
        if pos >= 0:
            return search_start + pos

        return -1


def _levenshtein(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]
