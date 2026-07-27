"""Variant classifier: categorize diffs between two editions.

Classifies each diff into a variant category (textual, punctuation,
spelling, normalization, etc.) and assigns a significance score
(major, moderate, minor, trivial).

Reuses us_uk_spelling.py for British/American spelling detection.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from book_projects.gerrata.src.gerrata.checker.us_uk_spelling import is_us_uk_variant
from book_projects.gerrata.src.gerrata.models import (
    CandidateError,
    TextualVariant,
    VariantCategory,
    VariantSignificance,
)

# Function words that rarely change meaning when substituted
_FUNCTION_WORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "nor", "for", "yet", "so",
    "in", "on", "at", "to", "of", "by", "with", "from", "into", "upon",
    "is", "was", "are", "were", "be", "been", "being", "am",
    "have", "has", "had", "do", "does", "did",
    "will", "would", "shall", "should", "may", "might", "must", "can", "could",
    "he", "she", "it", "they", "we", "you", "i",
    "his", "her", "its", "their", "our", "your", "my",
    "him", "them", "us",
    "this", "that", "these", "those",
    "which", "who", "whom", "whose", "what",
    "not", "no", "all", "each", "every", "some", "any", "both",
    "very", "quite", "rather", "somewhat", "too", "also",
    "here", "there", "now", "then", "when", "where",
    "if", "than", "as", "while", "after", "before", "since", "until",
})

# Punctuation characters only
_PUNCT_ONLY = re.compile(r'^[^\w\s]+$')

# Only whitespace/linebreak differences
_WHITESPACE_ONLY = re.compile(r'^\s*$')

# Words only (letters, possibly with hyphens/apostrophes)
_WORD_TOKEN_RE = re.compile(r"[a-zA-Z]+(?:[-'][a-zA-Z]+)*")


def _strip_punctuation(text: str) -> str:
    """Remove all punctuation from text, keeping only word characters and spaces."""
    return re.sub(r'[^\w\s]', '', text)


def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace to single spaces."""
    return re.sub(r'\s+', ' ', text).strip()


def _extract_words(text: str) -> list[str]:
    """Extract word tokens from text."""
    return _WORD_TOKEN_RE.findall(text)


def _is_punctuation_only_diff(text_a: str, text_b: str) -> bool:
    """Check if two texts differ only in punctuation."""
    return _strip_punctuation(text_a) == _strip_punctuation(text_b) and _strip_punctuation(text_a)


def _is_whitespace_only_diff(text_a: str, text_b: str) -> bool:
    """Check if two texts differ only in whitespace."""
    return _normalize_whitespace(text_a) == _normalize_whitespace(text_b)


def _count_content_words(text: str) -> int:
    """Count content (non-function) words in text."""
    words = _extract_words(text.lower())
    return sum(1 for w in words if w not in _FUNCTION_WORDS)


def _has_negation_change(text_a: str, text_b: str) -> bool:
    """Check if the diff introduces or removes a negation."""
    negations = {"not", "no", "never", "neither", "nor", "nothing", "none", "nowhere",
                 "n't", "don't", "doesn't", "didn't", "won't", "wouldn't", "can't",
                 "couldn't", "shouldn't", "wouldn't", "isn't", "aren't", "wasn't",
                 "weren't", "hasn't", "haven't", "hadn't"}
    words_a = set(_extract_words(text_a.lower()))
    words_b = set(_extract_words(text_b.lower()))
    # Check if a negation was added or removed
    neg_a = words_a & negations
    neg_b = words_b & negations
    return bool(neg_a) != bool(neg_b) or neg_a != neg_b


class VariantClassifier:
    """Classify diffs between two editions into variant categories and significance."""

    def __init__(self):
        pass

    def classify(self, candidate: CandidateError) -> TextualVariant:
        """Classify a single CandidateError diff result into a TextualVariant.

        Args:
            candidate: A CandidateError from the text diff checker.

        Returns:
            A TextualVariant with category and significance assigned.
        """
        a_text = candidate.pg_text
        b_text = candidate.scan_text

        # Handle special markers
        is_missing = "(absent in" in a_text or "(absent in" in b_text

        category = self._classify_category(a_text, b_text, is_missing)
        significance = self._classify_significance(
            a_text, b_text, category, is_missing
        )

        # Build context string (surrounding text) — not available from
        # CandidateError alone, so we leave it empty; it can be enriched
        # later by the reporter.
        return TextualVariant(
            edition_a_text=a_text,
            edition_b_text=b_text,
            edition_a_offset=candidate.pg_offset,
            edition_b_offset=0,  # Will be filled by cross-alignment
            edition_a_page=candidate.scan_page,
            edition_b_page=0,  # Will be filled by cross-alignment
            category=category,
            significance=significance,
            confidence=0.5,  # Default; can be upgraded by verification
            context="",
        )

    def classify_batch(
        self, candidates: list[CandidateError]
    ) -> list[TextualVariant]:
        """Classify multiple CandidateError diffs into TextualVariants."""
        return [self.classify(c) for c in candidates]

    def _classify_category(
        self, text_a: str, text_b: str, is_missing: bool
    ) -> VariantCategory:
        """Determine the variant category for a diff."""
        if is_missing:
            # Gerrata convention:
            # pg_text=real, scan_text="(absent in scan)" → A has text B lacks → missing_content
            # pg_text="(absent in PG)", scan_text=real → B has text A lacks → added_content
            if "(absent in PG)" in text_a:
                # Edition B (scan) has text that Edition A (PG) doesn't
                return VariantCategory.ADDED_CONTENT
            else:
                # Edition A has text that Edition B doesn't
                return VariantCategory.MISSING_CONTENT

        # Whitespace-only difference
        if _is_whitespace_only_diff(text_a, text_b):
            return VariantCategory.NORMALIZATION

        # Punctuation-only difference
        if _is_punctuation_only_diff(text_a, text_b):
            return VariantCategory.PUNCTUATION_VARIANT

        # Check for US/UK spelling variant
        words_a = _extract_words(text_a)
        words_b = _extract_words(text_b)

        if len(words_a) == 1 and len(words_b) == 1:
            if is_us_uk_variant(words_a[0], words_b[0]):
                return VariantCategory.SPELLING_CHANGE

        # Check for spelling changes in multi-word diffs
        if len(words_a) == len(words_b):
            all_spelling = True
            any_spelling = False
            for wa, wb in zip(words_a, words_b):
                wa_l, wb_l = wa.lower(), wb.lower()
                if wa_l != wb_l:
                    if is_us_uk_variant(wa_l, wb_l):
                        any_spelling = True
                    else:
                        all_spelling = False
            if all_spelling and any_spelling:
                return VariantCategory.SPELLING_CHANGE

        # Check for linebreak/hyphenation differences
        # e.g., "word-" (end of line) vs "word" (next line)
        a_clean = re.sub(r'(\w)-\s+', r'\1', text_a)
        b_clean = re.sub(r'(\w)-\s+', r'\1', text_b)
        if _normalize_whitespace(a_clean) == _normalize_whitespace(b_clean) and a_clean != text_a + b_clean:
            # Only differs by hyphenation
            if re.search(r'\b\w-\s*\n', text_a) or re.search(r'\b\w-\s*\n', text_b):
                return VariantCategory.LINEBREAK_VARIANT

        # Substantive word change → textual variant
        return VariantCategory.TEXTUAL_VARIANT

    def _classify_significance(
        self,
        text_a: str,
        text_b: str,
        category: VariantCategory,
        is_missing: bool,
    ) -> VariantSignificance:
        """Determine the significance level of a variant."""
        # Trivial categories
        if category == VariantCategory.NORMALIZATION:
            return VariantSignificance.TRIVIAL
        if category == VariantCategory.LINEBREAK_VARIANT:
            return VariantSignificance.TRIVIAL
        if category == VariantCategory.FORMATTING_VARIANT:
            return VariantSignificance.TRIVIAL

        # Spelling changes are always minor
        if category == VariantCategory.SPELLING_CHANGE:
            return VariantSignificance.MINOR

        # Punctuation variants are usually minor, but can be moderate
        if category == VariantCategory.PUNCTUATION_VARIANT:
            return self._punctuation_significance(text_a, text_b)

        # Missing/added content
        if is_missing:
            return self._content_significance(text_a, text_b)

        # Textual variants need deeper analysis
        return self._textual_significance(text_a, text_b)

    def _punctuation_significance(
        self, text_a: str, text_b: str
    ) -> VariantSignificance:
        """Assess significance of a punctuation difference."""
        # Period/comma differences are minor
        diff_chars = set(text_a) ^ set(text_b)
        meaningful_punct = {'.', '!', '?', ';', ':'}
        if diff_chars & meaningful_punct:
            # Could change sentence structure
            return VariantSignificance.MODERATE
        return VariantSignificance.MINOR

    def _content_significance(
        self, text_a: str, text_b: str
    ) -> VariantSignificance:
        """Assess significance of missing/added content."""
        present_text = text_b if "(absent in" in text_a else text_a
        word_count = len(_extract_words(present_text))

        if word_count == 0:
            return VariantSignificance.TRIVIAL
        if word_count <= 2:
            return VariantSignificance.MINOR
        if word_count <= 10:
            return VariantSignificance.MODERATE
        return VariantSignificance.MAJOR

    def _textual_significance(
        self, text_a: str, text_b: str
    ) -> VariantSignificance:
        """Assess significance of a textual variant."""
        words_a = _extract_words(text_a)
        words_b = _extract_words(text_b)

        if not words_a and not words_b:
            return VariantSignificance.TRIVIAL

        # Check for negation changes — these can flip meaning
        if _has_negation_change(text_a, text_b):
            return VariantSignificance.MAJOR

        # Count how many content words changed
        set_a = set(w.lower() for w in words_a)
        set_b = set(w.lower() for w in words_b)

        # Words that differ (in either direction)
        changed_a = set_a - set_b
        changed_b = set_b - set_a

        # Filter out function words from the changed sets
        content_changed_a = changed_a - _FUNCTION_WORDS
        content_changed_b = changed_b - _FUNCTION_WORDS

        total_content_changes = len(content_changed_a) + len(content_changed_b)

        # No content words changed → trivial or minor
        if total_content_changes == 0:
            # Only function words or punctuation changed
            return VariantSignificance.MINOR

        # Single content word change
        if total_content_changes == 1:
            return VariantSignificance.MODERATE

        # Multiple content word changes
        if total_content_changes >= 3:
            return VariantSignificance.MAJOR

        return VariantSignificance.MODERATE
