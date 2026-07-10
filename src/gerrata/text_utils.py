"""Shared text utilities for Gerrata."""

import re


def trim_shared_edges(pg_text: str, scan_text: str) -> tuple[str, str]:
    """Trim common leading/trailing punctuation from both texts.

    When the diff algorithm extracts phrases, surrounding punctuation
    (commas, semicolons, periods, quotes) gets included even though
    the actual difference is the word itself. This strips characters
    that are identical on both sides so the arrow fix shows only the
    real change.

    Only trims punctuation — never alphanumeric characters — and
    only when both texts share the same edge character (treating
    straight and curly quotes as equivalent).
    """
    edge_chars = set("'\";:,.!?()[]*\u2014\u2013 \u2018\u2019\u201c\u201d")

    # Quote equivalence: treat straight and curly quotes as the same
    quote_map = {
        "'": "'", "\u2018": "'", "\u2019": "'",  # single quotes
        '"': '"', "\u201c": '"', "\u201d": '"',     # double quotes
    }

    def _norm_char(c: str) -> str:
        return quote_map.get(c, c)

    pg = pg_text
    scan = scan_text

    # Trim common leading characters
    while pg and scan and pg[0] in edge_chars and scan[0] in edge_chars:
        if _norm_char(pg[0]) != _norm_char(scan[0]):
            break
        pg = pg[1:]
        scan = scan[1:]

    # Trim common trailing characters
    while pg and scan and pg[-1] in edge_chars and scan[-1] in edge_chars:
        if _norm_char(pg[-1]) != _norm_char(scan[-1]):
            break
        pg = pg[:-1]
        scan = scan[:-1]

    # Don't return empty strings — if everything got trimmed,
    # fall back to originals
    if not pg or not scan:
        return pg_text, scan_text

    return pg, scan


def normalize_possessive(word: str) -> str:
    """Strip possessive 's / 's / 's suffix from a word."""
    word = re.sub(r"[\u2018\u2019's]s$", "", word)
    return word.strip()
