"""Vision-first aligner: transcribe page images and match to PG text.

Replaces the OCR-text-based coarse aligner with a vision model approach:
1. For each page image, send to vision model for transcription
2. Match transcribed text against PG text paragraphs using fuzzy matching
3. Return Alignment objects with transcribed text included

The vision model is accessed via the OpenClaw image tool or direct API call.
Default model: zai/glm-4.6v, fallback: zai/glm-4.5v.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default transcription prompt
TRANSCRIPTION_PROMPT = (
    "Transcribe all the text on this page image. "
    "Return ONLY the transcribed text, nothing else. "
    "Preserve original spelling, punctuation, and line breaks. "
    "Do not add any commentary or formatting."
)

# Default API configuration
# Z.AI native GLM API — this endpoint supports vision (unlike the OpenAI-compatible proxy)
DEFAULT_API_URL = "https://api.z.ai/api/paas/v4/chat/completions"
DEFAULT_API_KEY = os.environ.get("ZAI_API_KEY", "")
DEFAULT_MODELS = ["zai/glm-4.6v", "zai/glm-4.5v"]

# Map from "zai/" prefixed model names to actual API model names
MODEL_NAME_MAP = {
    "zai/glm-4.6v": "glm-4.6v",
    "zai/glm-4.5v": "glm-4.5v",
    "zai/glm-4.6v-flashx": "glm-4.6v-flashx",
    "zai/glm-ocr": "glm-ocr",
}


def strip_paratext(text: str) -> str:
    """Remove paratext from OCR/vision transcription: headers, page numbers, running feet.

    PG texts never include page headers, page numbers, running feet, or short decorative
    lines. Removing these before matching dramatically improves alignment quality.
    """
    lines = text.split("\n")
    cleaned = []

    for line in lines:
        stripped = line.strip()

        # Skip empty lines but preserve paragraph breaks
        if not stripped:
            cleaned.append("")
            continue

        # Skip standalone page numbers (e.g., "57", "Page 88", "146")
        if re.match(r"^[\d]+$", stripped) or re.match(r"^Page\s+\d+$", stripped):
            continue

        # Skip page number + header combos (e.g., "57 DR. JEKYLL AND MR. HYDE")
        if re.match(r"^\d+\s+[A-Z]", stripped) and len(stripped) < 60:
            continue

        # Skip ALL-CAPS lines that look like headers/running feet
        # (short, uppercase-heavy, likely book title repeated at page top/bottom)
        if (stripped.isupper() and
            len(stripped) < 80 and
            sum(1 for c in stripped if c.isalpha()) > len(stripped) * 0.4 and
            not stripped.endswith((".", '"', "!", "?"))):
            continue

        # Skip very short decorative lines (< 5 chars of actual text)
        alpha_count = sum(1 for c in stripped if c.isalpha())
        if alpha_count < 5:
            continue

        # Skip illustration markers
        if re.match(r"^\[.*[Ii]llustration", stripped):
            continue

        cleaned.append(stripped)

    # Collapse multiple blank lines to max 2
    result_lines = []
    blank_count = 0
    for line in cleaned:
        if not line:
            blank_count += 1
            if blank_count <= 2:
                result_lines.append(line)
        else:
            blank_count = 0
            result_lines.append(line)

    # Strip leading/trailing whitespace per line, join with single newline
    return "\n".join(line.strip() for line in result_lines).strip()


def normalize_for_matching(text: str) -> str:
    """Normalize text for fuzzy matching: lowercase, strip punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFC", text)
    text = text.lower()
    # Remove punctuation (keep alphanumeric and spaces)
    text = re.sub(r"[^\w\s]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def chunk_text_for_matching(text: str, min_length: int = 40, max_length: int = 300) -> list[str]:
    """Split text into overlapping chunks suitable for matching.

    Uses sentence boundaries where possible, falls back to fixed-size chunks.
    """
    # Split on sentence boundaries
    sentence_end = re.compile(r"(?<=[.!?;])\s+")
    sentences = sentence_end.split(text)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        if current_len + len(sentence) > max_length and current:
            chunks.append(" ".join(current))
            # Overlap: keep last sentence for context
            if len(current) > 1:
                current = [current[-1]]
                current_len = len(current[0])
            else:
                current = []
                current_len = 0

        current.append(sentence)
        current_len += len(sentence)

    if current:
        chunks.append(" ".join(current))

    # Filter too-short chunks
    chunks = [c for c in chunks if len(c.strip()) >= min_length]

    return chunks


@dataclass
class PageTranscription:
    """Result of transcribing a single page image."""

    page_num: int
    image_path: Path
    transcription: str
    transcription_cleaned: str = ""
    model_used: str = ""
    success: bool = True
    error: str = ""


@dataclass
class VisionAlignmentResult:
    """Result of aligning a page to PG text."""

    alignment: "Alignment"  # noqa: F821 - forward ref
    transcription: PageTranscription
    matched_pg_chunks: list[str] = field(default_factory=list)
    best_score: float = 0.0
    anchored: bool = True  # True if RETAS found unique word anchors; False for brute-force only


class VisionTranscriber:
    """Transcribe page images using a vision LLM."""

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        api_key: str = "",
        models: list[str] | None = None,
        prompt: str = TRANSCRIPTION_PROMPT,
        timeout: float = 120.0,
        ocr_engine: str = "vision",
        cache_file: str | Path | None = None,
        disable_cache: bool = False,
        concurrency: int = 1,
    ):
        """Initialize transcriber.

        Args:
            api_url: API endpoint URL.
            api_key: API key for authentication.
            models: List of model names to try (primary, then fallbacks).
            prompt: System/user prompt for transcription.
            timeout: Request timeout in seconds.
            ocr_engine: "tesseract" (local, free) or "vision" (LLM API).
            cache_file: Path to transcription cache file (default: cache/transcription_cache.json).
            disable_cache: If True, disable all caching.
            concurrency: Number of concurrent API calls (default: 1).
        """
        self.api_url = api_url
        self.api_key = api_key or DEFAULT_API_KEY
        self.models = models or DEFAULT_MODELS
        self.prompt = prompt
        self.timeout = timeout
        self.ocr_engine = ocr_engine
        self.cache_file = Path(cache_file) if cache_file else None
        self.disable_cache = disable_cache
        self.concurrency = concurrency
        self.cache_data = self._load_cache() if not disable_cache and self.cache_file else {}
        self.cache_stats = {"hits": 0, "misses": 0, "saves": 0}

    def _load_cache(self) -> dict:
        """Load transcription cache from disk."""
        if not self.cache_file or not self.cache_file.exists():
            return {"version": 1, "model": "", "pages": {}}

        try:
            with open(self.cache_file, "r") as f:
                cache = json.load(f)
                # Validate cache structure
                if not isinstance(cache, dict) or "pages" not in cache:
                    logger.warning(f"Invalid cache file {self.cache_file}, starting fresh")
                    return {"version": 1, "model": "", "pages": {}}
                return cache
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load cache {self.cache_file}: {e}")
            return {"version": 1, "model": "", "pages": {}}

    def _save_cache(self) -> None:
        """Save transcription cache to disk."""
        if not self.cache_file or self.disable_cache:
            return

        try:
            # Ensure parent directory exists
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            # Write atomically
            temp_file = self.cache_file.with_suffix(".tmp")
            with open(temp_file, "w") as f:
                json.dump(self.cache_data, f, indent=2)
            temp_file.replace(self.cache_file)
        except IOError as e:
            logger.warning(f"Failed to save cache {self.cache_file}: {e}")

    def _compute_image_hash(self, image_path: Path) -> str:
        """Compute SHA-256 hash of an image file."""
        hash_sha256 = hashlib.sha256()
        with open(image_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return f"sha256:{hash_sha256.hexdigest()}"

    def _get_cached_transcription(self, image_path: Path, model: str) -> Optional[str]:
        """Check if we have a cached transcription for this image."""
        if self.disable_cache or not self.cache_file:
            return None

        filename = image_path.name
        file_hash = self._compute_image_hash(image_path)

        # Check cache entry
        if filename in self.cache_data.get("pages", {}):
            entry = self.cache_data["pages"][filename]
            # Validate hash and model match
            if (entry.get("hash") == file_hash and
                entry.get("model") == model and
                entry.get("success", False)):
                self.cache_stats["hits"] += 1
                return entry.get("text", "")

        self.cache_stats["misses"] += 1
        return None

    def _cache_transcription(self, image_path: Path, model: str, text: str, success: bool) -> None:
        """Cache a transcription result."""
        if self.disable_cache or not self.cache_file:
            return

        filename = image_path.name
        file_hash = self._compute_image_hash(image_path)

        # Initialize cache structure if needed
        if "pages" not in self.cache_data:
            self.cache_data["pages"] = {}

        # Store entry
        self.cache_data["pages"][filename] = {
            "hash": file_hash,
            "success": success,
            "text": text if success else "",
            "model": model,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        self.cache_stats["saves"] += 1
        # Write immediately for crash resilience
        self._save_cache()

    def _tesseract_ocr(self, image_path: Path) -> str:
        """Run Tesseract OCR locally. Free, fast, no API needed."""
        import subprocess
        try:
            result = subprocess.run(
                ["tesseract", str(image_path), "stdout", "--psm", "6"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return ""
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.debug(f"Tesseract failed: {e}")
            return ""

    async def transcribe_page(self, image_path: Path, page_num: int = 0) -> PageTranscription:
        """Transcribe a single page image.

        Uses Tesseract by default (local, free), falls back to LLM vision API.

        Args:
            image_path: Path to the page PNG image.
            page_num: Page number for tracking.

        Returns:
            PageTranscription with the transcribed text.
        """
        if not image_path.exists():
            return PageTranscription(
                page_num=page_num,
                image_path=image_path,
                transcription="",
                success=False,
                error=f"Image not found: {image_path}",
            )

        # For vision models, check cache first
        if self.ocr_engine == "vision":
            primary_model = self.models[0] if self.models else "unknown"
            cached_text = self._get_cached_transcription(image_path, primary_model)
            if cached_text is not None:
                # Cache hit
                cleaned = strip_paratext(cached_text)
                logger.info(f"  Cache hit: {image_path.name} (cached, {len(cached_text)} chars)")
                return PageTranscription(
                    page_num=page_num,
                    image_path=image_path,
                    transcription=cached_text,
                    transcription_cleaned=cleaned,
                    model_used=primary_model,
                    success=True,
                )

        # Try Tesseract first (local, free, fast)
        if self.ocr_engine == "tesseract":
            text = self._tesseract_ocr(image_path)
            if text and len(text.strip()) > 20:
                cleaned = strip_paratext(text)
                return PageTranscription(
                    page_num=page_num,
                    image_path=image_path,
                    transcription=text,
                    transcription_cleaned=cleaned,
                    model_used="tesseract",
                    success=True,
                )
            logger.debug(f"Tesseract returned insufficient text for page {page_num}, trying vision API")

        # Fall back to LLM vision API
        import httpx

        # Read and encode image
        image_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        suffix = image_path.suffix.lower().lstrip(".")
        media_type = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
        }.get(suffix, "image/png")

        import asyncio

        max_retries = 3
        for model in self.models:
            # Map model name (zai/glm-4.6v → glm-4.6v) for the native API
            api_model = MODEL_NAME_MAP.get(model, model)
            for attempt in range(max_retries + 1):
                try:
                    transcription = await self._call_api(
                        model=api_model,
                        image_data=image_data,
                        media_type=media_type,
                    )
                    if transcription and len(transcription.strip()) > 10:
                        cleaned = strip_paratext(transcription.strip())
                        # Cache the result
                        if self.ocr_engine == "vision":
                            self._cache_transcription(image_path, model, transcription.strip(), True)
                            logger.info(f"  Cache saved: {image_path.name} ({len(transcription.strip())} chars)")
                        return PageTranscription(
                            page_num=page_num,
                            image_path=image_path,
                            transcription=transcription.strip(),
                            transcription_cleaned=cleaned,
                            model_used=model,
                            success=True,
                        )
                except Exception as e:
                    error_str = str(e)
                    if "429" in error_str and attempt < max_retries:
                        wait = 5 * (attempt + 1)  # 5s, 10s, 15s
                        logger.debug(f"Model {model} rate-limited for page {page_num}, retry {attempt+1}/{max_retries} in {wait}s")
                        await asyncio.sleep(wait)
                        continue
                    logger.debug(f"Model {model} failed for page {page_num}: {e}")
                    break  # non-429 error → try next model

        # Cache the failure
        if self.ocr_engine == "vision":
            primary_model = self.models[0] if self.models else "unknown"
            self._cache_transcription(image_path, primary_model, "", False)

        return PageTranscription(
            page_num=page_num,
            image_path=image_path,
            transcription="",
            success=False,
            error="All models failed",
        )

    async def transcribe_pages(
        self,
        image_paths: list[Path],
        skip_existing: bool = True,
    ) -> list[PageTranscription]:
        """Transcribe multiple page images with concurrency control.

        Args:
            image_paths: List of page image paths.
            skip_existing: If True, skip pages that already have cached transcriptions.

        Returns:
            List of PageTranscription objects.
        """
        import asyncio

        if self.concurrency > 1:
            logger.info(f"Using {self.concurrency} concurrent API calls for transcription")

        semaphore = asyncio.Semaphore(self.concurrency)

        async def process_page(i, path):
            async with semaphore:
                logger.info(f"Transcribing page {i+1}/{len(image_paths)}: {path.name}")
                result = await self.transcribe_page(path, page_num=i)
                if result.success:
                    # Don't print char count for cache hits (already logged)
                    if not (self.ocr_engine == "vision" and
                            self._get_cached_transcription(path, result.model_used)):
                        logger.info(f"  → {len(result.transcription)} chars via {result.model_used}")
                else:
                    logger.warning(f"  → Failed: {result.error}")
                return (i, result)

        tasks = [process_page(i, path) for i, path in enumerate(image_paths)]
        results_raw = await asyncio.gather(*tasks)

        # Sort by original index to maintain ordering
        results_raw.sort(key=lambda x: x[0])
        results = [r[1] for r in results_raw]

        # Print summary statistics
        if not self.disable_cache and self.cache_stats["hits"] + self.cache_stats["misses"] > 0:
            fresh = self.cache_stats["saves"]
            cached = self.cache_stats["hits"]
            total = len(results)
            logger.info(f"Transcribed {total}/{len(image_paths)} pages ({cached} from cache, {fresh} fresh)")

        return results

    async def _call_api(
        self,
        model: str,
        image_data: str,
        media_type: str,
    ) -> str:
        """Call the vision API for transcription."""
        import httpx
        import asyncio

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Check if this is a GLM-OCR model
        is_glm_ocr = "glm-ocr" in model.lower() or "glm_ocr" in model.lower()

        if is_glm_ocr:
            # GLM-OCR uses a different API endpoint and format
            # Extract base URL and convert to layout_parsing endpoint
            base_url = self.api_url
            if "/chat/completions" in base_url:
                # Convert chat/completions URL to layout_parsing URL
                base_url = base_url.replace("/chat/completions", "/layout_parsing")
            elif not base_url.endswith("/layout_parsing"):
                # If it's not already a layout_parsing URL, try to construct it
                # from the base URL
                parts = base_url.split("/api/paas/v4/")
                if len(parts) == 2:
                    base_url = f"{parts[0]}/api/paas/v4/layout_parsing"
                else:
                    # Fallback: just append layout_parsing
                    base_url = base_url.rstrip("/") + "/layout_parsing"

            # GLM-OCR request format
            payload = {
                "model": "glm-ocr",
                "file": f"data:{media_type};base64,{image_data}",
            }

            # Retry logic for rate limiting
            max_retries = 3
            base_delay = 2.0

            for attempt in range(max_retries):
                try:
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        resp = await client.post(
                            base_url,
                            json=payload,
                            headers=headers,
                        )

                        # Handle rate limiting
                        if resp.status_code == 429:
                            if attempt < max_retries - 1:
                                retry_after = resp.headers.get("Retry-After")
                                if retry_after:
                                    try:
                                        delay = float(retry_after)
                                    except ValueError:
                                        delay = base_delay * (2 ** attempt)
                                else:
                                    delay = base_delay * (2 ** attempt)

                                logger.warning(f"GLM-OCR rate limited, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                                await asyncio.sleep(delay)
                                continue
                            else:
                                raise ValueError(f"GLM-OCR API rate limit exceeded after {max_retries} retries")

                        resp.raise_for_status()
                        data = resp.json()

                    # Extract text from GLM-OCR response
                    if "md_results" in data:
                        return data["md_results"]
                    elif "result" in data:
                        return data["result"]
                    else:
                        raise ValueError(f"GLM-OCR response missing expected fields: {data}")

                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429 and attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(f"GLM-OCR HTTP 429, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        raise ValueError(f"GLM-OCR API error: {e}")
                except Exception as e:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(f"GLM-OCR error, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries}): {e}")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        raise ValueError(f"GLM-OCR failed after {max_retries} retries: {e}")

            # Should not reach here
            raise ValueError("GLM-OCR failed: max retries exceeded")

        else:
            # Standard chat/completions format (GLM-4.6V, GPT-4V, etc.)
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{image_data}",
                            },
                        },
                    ],
                }
            ]

            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": 4000,
                "temperature": 0.1,
            }

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

            # Extract text from response
            choices = data.get("choices", [])
            if not choices:
                raise ValueError("Empty API response")
            return choices[0].get("message", {}).get("content", "")


class VisionAligner:
    """Align PG text to scan pages using vision transcription + fuzzy matching.

    Pipeline:
    1. Transcribe each page image via vision model
    2. Chunk PG text into matching-sized segments
    3. Match transcription against PG chunks using SequenceMatcher
    4. Return Alignment objects
    """

    def __init__(
        self,
        match_threshold: float = 0.5,
        min_chunk_length: int = 30,
        min_match_chars: int = 20,
    ):
        """Initialize vision aligner.

        Args:
            match_threshold: Minimum SequenceMatcher ratio to consider a match.
            min_chunk_length: Minimum PG text chunk length for matching.
            min_match_chars: Minimum characters of transcribed text to require for matching.
        """
        self.match_threshold = match_threshold
        self.min_chunk_length = min_chunk_length
        self.min_match_chars = min_match_chars

    @staticmethod
    def _tokenize_words(text: str) -> list[str]:
        """Tokenize text into words (lowercase, alphanumeric)."""
        return re.findall(r'\b[a-z0-9]+\b', text.lower())

    @staticmethod
    def _word_positions(words: list[str]) -> dict[str, list[int]]:
        """Build a word→list-of-positions map."""
        positions: dict[str, list[int]] = {}
        for i, w in enumerate(words):
            positions.setdefault(w, []).append(i)
        return positions

    @staticmethod
    def _word_lcs(seq1: list[str], seq2: list[str]) -> set[str]:
        """Find LCS of two ordered word sequences using SequenceMatcher.

        Returns the set of words that participate in the LCS.
        """
        sm = SequenceMatcher(None, seq1, seq2)
        lcs_words: set[str] = set()
        for a_start, b_start, size in sm.get_matching_blocks():
            if size > 0:
                for i in range(size):
                    lcs_words.add(seq1[a_start + i])
        return lcs_words

    @staticmethod
    def _word_pos_to_char_pos(text: str, word_index: int) -> int | None:
        """Convert a word index to character position in the text."""
        for i, m in enumerate(re.finditer(r'\b[a-z0-9]+\b', text.lower())):
            if i == word_index:
                return m.start()
        return None

    @staticmethod
    def _word_idx_to_norm_char_pos(norm_text: str, word_idx: int) -> int:
        """Convert a word index (in normalized text) to character position in norm text.

        Returns the character position of the word_idx-th word in the normalized text.
        Falls back to 0 if the word_idx is out of range.
        """
        for i, m in enumerate(re.finditer(r'[a-z0-9]+', norm_text)):
            if i == word_idx:
                return m.start()
        return 0

    def _find_unique_word_anchor(
        self,
        trans_text: str,
        pg_text: str,
        search_start: int = 0,
        search_end: int = -1,
        min_word_len: int = 4,
    ) -> tuple[list[int], list[int], list[int]] | None:
        """Find anchor positions using RETAS-style unique word anchoring.

        Phase 1 of the RETAS algorithm:
        1. Tokenize both texts into words
        2. Find words that appear exactly once in each text (unique common words)
        3. Order unique words by their position in each text
        4. Compute LCS of the two ordered sequences to get correctly-ordered anchors
        5. Return the word-index positions of LCS anchors in both texts + char positions

        Args:
            trans_text: Transcription text (raw or normalized).
            pg_text: PG text (raw or normalized).
            search_start: Only consider PG words from this character offset.
            search_end: Only consider PG words up to this character offset (-1 = end).
            min_word_len: Minimum word length to consider as an anchor.

        Returns:
            Tuple of (anchor_pg_word_positions, anchor_trans_word_positions,
                      anchor_pg_char_positions) or None if no anchors found.
        """
        if search_end == -1:
            search_end = len(pg_text)

        trans_words = self._tokenize_words(trans_text)
        pg_words = self._tokenize_words(pg_text)

        if len(trans_words) < 3 or len(pg_words) < 3:
            return None

        # Build word→position maps
        trans_pos_map = self._word_positions(trans_words)
        pg_pos_map = self._word_positions(pg_words)

        # Find words unique in each text
        trans_unique = {
            w for w, positions in trans_pos_map.items()
            if len(positions) == 1 and len(w) >= min_word_len
        }
        pg_unique = {
            w for w, positions in pg_pos_map.items()
            if len(positions) == 1 and len(w) >= min_word_len
        }

        # Common unique words
        common_unique = trans_unique & pg_unique
        if not common_unique:
            return None

        # Apply search window constraint on PG side
        # Pre-compute char positions for all PG words
        pg_char_positions: dict[str, int] = {}
        for w in common_unique:
            if w in pg_pos_map and pg_pos_map[w]:
                cp = self._word_pos_to_char_pos(pg_text, pg_pos_map[w][0])
                if cp is not None:
                    pg_char_positions[w] = cp

        # Filter by search window
        common_unique = {
            w for w in common_unique
            if w in pg_char_positions
            and search_start <= pg_char_positions[w] < search_end
        }

        if not common_unique:
            return None

        # Order by position in each text
        ordered_trans = sorted(common_unique, key=lambda w: trans_pos_map[w][0])
        ordered_pg = sorted(common_unique, key=lambda w: pg_pos_map[w][0])

        # LCS to find correctly-ordered anchors
        lcs_words = self._word_lcs(ordered_trans, ordered_pg)

        if not lcs_words:
            return None

        # Get word-index positions of LCS anchors, PAIRED (not independently sorted)
        # Each LCS word maps to a specific position in transcription and a specific
        # position in PG — they must stay paired.
        anchor_pairs: list[tuple[int, int, int]] = []  # (pg_word_idx, trans_word_idx, pg_char)
        for w in lcs_words:
            if w in pg_pos_map and w in trans_pos_map and w in pg_char_positions:
                anchor_pairs.append((
                    pg_pos_map[w][0],
                    trans_pos_map[w][0],
                    pg_char_positions[w],
                ))

        # Sort by transcription position (reading order of the scan page)
        anchor_pairs.sort(key=lambda x: x[1])

        if not anchor_pairs:
            return None

        # Filter outlier anchors: in a single page (~500-800 chars), all correct
        # anchors should be within ~2000 chars of each other in PG text.
        # Anchors that are far apart are from different editions (e.g., "downright"
        # vs "down-right" — the word is unique in both texts but at wildly different
        # PG positions because the other occurrence is from a different chapter).
        if len(anchor_pairs) >= 2:
            pg_spread = max(p[2] for p in anchor_pairs) - min(p[2] for p in anchor_pairs)
            # If anchors span more than 3000 chars in PG but the transcription is
            # only ~500-800 chars, some anchors are wrong
            if pg_spread > 3000:
                # Keep only the cluster of anchors closest together
                # Use the first anchor as reference and keep those within 2000 chars
                ref_char = anchor_pairs[0][2]
                anchor_pairs = [p for p in anchor_pairs if abs(p[2] - ref_char) <= 2000]
                if not anchor_pairs:
                    return None

        anchor_pg_word_positions = [p[0] for p in anchor_pairs]
        anchor_trans_word_positions = [p[1] for p in anchor_pairs]
        anchor_pg_char_positions = [p[2] for p in anchor_pairs]

        if not anchor_pg_word_positions:
            return None

        return (
            anchor_pg_word_positions,
            anchor_trans_word_positions,
            anchor_pg_char_positions,
        )

    def _anchor_transcription(
        self,
        trans_norm: str,
        pg_norm: str,
        search_start: int = 0,
        search_end: int = -1,
    ) -> tuple[int | None, int]:
        """Anchor a transcription to PG text using unique n-gram matching.

        Legacy method kept as fallback when unique word anchoring fails.
        """
        if search_end == -1:
            search_end = len(pg_norm)
        pg_search = pg_norm[search_start:search_end]

        trans_words = trans_norm.split()
        if len(trans_words) < 3:
            return None, 0

        # Build index of all n-gram positions in PG text for efficient lookup
        # We try n-grams of sizes 4..8, longest first (most specific)
        pg_words = pg_search.split()

        # Find all n-gram positions in PG search region
        def find_ngram_positions(ngram: str, text: str) -> list[int]:
            """Find all positions of an n-gram in text."""
            positions = []
            start = 0
            while True:
                idx = text.find(ngram, start)
                if idx == -1:
                    break
                positions.append(idx)
                start = idx + 1
            return positions

        # Collect candidate anchors with their uniqueness and position
        candidates: list[tuple[int, int, bool, int]] = []  # (pg_pos, length, is_unique, ngram_size)

        for ngram_size in range(8, 3, -1):  # 8, 7, 6, 5, 4 — longest first
            for i in range(len(trans_words) - ngram_size + 1):
                ngram = " ".join(trans_words[i : i + ngram_size])
                positions = find_ngram_positions(ngram, pg_search)
                for pos in positions:
                    is_unique = len(positions) == 1
                    # Prefer unique, longer n-grams
                    candidates.append((pos + search_start, len(ngram), is_unique, ngram_size))
                # If we found a unique anchor with this ngram_size, that's good enough
                if any(c[2] for c in candidates if c[3] == ngram_size):
                    break
            # If we have any unique anchors from this size, we can stop trying shorter
            if any(c[2] for c in candidates):
                break

        if not candidates:
            return None, 0

        # Rank candidates: unique > non-unique, then longer n-gram, then longer anchor string
        def rank_key(c: tuple) -> tuple:
            pos, length, is_unique, ngram_size = c
            return (0 if is_unique else 1, -ngram_size, -length)

        candidates.sort(key=rank_key)
        best = candidates[0]
        return best[0], best[1]

    def _unique_word_anchor_to_position(
        self,
        anchor_pg_char_positions: list[int],
        anchor_trans_word_positions: list[int],
        trans_word_count: int,
        pg_text_len: int,
        trans_text_len: int,
    ) -> int:
        """Convert anchor positions to a single best alignment position in PG text.

        For cross-edition comparison (our use case), anchor spreads can be wildly
        misleading — a word unique in both texts may appear at very different PG
        positions because the editions differ. Instead of using spread-based
        interpolation (which fails when anchors disagree), we use a robust
        approach: take the median of all anchor-derived start estimates, which
        is resistant to outlier anchors from edition differences.

        Args:
            anchor_pg_char_positions: Character positions of anchors in PG text.
            anchor_trans_word_positions: Word positions of anchors in transcription.
            trans_word_count: Total word count of transcription.
            pg_text_len: Total length of PG text.
            trans_text_len: Character length of transcription.

        Returns:
            Best estimate of where the transcription starts in PG text (character offset).
        """
        if not anchor_pg_char_positions:
            return 0

        # For each anchor, estimate where the transcription starts
        # based on that anchor's position and its offset into the transcription.
        # Then take the MEDIAN of these estimates — robust to outlier anchors.
        start_estimates = []
        for pg_pos, trans_word in zip(anchor_pg_char_positions, anchor_trans_word_positions):
            if trans_word_count > 0 and trans_word > 0:
                # Assume roughly uniform character density
                chars_per_word = trans_text_len / trans_word_count
                estimate = pg_pos - (trans_word * chars_per_word)
                start_estimates.append(max(0, estimate))
            else:
                start_estimates.append(pg_pos)

        # Median is robust to outlier anchors from edition differences
        start_estimates.sort()
        mid = len(start_estimates) // 2
        if len(start_estimates) % 2 == 0:
            median_estimate = (start_estimates[mid - 1] + start_estimates[mid]) // 2
        else:
            median_estimate = start_estimates[mid]

        return max(0, int(median_estimate))

    def _score_windowed_match(
        self,
        trans_norm: str,
        pg_norm: str,
        pg_text: str,
        anchor_pos: int,
    ) -> tuple[float, int, int]:
        """Score transcription against PG text around an anchored position.

        Extracts a window of PG text centered on the anchor position and
        computes a fuzzy match score.

        Args:
            trans_norm: Normalized transcription text.
            pg_norm: Normalized PG text.
            pg_text: Raw PG text (for offset mapping).
            anchor_pos: Position of anchor in pg_norm.

        Returns:
            Tuple of (score, pg_start_raw, pg_end_raw).
        """
        trans_len = len(trans_norm)

        # Window size: transcription length × 2.5 to allow for edition differences
        window_size = max(500, trans_len * 3)
        window_start = max(0, anchor_pos - window_size // 4)
        window_end = min(len(pg_norm), anchor_pos + window_size)

        pg_window = pg_norm[window_start:window_end]

        # Try SequenceMatcher on the window
        score = SequenceMatcher(None, trans_norm, pg_window).ratio()

        # Also try matching against the paragraph that contains the anchor
        # Map pg_norm position back to pg_text position (approximate)
        # pg_norm strips punctuation but preserves character order roughly
        pg_start_approx = window_start
        pg_end_approx = min(len(pg_text), window_end + 200)  # Account for punctuation removal

        return score, pg_start_approx, pg_end_approx

    @staticmethod
    def _map_norm_to_raw(norm_pos: int, raw_text: str, raw_len: int) -> int:
        """Map a position in normalized text back to the original raw text.

        Normalization removes punctuation and collapses whitespace, so the
        normalized text is shorter. We approximate by finding the nearest
        alphanumeric character at or after the raw position corresponding
        to the normalized position.
        """
        if norm_pos <= 0:
            return 0
        # Simple approximation: norm_pos / norm_len * raw_len
        # This is rough but sufficient for offset estimation
        return min(int(norm_pos * raw_len / max(1, raw_len * 0.95)), raw_len - 1)

    @staticmethod
    def _build_norm_offset_map(raw_text: str) -> list[int]:
        """Build a mapping from normalized text positions to raw text positions.

        Returns a list where map[i] = position in raw_text corresponding to
        position i in normalized text.
        """
        norm_to_raw = []
        raw_pos = 0
        for ch in raw_text.lower():
            if ch.isalnum() or ch == " ":
                norm_to_raw.append(raw_pos)
            raw_pos += 1
        return norm_to_raw

    def _find_best_window(
        self,
        trans_norm: str,
        pg_norm: str,
        anchor_pos: int,
    ) -> tuple[int, int, float]:
        """Find the best scoring window of PG text around an anchor position.

        Tries multiple window sizes and positions around the anchor to find
        the highest SequenceMatcher ratio. The window is in pg_norm space.

        Args:
            trans_norm: Normalized transcription.
            pg_norm: Normalized PG text.
            anchor_pos: Anchor position in pg_norm.

        Returns:
            Tuple of (window_start, window_end, best_score).
        """
        trans_len = len(trans_norm)

        best_score = 0.0
        best_start = 0
        best_end = 0

        # Try multiple window sizes, from tight to loose
        # The window should be roughly the same size as the transcription
        window_sizes = [
            trans_len,           # Same size (best for exact matches)
            int(trans_len * 1.3),  # 30% larger (allows for headers/footers)
            int(trans_len * 1.6),  # 60% larger
            int(trans_len * 2.0),  # 2x (significant edition differences)
        ]

        for window_size in window_sizes:
            # Try sliding the window around the anchor
            # The anchor should be roughly in the first third of the transcription
            # (transcription starts near the top of the page)
            for offset_pct in [0.0, 0.1, 0.2, 0.3, -0.1]:
                offset = int(window_size * offset_pct)
                win_start = max(0, anchor_pos - offset)
                win_end = min(len(pg_norm), win_start + window_size)
                if win_end - win_start < min(100, trans_len * 0.5):
                    continue

                pg_window = pg_norm[win_start:win_end]
                score = SequenceMatcher(None, trans_norm, pg_window).ratio()

                # Weight: prefer windows closer to transcription length
                length_ratio = min(win_end - win_start, trans_len) / max(win_end - win_start, trans_len)
                weighted = score * (0.7 + 0.3 * length_ratio)

                if weighted > best_score:
                    best_score = score
                    best_start = win_start
                    best_end = win_end

        return best_start, best_end, best_score

    def align_transcription_to_pg(
        self,
        transcription: PageTranscription,
        pg_text: str,
        pg_paragraphs: list[str],
        scan_page: int = 0,
        search_start: int = 0,
        search_end: int = -1,
    ) -> VisionAlignmentResult | None:
        """Align a single page transcription to PG text.

        Uses n-gram anchoring to find the correct region of PG text,
        then scores the match within focused windows. Falls back to
        brute-force matching if anchoring fails.

        Args:
            transcription: The page transcription result.
            pg_text: Full PG body text.
            pg_paragraphs: PG paragraphs for offset tracking.
            scan_page: Scan page number.
            search_start: Only search PG text from this offset (for sequential constraint).
            search_end: Only search PG text up to this offset (-1 = end).

        Returns:
            VisionAlignmentResult if a match is found, None otherwise.
        """
        if not transcription.success or not transcription.transcription:
            return None

        # Use cleaned transcription (paratext stripped) for matching
        trans_text = transcription.transcription_cleaned or transcription.transcription
        trans_norm = normalize_for_matching(trans_text)

        if len(trans_norm) < self.min_match_chars:
            logger.debug(f"Page {scan_page}: transcription too short ({len(trans_norm)} chars)")
            return None

        # Normalize PG text once
        pg_norm = normalize_for_matching(pg_text)

        # Build offset map for norm→raw conversion
        norm_map = self._build_norm_offset_map(pg_text)

        # Determine search window from sequential constraint
        effective_start = search_start
        effective_end = search_end if search_end != -1 else len(pg_norm)

        best_score = 0.0
        best_pg_start = 0
        best_pg_end = 0
        best_match_len = 0
        was_anchored = False  # Track whether match came from RETAS/n-gram anchors

        # ── Phase 1: RETAS unique word anchoring (preferred) ──
        anchor_result = self._find_unique_word_anchor(
            trans_text, pg_text,
            search_start=effective_start,
            search_end=effective_end,
        )

        if anchor_result is not None:
            anchor_pg_word_positions, anchor_trans_word_positions, anchor_pg_char_positions = anchor_result

            # Estimate where the transcription starts in PG text.
            # Use norm-space positions to avoid raw→norm offset drift.
            pg_norm_words = re.findall(r'[a-z0-9]+', pg_norm)
            trans_word_count = len(self._tokenize_words(trans_text))
            anchor_pg_norm_char_positions = []
            for word_idx in anchor_pg_word_positions:
                if word_idx < len(pg_norm_words):
                    # Find norm char position of this word index
                    pos = 0
                    count = 0
                    for m in re.finditer(r'[a-z0-9]+', pg_norm):
                        if count == word_idx:
                            anchor_pg_norm_char_positions.append(m.start())
                            break
                        count += 1
                    else:
                        anchor_pg_norm_char_positions.append(0)

            estimated_norm_start = self._unique_word_anchor_to_position(
                anchor_pg_norm_char_positions,
                anchor_trans_word_positions,
                trans_word_count,
                len(pg_norm),
                len(trans_norm),
            )

            logger.debug(
                f"Page {scan_page}: RETAS anchor found with {len(anchor_pg_char_positions)} anchors, "
                f"estimated PG norm start ~{estimated_norm_start}"
            )

            # ── Phase 2: Sliding window best match (Solution B from RESEARCH-ALIGNMENT.md) ──
            # After anchoring, use a sliding window to find the *best-matching substring*
            # of the PG text against the transcription, rather than extrapolating from
            # anchor positions. This produces tighter windows that reduce "absent in scan" noise.
            trans_len = len(trans_norm)
            trans_word_count = len(self._tokenize_words(trans_text))

            if anchor_pg_char_positions:
                # Get the median anchor position as our starting point
                anchor_positions_sorted = sorted(anchor_pg_char_positions)
                median_idx = len(anchor_positions_sorted) // 2
                median_anchor_pos = anchor_positions_sorted[median_idx]

                # Also get the estimated start from anchor interpolation
                if anchor_trans_word_positions and trans_word_count > 0:
                    pg_words = self._tokenize_words(pg_text)
                    chars_per_word_est = len(pg_text) / max(1, len(pg_words))
                    first_trans_word = min(anchor_trans_word_positions)
                    first_anchor_raw = min(anchor_pg_char_positions)
                    estimated_start = first_anchor_raw - int(first_trans_word * chars_per_word_est)
                    estimated_start = max(0, estimated_start)
                else:
                    estimated_start = median_anchor_pos

                logger.debug(
                    f"Page {scan_page}: RETAS anchor found with {len(anchor_pg_char_positions)} anchors, "
                    f"median anchor at {median_anchor_pos}, estimated start ~{estimated_start}"
                )

                # Sliding window approach: try different start positions around the anchor
                # and score each to find the best matching window
                window_size = int(trans_len * 1.2)  # Allow 20% extra for edition differences
                best_sliding_score = 0.0
                best_sliding_start = 0
                best_sliding_end = 0

                # Search range: ±300 chars around estimated start, in 50-char increments
                # This covers the uncertainty in anchor positioning while being efficient
                search_range = 300
                step_size = 50

                for offset in range(-search_range, search_range + 1, step_size):
                    # Calculate window start position
                    start_pos = max(0, estimated_start + offset)

                    # Ensure the window doesn't go beyond PG text bounds
                    end_pos = min(len(pg_text), start_pos + window_size)

                    # Extract and normalize the PG window
                    pg_window = pg_text[start_pos:end_pos]
                    pg_window_norm = normalize_for_matching(pg_window)

                    # Score this window against the transcription
                    if len(pg_window_norm) >= trans_len * 0.5:  # Only score reasonably-sized windows
                        score = SequenceMatcher(None, trans_norm, pg_window_norm).ratio()

                        # Small preference for windows closer to the estimated position
                        # (reduces bias toward windows at the very start/end of text)
                        distance_penalty = abs(offset) / search_range * 0.05
                        adjusted_score = score - distance_penalty

                        if adjusted_score > best_sliding_score:
                            best_sliding_score = adjusted_score
                            best_sliding_start = start_pos
                            best_sliding_end = end_pos

                # If we found a good match with sliding window, use it
                if best_sliding_score > 0:
                    # Normalize the best window for final scoring
                    best_pg_raw = pg_text[best_sliding_start:best_sliding_end]
                    best_pg_norm = normalize_for_matching(best_pg_raw)
                    final_score = SequenceMatcher(None, trans_norm, best_pg_norm).ratio()

                    logger.debug(
                        f"  Sliding window: pg_text[{best_sliding_start}:{best_sliding_end}] "
                        f"(len={best_sliding_end - best_sliding_start}), score={final_score:.3f}"
                    )

                    best_pg_start = best_sliding_start
                    best_pg_end = best_sliding_end
                    best_ratio = final_score
                    weighted_score = final_score * (1.0 + 0.2 * min(1.0, (best_pg_end - best_pg_start) / 500.0))

                    if weighted_score > best_score:
                        best_score = weighted_score
                        best_match_len = best_pg_end - best_pg_start
                        best_pg_end = min(best_pg_end, len(pg_text))
                        was_anchored = True

                        logger.debug(
                            f"  RETAS final: pg_text[{best_pg_start}:{best_pg_end}], "
                            f"score={best_ratio:.3f}"
                        )
        # ── Fallback: N-gram anchoring if RETAS found no anchors ──
        if best_score == 0:
            anchor_pos, anchor_len = self._anchor_transcription(
                trans_norm, pg_norm,
                search_start=effective_start,
                search_end=effective_end,
            )

            if anchor_pos is not None:
                logger.debug(
                    f"Page {scan_page}: n-gram anchor at pg_norm[{anchor_pos}] "
                    f"(len={anchor_len}, search=[{effective_start}:{effective_end}])"
                )

                win_start, win_end, win_score = self._find_best_window(
                    trans_norm, pg_norm, anchor_pos
                )

                if win_score > self.match_threshold * 0.4:
                    best_score = win_score * (1.0 + 0.3 * min(1.0, (win_end - win_start) / 500.0))
                    best_match_len = win_end - win_start
                    was_anchored = True

                    if win_start < len(norm_map):
                        best_pg_start = norm_map[win_start]
                    else:
                        best_pg_start = len(pg_text) - 1
                    if win_end - 1 < len(norm_map) and win_end > 0:
                        best_pg_end = norm_map[win_end - 1] + 20
                    else:
                        best_pg_end = len(pg_text)
                    best_pg_end = min(best_pg_end, len(pg_text))

                # Paragraph-level matching near the n-gram anchor
                para_best_score = 0.0
                para_best_start = 0
                para_best_end = 0
                para_best_len = 0

                for i, para in enumerate(pg_paragraphs):
                    para_start_raw = pg_text.find(para)
                    if para_start_raw == -1:
                        continue
                    if para_start_raw < effective_start - 200:
                        continue
                    if effective_end != len(pg_norm) and para_start_raw > effective_end + 200:
                        continue

                    para_norm = normalize_for_matching(para)
                    if len(para_norm) < self.min_match_chars:
                        continue

                    para_in_pg = pg_norm.find(para_norm)
                    if para_in_pg == -1:
                        continue
                    distance = abs(para_in_pg - anchor_pos)
                    max_distance = max(300, len(trans_norm) * 2.0)
                    if distance > max_distance:
                        continue

                    for combo in range(4):
                        combined_parts = []
                        for j in range(max(0, i - combo // 2), min(len(pg_paragraphs), i + combo + 1)):
                            combined_parts.append(pg_paragraphs[j])
                        combined = "\n\n".join(combined_parts)
                        combined_norm = normalize_for_matching(combined)
                        if len(combined_norm) < self.min_match_chars:
                            continue

                        score = SequenceMatcher(None, trans_norm, combined_norm).ratio()
                        weighted = score * (1.0 + 0.3 * min(1.0, len(combined_norm) / 500.0))
                        if weighted > para_best_score:
                            para_best_score = weighted
                            para_best_start = pg_text.find(combined_parts[0])
                            last_para = combined_parts[-1]
                            para_best_end = pg_text.find(last_para, para_best_start) + len(last_para)
                            para_best_len = len(combined_norm)

                if para_best_score > best_score:
                    best_score = para_best_score
                    best_pg_start = para_best_start
                    best_pg_end = para_best_end
                    best_match_len = para_best_len
                    was_anchored = True

        # ── Final fallback: Brute-force matching ──
        if best_score == 0:
            logger.debug(f"Page {scan_page}: no anchor found, falling back to brute-force")

            pg_chunks = chunk_text_for_matching(pg_text, min_length=self.min_chunk_length)

            for i, para in enumerate(pg_paragraphs):
                para_start = pg_text.find(para)
                if para_start == -1:
                    continue
                if para_start < effective_start - 200:
                    continue
                if effective_end != len(pg_norm) and para_start > effective_end + 200:
                    continue

                para_norm = normalize_for_matching(para)
                if len(para_norm) < self.min_match_chars:
                    continue
                score = SequenceMatcher(None, trans_norm, para_norm).ratio()
                weighted = score * (1.0 + 0.3 * min(1.0, len(para_norm) / 500.0))
                if weighted > best_score:
                    best_score = weighted
                    best_match_len = len(para_norm)
                    best_pg_start = para_start
                    best_pg_end = para_start + len(para)

            for i, chunk in enumerate(pg_chunks):
                chunk_norm = normalize_for_matching(chunk)
                if len(chunk_norm) < self.min_match_chars:
                    continue
                chunk_prefix = chunk[:80]
                idx = pg_text.find(chunk_prefix)
                if idx == -1:
                    continue
                if idx < effective_start - 200:
                    continue
                if effective_end != len(pg_norm) and idx > effective_end + 200:
                    continue
                score = SequenceMatcher(None, trans_norm, chunk_norm).ratio()
                weighted = score * (1.0 + 0.3 * min(1.0, len(chunk_norm) / 500.0))
                if weighted > best_score:
                    best_score = weighted
                    best_match_len = len(chunk_norm)
                    best_pg_start = idx
                    best_pg_end = idx + len(chunk)

        # Use raw score (unweighted) for threshold check
        raw_score = best_score / (1.0 + 0.3 * min(1.0, best_match_len / 500.0)) if best_match_len else 0.0

        if raw_score < self.match_threshold:
            logger.debug(
                f"Page {scan_page}: raw score {raw_score:.2f} below threshold {self.match_threshold}"
            )
            return None

        # Clamp offsets
        best_pg_start = max(0, min(best_pg_start, len(pg_text)))
        best_pg_end = max(best_pg_start, min(best_pg_end, len(pg_text)))

        # Determine confidence based on raw score + match length
        confidence = min(1.0, raw_score * 1.2)
        if len(trans_norm) < 50:
            confidence *= 0.7
        # Bonus for longer matches
        if best_match_len > 200:
            confidence = min(1.0, confidence + 0.1)

        # Import here to avoid circular imports
        from gerrata.models import Alignment, AlignmentMethod

        alignment = Alignment(
            pg_start=best_pg_start,
            pg_end=best_pg_end,
            scan_page=scan_page,
            scan_image_path=str(transcription.image_path),
            confidence=confidence,
            method=AlignmentMethod.LLM_VISION,
        )

        return VisionAlignmentResult(
            alignment=alignment,
            transcription=transcription,
            best_score=best_score,
            anchored=was_anchored,
        )

    def align_all_pages(
        self,
        transcriptions: list[PageTranscription],
        pg_text: str,
        pg_paragraphs: list[str],
    ) -> list[VisionAlignmentResult]:
        """Align all page transcriptions to PG text.

        Pages are processed in reading order. Each page's alignment
        constrains the next page's search window to PG text after
        the current match (sequential alignment).

        Args:
            transcriptions: List of page transcription results.
            pg_text: Full PG body text.
            pg_paragraphs: PG paragraphs.

        Returns:
            List of successful alignment results.
        """
        results: list[VisionAlignmentResult] = []
        search_start = 0  # Sequential constraint: next page searches after this

        # Normalize PG text once for n-gram indexing
        pg_norm = normalize_for_matching(pg_text)

        for trans in transcriptions:
            # Skip duplicate pages: check if this transcription is very similar
            # to any already-aligned page (using cleaned transcription)
            if trans.transcription_cleaned:
                is_duplicate = False
                for result in results:
                    if result.transcription.transcription_cleaned:
                        # Use SequenceMatcher to detect near-duplicate pages
                        ratio = SequenceMatcher(
                            None,
                            trans.transcription_cleaned,
                            result.transcription.transcription_cleaned
                        ).ratio()
                        if ratio > 0.9:
                            logger.info(
                                f"Page {trans.page_num}: skipping duplicate "
                                f"(ratio={ratio:.2f} vs page {result.transcription.page_num})"
                            )
                            is_duplicate = True
                            break
                
                if is_duplicate:
                    continue

            result = self.align_transcription_to_pg(
                transcription=trans,
                pg_text=pg_text,
                pg_paragraphs=pg_paragraphs,
                scan_page=trans.page_num,
                search_start=search_start,
            )
            if result:
                results.append(result)
                # Only advance search_start for anchored matches (RETAS/n-gram).
                # Brute-force matches are positionally unreliable — they can match
                # short text to random locations, which would poison the constraint
                # for all subsequent pages.
                if result.anchored:
                    search_start = result.alignment.pg_end
                logger.info(
                    f"Page {trans.page_num}: matched PG [{result.alignment.pg_start}:{result.alignment.pg_end}] "
                    f"(score={result.best_score:.2f}, conf={result.alignment.confidence:.2f}"
                    f"{'' if result.anchored else ', brute-force (not advancing constraint)'})"
                )
            else:
                logger.debug(f"Page {trans.page_num}: no match found")

        return results

    def get_alignments(self, results: list[VisionAlignmentResult]) -> list:
        """Extract Alignment objects from results.

        Returns sorted by pg_start position.
        """
        from gerrata.models import Alignment

        alignments = [r.alignment for r in results]
        alignments.sort(key=lambda a: a.pg_start)
        return alignments

    def build_scan_pages_from_transcriptions(
        self,
        transcriptions: list[PageTranscription],
    ) -> list:
        """Build ScanPage objects from transcription results.

        Used to populate scan_pages with vision text for the diff stage.
        """
        from gerrata.fetcher.scans import ScanPage

        pages = []
        for trans in transcriptions:
            # Use cleaned transcription (paratext stripped) when available
            text_for_diff = trans.transcription_cleaned if trans.transcription_cleaned else trans.transcription
            page = ScanPage(
                page_num=trans.page_num,
                ocr_text=text_for_diff if trans.success else "",
                vision_text=text_for_diff if trans.success else "",
                image_path=trans.image_path if trans.success else None,
            )
            pages.append(page)
        return pages

    def alignment_confidence(self, alignments: list, pg_text_len: int) -> float:
        """Calculate overall alignment coverage."""
        if pg_text_len == 0:
            return 0.0

        covered = sum(a.pg_end - a.pg_start for a in alignments)
        return min(1.0, covered / pg_text_len)

# Paratext stripping needs to be added as a function
# I'll write it inline
