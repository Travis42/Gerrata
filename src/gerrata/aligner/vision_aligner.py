"""Vision-first aligner: transcribe page images and match to PG text.

Replaces the OCR-text-based coarse aligner with a vision model approach:
1. For each page image, send to vision model for transcription
2. Match transcribed text against PG text paragraphs using fuzzy matching
3. Return Alignment objects with transcribed text included

Default model: google/gemini-3.1-flash-lite via OpenRouter.
"""

from __future__ import annotations

import asyncio
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

# Load OpenRouter key from file if env var not set
def _load_openrouter_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        key_path = Path.home() / ".secrets" / "openrouter.key"
        if key_path.exists():
            key = key_path.read_text().strip()
    return key

logger = logging.getLogger(__name__)

# Default transcription prompt — aggressively literal to prevent modernization
TRANSCRIPTION_PROMPT = (
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
    "Preserve all line breaks. "
    "Return ONLY the reproduced text."
)

# Minimum cleaned transcription length to accept as "successful".
# Pages of real text typically yield 500-4000 chars after paratext stripping.
# A response shorter than this likely means the model refused, produced garbage,
# or the page is blank/decorative. Set conservatively low to avoid false negatives.
MIN_TRANSCRIPTION_CHARS = 100

# Patterns that indicate the model refused or produced meta-commentary
# instead of transcribing the page.
REFUSAL_PATTERNS = [
    r"^i (can't|cannot|am unable|don't|do not)",
    r"^i'm (not able|unable|sorry)",
    r"^as an ai",
    r"^i am (an?|not)",
    r"^sorry,? (i|but|but i)",
    r"^(i )?apologize",
    r"^this (image|page|scan) (is |appears )?(unclear|illegible|blurry|blank|too dark|damaged)",
    r"^(the )?(image|text|page) (is |appears )?(not|too|very)",
    r"^unable to (read|transcribe|determine|process)",
    r"^no (text|readable|visible|content)",
]


def check_transcription_quality(raw: str, cleaned: str) -> tuple[bool, str]:
    """Check if a transcription is usable or should be rejected.

    Returns (is_good, reason). If is_good is False, reason explains why.
    No extra LLM calls — uses only heuristic checks on the text.
    """
    if not raw or not raw.strip():
        return False, "empty response"

    first_line = raw.strip().split(chr(10))[0].strip().lower()
    for pattern in REFUSAL_PATTERNS:
        if re.match(pattern, first_line, re.IGNORECASE):
            return False, f"refusal pattern matched: {first_line[:60]}"

    if len(cleaned) < MIN_TRANSCRIPTION_CHARS:
        return False, f"cleaned text too short ({len(cleaned)} chars < {MIN_TRANSCRIPTION_CHARS})"

    return True, ""


# Default API configuration — OpenRouter
DEFAULT_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_API_KEY = os.environ.get("OPENROUTER_API_KEY", "") or (
    Path.home().joinpath(".secrets/openrouter.key").read_text().strip()
    if Path.home().joinpath(".secrets/openrouter.key").exists() else ""
)
DEFAULT_MODELS = ["google/gemini-3.1-flash-lite"]

# Map from model name aliases to actual API model names
MODEL_NAME_MAP = {}


def find_body_start(pg_text: str) -> int:
    """Detect where prose body begins in PG text, skipping TOC/front-matter.

    Algorithm:
    1. Split into paragraphs, skip CSS/HTML noise
    2. Find first chapter-like heading (CHAPTER, PART, Book, etc.)
    3. Walk forward from there to find first prose paragraph:
       - >150 chars, not all short lines, >=3 sentence-ending patterns
    4. Fallback: walk from start looking for long prose paragraphs
    5. Returns character offset of detected body start (0 if not found)

    This prevents the aligner from matching scan front-matter to PG TOC entries,
    which are dense chapter heading fragments that cause false anchor matches.
    """
    paras = [p.strip() for p in pg_text.split("\n\n") if p.strip()]
    if not paras:
        return 0

    def _is_noise(p: str) -> bool:
        return any(marker in p for marker in ("{", "}", "margin-", "font-"))

    def _is_chapter_heading(p: str) -> bool:
        return bool(
            re.match(
                r"(CHAPTER|PART|Book|Section|ACT|I+\.\s+|CHAPTER\s+[IVXLCDM]+)",
                p.strip(),
                re.IGNORECASE,
            )
        )

    def _is_prose(p: str) -> bool:
        if len(p) < 150:
            return False
        lines = [l for l in p.split("\n") if l.strip()]
        if lines and all(len(l) < 100 for l in lines):
            return False  # All short lines = TOC-like
        sentences = len(re.findall(r"[.!?]\s+[A-Z\"]", p))
        return sentences >= 3

    # Find first chapter-like heading
    first_chapter_idx = -1
    for i, p in enumerate(paras):
        if _is_noise(p):
            continue
        if _is_chapter_heading(p):
            first_chapter_idx = i
            break

    # Walk forward from chapter heading (or start) to find prose
    search_start = max(0, first_chapter_idx) if first_chapter_idx >= 0 else 0
    for i in range(search_start, min(search_start + 50, len(paras))):
        p = paras[i]
        if _is_noise(p):
            continue
        if _is_prose(p):
            return sum(len(paras[j]) + 2 for j in range(i))

    # Fallback: no chapter heading found, search from beginning for long prose
    for i, p in enumerate(paras):
        if _is_noise(p):
            continue
        if len(p) > 500 and len(re.findall(r"[.!?]\s+[A-Z\"]", p)) >= 5:
            return sum(len(paras[j]) + 2 for j in range(i))

    return 0


def strip_paratext(text: str) -> str:
    """Remove paratext from OCR/vision transcription: headers, page numbers, running feet.

    PG texts never include page headers, page numbers, running feet, or short decorative
    lines. Removing these before matching dramatically improves alignment quality.
    """
    # ... existing implementation unchanged ...
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
class SequentialTracker:
    """Tracks confirmed match positions for sequential search estimation."""

    matches: list[tuple[int, int, float, int]] = field(default_factory=list)
    # Each entry: (page_num, pg_end_offset, confidence_score, match_length)

    @property
    def last_confirmed_position(self) -> int:
        """Most recent match end from HIGH or MEDIUM confidence matches."""
        for _pn, offset, confidence, _ml in reversed(self.matches):
            if confidence >= 0.40:
                return offset
        return 0

    @property
    def last_confirmed_page(self) -> int:
        """Page number of the most recent HIGH or MEDIUM confidence match."""
        for pn, _off, confidence, _ml in reversed(self.matches):
            if confidence >= 0.40:
                return pn
        return 0

    @property
    def chars_per_page(self) -> float:
        """Estimated chars per page from recent confirmed matches."""
        recent = [(pn, off) for pn, off, conf, _ml in self.matches if conf >= 0.40]
        if len(recent) < 2:
            return 1500.0  # Default estimate
        recent = recent[-10:]
        total_chars = recent[-1][1] - recent[0][1]
        total_pages = recent[-1][0] - recent[0][0]
        return max(500.0, total_chars / max(1, total_pages))

    def expected_position(self, page_num: int) -> int:
        """Estimate where page_num's content should start in PG text."""
        last_pos = self.last_confirmed_position
        last_page = self.last_confirmed_page
        if last_page == 0:
            # No confirmed position yet — start from beginning
            return 0
        pages_gap = page_num - last_page
        if pages_gap <= 0:
            return last_pos
        return last_pos + int(pages_gap * self.chars_per_page)

    def record(self, page_num: int, pg_end: int, confidence: float, match_length: int):
        """Record a confirmed match."""
        self.matches.append((page_num, pg_end, confidence, match_length))

    def search_window(self, page_num: int, pg_text_length: int) -> tuple[int, int]:
        """Return (start, end) search window centered on expected position.

        When no confirmed position exists (cold start), return a wide window
        covering the full text so the first successful alignment can lock on
        anywhere in the book.
        """
        if self.last_confirmed_page == 0:
            # Cold start: search entire text to find first lock-on point
            return (0, pg_text_length)
        expected = self.expected_position(page_num)
        start = max(0, expected - 2000)
        end = min(pg_text_length, expected + 8000)
        return (start, end)

    def reanchor(
        self,
        aligner=None,
        transcription=None,
        pg_text: str = "",
        pg_paragraphs: list[str] | None = None,
        page_num: int = 0,
    ) -> bool:
        """Reset tracker to last N high-confidence matches to prevent drift.

        When processing many pages, small alignment errors accumulate in
        chars_per_page estimation, causing the search window to drift. This
        keeps only recent confirmed matches, resetting the baseline.

        When aligner, transcription, pg_text, and pg_paragraphs are provided,
        also runs RETAS without sequential constraint to verify tracker accuracy.
        If the RETAS-estimated position differs from the tracker's expected
        position by more than 1.5× chars_per_page, the tracker is reset.

        Returns True if tracker was re-anchored (had enough data), False if
        there's insufficient history to re-anchor safely.
        """
        recent = [(pn, off, conf, ml) for pn, off, conf, ml in self.matches if conf >= 0.40]
        if len(recent) < 3:
            return False
        # Keep only the last 10 high-confidence matches
        self.matches = [(pn, off, conf, ml) for pn, off, conf, ml in self.matches if conf >= 0.40][-10:]
        old_cpp = self.chars_per_page

        # Enhanced re-anchor: verify with unconstrained RETAS if aligner provided
        if (
            aligner is not None
            and transcription is not None
            and pg_text
            and pg_paragraphs is not None
        ):
            self._verify_with_unconstrained_retas(
                aligner, transcription, pg_text, pg_paragraphs, page_num
            )

        logger.info(
            f"Tracker re-anchored: kept last {len(self.matches)} confirmed matches, "
            f"chars_per_page {old_cpp:.0f} → {self.chars_per_page:.0f}"
        )
        return True

    def _verify_with_unconstrained_retas(
        self,
        aligner,
        transcription,
        pg_text: str,
        pg_paragraphs: list[str],
        page_num: int,
    ) -> None:
        """Run RETAS without constraint and compare to tracker's expected position.

        If the difference exceeds 1.5× chars_per_page, reset the tracker to
        the RETAS-estimated position.
        """
        try:
            body_offset = find_body_start(pg_text)
            result = aligner.align_transcription_to_pg(
                transcription=transcription,
                pg_text=pg_text,
                pg_paragraphs=pg_paragraphs,
                scan_page=page_num,
                search_start=body_offset,
                search_end=len(pg_text),
                min_score=SEQUENTIAL_THRESHOLD,
            )
            if result is not None:
                retas_position = result.alignment.pg_start
                expected = self.expected_position(page_num)
                cpp = self.chars_per_page
                threshold = int(cpp * 1.5)
                difference = abs(retas_position - expected)

                if difference > threshold:
                    logger.warning(
                        f"Re-anchor verification: RETAS position {retas_position} differs "
                        f"from tracker expected {expected} by {difference} chars "
                        f"(threshold={threshold}, cpp={cpp:.0f}). Resetting tracker."
                    )
                    # Reset tracker: keep only this RETAS-derived match
                    self.matches = [
                        (
                            page_num,
                            result.alignment.pg_end,
                            result.best_score,
                            result.alignment.pg_end - result.alignment.pg_start,
                        )
                    ]
                else:
                    logger.debug(
                        f"Re-anchor verification: tracker accurate "
                        f"(expected={expected}, retas={retas_position}, diff={difference})"
                    )
        except Exception as e:
            logger.debug(f"Re-anchor verification failed: {e}")


# Alignment phase thresholds
CHAPTER_THRESHOLD = 0.50
SEQUENTIAL_THRESHOLD = 0.40
GLOBAL_THRESHOLD = 0.35

# Drift control: re-anchor tracker every N pages
REANCHOR_INTERVAL = 10


@dataclass
class VisionAlignmentResult:
    """Result of aligning a page to PG text."""

    alignment: "Alignment"  # noqa: F821 - forward ref
    transcription: PageTranscription
    matched_pg_chunks: list[str] = field(default_factory=list)
    best_score: float = 0.0
    anchored: bool = True  # True if RETAS found unique word anchors; False for brute-force only


@dataclass
class ValidationResult:
    """Result of post-alignment validation."""

    offset_mean: float = 0.0  # Mean offset in chars (positive = PG position too high)
    offset_stddev: float = 0.0  # Standard deviation of offsets (raw, not residual)
    residual_stddev: float = 0.0  # Stddev of residuals after linear regression fit
    drift_slope: float = 0.0  # Linear drift rate (chars per page)
    drift_intercept: float = 0.0  # Linear drift intercept (chars at page 0)
    sample_size: int = 0  # Number of pages sampled
    pages_correct: int = 0  # Pages where |offset| < offset_tolerance
    pages_incorrect: int = 0  # Pages where |offset| >= offset_tolerance
    corrected: bool = False  # Whether correction was applied
    dropped: int = 0  # Pages dropped during re-scoring
    verdict: str = "ok"  # "ok" | "corrected" | "drift_corrected" | "failed"


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
        transcription_log: str | Path | None = None,
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
            transcription_log: Path to JSONL file for crash-resilient transcription progress.
                Each completed page is appended as one line. Used for auto-resume after crashes.
        """
        self.api_url = api_url
        self.api_key = api_key or _load_openrouter_key() or DEFAULT_API_KEY
        self.models = models or DEFAULT_MODELS
        self.prompt = prompt
        self.timeout = timeout
        self.ocr_engine = ocr_engine
        self.cache_file = Path(cache_file) if cache_file else None
        self.disable_cache = disable_cache
        self.concurrency = concurrency
        self.transcription_log = Path(transcription_log) if transcription_log else None
        self.cache_data = self._load_cache() if not disable_cache and self.cache_file else {}
        self.cache_stats = {"hits": 0, "misses": 0, "saves": 0}

    def _load_cache(self) -> dict:
        """Load transcription cache from disk, evicting entries for wrong models."""
        if not self.cache_file or not self.cache_file.exists():
            return {"version": 1, "model": "", "pages": {}}

        try:
            with open(self.cache_file, "r") as f:
                cache = json.load(f)
                # Validate cache structure
                if not isinstance(cache, dict) or "pages" not in cache:
                    logger.warning(f"Invalid cache file {self.cache_file}, starting fresh")
                    return {"version": 1, "model": "", "pages": {}}
                # Evict entries cached for a different model — they're useless
                # and would waste API calls on retry (cache miss + re-fail).
                current_model = self.models[0] if self.models else ""
                evicted = 0
                stale_pages = {}
                for filename, entry in cache.get("pages", {}).items():
                    if entry.get("model") != current_model:
                        evicted += 1
                    else:
                        stale_pages[filename] = entry
                if evicted > 0:
                    cache["pages"] = stale_pages
                    logger.info(
                        f"Evicted {evicted} stale cache entries (model != {current_model})"
                    )
                    # Rewrite cache immediately so we don't carry stale data
                    self._save_cache_from(cache)
                return cache
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load cache {self.cache_file}: {e}")
            return {"version": 1, "model": "", "pages": {}}

    def _save_cache(self) -> None:
        """Save transcription cache to disk."""
        if not self.cache_file or self.disable_cache:
            return
        self._save_cache_from(self.cache_data)

    def _save_cache_from(self, cache_data: dict) -> None:
        """Save cache data dict to disk (used by _save_cache and _load_cache)."""

        try:
            # Ensure parent directory exists
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            # Write atomically
            temp_file = self.cache_file.with_suffix(".tmp")
            with open(temp_file, "w") as f:
                json.dump(cache_data, f, indent=2)
            temp_file.replace(self.cache_file)
        except IOError as e:
            logger.warning(f"Failed to save cache {self.cache_file}: {e}")

    # --- Crash-resilient JSONL transcription log ---

    def _load_transcription_log(self) -> set[int]:
        """Load set of page numbers already recorded in the JSONL log."""
        if not self.transcription_log or not self.transcription_log.exists():
            return set()
        completed = set()
        try:
            with open(self.transcription_log, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        completed.add(int(entry.get("page_num", -1)))
                    except (json.JSONDecodeError, ValueError):
                        continue
        except IOError as e:
            logger.warning(f"Failed to read transcription log {self.transcription_log}: {e}")
        return completed

    def _append_transcription_log(self, page_transcription) -> None:
        """Append a completed page transcription to the JSONL log.

        This is called after each successful page, so the log survives crashes.
        """
        if not self.transcription_log:
            return
        try:
            self.transcription_log.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "page_num": page_transcription.page_num,
                "image_path": str(page_transcription.image_path) if page_transcription.image_path else None,
                "transcription": page_transcription.transcription,
                "transcription_cleaned": page_transcription.transcription_cleaned,
                "success": page_transcription.success,
                "error": page_transcription.error,
                "model_used": page_transcription.model_used or "",
            }
            with open(self.transcription_log, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except IOError as e:
            logger.warning(f"Failed to write transcription log: {e}")

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
                # Cache hit — still validate quality
                cleaned = strip_paratext(cached_text)
                is_good, reason = check_transcription_quality(cached_text, cleaned)
                if is_good:
                    logger.info(f"  Cache hit: {image_path.name} (cached, {len(cached_text)} chars)")
                    return PageTranscription(
                        page_num=page_num,
                        image_path=image_path,
                        transcription=cached_text,
                        transcription_cleaned=cleaned,
                        model_used=primary_model,
                        success=True,
                    )
                else:
                    logger.warning(
                        f"  Cache hit but failed quality check: {image_path.name} — {reason}, re-transcribing"
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
                        is_good, reason = check_transcription_quality(transcription.strip(), cleaned)
                        if is_good:
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
                        else:
                            logger.warning(
                                f"  Quality check failed: {image_path.name} — {reason}"
                            )
                            # Don't cache — let retry happen
                            continue
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
            transcription_cleaned="",
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

        # Auto-resume: load completed pages from JSONL log
        logged_pages = self._load_transcription_log()
        if logged_pages:
            logger.info(
                f"Resuming transcription: {len(logged_pages)}/{len(image_paths)} pages already logged"
            )

        if self.concurrency > 1:
            logger.info(f"Using {self.concurrency} concurrent API calls for transcription")

        semaphore = asyncio.Semaphore(self.concurrency)

        async def process_page(i, path):
            # Skip pages already in the JSONL log (crash resume)
            if i in logged_pages:
                return (i, None)  # None signals "already done"
            async with semaphore:
                logger.info(f"Transcribing page {i+1}/{len(image_paths)}: {path.name}")
                result = await self.transcribe_page(path, page_num=i)
                if result.success:
                    # Don't print char count for cache hits (already logged)
                    if not (self.ocr_engine == "vision" and
                            self._get_cached_transcription(path, result.model_used)):
                        logger.info(f"  → {len(result.transcription)} chars via {result.model_used}")
                    # Append to crash-resilient log
                    self._append_transcription_log(result)
                else:
                    logger.warning(f"  → Failed: {result.error}")
                return (i, result)

        tasks = [process_page(i, path) for i, path in enumerate(image_paths)]
        results_raw = await asyncio.gather(*tasks)

        # Build full results list: load logged pages from JSONL, fill in fresh results
        results: list[PageTranscription | None] = [None] * len(image_paths)

        # Load logged pages from JSONL
        if logged_pages:
            logged_transcriptions: dict[int, PageTranscription] = {}
            if self.transcription_log and self.transcription_log.exists():
                with open(self.transcription_log, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            pt = PageTranscription(
                                page_num=int(entry.get("page_num", 0)),
                                image_path=Path(entry["image_path"]) if entry.get("image_path") else None,
                                transcription=entry.get("transcription", ""),
                                transcription_cleaned=entry.get("transcription_cleaned", ""),
                                success=entry.get("success", False),
                                error=entry.get("error"),
                                model_used=entry.get("model_used", ""),
                            )
                            logged_transcriptions[pt.page_num] = pt
                        except (json.JSONDecodeError, ValueError, KeyError):
                            continue
            for idx, pt in logged_transcriptions.items():
                if 0 <= idx < len(results):
                    results[idx] = pt

        # Fill in freshly transcribed pages
        for idx, result in results_raw:
            if result is not None:
                results[idx] = result

        # Identify failed pages
        failed_indices = [(i, r) for i, r in enumerate(results) if r is not None and not r.success]
        successes = len([r for r in results if r is not None and r.success])

        # Retry failed pages once with a brief pause
        if failed_indices:
            logger.warning(
                f"{len(failed_indices)} pages failed, retrying once... (pages: "
                f"{', '.join(str(image_paths[i].name) for i, _ in failed_indices[:5])}{'...' if len(failed_indices) > 5 else ''})"
            )
            await asyncio.sleep(2)  # Brief pause before retry
            semaphore = asyncio.Semaphore(self.concurrency)

            async def retry_page(idx, path):
                async with semaphore:
                    result = await self.transcribe_page(path, page_num=idx)
                    return (idx, result)

            retry_tasks = [retry_page(idx, image_paths[idx]) for idx, _ in failed_indices]
            retry_results = await asyncio.gather(*retry_tasks)
            for idx, retry_result in retry_results:
                results[idx] = retry_result
                # Log retried pages too
                if retry_result.success:
                    self._append_transcription_log(retry_result)

            still_failed = [(i, r) for i, r in enumerate(results) if r is not None and not r.success]
            recovered = len(failed_indices) - len(still_failed)
            if recovered > 0:
                logger.info(f"Retry recovered {recovered} of {len(failed_indices)} failed pages")
            if still_failed:
                logger.error(
                    f"{len(still_failed)} pages still failed after retry: "
                    f"{', '.join(image_paths[i].name for i, _ in still_failed)}"
                )

        # Print summary statistics
        successes = sum(1 for r in results if r is not None and r.success)
        fresh = self.cache_stats["saves"]
        cached = self.cache_stats["hits"]
        failed = len(results) - successes
        logger.info(
            f"Transcription complete: {successes}/{len(image_paths)} pages OK "
            f"({cached} from cache, {fresh} fresh, {failed} failed)"
        )
        if failed > 0:
            failed_names = [image_paths[i].name for i, r in enumerate(results) if not r.success]
            logger.warning(f"Failed pages: {', '.join(failed_names)}")

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

    def align_transcription_to_pg(
        self,
        transcription: PageTranscription,
        pg_text: str,
        pg_paragraphs: list[str],
        scan_page: int = 0,
        search_start: int = 0,
        search_end: int = -1,
        min_score: float = 0.35,
    ) -> VisionAlignmentResult | None:
        """Align a single page transcription to PG text using RETAS only.

        Uses unique word anchoring (RETAS) to find the correct region of PG text.
        No fallback to GSA, n-gram, or brute-force matching — if RETAS cannot
        anchor the page, it is returned as unmatched.

        After scoring, applies an edit density quality check: if the aligned
        window has more than 15% edit operations relative to its length, the
        alignment is rejected.

        Args:
            transcription: The page transcription result.
            pg_text: Full PG body text.
            pg_paragraphs: PG paragraphs for offset tracking.
            scan_page: Scan page number.
            search_start: Only search PG text from this offset (for sequential constraint).
            search_end: Only search PG text up to this offset (-1 = end).
            min_score: Minimum score to accept (default 0.35).

        Returns:
            VisionAlignmentResult if a match is found (score >= min_score), None otherwise.
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

        # Determine search window from sequential constraint
        effective_start = search_start
        effective_end = search_end if search_end != -1 else len(pg_norm)

        best_score = 0.0
        best_pg_start = 0
        best_pg_end = 0
        best_match_len = 0
        best_pg_raw = ""

        # ── RETAS unique word anchoring (sole aligner) ──
        anchor_result = self._find_unique_word_anchor(
            trans_text, pg_text,
            search_start=effective_start,
            search_end=effective_end,
        )

        if anchor_result is None:
            logger.debug(f"Page {scan_page}: RETAS found no unique word anchors")
            return None

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

        # ── Direct interpolation when ≥3 anchors ──
        if len(anchor_pg_char_positions) >= 3 and anchor_trans_word_positions:
            pg_words = self._tokenize_words(pg_text)
            avg_chars_per_word = len(pg_text) / max(1, len(pg_words))

            estimated_pg_start = min(anchor_pg_char_positions) - min(anchor_trans_word_positions) * avg_chars_per_word
            estimated_pg_start = max(0, estimated_pg_start)
            # Scale transcription length to raw text space
            norm_scale = len(pg_text) / max(1, len(pg_norm))
            estimated_pg_end = estimated_pg_start + len(trans_norm) * norm_scale
            estimated_pg_end = min(len(pg_text), estimated_pg_end)

            best_pg_start = int(estimated_pg_start)
            best_pg_end = int(estimated_pg_end)

            best_pg_raw = pg_text[best_pg_start:best_pg_end]
            best_pg_norm_window = normalize_for_matching(best_pg_raw)
            final_score = SequenceMatcher(None, trans_norm, best_pg_norm_window).ratio()

            best_ratio = final_score
            weighted_score = final_score * (1.0 + 0.2 * min(1.0, (best_pg_end - best_pg_start) / 500.0))
            best_score = weighted_score
            best_match_len = best_pg_end - best_pg_start
            best_pg_end = min(best_pg_end, len(pg_text))

            logger.debug(
                f"  RETAS direct interpolation (≥3 anchors): pg_text[{best_pg_start}:{best_pg_end}], "
                f"score={final_score:.3f}"
            )
        else:
            # ── Sliding window (when 1-2 anchors) ──
            trans_len = len(trans_norm)

            if anchor_pg_char_positions:
                anchor_positions_sorted = sorted(anchor_pg_char_positions)
                median_idx = len(anchor_positions_sorted) // 2
                median_anchor_pos = anchor_positions_sorted[median_idx]

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

                window_size = int(trans_len * 1.2)
                best_sliding_score = 0.0
                best_sliding_start = 0
                best_sliding_end = 0

                search_range = 150
                step_size = 20

                for offset in range(-search_range, search_range + 1, step_size):
                    start_pos = max(0, estimated_start + offset)
                    end_pos = min(len(pg_text), start_pos + window_size)
                    pg_window = pg_text[start_pos:end_pos]
                    pg_window_norm = normalize_for_matching(pg_window)

                    if len(pg_window_norm) >= trans_len * 0.5:
                        score = SequenceMatcher(None, trans_norm, pg_window_norm).ratio()
                        distance_penalty = abs(offset) / search_range * 0.05
                        adjusted_score = score - distance_penalty

                        if adjusted_score > best_sliding_score:
                            best_sliding_score = adjusted_score
                            best_sliding_start = start_pos
                            best_sliding_end = end_pos

                if best_sliding_score > 0:
                    best_pg_raw = pg_text[best_sliding_start:best_sliding_end]
                    best_pg_norm = normalize_for_matching(best_pg_raw)
                    final_score = SequenceMatcher(None, trans_norm, best_pg_norm).ratio()

                    best_pg_start = best_sliding_start
                    best_pg_end = best_sliding_end
                    best_ratio = final_score
                    weighted_score = final_score * (1.0 + 0.2 * min(1.0, (best_pg_end - best_pg_start) / 500.0))

                    if weighted_score > best_score:
                        best_score = weighted_score
                        best_match_len = best_pg_end - best_pg_start
                        best_pg_end = min(best_pg_end, len(pg_text))

                        logger.debug(
                            f"  RETAS sliding window: pg_text[{best_pg_start}:{best_pg_end}], "
                            f"score={best_ratio:.3f}"
                        )

        # ── Score threshold check ──
        # Use raw score (unweighted) for threshold check
        raw_score = best_score / (1.0 + 0.2 * min(1.0, best_match_len / 500.0)) if best_match_len else 0.0

        if raw_score < self.match_threshold:
            logger.debug(
                f"Page {scan_page}: raw score {raw_score:.2f} below threshold {self.match_threshold}"
            )
            return None

        # ── Post-alignment edit density quality check (Recovery 4) ──
        # Reject alignments with too many edits relative to alignment length.
        # Only applies for longer transcriptions (>100 chars) where the density
        # metric is meaningful. Short transcriptions (headers, chapter starts)
        # inherently have high edit density due to interpolation imprecision.
        if best_pg_raw and len(best_pg_raw) > 0 and len(trans_norm) > 100:
            aligned_pg_norm = normalize_for_matching(best_pg_raw)
            sm = SequenceMatcher(None, trans_norm, aligned_pg_norm)
            total_edits = 0
            for op, i1, i2, j1, j2 in sm.get_opcodes():
                if op != 'equal':
                    total_edits += max(i2 - i1, j2 - j1)
            alignment_length = max(len(trans_norm), len(aligned_pg_norm))
            edit_density = total_edits / alignment_length if alignment_length > 0 else 0.0

            if edit_density > 0.15:
                logger.debug(
                    f"Page {scan_page}: edit density {edit_density:.3f} > 0.15, rejecting alignment"
                )
                return None

        # Clamp offsets
        best_pg_start = max(0, min(best_pg_start, len(pg_text)))
        best_pg_end = max(best_pg_start, min(best_pg_end, len(pg_text)))

        # Determine confidence based on raw score + match length
        confidence = min(1.0, raw_score * 1.2)
        if len(trans_norm) < 50:
            confidence *= 0.7
        if best_match_len > 200:
            confidence = min(1.0, confidence + 0.1)

        # Check minimum score threshold
        if best_score < min_score:
            logger.debug(
                f"Page {scan_page}: score {best_score:.3f} below threshold {min_score:.2f}"
            )
            return None

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
            anchored=True,  # RETAS always anchors when it returns
        )

    @staticmethod
    def check_position_consistency(
        prev_pg_end: int,
        prev_page_num: int,
        pg_start: int,
        page_num: int,
        chars_per_page: float,
    ) -> bool:
        """Check if the gap between consecutive alignments is within expected bounds.

        An anomaly is detected when the actual gap between the previous alignment's
        end and the current alignment's start is outside 0.5×-2.0× the expected gap.

        Args:
            prev_pg_end: PG text offset where the previous page's alignment ended.
            prev_page_num: Page number of the previous confirmed alignment.
            pg_start: PG text offset where the current page's alignment starts.
            page_num: Current page number.
            chars_per_page: Estimated characters per page.

        Returns:
            True if position is consistent (no anomaly), False if anomaly detected.
        """
        pages_between = page_num - prev_page_num - 1
        if pages_between < 0:
            return True  # Same or earlier page, can't check consistency
        actual_gap = pg_start - prev_pg_end
        expected_gap = chars_per_page * pages_between

        if expected_gap <= 0:
            # Consecutive pages — expected gap is ~0, but allow some paratext.
            # Use chars_per_page as reference for bounds.
            lower_bound = -chars_per_page * 0.5
            upper_bound = chars_per_page * 2.0
        else:
            lower_bound = expected_gap * 0.5
            upper_bound = expected_gap * 2.0

        return lower_bound <= actual_gap <= upper_bound

    @staticmethod
    def check_scoring_regression(
        current_score: float,
        rolling_scores: list[float],
        threshold: float = 0.20,
    ) -> bool:
        """Check if current score has dropped significantly below rolling average.

        Args:
            current_score: The alignment score of the current page.
            rolling_scores: List of recent alignment scores (up to 10).
            threshold: Fractional drop threshold (default 0.20 = 20%).

        Returns:
            True if scoring regression detected (current > threshold% below average).
        """
        if len(rolling_scores) < 5:
            return False  # Not enough data to establish a baseline

        avg = sum(rolling_scores) / len(rolling_scores)
        if avg <= 0:
            return False

        drop_fraction = (avg - current_score) / avg
        return drop_fraction > threshold + 1e-9

    def _detect_scan_chapter(self, transcription: str) -> str | None:
        """Extract chapter title from scan page transcription.

        Scan pages often have a chapter title as the first line (on verso pages).
        Returns the chapter title if detected, None otherwise.
        """
        if not transcription:
            return None

        lines = [l.strip() for l in transcription.split("\n") if l.strip()]
        if not lines:
            return None

        first_line = lines[0]
        # Match "CHAPTER \d+. Title" or just standalone title lines
        ch_match = re.match(r"^CHAPTER\s+\d+[.\s]+(.+)", first_line, re.IGNORECASE)
        if ch_match:
            return ch_match.group(1).strip().rstrip(".")

        # Standalone title: short first line that looks like a chapter title
        # (no running header "Moby Dick", no prose, no page numbers)
        if len(first_line) < 80 and not first_line.isdigit():
            # Strip trailing page numbers (e.g. "The Spouter-Inn 15")
            clean = re.sub(r"\s+\d+$", "", first_line)
            # Strip trailing punctuation (periods, semicolons, em dashes, colons)
            clean = re.sub(r"[\.\;\u2014\u2013:]+\s*$", "", clean).strip()

            # Single word + trailing comma = prose continuation ("Midnight,")
            # Multi-word titles with commas are valid ("The Negro of the Wreck,")
            if clean.endswith(",") and len(clean.split()) == 1:
                return None
            clean = clean.rstrip(".")

            # Skip common non-chapter first lines
            skip = {"moby dick", "extracts", "etyymology", "contents",
                    "preface", "introduction", "the end"}
            if clean.lower() in skip:
                return None

            # Check if it looks like a title (capitalized words, no sentence punctuation)
            words = clean.split()
            if words and all(w[0].isupper() for w in words if len(w) > 2):
                return clean

        return None

    def _match_chapter_heading(self, heading: str, chapters: list, current_idx: int = 0) -> int | None:
        """Match a scan chapter heading to a PG chapter.

        Uses normalized fuzzy matching on the title portion. Prefers the
        nearest chapter to current_idx when multiple matches exist.

        Args:
            heading: Chapter title from scan (e.g. "Loomings", "The Carpet-Bag")
            chapters: List of ChapterLocation objects
            current_idx: Current chapter index (prefer nearby matches)

        Returns:
            Index into chapters list, or None if no match found.
        """
        if not heading:
            return None

        heading_norm = normalize_for_matching(heading)

        # Collect all matches with their scores
        candidates = []
        for i, ch in enumerate(chapters):
            # Extract title portion from "CHAPTER 1. Loomings." → "Loomings"
            title = re.sub(r"^CHAPTER\s+\d+[.\s]+", "", ch.title, flags=re.IGNORECASE).strip().rstrip(".")
            if not title:
                continue

            title_norm = normalize_for_matching(title)

            # Exact match after normalization
            if heading_norm == title_norm:
                candidates.append((i, 1.0))
                continue

            # Fuzzy match: require longer titles for fuzzy to avoid
            # short common words matching random chapters
            if len(heading_norm) > 4 and len(title_norm) > 4:
                ratio = SequenceMatcher(None, heading_norm, title_norm).ratio()
                if ratio > 0.75:
                    candidates.append((i, ratio))

        if not candidates:
            return None

        # Prefer: exact match > nearby chapter > high score
        # Sort by: exact match first, then by distance from current, then by score
        def sort_key(item):
            idx, score = item
            is_exact = score >= 1.0
            distance = abs(idx - current_idx)
            return (not is_exact, distance, -score)

        candidates.sort(key=sort_key)
        return candidates[0][0]

    def align_all_pages(
        self,
        transcriptions: list[PageTranscription],
        pg_text: str,
        pg_paragraphs: list[str],
        chapters: list | None = None,
    ) -> list[VisionAlignmentResult]:
        """Align all page transcriptions to PG text using RETAS with recovery.

        Phase 1 (chapter-constrained): Fast, usually correct. Requires score >= 0.50.
        Phase 2 (sequential neighborhood): Covers drift. Requires score >= 0.40.

        Recovery mechanisms:
        - Position consistency check (Recovery 1): detects missing pages.
        - Scoring regression monitor (Recovery 2): detects slow drift.
        - Periodic forced re-anchor (Recovery 3): every 10 pages, verifies with
          unconstrained RETAS and resets tracker if needed.
        - Post-alignment edit density check (Recovery 4): built into
          align_transcription_to_pg.

        After forward pass, a backward repair pass fills remaining gaps
        using bilateral constraints from neighboring confirmed matches.
        """
        # Initialize results as list of None (same length as transcriptions)
        results: list[VisionAlignmentResult | None] = [None] * len(transcriptions)
        tracker = SequentialTracker()

        body_offset = find_body_start(pg_text)
        if body_offset > 0:
            logger.info(
                f"Detected PG body start at offset {body_offset} "
                f"({body_offset / len(pg_text) * 100:.1f}% of text), skipping TOC/front-matter"
            )

        # Parse chapters
        real_chapters: list = []
        current_chapter_idx = -1
        if chapters:
            real_chapters = [ch for ch in chapters if ch.end_offset - ch.offset > 1000]
            if not real_chapters:
                real_chapters = chapters
            for i, ch in enumerate(real_chapters):
                if ch.offset >= body_offset:
                    current_chapter_idx = i
                    break
            if current_chapter_idx < 0 and real_chapters:
                current_chapter_idx = len(real_chapters) - 1
            logger.info(
                f"Soft alignment: {len(real_chapters)} chapters, "
                f"starting at chapter {current_chapter_idx + 1} "
                f"({real_chapters[current_chapter_idx].title if current_chapter_idx >= 0 else 'N/A'})"
            )

        # -- Forward pass: RETAS-only alignment with recovery --
        pages_since_reanchor = 0
        rolling_scores: list[float] = []

        for i, trans in enumerate(transcriptions):
            page_num = trans.page_num

            # Skip pages with very short transcriptions (blank, decorative, title
            # pages). These produce noise in alignment without contributing coverage.
            if trans.success and trans.transcription and len(trans.transcription.strip()) < 30:
                logger.info(
                    f"Page {page_num}: skipping short transcription "
                    f"({len(trans.transcription.strip())} chars < 30 min)"
                )
                continue
            if not trans.success or not trans.transcription:
                logger.info(f"Page {page_num}: skipping failed/empty transcription")
                continue

            # Recovery 3: Periodic forced re-anchor (every REANCHOR_INTERVAL pages)
            pages_since_reanchor += 1
            if pages_since_reanchor >= REANCHOR_INTERVAL:
                tracker.reanchor(
                    aligner=self,
                    transcription=trans,
                    pg_text=pg_text,
                    pg_paragraphs=pg_paragraphs,
                    page_num=page_num,
                )
                pages_since_reanchor = 0

            # Skip duplicate pages
            if trans.transcription_cleaned:
                is_dup = False
                for r in results:
                    if r and r.transcription.transcription_cleaned:
                        ratio = SequenceMatcher(
                            None, trans.transcription_cleaned,
                            r.transcription.transcription_cleaned
                        ).ratio()
                        if ratio > 0.9:
                            logger.info(
                                f"Page {page_num}: skipping duplicate "
                                f"(ratio={ratio:.2f} vs page {r.transcription.page_num})"
                            )
                            is_dup = True
                            break
                if is_dup:
                    continue

            # -- Phase 1: Chapter-constrained search --
            heading = self._detect_scan_chapter(trans.transcription)
            ch_window = None
            if heading and real_chapters and current_chapter_idx >= 0:
                match_idx = self._match_chapter_heading(heading, real_chapters, current_chapter_idx)
                if match_idx is not None and match_idx >= current_chapter_idx - 2:
                    ch = real_chapters[match_idx]
                    ch_window = (ch.offset, ch.end_offset)
                    current_chapter_idx = match_idx
                    logger.info(
                        f"Page {page_num}: Phase 1 (chapter '{heading}') "
                        f"[{ch.offset}:{ch.end_offset}]"
                    )

            if ch_window:
                result = self.align_transcription_to_pg(
                    transcription=trans, pg_text=pg_text, pg_paragraphs=pg_paragraphs,
                    scan_page=page_num, search_start=ch_window[0], search_end=ch_window[1],
                    min_score=CHAPTER_THRESHOLD,
                )
                if result:
                    results[i] = result
                    tracker.record(page_num, result.alignment.pg_end, 0.50,
                                 result.alignment.pg_end - result.alignment.pg_start)
                    rolling_scores.append(result.best_score)
                    if len(rolling_scores) > 10:
                        rolling_scores.pop(0)
                    current_chapter_idx = self._update_chapter_from_position(
                        result.alignment.pg_start, real_chapters, current_chapter_idx
                    )
                    logger.info(
                        f"Page {page_num}: Phase 1 match "
                        f"[{result.alignment.pg_start}:{result.alignment.pg_end}] "
                        f"score={result.best_score:.2f} HIGH"
                    )
                    continue

            # -- Phase 2: Sequential neighborhood search --
            seq_window = tracker.search_window(page_num, len(pg_text))
            result = self.align_transcription_to_pg(
                transcription=trans, pg_text=pg_text, pg_paragraphs=pg_paragraphs,
                scan_page=page_num, search_start=seq_window[0], search_end=seq_window[1],
                min_score=SEQUENTIAL_THRESHOLD,
            )
            if result:
                # Recovery 1: Position consistency check
                position_ok = True
                if tracker.last_confirmed_position > 0 and tracker.last_confirmed_page > 0:
                    position_ok = self.check_position_consistency(
                        prev_pg_end=tracker.last_confirmed_position,
                        prev_page_num=tracker.last_confirmed_page,
                        pg_start=result.alignment.pg_start,
                        page_num=page_num,
                        chars_per_page=tracker.chars_per_page,
                    )
                    if not position_ok:
                        logger.warning(
                            f"Page {page_num}: position anomaly detected "
                            f"(pg_start={result.alignment.pg_start}, "
                            f"prev_end={tracker.last_confirmed_position}, "
                            f"pages={tracker.last_confirmed_page}→{page_num})"
                        )

                # Recovery 2: Scoring regression check
                scoring_ok = True
                if rolling_scores and not self.check_scoring_regression(result.best_score, rolling_scores):
                    scoring_ok = False
                    avg = sum(rolling_scores) / len(rolling_scores)
                    logger.warning(
                        f"Page {page_num}: scoring regression "
                        f"(score={result.best_score:.2f}, rolling_avg={avg:.2f})"
                    )

                # If any recovery triggered, re-run RETAS without constraint
                if not position_ok or not scoring_ok:
                    logger.info(
                        f"Page {page_num}: recovery triggered "
                        f"(position_ok={position_ok}, scoring_ok={scoring_ok}), "
                        f"re-running unconstrained RETAS"
                    )
                    body_off = find_body_start(pg_text)
                    unconstrained = self.align_transcription_to_pg(
                        transcription=trans, pg_text=pg_text, pg_paragraphs=pg_paragraphs,
                        scan_page=page_num, search_start=body_off, search_end=len(pg_text),
                        min_score=SEQUENTIAL_THRESHOLD,
                    )
                    if unconstrained:
                        # Compare unconstrained result to tracker expectation
                        expected = tracker.expected_position(page_num)
                        cpp = tracker.chars_per_page
                        threshold = int(cpp * 1.5)
                        difference = abs(unconstrained.alignment.pg_start - expected)

                        if difference > threshold:
                            logger.warning(
                                f"Page {page_num}: unconstrained RETAS corrected tracker "
                                f"(expected={expected}, retas={unconstrained.alignment.pg_start}, "
                                f"diff={difference}, threshold={threshold})"
                            )
                            result = unconstrained
                            # Reset tracker to the corrected position
                            tracker.matches = [
                                (
                                    page_num,
                                    result.alignment.pg_end,
                                    result.best_score,
                                    result.alignment.pg_end - result.alignment.pg_start,
                                )
                            ]
                        else:
                            logger.debug(
                                f"Page {page_num}: unconstrained RETAS confirms tracker "
                                f"(diff={difference} < threshold={threshold})"
                            )
                    # If unconstrained RETAS also failed, skip this page
                    else:
                        logger.info(
                            f"Page {page_num}: unconstrained RETAS also failed, skipping"
                        )
                        continue

                results[i] = result
                tracker.record(page_num, result.alignment.pg_end, 0.40,
                             result.alignment.pg_end - result.alignment.pg_start)
                rolling_scores.append(result.best_score)
                if len(rolling_scores) > 10:
                    rolling_scores.pop(0)
                current_chapter_idx = self._update_chapter_from_position(
                    result.alignment.pg_start, real_chapters, current_chapter_idx
                )
                logger.info(
                    f"Page {page_num}: Phase 2 match "
                    f"[{result.alignment.pg_start}:{result.alignment.pg_end}] "
                    f"score={result.best_score:.2f} MEDIUM"
                )
                continue

            # No match — log and skip (no Phase 3 / global search)
            logger.info(f"Page {page_num}: NO MATCH")

        # -- Backward repair pass --
        results = self._repair_pass(results, transcriptions, pg_text, pg_paragraphs, tracker)

        return [r for r in results if r is not None]

    def _update_chapter_from_position(
        self, pg_start: int, chapters: list, current_idx: int
    ) -> int:
        """Derive current chapter from match position instead of heading.

        Returns the chapter index containing pg_start.
        """
        if not chapters:
            return current_idx
        for i, ch in enumerate(chapters):
            if ch.offset <= pg_start < ch.end_offset:
                if i != current_idx:
                    logger.debug(
                        f"  Chapter tracker corrected: {current_idx + 1} -> {i + 1} "
                        f"({ch.title}) from match position {pg_start}"
                    )
                return i
        return current_idx

    def _repair_pass(
        self,
        results: list[VisionAlignmentResult | None],
        transcriptions: list[PageTranscription],
        pg_text: str,
        pg_paragraphs: list[str],
        tracker: SequentialTracker,
    ) -> list[VisionAlignmentResult | None]:
        """Fill gaps using bilateral constraints from neighboring matches."""
        repaired = list(results)
        changed = True
        max_iterations = 5
        iteration = 0

        while changed and iteration < max_iterations:
            changed = False
            iteration += 1
            for i in range(len(repaired)):
                if repaired[i] is not None:
                    continue

                # Find nearest confirmed match before and after
                before = None
                after = None
                for j in range(i - 1, -1, -1):
                    if repaired[j] is not None:
                        before = repaired[j]
                        break
                for j in range(i + 1, len(repaired)):
                    if repaired[j] is not None:
                        after = repaired[j]
                        break

                if before and after:
                    window_start = max(0, before.alignment.pg_end - 500)
                    window_end = min(len(pg_text), after.alignment.pg_start + 500)
                elif before:
                    window_start = max(0, before.alignment.pg_end - 500)
                    window_end = min(len(pg_text), window_start + 10000)
                elif after:
                    window_end = after.alignment.pg_start + 500
                    window_start = max(0, window_end - 10000)
                else:
                    continue

                result = self.align_transcription_to_pg(
                    transcription=transcriptions[i], pg_text=pg_text,
                    pg_paragraphs=pg_paragraphs,
                    scan_page=transcriptions[i].page_num,
                    search_start=window_start, search_end=window_end,
                    min_score=GLOBAL_THRESHOLD,
                )
                if result:
                    repaired[i] = result
                    tracker.record(
                        transcriptions[i].page_num, result.alignment.pg_end, 0.35,
                        result.alignment.pg_end - result.alignment.pg_start,
                    )
                    logger.info(
                        f"Page {transcriptions[i].page_num}: REPAIR match "
                        f"[{result.alignment.pg_start}:{result.alignment.pg_end}] "
                        f"score={result.best_score:.2f}"
                    )
                    changed = True

        return repaired

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

    def _extract_distinctive_phrase(self, cleaned_text: str, start_offset: int = 30) -> str | None:
        """Extract a distinctive phrase from cleaned transcription for PG matching.

        Skips the first `start_offset` chars (chapter titles/headers), then
        extracts a 30-80 char sentence fragment suitable for str.find() search.
        """
        text = cleaned_text[start_offset:]
        if not text or len(text) < 20:
            return None

        # Find a sentence boundary after the header skip
        for delim in [". ", "! ", "? "]:
            idx = text.find(delim)
            if 0 < idx < 60:
                text = text[idx + 2:]
                break

        if len(text) < 20:
            return None

        # Take 30-80 chars (stop at sentence boundary if within range)
        phrase = text[:80]
        for delim in [". ", "! ", "? "]:
            idx = phrase.find(delim)
            if 30 <= idx <= 80:
                phrase = phrase[: idx + 1]
                break

        # If still too long, truncate at a word boundary
        if len(phrase) > 80:
            space_idx = phrase[:80].rfind(" ")
            if space_idx > 30:
                phrase = phrase[:space_idx]

        return phrase.strip() if len(phrase.strip()) >= 20 else None

    def validate_and_correct(
        self,
        alignments: list,
        transcriptions: list,
        pg_text: str,
        sample_size: int = 20,
        offset_tolerance: int = 500,
    ) -> tuple[list, ValidationResult]:
        """Validate alignment offsets and correct systematic drift.

        After align_all_pages(), run a validation pass that samples pages,
        extracts distinctive phrases from transcriptions, and measures offset
        against PG text. If a consistent shift is detected, corrects all
        alignments. No additional LLM calls — uses cached data only.

        Args:
            alignments: List of Alignment objects from align_all_pages().
            transcriptions: List of PageTranscription objects.
            pg_text: The full Project Gutenberg body text.
            sample_size: Max number of pages to sample.
            offset_tolerance: Max acceptable per-page offset in chars.

        Returns:
            Tuple of (corrected_alignments, ValidationResult).
        """
        result = ValidationResult()

        if not alignments:
            return alignments, result

        # Build lookup: page_num → transcription
        trans_by_page: dict[int, PageTranscription] = {}
        for t in transcriptions:
            if t.success and t.transcription_cleaned:
                trans_by_page[t.page_num] = t

        # Step 1: Sample pages spread across the book
        sorted_alignments = sorted(alignments, key=lambda a: a.scan_page)
        n = len(sorted_alignments)
        step = max(1, n // sample_size)

        candidates: list[tuple] = []  # (alignment, transcription, confidence)
        for i in range(0, n, step):
            a = sorted_alignments[i]
            t = trans_by_page.get(a.scan_page)
            if t and len(t.transcription_cleaned) >= 50:
                candidates.append((a, t, a.confidence))

        # Sort by confidence descending, take top sample_size
        candidates.sort(key=lambda x: x[2], reverse=True)
        candidates = candidates[:sample_size]
        result.sample_size = len(candidates)

        # Edge case: too few valid samples
        if len(candidates) < 5:
            logger.info(
                f"Validation skipped: only {len(candidates)} valid samples (< 5 required)"
            )
            return alignments, result

        # Step 2: Measure per-sample offset
        norm_pg = normalize_for_matching(pg_text)
        page_offsets: list[tuple[int, int]] = []  # (scan_page, offset)

        for alignment, trans, _conf in candidates:
            phrase = self._extract_distinctive_phrase(trans.transcription_cleaned)
            if not phrase:
                continue

            norm_phrase = normalize_for_matching(phrase)
            if len(norm_phrase) < 15:
                continue

            found_pos = norm_pg.find(norm_phrase)
            if found_pos == -1:
                # Try shorter substrings
                for sub_len in [len(norm_phrase) // 2, 15]:
                    if sub_len < 15:
                        break
                    found_pos = norm_pg.find(norm_phrase[:sub_len])
                    if found_pos != -1:
                        break

            if found_pos == -1:
                continue

            # If phrase appears multiple times, pick the position closest to alignment
            search_start = norm_pg.find(norm_phrase[:30]) if len(norm_phrase) >= 30 else found_pos
            if search_start != -1:
                next_pos = norm_pg.find(norm_phrase[:30], search_start + 1)
                if next_pos != -1:
                    if abs(found_pos - alignment.pg_start) > abs(next_pos - alignment.pg_start):
                        found_pos = next_pos

            page_offset = found_pos - alignment.pg_start
            page_offsets.append((alignment.scan_page, page_offset))

        if len(page_offsets) < 5:
            logger.info(
                f"Validation skipped: only {len(page_offsets)} offset measurements (< 5)"
            )
            return alignments, result

        result.sample_size = len(page_offsets)

        # Step 3: Compute statistics
        offsets_only = [o for _, o in page_offsets]
        result.offset_mean = sum(offsets_only) / len(offsets_only)
        variance = sum((o - result.offset_mean) ** 2 for o in offsets_only) / len(offsets_only)
        result.offset_stddev = variance**0.5
        result.pages_correct = sum(1 for o in offsets_only if abs(o) < offset_tolerance)
        result.pages_incorrect = len(page_offsets) - result.pages_correct

        accuracy = result.pages_correct / len(page_offsets)

        # Step 3b: Linear regression for drift detection
        # Fit: offset = slope * page + intercept
        n_samples = len(page_offsets)
        sum_x = sum(p for p, _ in page_offsets)
        sum_y = sum(o for _, o in page_offsets)
        sum_xy = sum(p * o for p, o in page_offsets)
        sum_x2 = sum(p * p for p, _ in page_offsets)
        denom = n_samples * sum_x2 - sum_x * sum_x

        if denom != 0:
            result.drift_slope = (n_samples * sum_xy - sum_x * sum_y) / denom
            result.drift_intercept = (sum_y - result.drift_slope * sum_x) / n_samples
        else:
            result.drift_slope = 0.0
            result.drift_intercept = result.offset_mean

        # Compute residual stddev (how well the line fits)
        if denom != 0:
            residuals = [o - (result.drift_slope * p + result.drift_intercept) for p, o in page_offsets]
            residual_var = sum(r * r for r in residuals) / len(residuals)
            result.residual_stddev = residual_var**0.5
        else:
            result.residual_stddev = result.offset_stddev

        # Step 4: Decision
        if accuracy >= 0.8:
            result.verdict = "ok"
            logger.info(
                f"Validation ok: {result.pages_correct}/{result.sample_size} samples "
                f"within tolerance, mean_offset={result.offset_mean:+.0f}"
            )
            return alignments, result

        # Check if the drift is linear (good fit) or random (bad fit)
        # A good linear fit means residual stddev << raw stddev
        # Threshold: residual < 30% of raw stddev indicates meaningful linear trend
        linear_fit_quality = result.residual_stddev / max(result.offset_stddev, 1.0)

        if linear_fit_quality < 0.30 and abs(result.drift_slope) > 10:
            # Cumulative drift — apply per-page linear correction, then re-measure
            # and apply residual constant shift in one pass
            result.verdict = "drift_corrected"
            result.corrected = True
            first_page = min(p for p, _ in page_offsets)

            # Phase 1: linear drift correction
            for a in alignments:
                page_relative = a.scan_page - first_page
                correction = round(result.drift_slope * page_relative + result.drift_intercept)
                a.pg_start += correction
                a.pg_end += correction
                a.pg_start = max(0, a.pg_start)
                a.pg_end = max(a.pg_start, min(a.pg_end, len(pg_text)))

            # Phase 2: re-measure offsets on corrected alignments to find residual shift
            residual_offsets = []
            for alignment, trans, _conf in candidates:
                phrase = self._extract_distinctive_phrase(trans.transcription_cleaned)
                if not phrase:
                    continue
                norm_phrase = normalize_for_matching(phrase)
                if len(norm_phrase) < 15:
                    continue
                found_pos = norm_pg.find(norm_phrase)
                if found_pos == -1:
                    for sub_len in [len(norm_phrase) // 2, 15]:
                        if sub_len < 15:
                            break
                        found_pos = norm_pg.find(norm_phrase[:sub_len])
                        if found_pos != -1:
                            break
                if found_pos == -1:
                    continue
                residual_offsets.append(found_pos - alignment.pg_start)

            if len(residual_offsets) >= 5:
                residual_mean = sum(residual_offsets) / len(residual_offsets)
                residual_shift = round(residual_mean)
                if abs(residual_shift) > 50:
                    for a in alignments:
                        a.pg_start += residual_shift
                        a.pg_end += residual_shift
                        a.pg_start = max(0, a.pg_start)
                        a.pg_end = max(a.pg_start, min(a.pg_end, len(pg_text)))
                    result.drift_intercept += residual_shift
                    logger.info(
                        f"Alignment drift-corrected: slope={result.drift_slope:.1f} chars/page, "
                        f"intercept={result.drift_intercept:+.0f}, "
                        f"raw_σ={result.offset_stddev:.0f}, residual_σ={result.residual_stddev:.0f}, "
                        f"second_pass_shift={residual_shift:+d}"
                    )
                else:
                    logger.info(
                        f"Alignment drift-corrected: slope={result.drift_slope:.1f} chars/page, "
                        f"intercept={result.drift_intercept:+.0f}, "
                        f"raw_σ={result.offset_stddev:.0f}, residual_σ={result.residual_stddev:.0f}"
                    )
            else:
                logger.info(
                    f"Alignment drift-corrected: slope={result.drift_slope:.1f} chars/page, "
                    f"intercept={result.drift_intercept:+.0f}, "
                    f"raw_σ={result.offset_stddev:.0f}, residual_σ={result.residual_stddev:.0f}"
                )
            return alignments, result

        if result.offset_stddev < 1500:
            # Moderate scatter — constant shift + drop worst
            result.verdict = "corrected"
            result.corrected = True
            shift = round(result.offset_mean)
            for a in alignments:
                a.pg_start += shift
                a.pg_end += shift
                a.pg_start = max(0, a.pg_start)
                a.pg_end = max(a.pg_start, min(a.pg_end, len(pg_text)))
            # Drop bottom 20% by confidence
            if alignments:
                threshold_conf = sorted(a.confidence for a in alignments)[len(alignments) // 5]
                dropped_count = sum(1 for a in alignments if a.confidence < threshold_conf)
                alignments = [a for a in alignments if a.confidence >= threshold_conf]
                result.dropped = dropped_count
            logger.info(
                f"Alignment corrected + rescored: shifted {shift:+d} chars, "
                f"dropped {result.dropped} low-confidence pages "
                f"(mean={result.offset_mean:+.0f}, σ={result.offset_stddev:.0f})"
            )
            return alignments, result

        # Random errors, not a correctable pattern
        result.verdict = "failed"
        logger.warning(
            f"Alignment validation failed: offset inconsistent "
            f"(mean={result.offset_mean:+.0f}, σ={result.offset_stddev:.0f}, "
            f"drift_slope={result.drift_slope:.1f}, fit_quality={linear_fit_quality:.2f})"
        )
        return alignments, result

# Paratext stripping needs to be added as a function
# I'll write it inline
