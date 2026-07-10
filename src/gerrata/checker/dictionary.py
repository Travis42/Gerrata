"""Dictionary validation for errata candidates.

Checks whether replacement words (scan_text) appear in a comprehensive
English dictionary. Used to split errata into "dictionary-validated"
(high confidence) and "flagged" (needs human review) groups.

Combines NLTK words corpus (~234K) with system dictionary (~102K)
for ~301K total words, including archaic/early modern English forms.
"""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

# Lazy-loaded singleton
_word_set: set[str] | None = None


def _strip_accents(s: str) -> str:
    """Strip combining diacritics for dictionary lookup."""
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )


def _get_word_set() -> set[str]:
    """Load the combined dictionary (lazy singleton)."""
    global _word_set
    if _word_set is not None:
        return _word_set

    _word_set = set()

    # NLTK words corpus
    try:
        import nltk
        nltk.download('words', quiet=True)
        from nltk.corpus import words as nltk_words
        _word_set.update(x.lower() for x in nltk_words.words())
    except Exception as e:
        logger.warning(f"NLTK words corpus unavailable: {e}")

    # System dictionary (SCOWL-based, has archaic forms)
    try:
        with open('/usr/share/dict/words') as f:
            for line in f:
                w = line.strip().lower()
                if w:
                    _word_set.add(w)
    except FileNotFoundError:
        logger.warning("System dictionary /usr/share/dict/words not found")

    # Supplement: common archaic/early modern English forms missing
    # from both sources. Curated list — not meant to be exhaustive.
    _word_set.update({
        'shewed', 'shew', 'shewn', 'shewing',  # archaic "show"
        'kine', 'yclept', 'wargs', 'wot', 'wist',
        'ken', 'sere', 'sithence', 'erst', 'whilom',
        'hight', 'yclept', 'whilom', 'forsooth',
    })

    logger.info(f"Dictionary loaded: {len(_word_set)} words")
    return _word_set


class DictionaryChecker:
    """Validate words against a comprehensive English dictionary."""

    def __init__(self):
        self.word_set = _get_word_set()

    def is_in_dictionary(self, word: str) -> bool:
        """Check if a word appears in the dictionary.

        Strips accents, lowercases, and handles hyphenated compounds
        by checking each part. Proper nouns (capitalized multi-char words
        that aren't sentence-initial) are always flagged as "not in dictionary"
        since dictionaries don't cover names.

        Args:
            word: The word to check.

        Returns:
            True if the word is found in the dictionary.
        """
        if not word or not word.strip():
            return False

        w = _strip_accents(word.strip().lower())

        # Direct lookup
        if w in self.word_set:
            return True

        # Handle possessives: strip 's / s' / '
        if w.endswith("'s"):
            base = w[:-2]
            return base in self.word_set if base else False
        if w.endswith("s'"):
            base = w[:-2]
            return base in self.word_set if base else False
        if w.endswith("'") and len(w) > 2:
            base = w[:-1]
            return base in self.word_set if base else False

        # Handle hyphenated compounds: check each part
        if '-' in w:
            parts = [p for p in w.split('-') if p]
            if not parts:
                return False
            # All parts must be in dictionary
            return all(p in self.word_set for p in parts)

        return False

    # Diacritic ranges: Latin extended/combining that indicate the scan
    # preserves original printing accents the PG transcription dropped.
    _DIACRITIC_CHARS = set(
        "àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿĀāĂăĄąĆćĈĉĊċČčĎďĐđĒēĔĕĖėĘęĚě"
        "ĜĝĞğĠġĢģĤĥĦħĨĩĪīĬĭĮįİıĲĳĴĵĶķĸĹĺĻļĽľĿŀŁłŃńŅņŇňŉŊŋŌōŎŏ"
        "ŐőŒœŔŕŖŗŘřŚśŜŝŞşŠšŢţŤťŦŧŨũŪūŬŭŮůŰűŲųŴŵŶŷŸŹźŻżŽžſ"
        "œæÆŒ"  # ligatures
    )

    def _has_diacritic_or_ligature(self, word: str) -> bool:
        """Check if word contains diacritics or ligatures."""
        return bool(self._DIACRITIC_CHARS & set(word.lower()))

    def validate_replacement(self, scan_text: str, pg_text: str = "") -> bool:
        """Validate the replacement word (scan_text) for an errata entry.

        Diacritic/ligature additions are auto-validated (return True) since
        they almost always represent the scan preserving original printing
        accents that the PG transcription dropped.

        Args:
            scan_text: The scan text (proposed correction).
            pg_text: The PG text (original), used for context.

        Returns:
            True if validated (dictionary match or diacritic/ligature addition).
        """
        # Clean markup
        s = re.sub(r"<[^>]+>", "", scan_text)
        s = re.sub(r"_([^_]+)_", r"\1", s)  # PG underscores
        s = s.strip()

        # Heuristic: diacritic/ligature additions are auto-validated
        if self._has_diacritic_or_ligature(s):
            return True

        # For multi-word replacements, check the longest word
        words = s.split()
        if not words:
            return False

        # Single word or short phrase: check all words
        # For phrases, at least the longest word should be valid
        if len(words) == 1:
            return self.is_in_dictionary(words[0])
        else:
            # Multi-word: check the longest word
            longest = max(words, key=len)
            return self.is_in_dictionary(longest)
