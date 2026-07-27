"""Detect recurring find/replace patterns in verified errors."""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from book_projects.gerrata.src.gerrata.text_utils import trim_shared_edges, normalize_possessive


def is_diacritic_or_ligature_change(pg_word: str, scan_word: str) -> bool:
    """Check if the only difference between two words is diacritics or ligatures.

    When a PG transcriber systematically strips accents, tildes, umlauts,
    or expands ligatures (æ→ae, œ→oe), every occurrence in PG will be wrong
    in the same way. These replacements should be treated as global even
    if caught only once, because the mechanism is systematic.

    Examples:
        mediaeval → mediæval  (ligature)
        regime → régime        (accent)
        Compania → Compañia    (tilde)
        Tome → Tomé            (accent)
    """
    # Keep only alphabetic characters for comparison
    f = ''.join(c for c in pg_word if c.isalpha())
    r = ''.join(c for c in scan_word if c.isalpha())

    if not f or not r:
        return False

    # Decompose unicode into base + combining characters
    f_decomp = unicodedata.normalize('NFD', f)
    r_decomp = unicodedata.normalize('NFD', r)

    # Strip combining characters (accents, tildes, umlauts, etc.)
    f_base = ''.join(c for c in f_decomp if not unicodedata.combining(c))
    r_base = ''.join(c for c in r_decomp if not unicodedata.combining(c))

    # Expand ligatures to their component letters
    f_exp = f_base.replace('æ', 'ae').replace('Æ', 'AE').replace('œ', 'oe').replace('Œ', 'OE')
    r_exp = r_base.replace('æ', 'ae').replace('Æ', 'AE').replace('œ', 'oe').replace('Œ', 'OE')

    return f_exp.lower() == r_exp.lower()


@dataclass
class GlobalReplacement:
    """A recurring find/replace pattern detected in the PG text."""
    pg_text: str
    scan_text: str
    occurrences_in_pg: int
    caught_by_errata: int
    examples: list[int] = field(default_factory=list)


class GlobalReplacementDetector:
    """Extract global replacement candidates from verified errors."""

    # Maximum length for word-level diffs
    MAX_WORD_LEN = 30

    def detect(
        self,
        verified_errors: list[dict],
        pg_body_text: str,
    ) -> list[GlobalReplacement]:
        """Find recurring word-level replacements.

        Args:
            verified_errors: List of verified error dicts with 'candidate' key
            pg_body_text: Full PG body text to search for occurrences

        Returns:
            List of GlobalReplacement, sorted by occurrences_in_pg descending
        """
        # Step 1: Extract trimmed word pairs
        raw_pairs = []
        for err in verified_errors:
            candidate = err.get("candidate", err)
            pg_text = candidate.get("pg_text", "").strip()
            scan_text = candidate.get("scan_text", "").strip()

            if not pg_text or not scan_text:
                continue

            # Skip very different length pairs (alignment artifacts)
            if abs(len(pg_text) - len(scan_text)) > 50:
                continue

            # Trim shared punctuation
            pg_t, scan_t = trim_shared_edges(pg_text, scan_text)

            # Only keep word-level diffs
            if len(pg_t) > self.MAX_WORD_LEN or len(scan_t) > self.MAX_WORD_LEN:
                continue

            # Skip if identical after trimming
            if pg_t == scan_t:
                continue

            # Skip if only smart quotes differ
            pg_ascii = pg_t.replace("\u2018", "'").replace("\u2019", "'")
            scan_ascii = scan_t.replace("\u2018", "'").replace("\u2019", "'")
            if pg_ascii == scan_ascii:
                continue

            # Skip if only alphanumeric content is the same (punct-only diff)
            pg_alpha = re.sub(r"[^a-zA-Z]", "", pg_t)
            scan_alpha = re.sub(r"[^a-zA-Z]", "", scan_t)
            if pg_alpha == scan_alpha:
                continue

            # Skip OCR garbage markers
            if re.search(r"[*\u2020\u2021]", scan_t) and not re.search(r"[a-zA-Z]{3,}", scan_t):
                continue

            # Skip if scan side contains phrase-level content (alignment artifact)
            # A valid global replacement should be a single word or short name
            if len(scan_t.split()) > 2:
                continue
            # Skip if pg side is multi-word (phrase-level, not word-level)
            if len(pg_t.split()) > 2:
                continue
            # Skip if lengths are very different (insertion/deletion, not replacement)
            max_len = max(len(pg_t), len(scan_t))
            min_len = min(len(pg_t), len(scan_t))
            if max_len > min_len + 5:
                continue

            page = candidate.get("scan_page", candidate.get("display_page", 0))
            raw_pairs.append((pg_t, scan_t, page))

        # Step 2: Normalize and group
        groups: dict[tuple[str, str], list[tuple[str, str, int]]] = defaultdict(list)
        for pg_t, scan_t, page in raw_pairs:
            pg_root = normalize_possessive(pg_t.strip().strip("\"'\u201c\u201d"))
            scan_root = normalize_possessive(scan_t.strip().strip("\"'\u201c\u201d"))

            if not pg_root or not scan_root:
                continue
            if pg_root == scan_root:
                continue

            # Skip if either normalized form is multi-word
            # (global replacements should be single words/names)
            if " " in pg_root or " " in scan_root:
                continue

            groups[(pg_root, scan_root)].append((pg_t, scan_t, page))

        # Step 3: Count occurrences in PG text and qualify
        from book_projects.gerrata.src.gerrata.checker.dictionary import DictionaryChecker
        dict_checker = DictionaryChecker(pg_text=pg_body_text)

        results = []
        demoted = []  # pairs that stay as individual errata
        for (pg_word, scan_word), instances in groups.items():
            # Count whole-word, case-sensitive occurrences in PG text
            count = len(re.findall(r"\b" + re.escape(pg_word) + r"\b", pg_body_text))

            caught = len(instances)
            pages = list({p for _, _, p in instances})

            # Qualify if the word appears 2+ times in PG text.
            # Exception: diacritic/ligature-only changes qualify with 1+ occurrence,
            # because transcribers apply orthographic decisions systematically
            # (every instance of the word will have the same diacritic stripped).
            is_diacritic = is_diacritic_or_ligature_change(pg_word, scan_word)
            is_capitalized_scan = bool(scan_word) and scan_word[0].isupper()
            threshold = 1 if (is_diacritic or is_capitalized_scan) else 2

            if count < threshold:
                continue

            # Safety check: only promote to global if the replacement is
            # unambiguous. If both PG and scan words are valid dictionary
            # words, a global find/replace could introduce new errors.
            # Keep as individual errata instead.
            is_capitalized = is_capitalized_scan
            pg_in_dict = dict_checker.is_in_dictionary(pg_word)
            scan_in_dict = (
                dict_checker._has_diacritic_or_ligature(scan_word)
                or is_capitalized
                or dict_checker.is_in_dictionary(scan_word)
            )

            if is_diacritic or is_capitalized or (not pg_in_dict and scan_in_dict):
                # Unambiguous — safe for global replacement
                results.append(GlobalReplacement(
                    pg_text=pg_word,
                    scan_text=scan_word,
                    occurrences_in_pg=count,
                    caught_by_errata=caught,
                    examples=sorted(pages),
                ))
            else:
                # Ambiguous — both sides could be valid words.
                # Demote to individual errata.
                demoted.append((pg_word, scan_word, instances))

        # Sort by occurrences descending, then alphabetically
        results.sort(key=lambda r: (-r.occurrences_in_pg, r.pg_text.lower()))

        return results

    def is_global_instance(
        self,
        error: dict,
        global_replacements: list[GlobalReplacement],
    ) -> bool:
        """Check if an error is an instance of a promoted global replacement."""
        candidate = error.get("candidate", error)
        pg_text = candidate.get("pg_text", "").strip()
        scan_text = candidate.get("scan_text", "").strip()

        pg_t, scan_t = trim_shared_edges(pg_text, scan_text)
        pg_root = normalize_possessive(pg_t.strip().strip("\"'\u201c\u201d"))
        scan_root = normalize_possessive(scan_t.strip().strip("\"'\u201c\u201d"))

        for gr in global_replacements:
            if pg_root == gr.pg_text and scan_root == gr.scan_text:
                return True
        return False
