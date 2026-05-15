"""LLM vision verifier for candidate error classification.

Uses a vision-capable LLM to look at scan page images and classify
differences between PG text and the scan.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from pathlib import Path
from typing import Optional

import httpx

from gerrata.models import CandidateError, Error, Verdict

logger = logging.getLogger(__name__)

# No model name mapping needed — pass model names directly to OpenRouter
MODEL_NAME_MAP = {}

DEFAULT_SYSTEM_PROMPT = """You are a page transcription specialist. Your job is to examine a page scan image and transcribe exactly what you see.

You will be given a passage from a published text (the "PG text") and a scan page image. Your task:

1. Locate the PG text passage on the scan page image.
2. Transcribe exactly what appears on the scan at that location — character by character, preserving the original spelling, punctuation, capitalization, hyphenation, and line breaks as they appear on the printed page.
3. Quote 20+ characters of surrounding text from the page image to confirm you found the right location.

Guidelines:
- Transcribe what the pixels show. Preserve archaic spelling, unusual hyphenation, ligatures, and period typography exactly.
- If the PG text and scan show the same characters, your transcription will match the PG text.
- If they differ, your transcription will show what the scan actually has — make no corrections or editorial judgments.
- If the passage is unclear, illegible, or you cannot locate it on the page, respond with "unable_to_verify".

Respond in JSON format:
{"transcription": "...", "image_evidence": "...", "unable_to_verify": false}
"""


class VisionVerifier:
    """Verify candidate errors using an LLM vision model."""

    def __init__(
        self,
        api_url: str = "",
        api_key: str = "",
        model: str = "",
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        timeout: float = 60.0,
        max_retries: int = 3,
        base_delay: float = 2.0,
        concurrency: int = 1,
    ):
        """Initialize vision verifier.

        Args:
            api_url: Base URL for the LLM API (e.g., "https://api.openai.com/v1/chat/completions")
            api_key: API key for authentication.
            model: Model name (e.g., "gpt-4o", "claude-3.5-sonnet").
            system_prompt: System prompt for the verifier.
            timeout: Request timeout in seconds.
            max_retries: Maximum number of retries for rate limit errors.
            base_delay: Base delay in seconds for exponential backoff.
            concurrency: Number of concurrent API calls (default: 1).
        """
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.concurrency = concurrency

    def is_configured(self) -> bool:
        """Check if the verifier has required configuration."""
        return bool(self.api_url and self.model)

    async def verify_error(
        self,
        error: CandidateError,
        scan_image_path: Optional[Path] = None,
        pg_context: str = "",
    ) -> Error:
        """Verify a single candidate error using the LLM vision model.

        Args:
            error: The candidate error to verify.
            scan_image_path: Path to the scan page image (PNG/JPEG).
            pg_context: Additional PG text context around the error.

        Returns:
            Error with verdict, confidence, and reasoning.
        """
        if not self.is_configured():
            logger.warning("Vision verifier not configured, returning unable_to_verify")
            return Error(
                candidate=error,
                verdict=Verdict.UNABLE_TO_VERIFY,
                confidence=0.0,
                reasoning="Vision verifier not configured",
            )

        if not scan_image_path or not scan_image_path.exists():
            logger.debug(f"No scan image for page {error.scan_page}, skipping vision verify")
            return Error(
                candidate=error,
                verdict=Verdict.UNABLE_TO_VERIFY,
                confidence=0.0,
                reasoning="No scan page image available",
            )

        # Build the user prompt
        user_prompt = self._build_prompt(error, pg_context)

        # Build the API request
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Embed system prompt in user message for Z.AI API compatibility
        full_prompt = f"{self.system_prompt}\n\n{user_prompt}"
        messages = [
            {"role": "user", "content": self._build_multimodal_content(full_prompt, scan_image_path)},
        ]

        payload = {
            "model": MODEL_NAME_MAP.get(self.model, self.model),
            "messages": messages,
            "max_tokens": 500,
            "temperature": 0.1,
        }

        # Try with retry logic for rate limiting
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        self.api_url,
                        json=payload,
                        headers=headers,
                    )
                    
                    # Handle rate limiting with Retry-After header
                    if resp.status_code == 429:
                        if attempt < self.max_retries:
                            retry_after = resp.headers.get("Retry-After")
                            if retry_after:
                                try:
                                    delay = float(retry_after)
                                except ValueError:
                                    delay = self.base_delay * (2 ** attempt)
                            else:
                                delay = self.base_delay * (2 ** attempt)
                            
                            logger.warning(f"Rate limited (429), retrying in {delay:.1f}s (attempt {attempt + 1}/{self.max_retries})")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            logger.error(f"Max retries exceeded for rate limit errors")
                    
                    resp.raise_for_status()
                    data = resp.json()

                # Parse the response
                return self._parse_response(data, error)

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    logger.warning(f"HTTP 429 error, retrying in {delay:.1f}s (attempt {attempt + 1}/{self.max_retries})")
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(f"Vision API error: {e}")
                    return Error(
                        candidate=error,
                        verdict=Verdict.UNABLE_TO_VERIFY,
                        confidence=0.0,
                        reasoning=f"API error: {e}",
                    )
            except httpx.HTTPError as e:
                logger.error(f"Vision API error: {e}")
                return Error(
                    candidate=error,
                    verdict=Verdict.UNABLE_TO_VERIFY,
                    confidence=0.0,
                    reasoning=f"API error: {e}",
                )
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to parse vision API response: {e}")
                return Error(
                    candidate=error,
                    verdict=Verdict.UNABLE_TO_VERIFY,
                    confidence=0.0,
                    reasoning=f"Response parse error: {e}",
                )

        # Should not reach here, but just in case
        return Error(
            candidate=error,
            verdict=Verdict.UNABLE_TO_VERIFY,
            confidence=0.0,
            reasoning="Max retries exceeded",
        )

    async def verify_batch(
        self,
        errors: list[CandidateError],
        get_image_path=None,
        get_pg_context=None,
    ) -> list[Error]:
        """Verify multiple candidate errors.

        Args:
            errors: List of candidate errors.
            get_image_path: Optional callable(error) -> Path to get scan image.
            get_pg_context: Optional callable(error) -> str to get PG context.

        Returns:
            List of Error objects with verdicts.
        """
        results = []
        for error in errors:
            image_path = get_image_path(error) if get_image_path else None
            pg_context = get_pg_context(error) if get_pg_context else ""
            result = await self.verify_error(error, image_path, pg_context)
            results.append(result)
        return results

    async def verify_batch_per_page(
        self,
        errors: list[CandidateError],
        get_image_path=None,
        get_pg_context=None,
    ) -> list[Error]:
        """Verify multiple candidate errors grouped by scan page with concurrency control.

        This method groups all candidate errors by their scan_page and sends
        a single API call per page with all items from that page. This is
        much more efficient than verifying each error individually.

        Args:
            errors: List of candidate errors.
            get_image_path: Optional callable(error) -> Path to get scan image.
            get_pg_context: Optional callable(error) -> str to get PG context.

        Returns:
            List of Error objects with verdicts, in the same order as input.
        """
        if not self.is_configured():
            logger.warning("Vision verifier not configured, returning unable_to_verify")
            return [Error(
                candidate=error,
                verdict=Verdict.UNABLE_TO_VERIFY,
                confidence=0.0,
                reasoning="Vision verifier not configured",
            ) for error in errors]

        if self.concurrency > 1:
            logger.info(f"Using {self.concurrency} concurrent API calls for verification")

        # Group errors by scan page
        from collections import defaultdict
        page_groups: dict[int, list[tuple[int, CandidateError]]] = defaultdict(list)
        for idx, error in enumerate(errors):
            page_groups[error.scan_page].append((idx, error))

        # Process each page group with concurrency control
        import asyncio

        semaphore = asyncio.Semaphore(self.concurrency)
        results: list[Error] = [None] * len(errors)

        async def process_page_group(scan_page, items):
            async with semaphore:
                # Get the first error to retrieve image path
                first_error = items[0][1]
                image_path = get_image_path(first_error) if get_image_path else None

                if not image_path or not image_path.exists():
                    logger.debug(f"No scan image for page {scan_page}, marking all as unable_to_verify")
                    page_results = [
                        Error(
                            candidate=error,
                            verdict=Verdict.UNABLE_TO_VERIFY,
                            confidence=0.0,
                            reasoning="No scan page image available",
                        )
                        for idx, error in items
                    ]
                    return (scan_page, items, page_results)

                # Get PG context for each error
                items_with_context = []
                for idx, error in items:
                    pg_context = get_pg_context(error) if get_pg_context else ""
                    items_with_context.append((idx, error, pg_context))

                # Verify all items on this page in one API call
                page_results = await self._verify_page_batch(image_path, items_with_context)
                return (scan_page, items, page_results)

        # Create tasks for all page groups
        tasks = [process_page_group(page, items) for page, items in page_groups.items()]
        group_results = await asyncio.gather(*tasks)

        # Reconstruct results in original order
        pages_processed = 0
        items_processed = 0
        for scan_page, items, page_results in group_results:
            # Store results in the correct order
            for result_idx, error_result in zip([idx for idx, _ in items], page_results):
                results[result_idx] = error_result

            pages_processed += 1
            items_processed += len(items)
            logger.info(f"Verified page {scan_page}: {len(items)} items (total: {items_processed}/{len(errors)} items across {pages_processed} pages)")

        return results

    async def _verify_page_batch(
        self,
        image_path: Path,
        items: list[tuple[int, CandidateError, str]],
    ) -> list[Error]:
        """Verify all candidate errors from a single page in one API call.

        Args:
            image_path: Path to the scan page image.
            items: List of (index, error, pg_context) tuples.

        Returns:
            List of Error objects with verdicts, in the same order as input items.
        """
        # Build the batch prompt
        user_prompt = self._build_batch_prompt(items)

        # Build the API request
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Embed system prompt in user message for Z.AI API compatibility
        full_prompt = f"{self.system_prompt}\n\n{user_prompt}"
        messages = [
            {"role": "user", "content": self._build_multimodal_content(full_prompt, image_path)},
        ]

        payload = {
            "model": MODEL_NAME_MAP.get(self.model, self.model),
            "messages": messages,
            "max_tokens": 2000,  # Increased for batch responses
            "temperature": 0.1,
        }

        # Try with retry logic for rate limiting
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        self.api_url,
                        json=payload,
                        headers=headers,
                    )
                    
                    # Handle rate limiting with Retry-After header
                    if resp.status_code == 429:
                        if attempt < self.max_retries:
                            retry_after = resp.headers.get("Retry-After")
                            if retry_after:
                                try:
                                    delay = float(retry_after)
                                except ValueError:
                                    delay = self.base_delay * (2 ** attempt)
                            else:
                                delay = self.base_delay * (2 ** attempt)
                            
                            logger.warning(f"Rate limited (429), retrying in {delay:.1f}s (attempt {attempt + 1}/{self.max_retries})")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            logger.error(f"Max retries exceeded for rate limit errors")
                    
                    resp.raise_for_status()
                    data = resp.json()

                # Parse the batch response
                return self._parse_batch_response(data, items)

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    logger.warning(f"HTTP 429 error, retrying in {delay:.1f}s (attempt {attempt + 1}/{self.max_retries})")
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(f"Vision API error: {e}")
                    return self._create_unable_to_verify_errors(items, f"API error: {e}")
            except httpx.HTTPError as e:
                logger.error(f"Vision API error: {e}")
                return self._create_unable_to_verify_errors(items, f"API error: {e}")
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to parse vision API response: {e}")
                return self._create_unable_to_verify_errors(items, f"Response parse error: {e}")

        # Should not reach here, but just in case
        return self._create_unable_to_verify_errors(items, "Max retries exceeded")

    def _build_batch_prompt(self, items: list[tuple[int, CandidateError, str]]) -> str:
        """Build a batch prompt for multiple candidate errors on the same page."""
        parts = [
            "You will transcribe MULTIPLE passages from this scan page. ",
            "For EACH item, locate the passage on the page image and transcribe exactly what you see.\n\n",
        ]

        for idx, error, pg_context in items:
            parts.append(f"**Item {idx}:**\n")
            parts.append(f"PG text passage: {error.pg_text}\n")
            if len(error.pg_text.strip()) < 5:
                parts.append("This passage is too short to locate. Respond with unable_to_verify: true.\n")
            if pg_context:
                parts.append(f"Surrounding PG text: {pg_context[:200]}...\n")
            parts.append("\n")

        parts.append(
            "Respond with a JSON ARRAY containing one object per item, in the same order as above. "
            "Each object must have: index (int), transcription (str — exact characters from the scan), "
            "image_evidence (str — quote 20+ chars of surrounding text from the page), "
            "and unable_to_verify (bool).\n\n"
            "Instructions:\n"
            "1. For EACH item, locate the PG text passage on the scan page image.\n"
            "2. Transcribe the exact characters visible at that location — character by character, "
            "preserving spelling, punctuation, hyphenation, and line breaks.\n"
            "3. In image_evidence, quote surrounding text as it appears on the page to confirm location.\n"
            "4. If the passage is unclear or cannot be located, set unable_to_verify: true.\n\n"
            "Example format:\n"
            '[\n  {"index": 0, "transcription": "exactly what the scan shows", '
            '"image_evidence": "...surrounding text from page...", "unable_to_verify": false},\n'
            '  {"index": 1, "transcription": "...", '
            '"image_evidence": "...", "unable_to_verify": false}\n'
            "]\n"
        )

        return "".join(parts)

    def _derive_verdict_from_transcription(
        self, pg_text: str, transcription: str
    ) -> tuple[Verdict, float, str, str]:
        """Derive verdict by comparing PG text against scan transcription.

        Instead of asking the LLM to make editorial judgments, we compare
        the raw transcription to the PG text and classify mechanically.

        Returns: (verdict, confidence, reasoning, suggested_fix)
        """
        import difflib

        pg_clean = pg_text.strip()
        trans_clean = transcription.strip()

        if not trans_clean or pg_clean.lower() == trans_clean.lower():
            return (Verdict.PG_CORRECT, 0.95, "Scan transcription matches PG text.", "")

        # Compute normalized diff to ignore whitespace differences
        pg_norm = " ".join(pg_clean.split())
        trans_norm = " ".join(trans_clean.split())

        if pg_norm == trans_norm:
            return (Verdict.PG_CORRECT, 0.9,
                    "Scan matches PG text (whitespace differences only).", "")

        # They genuinely differ. Use SequenceMatcher ratio as the distance metric.
        sm = difflib.SequenceMatcher(None, pg_clean, trans_clean)
        ratio = sm.ratio()

        # Classification based on similarity ratio:
        # - High similarity (>=0.8): small edits (typos, single-char changes)
        #   → scan_correct, these are the real OCR differences worth reporting
        # - Medium similarity (>=0.6): moderate differences, could be edition variants
        #   → edition_variant
        # - Low similarity (<0.6): likely misaligned or substantially different text
        #   → ambiguous

        if ratio >= 0.8:
            return (Verdict.SCAN_CORRECT, 0.90,
                    f"Scan transcription differs from PG text ({ratio:.0%} similar).",
                    trans_clean)

        if ratio >= 0.6:
            return (Verdict.EDITION_VARIANT, 0.80,
                    f"Scan differs from PG ({ratio:.0%} similar). Possible edition variant.",
                    trans_clean)

        return (Verdict.AMBIGUOUS, 0.50,
                f"Scan transcription substantially differs from PG ({ratio:.0%} similar). Possible misalignment.",
                trans_clean)

    def _parse_batch_response(self, data: dict, items: list[tuple[int, CandidateError, str]]) -> list[Error]:
        """Parse a batch API response into Error objects."""
        results = []

        try:
            # Extract the response text
            choices = data.get("choices", [])
            if not choices:
                return self._create_unable_to_verify_errors(items, "Empty API response")

            message = choices[0].get("message", {})
            content = message.get("content", "")

            # Try to parse JSON array from the response
            result_array = self._extract_json_array(content)

            if not result_array:
                logger.error(f"Could not parse JSON array from response: {content[:200]}")
                return self._create_unable_to_verify_errors(items, "Could not parse JSON array response")

            # Build a map of index -> result
            results_map = {}
            for item in result_array:
                idx = item.get("index")
                if idx is not None:
                    results_map[idx] = item

            # Create Error objects in the same order as input items
            for idx, error, _ in items:
                if idx not in results_map:
                    # Missing result for this index
                    results.append(Error(
                        candidate=error,
                        verdict=Verdict.UNABLE_TO_VERIFY,
                        confidence=0.0,
                        reasoning="Missing result in batch response",
                    ))
                    continue

                item_result = results_map[idx]
                transcription = item_result.get("transcription", "")
                image_evidence = item_result.get("image_evidence", "")
                unable = item_result.get("unable_to_verify", False)

                if unable or not transcription:
                    results.append(Error(
                        candidate=error,
                        verdict=Verdict.UNABLE_TO_VERIFY,
                        confidence=0.0,
                        reasoning="Verifier could not locate or transcribe passage.",
                        image_evidence=image_evidence,
                    ))
                    continue

                # Derive verdict mechanically from transcription vs PG text
                verdict, confidence, reasoning, suggested_fix = (
                    self._derive_verdict_from_transcription(error.pg_text, transcription)
                )

                results.append(Error(
                    candidate=error,
                    verdict=verdict,
                    confidence=confidence,
                    reasoning=reasoning,
                    suggested_fix=suggested_fix,
                    image_evidence=image_evidence,
                ))

        except Exception as e:
            logger.error(f"Error parsing batch response: {e}")
            return self._create_unable_to_verify_errors(items, f"Parse error: {e}")

        return results

    def _extract_json_array(self, text: str) -> list:
        """Extract JSON array from LLM response text."""
        # Try to find JSON array in the response
        # First try direct parse
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # Try to find JSON array in code blocks
        json_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find any JSON array
        json_match = re.search(r"\[.*?\]", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        return []

    def _create_unable_to_verify_errors(
        self,
        items: list[tuple[int, CandidateError, str]],
        reason: str,
    ) -> list[Error]:
        """Create unable-to-verify errors for all items."""
        return [
            Error(
                candidate=error,
                verdict=Verdict.UNABLE_TO_VERIFY,
                confidence=0.0,
                reasoning=reason,
            )
            for _, error, _ in items
        ]

    def _build_prompt(self, error: CandidateError, pg_context: str = "") -> str:
        """Build the user prompt for the vision model."""
        parts = [
            "Locate this PG text passage on the scan page image and transcribe exactly what you see:\n\n",
            f"**PG text passage:** {error.pg_text}\n",
        ]
        if len(error.pg_text.strip()) < 5:
            parts.append(
                "\nThis passage is too short to reliably locate on a page image. "
                "Respond with unable_to_verify: true.\n"
            )
        if pg_context:
            parts.append(f"\n**Surrounding PG text:**\n{pg_context}\n")
        parts.append(
            "\nFind the passage on the scan page. Transcribe the exact characters visible at that "
            "location — character by character, preserving spelling, punctuation, and hyphenation. "
            "Include 20+ characters of surrounding text from the page in your image_evidence.\n"
        )
        return "".join(parts)

    def _build_multimodal_content(self, prompt: str, image_path: Path) -> list[dict]:
        """Build multimodal content with text and image for the API request."""
        content = [{"type": "text", "text": prompt}]

        # Add image
        image_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        suffix = image_path.suffix.lower().lstrip(".")
        media_type = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
        }.get(suffix, "image/png")

        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{media_type};base64,{image_data}",
            },
        })

        return content

    def _parse_response(self, data: dict, error: CandidateError) -> Error:
        """Parse the LLM API response into an Error object."""
        try:
            # Extract the response text
            choices = data.get("choices", [])
            if not choices:
                return Error(candidate=error, verdict=Verdict.UNABLE_TO_VERIFY,
                           confidence=0.0, reasoning="Empty API response")

            message = choices[0].get("message", {})
            content = message.get("content", "")

            # Try to parse JSON from the response
            result = self._extract_json(content)

            transcription = result.get("transcription", "")
            image_evidence = result.get("image_evidence", "")
            unable = result.get("unable_to_verify", False)

            if unable or not transcription:
                return Error(
                    candidate=error,
                    verdict=Verdict.UNABLE_TO_VERIFY,
                    confidence=0.0,
                    reasoning="Verifier could not locate or transcribe passage.",
                    image_evidence=image_evidence,
                )

            # Derive verdict mechanically from transcription vs PG text
            verdict, confidence, reasoning, suggested_fix = (
                self._derive_verdict_from_transcription(error.pg_text, transcription)
            )

            return Error(
                candidate=error,
                verdict=verdict,
                confidence=confidence,
                reasoning=reasoning,
                suggested_fix=suggested_fix,
                image_evidence=image_evidence,
            )

        except Exception as e:
            logger.error(f"Error parsing response: {e}")
            return Error(candidate=error, verdict=Verdict.UNABLE_TO_VERIFY,
                       confidence=0.0, reasoning=f"Parse error: {e}")

    def _extract_json(self, text: str) -> dict:
        """Extract JSON object from LLM response text."""
        # Try to find JSON in the response
        # First try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON in code blocks
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find any JSON object
        json_match = re.search(r"\{[^}]+\}", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        return {}
