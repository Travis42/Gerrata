"""gerrata CLI — automated post-publication quality audit for Project Gutenberg texts."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.logging import RichHandler

from gerrata.models import Error, Report, PGMetadata
from gerrata.fetcher.pg import PGFetcher, PGParsedText
from gerrata.fetcher.scans import ScanFetcher, ScanData
from gerrata.aligner.coarse import CoarseAligner
from gerrata.aligner.vision_aligner import VisionAligner, VisionTranscriber
from gerrata.checker.text_diff import TextDiffChecker
from gerrata.checker.rules import FalsePositiveFilter
from gerrata.verifier.vision import VisionVerifier
from gerrata.reporter.generator import ReportGenerator



def load_intermediate(cache_dir: Path, name: str) -> Any | None:
    """Load an intermediate pipeline result. Returns None if not found."""
    path = cache_dir / f"{name}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)
    logging.getLogger(__name__).info(f"  Loaded intermediate: {path}")


def save_intermediate(cache_dir: Path, name: str, data) -> None:
    """Save intermediate pipeline result for re-running later steps."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{name}.json"

    def _serialize(obj):
        """Recursively serialize dataclasses and common types to JSON-safe forms."""
        from dataclasses import asdict, is_dataclass
        if isinstance(obj, dict):
            return {k: _serialize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_serialize(item) for item in obj]
        if is_dataclass(obj) and not isinstance(obj, type):
            return _serialize(asdict(obj))
        if hasattr(obj, 'to_dict'):
            return _serialize(obj.to_dict())
        return obj

    serializable = _serialize(data)
    with open(path, 'w') as f:
        json.dump(serializable, f, indent=2, default=str)
    logging.getLogger(__name__).info(f"  Saved intermediate: {path}")

def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=Console(stderr=True), show_time=False)],
    )


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="gerrata",
        description="Automated post-publication quality audit for Project Gutenberg texts",
    )
    parser.add_argument(
        "pg_id",
        type=int,
        help="Project Gutenberg ebook ID",
    )
    parser.add_argument(
        "--scan-id",
        type=str,
        default="",
        help="Internet Archive identifier for source scan (default: auto-detect or prompt)",
    )
    parser.add_argument(
        "--pg-file",
        type=str,
        default="",
        help="Local path to PG text file (skip download)",
    )
    parser.add_argument(
        "--ocr-file",
        type=str,
        default="",
        help="Local path to OCR text file (skip download)",
    )
    parser.add_argument(
        "--pages-dir",
        type=str,
        default="",
        help="Local directory with pre-extracted PNG page images (skip zip download)",
    )
    parser.add_argument(
        "--jp2-zip",
        type=str,
        default="",
        help="Local path to JP2 zip file (skip download)",
    )
    parser.add_argument(
        "--jp2-pattern",
        type=str,
        default="",
        help="URL pattern for JP2 page images (NNNN = page number placeholder)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="./reports",
        help="Output directory for reports (default: ./reports)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip LLM vision verification and use OCR-based alignment (fast, free)",
    )
    parser.add_argument(
        "--vision-transcribe",
        action="store_true",
        default=True,
        help="Use vision model to transcribe pages for alignment (default: true)",
    )
    parser.add_argument(
        "--no-vision-transcribe",
        dest="vision_transcribe",
        action="store_false",
        help="Disable vision transcription, use OCR-based alignment instead",
    )
    parser.add_argument(
        "--vision-url",
        type=str,
        default="https://openrouter.ai/api/v1/chat/completions",
        help="Vision model API URL for page transcription (Step 3)",
    )
    parser.add_argument(
        "--vision-key",
        type=str,
        default=os.environ.get("OPENROUTER_API_KEY", ""),
        help="Vision model API key for transcription (default: OPENROUTER_API_KEY environment variable)",
    )
    parser.add_argument(
        "--vision-model",
        type=str,
        default="",
        help="Vision model name for transcription (default: auto-select from fallback chain)",
    )
    parser.add_argument(
        "--verify-url",
        type=str,
        default="",
        help="Vision model API URL for verification (Step 7) (default: same as --vision-url)",
    )
    parser.add_argument(
        "--verify-key",
        type=str,
        default="",
        help="Vision model API key for verification (default: same as --vision-key)",
    )
    parser.add_argument(
        "--verify-model",
        type=str,
        default="",
        help="Vision model name for verification (default: same as --vision-model)",
    )
    parser.add_argument(
        "--page-range",
        type=str,
        default="",
        help="Page range to process, e.g. '48-100' (inclusive)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Use strict false positive filtering (keep more candidates)",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default="",
        help="Cache directory for downloads",
    )
    parser.add_argument(
        "--resume-from",
        type=str,
        choices=["pg-parsed", "transcriptions", "alignments", "candidates-raw", "candidates-filtered"],
        default="",
        help="Resume pipeline from an intermediate save point. "
             "Requires cached results in the scan ID cache directory.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Number of concurrent API calls for transcription and verification (default: 5)",
    )
    parser.add_argument(
        "--verify-provider",
        type=str,
        default="",
        choices=["zai", "openrouter", "openai", "anthropic"],
        help="Preset API provider for verification. Sets --verify-url and --verify-key "
             "automatically. Override with --verify-url/--verify-key if needed. "
             "zai: Z.AI native API (default). "
             "openrouter: openrouter.ai (reads OPENROUTER_API_KEY env var or ~/.secrets/openrouter.key). "
             "openai: OpenAI API. "
             "anthropic: Anthropic API.",
    )
    return parser


def parse_page_range(range_str: str) -> tuple[int, int] | None:
    """Parse a page range string like '48-100' into (start, end) tuple."""
    if not range_str:
        return None
    parts = range_str.split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid page range format: {range_str} (expected 'START-END')")
    return int(parts[0]), int(parts[1])


def resolve_verify_provider(args: argparse.Namespace) -> tuple[str, str]:
    """Resolve verification API URL and key from provider preset.

    Priority: explicit --verify-url/--verify-key > --verify-provider preset > defaults.

    Returns (url, key).
    """
    # Explicit overrides take priority
    if args.verify_url and args.verify_key:
        return args.verify_url, args.verify_key

    provider = args.verify_provider

    if provider == "openrouter":
        url = "https://openrouter.ai/api/v1/chat/completions"
        key = args.verify_key
        if not key:
            key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            key_path = Path.home() / ".secrets" / "openrouter.key"
            if key_path.exists():
                key = key_path.read_text().strip()
        if not key:
            raise ValueError(
                "OpenRouter provider requires an API key. Set OPENROUTER_API_KEY env var "
                "or create ~/.secrets/openrouter.key"
            )
        return url, key

    if provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
        key = args.verify_key or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise ValueError("OpenAI provider requires OPENAI_API_KEY env var")
        return url, key

    if provider == "anthropic":
        # Anthropic uses the OpenAI-compatible Messages API format
        url = "https://api.anthropic.com/v1/messages"
        key = args.verify_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ValueError("Anthropic provider requires ANTHROPIC_API_KEY env var")
        return url, key

    # Default: OpenRouter (uses --vision-url/--vision-key if set, or env var / key file)
    url = args.verify_url or args.vision_url
    key = args.verify_key or args.vision_key
    if not key:
        key_path = Path.home() / ".secrets" / "openrouter.key"
        if key_path.exists():
            key = key_path.read_text().strip()
    return url, key


async def run_pipeline(args: argparse.Namespace) -> Report:
    """Run the full gerrata pipeline."""
    logger = logging.getLogger(__name__)
    console = Console()

    # Determine mode
    vision_mode = args.vision_transcribe

    # Initialize components
    cache_dir = Path(args.cache_dir) if args.cache_dir else Path("./cache")

    pg_fetcher = PGFetcher(cache_dir=cache_dir)
    scan_fetcher = ScanFetcher(cache_dir=cache_dir)

    scan_id = args.scan_id
    page_range = parse_page_range(args.page_range)
    intermed_dir = cache_dir / scan_id if scan_id else cache_dir / f"pg{args.pg_id}"
    resume_from = args.resume_from

    # Step 1: Parse PG text
    if resume_from in ("transcriptions", "alignments", "candidates-raw", "candidates-filtered"):
        cached = load_intermediate(intermed_dir, "01_pg_parsed")
        if not cached:
            raise ValueError(f"--resume-from={resume_from} but 01_pg_parsed.json not found in {intermed_dir}")
        from gerrata.fetcher.pg import ChapterLocation
        chapters = [
            ChapterLocation(**ch) if isinstance(ch, dict) else ch
            for ch in cached["chapters"]
        ]
        parsed = PGParsedText(
            metadata=PGMetadata(title=cached["title"], author=cached["author"], pg_id=args.pg_id),
            body_text=cached["body_text"],
            full_text=cached["body_text"],  # full_text not saved separately; body_text is sufficient
            paragraphs=cached["paragraphs"],
            chapters=chapters,
        )
        console.print("[bold blue]Step 1:[/bold blue] Parsing PG text...")
        console.print(f"  Title: {parsed.metadata.title}")
        console.print(f"  Author: {parsed.metadata.author}")
        console.print(f"  Body: {len(parsed.body_text):,} chars, {len(parsed.paragraphs)} paragraphs")
        console.print(f"  Chapters: {len(parsed.chapters)}")
        console.print(f"  [dim]Resumed from 01_pg_parsed[/dim]")
    else:
        console.print("[bold blue]Step 1:[/bold blue] Parsing PG text...")
        if args.pg_file:
            parsed = pg_fetcher.parse_file(args.pg_file)
        else:
            pg_path = await pg_fetcher.download(args.pg_id, dest=cache_dir or Path("./cache"))
            parsed = pg_fetcher.parse_file(pg_path)

        console.print(f"  Title: {parsed.metadata.title}")
        console.print(f"  Author: {parsed.metadata.author}")
        console.print(f"  Body: {len(parsed.body_text):,} chars, {len(parsed.paragraphs)} paragraphs")
        console.print(f"  Chapters: {len(parsed.chapters)}")
        save_intermediate(intermed_dir, "01_pg_parsed", {
            "title": parsed.metadata.title,
            "author": parsed.metadata.author,
            "body_text": parsed.body_text,
            "paragraphs": parsed.paragraphs,
            "chapters": parsed.chapters,
        })
        console.print(f"  Mode: {'[green]vision-first[/green]' if vision_mode else '[yellow]OCR-based[/yellow]'}")

    # Determine vision_mode when resuming — check if transcriptions cache exists
    if resume_from in ("alignments", "candidates-raw", "candidates-filtered"):
        cached_transcriptions = load_intermediate(intermed_dir, "02_transcriptions")
        vision_mode = cached_transcriptions is not None

    # Step 2: Get page images (vision mode) or load OCR (OCR mode)
    alignments = []
    scan_pages = []

    if vision_mode:
        if resume_from in ("transcriptions", "alignments", "candidates-raw", "candidates-filtered"):
            # Resuming — load transcriptions from pipeline intermediate cache
            cached = load_intermediate(intermed_dir, "02_transcriptions")
            if not cached:
                # Fallback: transcriber has its own cache file with a different
                # format. Convert it to the pipeline format so we can resume
                # after a crash that happened before save_intermediate ran.
                transcriber_cache = Path(f"cache/{scan_id}_transcriptions.json")
                if transcriber_cache.exists():
                    logging.getLogger(__name__).info(
                        f"Pipeline intermediate 02_transcriptions.json not found, "
                        f"falling back to transcriber cache {transcriber_cache}"
                    )
                    with open(transcriber_cache) as f:
                        tc_data = json.load(f)
                    cached_pages = tc_data.get("pages", {})
                    cached = []
                    for filename, entry in sorted(cached_pages.items()):
                        cached.append({
                            "page_num": int("".join(filter(str.isdigit, filename)) or 0),
                            "image_path": str(Path(f"cache/pages/{filename}")),
                            "transcription": entry.get("text", ""),
                            "transcription_cleaned": None,  # Will be re-derived
                            "success": entry.get("success", bool(entry.get("text"))),
                            "error": None,
                            "model_used": entry.get("model", ""),
                        })
                    console.print(
                        f"  [dim]Converted {len(cached)} transcriptions from "
                        f"transcriber cache[/dim]"
                    )
                else:
                    raise ValueError(
                        f"--resume-from={resume_from} but neither "
                        f"02_transcriptions.json nor transcriber cache found "
                        f"in {intermed_dir}"
                    )
            from gerrata.aligner.vision_aligner import PageTranscription
            successful = [PageTranscription(
                page_num=t["page_num"],
                image_path=Path(t["image_path"]) if t.get("image_path") else None,
                transcription=t["transcription"],
                transcription_cleaned=t.get("transcription_cleaned"),
                success=t["success"],
                error=t.get("error"),
            ) for t in cached]
            transcriptions = successful
            console.print(f"  [dim]Resumed {len(successful)} transcriptions from 02_transcriptions[/dim]")
        else:
            console.print("[bold blue]Step 2:[/bold blue] Getting page images...")

            # Get page image paths
            page_images: list[Path] = []

            if args.pages_dir:
                # Use pre-extracted pages
                pages_dir = Path(args.pages_dir)
                page_images = scan_fetcher.get_cached_pages(pages_dir)
                console.print(f"  Loaded {len(page_images)} pre-extracted pages from {pages_dir}")
            elif args.jp2_zip:
                # Use local zip
                zip_path = Path(args.jp2_zip)
                extract_dir = zip_path.parent / "pages"
                page_images = scan_fetcher.extract_jp2_zip(
                    zip_path, dest=extract_dir, page_range=page_range
                )
                console.print(f"  Extracted {len(page_images)} pages from {zip_path.name}")
            elif scan_id:
                # Download zip from IA
                if not scan_id:
                    raise ValueError("--scan-id is required for vision mode without --pages-dir or --jp2-zip")

                zip_path = await scan_fetcher.download_jp2_zip(
                    identifier=scan_id,
                    dest=cache_dir or Path("./cache"),
                )
                extract_dir = (cache_dir or Path("./cache")) / "pages"
                page_images = scan_fetcher.extract_jp2_zip(
                    zip_path, dest=extract_dir, page_range=page_range
                )
                console.print(f"  Downloaded and extracted {len(page_images)} pages")
            else:
                raise ValueError(
                    "Vision mode requires --scan-id, --pages-dir, or --jp2-zip"
                )

            if not page_images:
                console.print("[red]No page images found. Cannot proceed in vision mode.[/red]")
                raise ValueError("No page images available")

            # Step 3: Transcribe pages with vision model
            console.print("[bold blue]Step 3:[/bold blue] Transcribing pages with vision model...")

            models = [args.vision_model] if args.vision_model else None
            transcriber = VisionTranscriber(
            api_url=args.vision_url,
            api_key=args.vision_key or None,
            models=models,
            ocr_engine="vision",  # Use GLM vision model (best accuracy for old book pages)
            concurrency=args.concurrency,
            cache_file=f"cache/{scan_id}_transcriptions.json",
        )

            transcriptions = await transcriber.transcribe_pages(page_images)
            successful = [t for t in transcriptions if t.success]
            console.print(f"  Transcribed {len(successful)}/{len(transcriptions)} pages")
            save_intermediate(intermed_dir, "02_transcriptions", successful)

            if not successful:
                console.print("[red]All transcriptions failed. Falling back to OCR mode.[/red]")
                vision_mode = False
            # end else (non-resume) block for Steps 2-3

    if not vision_mode:
        # OCR-based pipeline (original)
        console.print("[bold blue]Step 2:[/bold blue] Loading scan OCR text...")
        if args.ocr_file:
            scan_data = await scan_fetcher.prepare_scan(
                identifier=scan_id or f"pg{args.pg_id}",
                ocr_file=args.ocr_file,
                jp2_pattern=args.jp2_pattern,
                known_pages=0,
            )
        elif not vision_mode:
            if not scan_id:
                raise ValueError("Either --scan-id or --ocr-file is required")

            scan_data = await scan_fetcher.prepare_scan(
                identifier=scan_id,
                jp2_pattern=args.jp2_pattern,
            )
        else:
            scan_data = None
            console.print("  Skipping OCR (vision mode)")

        if not vision_mode:
            scan_full_text = "\n\n".join(p.ocr_text for p in scan_data.pages if p.ocr_text)
            console.print(f"  Source: {scan_data.source_url}")
            console.print(f"  OCR: {len(scan_full_text):,} chars, {len(scan_data.pages)} pages")

            # Align using LCS
            console.print("[bold blue]Step 3:[/bold blue] Aligning PG text to scan (LCS)...")
            coarse_aligner = CoarseAligner()
            alignments = coarse_aligner.align(
                pg_text=parsed.body_text,
                pg_paragraphs=parsed.paragraphs,
                scan_ocr_text=scan_full_text,
                scan_pages=scan_data.pages,
            )
            scan_pages = scan_data.pages

    if vision_mode and resume_from in ("candidates-raw", "candidates-filtered"):
        # Resume from alignments cache
        cached = load_intermediate(intermed_dir, "03_alignments")
        if not cached:
            raise ValueError(f"--resume-from={resume_from} but 03_alignments.json not found in {intermed_dir}")
        from gerrata.models import Alignment, AlignmentMethod
        alignments = [Alignment(
            pg_start=a["pg_start"],
            pg_end=a["pg_end"],
            scan_page=a["scan_page"],
            scan_image_path=a.get("scan_image_path", ""),
            confidence=a.get("confidence", 0),
            method=AlignmentMethod(a.get("method", "llm_vision")),
        ) for a in cached]

        cached_pages = load_intermediate(intermed_dir, "03_scan_pages")
        if cached_pages:
            from gerrata.fetcher.scans import ScanPage
            scan_pages = [ScanPage(
                page_num=p["page_num"],
                ocr_text=p.get("ocr_text", ""),
                vision_text=p.get("vision_text", ""),
                image_path=Path(p["image_path"]) if p.get("image_path") else None,
            ) for p in cached_pages]

        alignment_confidence = VisionAligner().alignment_confidence(alignments, len(parsed.body_text))
        console.print("[bold blue]Step 4:[/bold blue] Aligning transcriptions to PG text...")
        console.print(f"  [dim]Resumed {len(alignments)} alignments from 03_alignments (coverage {alignment_confidence:.0%})[/dim]")
    elif vision_mode and successful:
        # Align transcriptions to PG text
        console.print("[bold blue]Step 4:[/bold blue] Aligning transcriptions to PG text...")
        vision_aligner = VisionAligner(match_threshold=0.35, min_match_chars=40)
        results = vision_aligner.align_all_pages(
            transcriptions=transcriptions,
            pg_text=parsed.body_text,
            pg_paragraphs=parsed.paragraphs,
            chapters=parsed.chapters,
        )
        alignments = vision_aligner.get_alignments(results)
        scan_pages = vision_aligner.build_scan_pages_from_transcriptions(transcriptions)

        alignment_confidence = vision_aligner.alignment_confidence(alignments, len(parsed.body_text))
        console.print(f"  Matched: {len(alignments)}/{len(transcriptions)} pages")
        console.print(f"  Coverage: {alignment_confidence:.0%}")

        # Step 4a: Validate and correct alignment offset
        alignments, validation = vision_aligner.validate_and_correct(
            alignments=alignments,
            transcriptions=transcriptions,
            pg_text=parsed.body_text,
        )
        console.print(f"  Validation: {validation.verdict}")
        if validation.verdict == "corrected":
            console.print(f"  Offset corrected: {validation.offset_mean:+.0f} chars (σ={validation.offset_stddev:.0f})")
            console.print(f"  Accuracy: {validation.pages_correct}/{validation.sample_size} samples")
        elif validation.verdict == "drift_corrected":
            console.print(f"  Drift corrected: {validation.drift_slope:.1f} chars/page")
            console.print(f"  Residual σ: {validation.residual_stddev:.0f} (from {validation.offset_stddev:.0f})")
            console.print(f"  Accuracy: {validation.pages_correct}/{validation.sample_size} samples")
        elif validation.verdict == "failed":
            console.print(f"  [yellow]Alignment validation failed — offset too inconsistent (σ={validation.offset_stddev:.0f})[/yellow]")
        elif validation.verdict == "failed":
            console.print(f"  [yellow]Alignment validation failed — offset too inconsistent (σ={validation.offset_stddev:.0f})[/yellow]")

        save_intermediate(intermed_dir, "03_alignments", alignments)
        save_intermediate(intermed_dir, "03_scan_pages", scan_pages)
    elif vision_mode:
        # Vision mode was set but all transcriptions failed — already handled above
        pass
    else:
        alignment_confidence = CoarseAligner().alignment_confidence(alignments, len(parsed.body_text))
        console.print(f"  Alignments: {len(alignments)}")
        console.print(f"  Coverage: {alignment_confidence:.0%}")

    # Step 5: Text diff
    step_num = 5 if vision_mode else 4
    if resume_from == "candidates-filtered":
        cached = load_intermediate(intermed_dir, "04_candidates_raw")
        if not cached:
            raise ValueError("--resume-from=candidates-filtered but 04_candidates_raw.json not found")
        from gerrata.models import CandidateError, ErrorCategory, ErrorSeverity
        candidates = []
        for c in cached:
            # Reconstruct enums that were serialized as strings
            c_copy = dict(c)
            if "category" in c_copy and isinstance(c_copy["category"], str):
                c_copy["category"] = ErrorCategory(c_copy["category"])
            if "severity" in c_copy and isinstance(c_copy["severity"], str):
                c_copy["severity"] = ErrorSeverity(c_copy["severity"])
            candidates.append(CandidateError(**c_copy))
        console.print(f"[bold blue]Step {step_num}:[/bold blue] Running text diff...")
        console.print(f"  [dim]Resumed {len(candidates)} raw candidates from 04_candidates_raw[/dim]")
    else:
        console.print(f"[bold blue]Step {step_num}:[/bold blue] Running text diff...")
        checker = TextDiffChecker()
        candidates = checker.check_all_alignments(
            pg_text=parsed.body_text,
            alignments=alignments,
            scan_pages=scan_pages,
        )
        console.print(f"  Raw candidates: {len(candidates)}")
        save_intermediate(intermed_dir, "04_candidates_raw", candidates)

    # Step 6: False positive filter
    console.print(f"[bold blue]Step {step_num + 1}:[/bold blue] Filtering false positives...")
    fp_filter = FalsePositiveFilter(strict=args.strict)
    candidates = fp_filter.filter(candidates)
    console.print(f"  After filtering: {len(candidates)}")

    # Step 6b: Word-boundary cutoff artifact filter
    def is_cutoff_artifact(scan_text: str, pg_text: str) -> bool:
        """Check if a diff is a word-boundary cutoff artifact.

        Diffs like "hen"→"when" are alignment artifacts where text was split at
        a word boundary during transcription. The scan says "when" but the PG
        alignment picked up only "hen" because the 'w' was part of a previous
        matched segment. These are not real errata.

        Args:
            scan_text: Text from the scan transcription
            pg_text: Text from the PG text

        Returns:
            True if this is likely a cutoff artifact
        """
        from gerrata.models import ErrorCategory

        s = scan_text.strip()
        p = pg_text.strip()

        # Both must be single words (no spaces)
        if ' ' in s or ' ' in p:
            return False

        # One must be suffix of the other with exactly 1 char difference
        if abs(len(s) - len(p)) != 1:
            return False

        longer, shorter = (s, p) if len(s) > len(p) else (p, s)

        # Check if shorter is a prefix or suffix of longer
        if not longer.startswith(shorter) and not longer.endswith(shorter):
            return False

        # If the shorter text is ≤ 3 chars, it's likely a fragment, not a real word
        # This catches "hen" (3 chars) but passes "clause" (6 chars)
        if len(shorter) <= 3:
            return True

        return False

    # Apply cutoff artifact filter
    filtered_candidates = []
    artifacts_count = 0
    for candidate in candidates:
        if is_cutoff_artifact(candidate.scan_text, candidate.pg_text):
            # Mark as alignment artifact
            from gerrata.models import CandidateError, ErrorCategory
            # Create a new candidate with the artifact category
            artifact_candidate = CandidateError(
                pg_text=candidate.pg_text,
                scan_text=candidate.scan_text,
                pg_offset=candidate.pg_offset,
                scan_page=candidate.scan_page,
                diff_description=candidate.diff_description,
                category=ErrorCategory.ALIGNMENT_ARTIFACT,
                severity=candidate.severity,
            )
            filtered_candidates.append(artifact_candidate)
            artifacts_count += 1
        else:
            filtered_candidates.append(candidate)

    candidates = filtered_candidates
    if artifacts_count > 0:
        console.print(f"  Filtered {artifacts_count} word-boundary cutoff artifacts")

    # Step 6b: Filter absent-in-PG entries
    # These are text present in the scan but completely missing from PG —
    # often alignment artifacts where the diff spanned a paragraph boundary.
    absent_count = 0
    filtered_candidates = []
    for candidate in candidates:
        if '(absent in PG)' in candidate.pg_text or '(absent in scan)' in candidate.scan_text:
            absent_count += 1
            continue
        filtered_candidates.append(candidate)
    candidates = filtered_candidates
    if absent_count > 0:
        console.print(f"  Filtered {absent_count} absent-text entries (alignment artifacts)")

    # Step 6c: Additional false positive filters
    console.print(f"[bold blue]Step {step_num + 1}c:[/bold blue] Additional filtering...")

    # Filter 1: Long mismatch filter
    def is_long_mismatch(scan_text: str, pg_text: str) -> bool:
        """Check if a diff is a long mismatch artifact.
        
        Alignment sometimes spans multiple sentences, producing diffs where one side
        is a short phrase and the other is 40+ chars of unrelated text.
        
        Args:
            scan_text: Text from the scan transcription
            pg_text: Text from the PG text
            
        Returns:
            True if this is likely a long mismatch artifact
        """
        return len(scan_text.strip()) > 40 or len(pg_text.strip()) > 40

    # Filter 2: HTML artifact filter
    def is_html_artifact(scan_text: str, pg_text: str) -> bool:
        """Check if a diff contains HTML/image markup artifacts.
        
        GLM-OCR sometimes picks up HTML markup from the scan.
        
        Args:
            scan_text: Text from the scan transcription
            pg_text: Text from the PG text
            
        Returns:
            True if this contains HTML/image markup
        """
        combined = scan_text + pg_text
        return any(marker in combined for marker in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])

    # Filter 3: ALL CAPS header filter
    def is_all_caps_header(scan_text: str) -> bool:
        """Check if scan text is an ALL CAPS header artifact.
        
        Chapter titles and ornamental headers get mismatched.
        Only check scan_text since pg_text could legitimately be uppercase.
        
        Args:
            scan_text: Text from the scan transcription
            
        Returns:
            True if this is likely an ALL CAPS header mismatch
        """
        stripped = scan_text.strip()
        return stripped.isupper() and len(stripped) > 5

    # Filter 4: Suffix fragment filter (extended)
    def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
        """Check if a diff is a suffix/prefix fragment artifact.
        
        Longer word-boundary fragments like "terson,"→"Utterson,", "ugh"→"through".
        These are cases where the diff picked up a tail end of a word.
        
        Args:
            scan_text: Text from the scan transcription
            pg_text: Text from the PG text
            
        Returns:
            True if this is likely a suffix fragment artifact
        """
        s = scan_text.strip()
        p = pg_text.strip()
        
        # Both must be single tokens (no spaces in shorter)
        shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
        if ' ' in shorter:
            return False
        
        # Length difference must be small (≤3 chars)
        if len(longer) - len(shorter) > 3:
            return False
        
        # Shorter must be a suffix of longer
        if not longer.endswith(shorter):
            return False
        
        # Exception: singular/plural (e.g., "clause" → "clauses")
        # If longer ends with 's' and removing it gives the shorter text, it's a real error
        if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
            return False  # Likely a real singular/plural difference
        if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
            return False  # Same for -es endings
        
        # Exception: the shorter text is long enough to be a real word (≥6 chars)
        # Let's be conservative: only filter if shorter is ≤8 chars
        if len(shorter) > 8:
            return False
        
        return True

    # Filter 5: Quoted fragment filter
    def is_quoted_fragment(scan_text: str, pg_text: str) -> bool:
        """Check if a diff is a quoted fragment artifact.
        
        When dialogue starts with a quotation mark, the alignment sometimes grabs
        just the opening quote+word while PG has the full quoted sentence.
        
        Args:
            scan_text: Text from the scan transcription
            pg_text: Text from the PG text
            
        Returns:
            True if this is likely a quoted fragment artifact
        """
        s = scan_text.strip()
        p = pg_text.strip()
        
        # Check if shorter starts with a quote
        shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
        if not shorter:
            return False
        
        starts_with_quote = shorter[0] in '"\u201c\u201d\u2018\u00ab'  # " " ' ' «
        if not starts_with_quote:
            return False
        
        # Length difference must be significant (>20 chars)
        if len(longer) - len(shorter) <= 20:
            return False
        
        return True

    # Apply all filters
    FILTERS = [
        ("Long mismatches", is_long_mismatch),
        ("HTML artifacts", is_html_artifact),
        ("ALL CAPS headers", lambda s, p: is_all_caps_header(s)),
        ("Suffix fragments", is_suffix_fragment),
        ("Quoted fragments", is_quoted_fragment),
    ]

    filtered_candidates = []
    filter_counts = {name: 0 for name, _ in FILTERS}
    
    for candidate in candidates:
        filtered = False
        for filter_name, filter_fn in FILTERS:
            if filter_fn(candidate.scan_text, candidate.pg_text):
                # Mark as alignment artifact
                from gerrata.models import CandidateError, ErrorCategory
                artifact_candidate = CandidateError(
                    pg_text=candidate.pg_text,
                    scan_text=candidate.scan_text,
                    pg_offset=candidate.pg_offset,
                    scan_page=candidate.scan_page,
                    diff_description=candidate.diff_description,
                    category=ErrorCategory.ALIGNMENT_ARTIFACT,
                    severity=candidate.severity,
                )
                filtered_candidates.append(artifact_candidate)
                filter_counts[filter_name] += 1
                filtered = True
                break
        if not filtered:
            filtered_candidates.append(candidate)

    # Log filtering results
    total_filtered = sum(filter_counts.values())
    if total_filtered > 0:
        console.print(f"  Filtered {total_filtered} additional alignment artifacts:")
        for filter_name, count in filter_counts.items():
            if count > 0:
                console.print(f"    - {filter_name}: {count}")

    candidates = filtered_candidates

    # Step 6d: Calculate pg_file_line for each candidate (body-text approximation)
    # Note: ReportGenerator.enrich_errors_with_context() will refine these to
    # PG HTML file line numbers using compute_line_number().
    console.print(f"[bold blue]Step {step_num + 1}d:[/bold blue] Computing line numbers...")
    for candidate in candidates:
        # Find the PG text by string search (pg_offset may be inaccurate)
        pg_text = candidate.pg_text
        if '(absent in PG)' in pg_text or '(absent in scan)' in pg_text:
            pg_text = candidate.scan_text
        pos = parsed.body_text.find(pg_text)
        if pos >= 0:
            candidate.pg_file_line = parsed.body_text[:pos].count('\n') + 1
        else:
            # Fallback to offset-based
            candidate.pg_file_line = parsed.body_text[:candidate.pg_offset].count('\n') + 1
    console.print(f"  Computed line numbers for {len(candidates)} candidates")
    save_intermediate(intermed_dir, "05_candidates_filtered", candidates)

    # Step 7: LLM vision verification (if configured and in vision mode)
    verified_errors: list[Error] = []

    # Determine verify config (fallback to vision config if not set)
    verify_url, verify_key = resolve_verify_provider(args)
    verify_model = args.verify_model or args.vision_model

    if args.no_verify or not vision_mode:
        console.print(f"[bold blue]Step {step_num + 2}:[/bold blue] Skipping LLM verification (--no-verify or OCR mode)")
        for candidate in candidates:
            verified_errors.append(Error(candidate=candidate))
    elif verify_url and verify_key:
        console.print(f"[bold blue]Step {step_num + 2}:[/bold blue] LLM vision verification...")
        verifier = VisionVerifier(
            api_url=verify_url,
            api_key=verify_key,
            model=verify_model,
            concurrency=args.concurrency,
        )

        # Use batch verification for better performance and rate limit handling
        def get_image_path(error):
            if scan_pages:
                page_idx = min(error.scan_page, len(scan_pages) - 1)
                if scan_pages[page_idx].image_path:
                    return Path(scan_pages[page_idx].image_path)
            return None

        def get_pg_context(error):
            # Find the PG text by string search (pg_offset may be inaccurate)
            pg_text = error.pg_text
            if '(absent in PG)' in pg_text or '(absent in scan)' in pg_text:
                pg_text = error.scan_text
            pos = parsed.body_text.find(pg_text)
            if pos >= 0:
                start = max(0, pos - 200)
                end = min(len(parsed.body_text), pos + len(pg_text) + 200)
                return parsed.body_text[start:end]
            # Fallback to offset-based
            return parsed.body_text[max(0, error.pg_offset - 200):error.pg_offset + 200]

        verified_errors = await verifier.verify_batch_per_page(
            candidates,
            get_image_path=get_image_path,
            get_pg_context=get_pg_context,
        )

        console.print(f"  Verified: {len(verified_errors)}")
    else:
        console.print(f"[bold blue]Step {step_num + 2}:[/bold blue] Skipping LLM verification (not configured)")
        for candidate in candidates:
            verified_errors.append(Error(candidate=candidate))

    # Build report
    console.print(f"[bold blue]Step {step_num + 3}:[/bold blue] Generating report...")

    # Compute average per-page confidence
    avg_page_conf = sum(a.confidence for a in alignments) / len(alignments) if alignments else 0.0

    report = Report(
        metadata=parsed.metadata,
        scan_source=f"https://archive.org/details/{scan_id}" if scan_id else "local",
        date=args.pg_id,
        pages_checked=len(set(a.scan_page for a in alignments)),
        total_pages=len(scan_pages) if scan_pages else 0,
        alignment_confidence=alignment_confidence if 'alignment_confidence' in dir() else 0.0,
        avg_page_confidence=avg_page_conf,
        errors=verified_errors,
        alignments=alignments,
    )

    # Create generator with PG text context for line number mapping
    generator = ReportGenerator(
        pg_parsed_text=parsed,
        pg_file_path=args.pg_file,
        scan_id=scan_id,
        scan_pages=scan_pages,
        body_text=parsed.body_text,
    )
    json_path, email_path = generator.save_reports(report, args.output)
    generator.print_summary(report)

    console.print(f"[bold green]Reports saved:[/bold green]")
    console.print(f"  JSON: {json_path}")
    console.print(f"  Errata Email: {email_path}")

    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    try:
        report = asyncio.run(run_pipeline(args))

        if report.high_confidence_errors:
            return 2
        elif report.errors:
            return 1
        else:
            return 0

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 3


if __name__ == "__main__":
    sys.exit(main())
