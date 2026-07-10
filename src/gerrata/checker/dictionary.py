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
from pathlib import Path

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

    # Load supplemental dictionary if it exists
    # (human-editable file for project-specific vocabulary)
    _supplement_path = Path(__file__).parent.parent.parent / "dictionary_supplement.txt"
    if _supplement_path.exists():
        with open(_supplement_path) as f:
            for line in f:
                w = line.strip().lower()
                if w and not w.startswith('#'):
                    _word_set.add(w)
        logger.info(f"Loaded supplemental dictionary from {_supplement_path}")

    logger.info(f"Dictionary loaded: {len(_word_set)} words")
    return _word_set


class DictionaryChecker:
    """Validate words against a comprehensive English dictionary.

    Combines NLTK words, system dictionary, and an optional supplemental
    file. Applies heuristics for diacritics, numbers, inflections, and
    proper noun detection.
    """

    def __init__(self, pg_text: str = ""):
        self.word_set = _get_word_set()
        self._pg_text = pg_text
        self._proper_noun_cache: dict[str, bool] = {}

    def set_pg_text(self, pg_text: str):
        """Set the PG body text for proper noun detection."""
        if pg_text != self._pg_text:
            self._pg_text = pg_text
            self._proper_noun_cache.clear()

    def is_in_dictionary(self, word: str) -> bool:
        """Check if a word appears in the dictionary.

        Strips accents, lowercases, and handles inflected forms
        (plurals, -ing, -ed, -ly) by trying to find the base form.
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
            return all(p in self.word_set for p in parts)

        # Try inflected forms — strip common English suffixes
        # and check if the base form is in the dictionary.
        if len(w) > 5:
            for suffix in ('ingly', 'edly'):
                if w.endswith(suffix):
                    base = w[:-2]  # strip 'ly' → 'ing'/'ed'
                    if base in self.word_set:
                        return True
                    base2 = w[:-4]  # strip 'gly'/'dly'
                    if base2 in self.word_set:
                        return True

        if len(w) > 5 and w.endswith('ing'):
            # harbouring → harbour
            base = w[:-3]
            if base in self.word_set:
                return True
            # running → run (double letter)
            if len(base) > 2 and base[-1] == base[-2] and base[:-1] in self.word_set:
                return True

        if len(w) > 4 and w.endswith('ed'):
            base = w[:-2]
            if base in self.word_set:
                return True
            # stopped → stop (double letter)
            if len(base) > 2 and base[-1] == base[-2] and base[:-1] in self.word_set:
                return True
            # loved → love (add e)
            if (base + 'e') in self.word_set:
                return True

        if len(w) > 4 and w.endswith('ly'):
            base = w[:-2]
            if base in self.word_set:
                return True

        if len(w) > 4 and w.endswith('er'):
            base = w[:-2]
            if base in self.word_set:
                return True

        if len(w) > 4 and w.endswith('est'):
            base = w[:-3]
            if base in self.word_set:
                return True

        if len(w) > 3 and w.endswith('s') and not w.endswith('ss'):
            base = w[:-1]
            if base in self.word_set:
                return True
            # -es plurals: boxes → box
            if len(w) > 4 and w.endswith('es'):
                base2 = w[:-2]
                if base2 in self.word_set:
                    return True
                # cities → city (y → ies)
                if (base2 + 'y') in self.word_set:
                    return True

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

    def is_proper_noun(self, word: str) -> bool:
        """Detect proper nouns by checking if the word appears capitalized
        but never lowercase in the PG text.

        A word like 'Frode' that appears 220 times capitalized and 0 times
        as 'frode' is clearly a proper noun. Common words like 'kingdom'
        may appear capitalized at sentence starts but also lowercase mid-sentence.

        Caches results per word for performance.
        """
        if not self._pg_text or not word:
            return False

        # Strip markup/punctuation for cache key
        cache_key = re.sub(r"<[^>]+>", "", word).strip().lower()
        if not cache_key or cache_key in self._proper_noun_cache:
            return self._proper_noun_cache.get(cache_key, False)

        clean = re.sub(r"<[^>]+>", "", word).strip()
        if len(clean) < 2:
            self._proper_noun_cache[cache_key] = False
            return False

        cap_form = clean[0].upper() + clean[1:]
        lower_form = clean[0].lower() + clean[1:]

        cap_count = self._pg_text.count(cap_form)
        lower_count = self._pg_text.count(lower_form)

        result = cap_count > 0 and lower_count == 0
        self._proper_noun_cache[cache_key] = result
        return result

    def validate_replacement(self, scan_text: str, pg_text: str = "") -> bool:
        """Validate the replacement word (scan_text) for an errata entry.

        Diacritic/ligature additions are auto-validated (return True) since
        they almost always represent the scan preserving original printing
        accents that the PG transcription dropped.

        Pure numbers are auto-validated (not dictionary words).

        Trailing/leading punctuation is stripped before lookup.

        Args:
            scan_text: The scan text (proposed correction).
            pg_text: The PG text (original), used for context.

        Returns:
            True if validated (dictionary match, diacritic, or number).
        """
        # Clean markup
        s = re.sub(r"<[^>]+>", "", scan_text)
        s = re.sub(r"_([^_]+)_", r"\1", s)  # PG underscores
        s = s.strip()

        # Heuristic: diacritic/ligature additions are auto-validated
        if self._has_diacritic_or_ligature(s):
            return True

        # Strip leading/trailing punctuation (keep internal: hyphens, apostrophes)
        s = re.sub(r"^[^\w']+", "", s)
        s = re.sub(r"[^\w']+$", "", s)
        if not s:
            return False

        # Pure numbers (including decimals, ranges) — auto-validate
        if re.match(r"^\d+([.,]\d+)*(-\d+)*$", s):
            return True

        # Capitalized scan word → proper noun (name, place). Don't flag.
        if s and s[0].isupper():
            return True

        # For multi-word replacements, check the longest word
        words = s.split()
        if not words:
            return False

        if len(words) == 1:
            return self.is_in_dictionary(words[0])
        else:
            # Multi-word: check the longest word
            longest = max(words, key=len)
            return self.is_in_dictionary(longest)
