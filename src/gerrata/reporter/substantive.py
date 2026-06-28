"""Substantive errata report generator via LLM analysis.

Calls an LLM (via OpenRouter) to analyze the generated errata report and produce
a curated markdown document identifying only meaning-changing errors, with
false positive categories documented and Internet Archive scan page links.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from gerrata.models import Report

logger = logging.getLogger(__name__)

# -- System prompt with classification rules from the spec --

SYSTEM_PROMPT = """\
You are a textual scholar specializing in proofreading and errata analysis for \
Project Gutenberg texts. You analyze errata reports comparing a PG transcription \
against a scanned book edition, and produce a curated "substantive errata" \
report that separates meaning-changing errors from noise.

## Your Task

Analyze the provided errata report (JSON + email format) and produce a \
substantive errata report in markdown.

## Classification Rules

### EXCLUDE from substantive errata (document as false positives):
1. **Diacritic-only changes**: Senor→Señor, Corbelan→Corbelàn, naive→naïve, etc. \
Same base word, different accent marks. These are encoding modernizations, \
not errors.
2. **Spelling variants**: corredor→corridor, colour→color, amongst→among, etc. \
Both forms are valid English. If the author consistently uses one form, \
it's an authorial choice.
3. **Scan OCR errors**: Where PG text is correct but the scan has a misread \
(e.g., "speeches" → "specches", "shriek" → "shrick"). PG is authoritative.
4. **Repeated detections**: The same word appearing on 10+ pages in the same \
pattern. Count once, exclude from substantive list.
5. **PG header/footer artifacts**: Pipeline alignment matching PG boilerplate \
to scan page content.
6. **Pipeline alignment artifacts**: Where PG and scan text share no meaningful \
words (obvious misalignment).
7. **Formatting-only changes**: Punctuation spacing, line breaks, paragraph \
boundaries.
8. **British/American spelling**: colour/color, defence/defense, etc. — both valid.
9. **French-influenced spellings**: confidante/confidant, lackey/lacquey, \
carbine/carabine — author's choice.
10. **Æ-ligature variants**: mediæval/mediaeval, Cæsar/Caesar — both valid.
11. **Trivial singular/plural**: Where both forms work in context and the \
difference doesn't change meaning.

### INCLUDE as substantive errata:
1. **Wrong word (different meaning)**: A word that means something different \
than intended (e.g., "Gefe" for "Jefe", "supreme" for "supremo" in a \
Spanish phrase).
2. **PG typos (misspellings)**: Clear compositor errors — extra letters, \
transpositions (e.g., "superintendendence" for "superintendence").
3. **Typesetting errors**: l/I confusion, rn/m confusion where the wrong \
character changes meaning (e.g., "d'ltalia" for "d'Italia").
4. **Missing words**: Words omitted from the PG text that are in the scan.
5. **Extra words**: Words present in PG but absent from the scan, where the \
scan is clearly correct.
6. **Encoding errors**: Characters that are clearly wrong (not just accent \
differences).

### INCLUDE as missing content:
- Scan pages with text that has no corresponding passage in PG text \
(from the "MISSING CONTENT" section of the email report).

## Output Format

Produce the markdown report exactly as specified below. For each substantive \
error, you MUST:
- Link to the specific scan page on Internet Archive
- Quote the full sentence containing the error
- Explain why it changes meaning
- Be concise — no more than 3-4 lines per entry

For false positives, list them grouped by category with one-line explanations \
and page links.

## Confidence Requirements

Only flag entries as substantive where the scan reading is unambiguous AND \
the PG text is clearly wrong. When in doubt, classify as a false positive or \
spelling variant.

Do NOT invent errors. Only report what the pipeline found. If the raw report \
has 300+ entries and only 4 are substantive, that's the correct answer.
"""


def _ia_page_url(scan_id: str, page: int) -> str:
    """Build a clickable Internet Archive page link."""
    return f"https://archive.org/details/{scan_id}/page/n{page}/mode/1up"


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


class SubstantiveErrataGenerator:
    """Generate substantive errata report via LLM analysis.

    The step is **non-blocking**: if no API key is available or the call
    fails, a warning is logged and the pipeline continues.
    """

    def __init__(
        self,
        api_url: str = "https://openrouter.ai/api/v1/chat/completions",
        api_key: str = "",
        model: str = "z-ai/glm-5.1",
        scan_id: str = "",
    ):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.scan_id = scan_id

    # -- public API --------------------------------------------------------

    async def generate(
        self,
        report: Report,
        email_content: str,
        json_content: str,
        output_path: Path,
    ) -> Optional[Path]:
        """Send report data to LLM and save substantive errata markdown.

        Returns the path to the generated file, or *None* if the step was
        skipped (no API key, or LLM call failed).
        """
        if not self.api_key:
            logger.warning(
                "Substantive errata report skipped: no API key. "
                "Set OPENROUTER_API_KEY or pass --substantive-key."
            )
            return None

        if not self.scan_id:
            logger.warning(
                "Substantive errata report skipped: no scan_id for link generation."
            )
            return None

        system_prompt, user_prompt = self._build_prompt(
            report, email_content, json_content
        )

        try:
            markdown = await self._call_llm(system_prompt, user_prompt)
        except Exception:
            logger.exception("Substantive errata LLM call failed — skipping step")
            return None

        if not markdown:
            logger.warning("LLM returned empty response — skipping substantive report")
            return None

        # Derive output path
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build filename matching the existing pattern
        stem = output_path.stem
        # Replace _errata_email or _errata with _substantive_errata
        stem = re.sub(r"_errata(_email)?$", "_substantive_errata", stem)
        if "_substantive_errata" not in stem:
            stem = stem + "_substantive_errata"
        substantive_path = output_path.parent / f"{stem}.md"

        substantive_path.write_text(markdown, encoding="utf-8")
        logger.info("Substantive errata report saved to %s", substantive_path)
        return substantive_path

    # -- prompt construction -----------------------------------------------

    def _build_prompt(
        self,
        report: Report,
        email_content: str,
        json_content: str,
    ) -> tuple[str, str]:
        """Build (system_prompt, user_prompt) for the LLM call."""
        truncated_json = self._truncate_json(json_content)

        user_prompt = f"""\
## Book Information
- Title: {report.metadata.title}
- Author: {report.metadata.author}
- PG ID: #{report.metadata.pg_id}
- Scan ID: {self.scan_id}
- Scan URL: {_ia_page_url(self.scan_id, 0).rsplit("/page/", 1)[0]}

## Raw Errata Report

{email_content}

## Full Error Data (JSON)

{truncated_json}

## Task

Produce a substantive errata report following the classification rules above."""
        return SYSTEM_PROMPT, user_prompt

    # -- JSON truncation ---------------------------------------------------

    @staticmethod
    def _truncate_json(json_content: str, max_tokens: int = 80000) -> str:
        """Truncate JSON to fit within token limits.

        Strategy:
        1. Parse the JSON, sort errors by confidence (descending).
        2. Include full data for errors with confidence ≥ 0.5.
        3. For low-confidence errors (0.2–0.5), include a one-line summary
           and drop ``pg_sentence`` / ``reasoning`` fields.
        4. If still over budget, drop low-confidence entries entirely.
        """
        try:
            data = json.loads(json_content)
        except (json.JSONDecodeError, TypeError):
            # Not valid JSON — return as-is (will likely be too large,
            # but better than crashing)
            return json_content

        errors = data.get("errors", [])
        if not errors:
            return json_content

        # Separate into high/medium and low confidence buckets
        high_med: list[dict] = []
        low: list[dict] = []

        for err in errors:
            conf = err.get("confidence", 0.0)
            if conf >= 0.5:
                high_med.append(err)
            else:
                low.append(err)

        # Build compact low-confidence entries (one-line summaries)
        compact_low: list[dict] = []
        for err in low:
            scan_id_raw = err.get("scan_page", "?")
            pg_text = err.get("pg_text", "")
            scan_text = err.get("scan_text", "")
            conf = err.get("confidence", 0)
            page = err.get("display_page", scan_id_raw)
            compact_low.append({
                "pg_text": pg_text,
                "scan_text": scan_text,
                "page": page,
                "confidence": conf,
            })

        # Reassemble
        result_data = dict(data)
        result_data["errors"] = high_med + compact_low
        result_data["_note"] = (
            "Low-confidence errors (<0.5) shown as summaries only. "
            f"Full data: {len(high_med)} high/medium + {len(compact_low)} low."
        )

        result_json = json.dumps(result_data, indent=2, ensure_ascii=False)

        # Check token budget; drop low-confidence if over
        if _estimate_tokens(result_json) > max_tokens and low:
            result_data["errors"] = high_med
            result_data["_note"] = (
                "Low-confidence errors dropped to fit token budget. "
                f"Full data: {len(high_med)} high/medium entries."
            )
            result_json = json.dumps(result_data, indent=2, ensure_ascii=False)

        return result_json

    # -- LLM call ----------------------------------------------------------

    async def _call_llm(self, system_prompt: str, user_prompt: str, timeout: float = 120.0) -> str:
        """Call the OpenRouter chat completions API and return the response text."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(self.api_url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
            return body["choices"][0]["message"]["content"]
