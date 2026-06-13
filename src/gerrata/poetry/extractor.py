"""Extract poetry formatting from vision LLM responses.

When the --poetry-formatting flag is active, the transcription prompt
is replaced with a combined prompt that asks for both text AND
indentation in a compact JSON format.

The compact format uses ["text", indent] arrays — no field names
repeated per line, no separate transcription field. This keeps
response size close to plain-text transcription.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

POETRY_FORMATTING_PROMPT = (
    "Reproduce the text on this page EXACTLY as printed. Every letter, "
    "every punctuation mark, every space. This is a 19th-century book — "
    "it uses archaic spellings that are NOT errors. Your job is to "
    "reproduce them, not fix them.\n\n"
    "Examples of what you MUST preserve (not change):\n"
    "- bowlders (NOT boulders)\n"
    "- unappropriate (NOT inappropriate)\n"
    "- subtile (NOT subtle)\n"
    "- colour (NOT color)\n"
    "- chuse (NOT choose)\n"
    "- Honour (NOT Honor)\n"
    "- shew (NOT show)\n"
    "- sopha (NOT sofa)\n"
    "- staid (NOT stayed)\n"
    "- cloaths (NOT clothes)\n"
    "- connexion (NOT connection)\n"
    "- publick (NOT public)\n"
    "- comprehended (NOT comprized)\n\n"
    "If you modernize, normalize, or 'correct' any spelling, "
    "you are destroying the historical record. "
    "Do NOT add, remove, or rearrange any words. "
    "Preserve all line breaks.\n\n"
    "ADDITIONALLY, record the indentation of each line relative to the "
    "leftmost text on the page.\n"
    "Indent: 0=leftmost, 1=~1em, 2=~2em, 3=~3+em. "
    "Blank lines (stanza breaks) = indent -1.\n\n"
    "If this page CONTINUES a poem from a previous page (no new title "
    "at top), set is_continuation to true.\n\n"
    "Respond in JSON. Each line is [text, indent] — write the text only "
    "once, with its indent:\n"
    "{\n"
    '  "poem_title": "THE TITLE" or "",\n'
    '  "is_continuation": false,\n'
    '  "stanzas": [\n'
    "    {\n"
    '      "label": "I" or "",\n'
    '      "lines": [\n'
    '        ["As long as Fame\'s imperious music rings", 0],\n'
    '        ["  Will poets mock it with crowned words", 1],\n'
    '        ["", -1],\n'
    '        ["And haggard men will clamber to be kings", 0]\n'
    "      ]\n"
    "    }\n"
    "  ]\n"
    "}"
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PoetryLine:
    text: str
    indent: int = 0


@dataclass
class PoetryStanza:
    stanza_number: int
    label: str = ""
    lines: list[PoetryLine] = field(default_factory=list)


@dataclass
class PoetryPage:
    page_num: int
    scan_image: str = ""
    poem_title: str = ""
    stanzas: list[PoetryStanza] = field(default_factory=list)
    is_continuation: bool = False


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_poetry_response(response: str, page_num: int, scan_image: str = "") -> tuple[str, PoetryPage]:
    """Parse a combined JSON response into (transcription, PoetryPage).

    Returns:
        Tuple of (plain_text_transcription, PoetryPage_with_formatting).
        If JSON parsing fails, returns (raw_response_text, empty_PoetryPage).
    """
    parsed = _extract_json(response)

    if parsed:
        poetry_page = _build_poetry_page(parsed, page_num, scan_image)
        transcription = _reconstruct_transcription(poetry_page)
        if transcription:
            return (transcription, poetry_page)

    # JSON parsing failed — try partial JSON extraction
    partial = _extract_from_partial_json(response, page_num, scan_image)
    if partial:
        return partial

    # Last resort: use raw text as transcription, no formatting
    logger.warning(f"Could not extract poetry formatting for page {page_num}")
    return (response, PoetryPage(page_num=page_num, scan_image=scan_image))


def _extract_json(text: str) -> dict | None:
    """Extract and parse JSON from LLM response."""
    # Try direct parse
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Try extracting from ```json blocks
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _build_poetry_page(parsed: dict, page_num: int, scan_image: str) -> PoetryPage:
    """Build a PoetryPage from parsed JSON dict."""
    poem_title = parsed.get("poem_title", "") or ""
    is_continuation = bool(parsed.get("is_continuation", False))

    stanzas = []
    for i, s in enumerate(parsed.get("stanzas", [])):
        label = s.get("label", "") or ""
        lines = []
        for entry in s.get("lines", []):
            if isinstance(entry, list) and len(entry) >= 2:
                text = str(entry[0]) if entry[0] else ""
                indent = int(entry[1]) if isinstance(entry[1], (int, float)) else 0
            elif isinstance(entry, dict):
                text = entry.get("text", "")
                indent = entry.get("indent", 0)
            elif isinstance(entry, str):
                text = entry
                indent = 0
            else:
                continue
            lines.append(PoetryLine(text=text, indent=indent))
        stanzas.append(PoetryStanza(
            stanza_number=i + 1,
            label=label,
            lines=lines,
        ))

    return PoetryPage(
        page_num=page_num,
        scan_image=scan_image,
        poem_title=poem_title,
        stanzas=stanzas,
        is_continuation=is_continuation,
    )


def _reconstruct_transcription(page: PoetryPage) -> str:
    """Reconstruct plain text from stanza lines."""
    lines = []
    for stanza in page.stanzas:
        for line in stanza.lines:
            lines.append(line.text)
    return "\n".join(lines) if lines else ""


def _extract_from_partial_json(
    text: str, page_num: int, scan_image: str
) -> tuple[str, PoetryPage] | None:
    """Try to salvage data from truncated JSON responses."""
    # Look for completed [text, indent] pairs
    pairs = re.findall(r'\["([^"]*?)",\s*(-?\d+)\]', text)
    if not pairs:
        return None

    lines = []
    for text_val, indent_val in pairs:
        lines.append(PoetryLine(text=text_val, indent=int(indent_val)))

    stanza = PoetryStanza(stanza_number=1, lines=lines)
    page = PoetryPage(page_num=page_num, scan_image=scan_image, stanzas=[stanza])
    transcription = "\n".join(l.text for l in lines)

    logger.info(f"Salvaged {len(lines)} lines from partial JSON for page {page_num}")
    return (transcription, page)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def pages_to_json(
    pages: list[PoetryPage],
    pg_id: int,
    scan_source: str,
    total_pages: int,
) -> dict:
    """Convert list of PoetryPage to the IE-compatible JSON format."""
    # Build per_page_raw
    per_page_raw = []
    for p in pages:
        if not p.stanzas:
            continue
        per_page_raw.append({
            "page_num": p.page_num,
            "scan_image": p.scan_image,
            "poem_title": p.poem_title,
            "stanzas": [
                {
                    "stanza_number": s.stanza_number,
                    "label": s.label,
                    "lines": [
                        {"text": l.text, "indent": l.indent}
                        for l in s.lines
                    ],
                }
                for s in p.stanzas
            ],
            "notes": "",
            "is_continuation": p.is_continuation,
        })

    # Build poems list (merge by title across pages)
    poems_map = {}
    poems_order = []
    for p in pages:
        title = p.poem_title or ""
        if title and title not in poems_map:
            poems_map[title] = {
                "title": title,
                "pages": [],
                "stanzas": [],
                "confidence": 0.0,
            }
            poems_order.append(title)
        if title:
            entry = poems_map[title]
            entry["pages"].append(p.page_num)
            for s in p.stanzas:
                stanza_data = {
                    "stanza_number": len(entry["stanzas"]) + 1,
                    "label": s.label,
                    "lines": [
                        {"text": l.text, "indent": l.indent}
                        for l in s.lines
                    ],
                }
                entry["stanzas"].append(stanza_data)

    poems = [poems_map[t] for t in poems_order]
    poetry_pages = sum(1 for p in pages if p.stanzas)

    return {
        "pg_id": pg_id,
        "scan_source": scan_source,
        "poems": poems,
        "summary": {
            "total_pages": total_pages,
            "poetry_pages": poetry_pages,
            "poems_found": [p["title"] for p in poems],
            "avg_confidence": 0.0,
        },
        "per_page_raw": per_page_raw,
    }
