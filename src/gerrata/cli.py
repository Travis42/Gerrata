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
from gerrata.aligner.vision_aligner import VisionAligner, VisionTranscriber
from gerrata.aligner.global_anchor import GlobalAnchorAligner
from gerrata.checker.text_diff import TextDiffChecker
from gerrata.checker.gap_detector import detect_scan_gaps, filter_for_report, gaps_to_candidate_errors
from gerrata.checker.rules import FalsePositiveFilter
from gerrata.verifier.programmatic import ProgrammaticVerifier
from gerrata.reporter.generator import ReportGenerator
from gerrata.reporter.substantive import SubstantiveErrataGenerator



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
    """Build CLI argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="gerrata",
        description="Automated post-publication quality audit for Project Gutenberg texts",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # ── verify-edition subcommand ──────────────────────────────────────
    ve_parser = subparsers.add_parser(
        "verify-edition",
        help="Verify whether an IA scan matches the PG source edition",
    )
    ve_parser.add_argument(
        "pg_id",
        type=int,
        help="Project Gutenberg ebook ID",
    )
    ve_parser.add_argument(
        "scan_id",
        type=str,
        help="Internet Archive identifier for the scan",
    )
    ve_parser.add_argument(
        "--approach",
        type=str,
        choices=["metadata", "linebreaks", "both"],
        default="both",
        help="Which approach(es) to use (default: both)",
    )
    ve_parser.add_argument(
        "--output", "-o",
        type=str,
        default="./reports",
        help="Output directory for results (default: ./reports)",
    )
    ve_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output",
    )
    ve_parser.add_argument(
        "--pg-file",
        type=str,
        default="",
        help="Local path to PG text file (skip download)",
    )
    ve_parser.add_argument(
        "--vision-url",
        type=str,
        default="",
        help="Vision model API URL (for line-break approach Strategy 2)",
    )
    ve_parser.add_argument(
        "--vision-key",
        type=str,
        default=os.environ.get("OPENROUTER_API_KEY", ""),
        help="Vision model API key (default: OPENROUTER_API_KEY env var)",
    )
    ve_parser.add_argument(
        "--vision-model",
        type=str,
        default="gemini-3.1-flash-lite",
        help="Vision model name (default: gemini-3.1-flash-lite)",
    )
    ve_parser.add_argument(
        "--cache-dir",
        type=str,
        default="",
        help="Cache directory for downloads",
    )

    # ── default pipeline (no subcommand) ──────────────────────────────
    parser.add_argument(
        "pg_id",
        type=int,
        nargs="?",
        default=None,
        help="Project Gutenberg ebook ID (for the main pipeline)",
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
        "--vision-transcribe",
        action="store_true",
        default=True,
        help=argparse.SUPPRESS,
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
        default="gemini-3.1-flash-lite",
        help="Vision model name for transcription (default: gemini-3.1-flash-lite)",
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
        choices=["pg-parsed", "transcriptions", "alignments", "candidates-raw", "pre-verify", "pre-report", "candidates-filtered"],
        default="",
        help="Resume pipeline from an intermediate save point. "
             "Requires cached results in the scan ID cache directory.",
    )
    parser.add_argument(
        "--substantive-report",
        action="store_true",
        default=False,
        help="Generate substantive errata report via LLM analysis (runs after report generation)",
    )
    parser.add_argument(
        "--substantive-model",
        type=str,
        default="google/glm-5.1",
        help="Model for substantive errata analysis (default: google/glm-5.1)",
    )
    parser.add_argument(
        "--substantive-key",
        type=str,
        default="",
        help="API key for substantive analysis (default: OPENROUTER_API_KEY env var)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Number of concurrent API calls (default: 10)",
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




async def run_pipeline(args: argparse.Namespace) -> Report:
    """Run the full gerrata pipeline."""
    logger = logging.getLogger(__name__)
    console = Console()

    # Initialize components
    cache_dir = Path(args.cache_dir) if args.cache_dir else Path("./cache")

    pg_fetcher = PGFetcher(cache_dir=cache_dir)
    scan_fetcher = ScanFetcher(cache_dir=cache_dir)

    scan_id = args.scan_id
    page_range = parse_page_range(args.page_range)
    intermed_dir = cache_dir / scan_id if scan_id else cache_dir / f"pg{args.pg_id}"
    resume_from = args.resume_from

    # Handle deprecated alias
    if resume_from == "candidates-filtered":
        console.print("[yellow]WARNING: --resume-from=candidates-filtered is deprecated, use --resume-from=pre-verify[/yellow]")
        resume_from = "pre-verify"

    # Print pipeline stage diagram
    STAGES = [
        ("pg-parse", "01_pg_parsed"),
        ("transcribe", "02_transcriptions"),
        ("align", "03_alignments"),
        ("diff", "04_candidates_raw"),
        ("filter", "05_candidates_filtered"),
        ("verify", "06_verified_errors"),
        ("report", None),
    ]
    RESUME_ORDER = ["pg-parsed", "transcriptions", "alignments", "candidates-raw", "pre-verify", "pre-report"]
    RESUME_INDEX = RESUME_ORDER.index(resume_from) if resume_from else -1

    # Map resume points to stage indices
    RESUME_TO_STAGE = {
        "pg-parsed": 0,
        "transcriptions": 1,
        "alignments": 2,
        "candidates-raw": 3,
        "pre-verify": 5,
        "pre-report": 6,
    }

    stage_labels = []
    for i, (name, intermed) in enumerate(STAGES):
        if resume_from and i < RESUME_TO_STAGE.get(resume_from, 0):
            stage_labels.append(f"[dim]{name} ✓ cached[/dim]")
        elif resume_from and i == RESUME_TO_STAGE.get(resume_from, -1):
            stage_labels.append(f"[bold cyan]{name} ← resume[/bold cyan]")
        else:
            stage_labels.append(f"[bold]{name}[/bold]")
    pipeline_str = " → ".join(stage_labels)
    console.print(f"[bold]Pipeline:[/bold] {pipeline_str}")
    console.print()

    # Step 1: Parse PG text
    if resume_from in ("transcriptions", "alignments", "candidates-raw", "pre-verify", "pre-report"):
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

    # Check transcriptions cache for resume
    if resume_from in ("alignments", "candidates-raw", "pre-verify", "pre-report"):
        cached_transcriptions = load_intermediate(intermed_dir, "02_transcriptions")

    # Step 2: Get page images
    alignments = []
    scan_pages = []

    if True:
        if resume_from in ("transcriptions", "alignments", "candidates-raw", "pre-verify", "pre-report"):
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
                if not scan_id:
                    # Derive scan_id from filename: "draculabr00stokuoft_jp2.zip" → "draculabr00stokuoft"
                    stem = zip_path.stem
                    if stem.endswith("_jp2"):
                        scan_id = stem[:-4]
                        console.print(f"  Derived scan_id: {scan_id}")
                    else:
                        console.print(f"  [dim]Could not derive scan_id from filename — report will show 'local'[/dim]")
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

            # Set up crash-resilient JSONL log for auto-resume
            transcription_log = intermed_dir / "02_transcriptions.jsonl"
            completed_pages = set()
            if transcription_log.exists():
                import json as _json
                for _line in transcription_log.read_text().strip().splitlines():
                    if _line:
                        try:
                            completed_pages.add(int(_json.loads(_line).get("page_num", -1)))
                        except (_json.JSONDecodeError, ValueError):
                            pass
                if completed_pages:
                    console.print(f"  [dim]Auto-resume: {len(completed_pages)}/{len(page_images)} pages already transcribed[/dim]")

            transcriber = VisionTranscriber(
            api_url=args.vision_url,
            api_key=args.vision_key or None,
            models=models,
            ocr_engine="vision",  # Use GLM vision model (best accuracy for old book pages)
            concurrency=args.concurrency,
            cache_file=f"cache/{scan_id}_transcriptions.json",
            transcription_log=transcription_log,
            cleanup_pages=True,
        )

            transcriptions = await transcriber.transcribe_pages(page_images)
            successful = [t for t in transcriptions if t.success]
            console.print(f"  Transcribed {len(successful)}/{len(transcriptions)} pages")
            save_intermediate(intermed_dir, "02_transcriptions", successful)

            if not successful:
                console.print("[red]All transcriptions failed. Aborting pipeline.[/red]")
                return

    if resume_from in ("alignments", "candidates-raw", "pre-verify", "pre-report"):
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
                vision_text=p.get("vision_text", ""),
                image_path=Path(p["image_path"]) if p.get("image_path") else None,
            ) for p in cached_pages]

        alignment_confidence = VisionAligner().alignment_confidence(alignments, len(parsed.body_text))
        console.print("[bold blue]Step 4:[/bold blue] Aligning transcriptions to PG text...")
        console.print(f"  [dim]Resumed {len(alignments)} alignments from 03_alignments (coverage {alignment_confidence:.0%})[/dim]")

        # Validate and correct cached alignments
        vision_aligner = VisionAligner()
        alignments, validation = vision_aligner.validate_and_correct(
            alignments=alignments,
            transcriptions=transcriptions,
            pg_text=parsed.body_text,
        )
        console.print(f"  Validation: {validation.verdict}")
        if validation.verdict == "drift_corrected":
            console.print(f"  Drift corrected: {validation.drift_slope:.1f} chars/page")
            console.print(f"  Residual σ: {validation.residual_stddev:.0f} (from {validation.offset_stddev:.0f})")
        elif validation.verdict == "corrected":
            console.print(f"  Offset corrected: {validation.offset_mean:+.0f} chars (σ={validation.offset_stddev:.0f})")
        elif validation.verdict == "failed":
            console.print(f"  [yellow]Alignment validation failed — offset too inconsistent (σ={validation.offset_stddev:.0f})[/yellow]")

        # Save corrected alignments back to cache
        save_intermediate(intermed_dir, "03_alignments", [a.to_dict() for a in alignments])
        save_intermediate(intermed_dir, "03_scan_pages", scan_pages)
    elif successful:
        # Align transcriptions to PG text using global word-sequence matching
        console.print("[bold blue]Step 4:[/bold blue] Aligning transcriptions to PG text...")
        console.print(f"  [dim]Using global anchor aligner (word-level matching)[/dim]")
        global_aligner = GlobalAnchorAligner(
            min_phrase_words=8,
            max_phrases_per_page=5,
            min_score=0.35,
        )
        alignments = global_aligner.align_all_pages(
            transcriptions=transcriptions,
            pg_text=parsed.body_text,
            pg_paragraphs=parsed.paragraphs,
            chapters=parsed.chapters,
        )
        scan_pages = VisionAligner().build_scan_pages_from_transcriptions(transcriptions)

        alignment_confidence = global_aligner.alignment_confidence(alignments, len(parsed.body_text))
        console.print(f"  Matched: {len(alignments)}/{len(transcriptions)} pages")
        console.print(f"  Coverage: {alignment_confidence:.0%}")

        # Step 4a: Validate and correct alignment offset
        # Note: Global anchor alignment doesn't accumulate sequential drift,
        # so we only run offset validation (not drift correction).
        vision_aligner = VisionAligner()
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
            # Global anchor aligner shouldn't have drift — reject the correction
            # if accuracy is low (suggests the correction is harmful)
            if validation.pages_correct / max(validation.sample_size, 1) < 0.5:
                console.print(f"  [yellow]Drift correction rejected: low accuracy ({validation.pages_correct}/{validation.sample_size})[/yellow]")
                console.print(f"  [dim](Global anchor alignment doesn't accumulate drift)[/dim]")
                # Revert to uncorrected alignments
                alignments = global_aligner.align_all_pages(
                    transcriptions=transcriptions,
                    pg_text=parsed.body_text,
                )
                scan_pages = VisionAligner().build_scan_pages_from_transcriptions(transcriptions)
            else:
                console.print(f"  Drift corrected: {validation.drift_slope:.1f} chars/page")
                console.print(f"  Residual σ: {validation.residual_stddev:.0f} (from {validation.offset_stddev:.0f})")
                console.print(f"  Accuracy: {validation.pages_correct}/{validation.sample_size} samples")
        elif validation.verdict == "failed":
            console.print(f"  [yellow]Alignment validation failed — offset too inconsistent (σ={validation.offset_stddev:.0f})[/yellow]")

        save_intermediate(intermed_dir, "03_alignments", alignments)
        save_intermediate(intermed_dir, "03_scan_pages", scan_pages)

    # Step 5: Text diff
    step_num = 5
    if resume_from == "pre-verify":
        cached = load_intermediate(intermed_dir, "05_candidates_filtered")
        if not cached:
            raise ValueError("--resume-from=pre-verify but 05_candidates_filtered.json not found")
        from gerrata.models import CandidateError, ErrorCategory, ErrorSeverity
        candidates = []
        for c in cached:
            c_copy = dict(c)
            if "category" in c_copy and isinstance(c_copy["category"], str):
                c_copy["category"] = ErrorCategory(c_copy["category"])
            if "severity" in c_copy and isinstance(c_copy["severity"], str):
                c_copy["severity"] = ErrorSeverity(c_copy["severity"])
            candidates.append(CandidateError(**c_copy))
        console.print(f"[bold blue]Step {step_num}:[/bold blue] Running text diff...")
        console.print(f"  [dim]Skipped — resumed {len(candidates)} filtered candidates from 05_candidates_filtered[/dim]")
    elif resume_from == "pre-report":
        cached = load_intermediate(intermed_dir, "06_verified_errors")
        if not cached:
            raise ValueError("--resume-from=pre-report but 06_verified_errors.json not found")
        from gerrata.models import Error, ErrorCategory, ErrorSeverity
        verified_errors = []
        for e in cached:
            e_copy = dict(e)
            if "category" in e_copy and isinstance(e_copy["category"], str):
                e_copy["category"] = ErrorCategory(e_copy["category"])
            if "severity" in e_copy and isinstance(e_copy["severity"], str):
                e_copy["severity"] = ErrorSeverity(e_copy["severity"])
            verified_errors.append(Error(**e_copy))
        console.print(f"[bold blue]Step {step_num}:[/bold blue] Running text diff...")
        console.print(f"  [dim]Skipped — resumed {len(verified_errors)} verified errors from 06_verified_errors[/dim]")
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

    # Step 5a: Detect coverage gaps (scan text missing from PG)
    if resume_from not in ("pre-verify", "pre-report"):
        console.print(f"  [bold blue]Detecting coverage gaps...[/bold blue]")
        all_gaps = detect_scan_gaps(
            pg_text=parsed.body_text,
            alignments=alignments,
            scan_pages=scan_pages,
        )
        if all_gaps:
            # Save all gaps to JSON for full analysis
            gap_json_path = intermed_dir / "05_gaps.json"
            with open(gap_json_path, "w") as f:
                json.dump([g.to_dict() for g in all_gaps], f, indent=2)
            console.print(f"  All gaps (saved to {gap_json_path.name}): {len(all_gaps)}")

            # Filter for report: only high-confidence, egregious gaps
            report_gaps = filter_for_report(all_gaps)
            if report_gaps:
                console.print(f"  High-confidence gaps for report: {len(report_gaps)}")
                candidates.extend(gaps_to_candidate_errors(report_gaps))
            else:
                console.print(f"  No high-confidence gaps for report")
        else:
            console.print(f"  No coverage gaps detected")

    # Step 6: False positive filter + additional filtering + line numbers
    if resume_from not in ("pre-verify", "pre-report"):
        # Step 6: False positive filter
        console.print(f"[bold blue]Step {step_num + 1}:[/bold blue] Filtering false positives...")
        fp_filter = FalsePositiveFilter(strict=args.strict)
        candidates = fp_filter.filter(candidates)
        console.print(f"  After filtering: {len(candidates)}")

        # Step 6b: Word-boundary cutoff artifact filter
        def is_cutoff_artifact(scan_text: str, pg_text: str) -> bool:
            """Check if a diff is a word-boundary cutoff artifact."""
            from gerrata.models import ErrorCategory
            s = scan_text.strip()
            p = pg_text.strip()
            if ' ' in s or ' ' in p:
                return False
            if abs(len(s) - len(p)) != 1:
                return False
            longer, shorter = (s, p) if len(s) > len(p) else (p, s)
            if not longer.startswith(shorter) and not longer.endswith(shorter):
                return False
            if len(shorter) <= 3:
                return True
            return False

        filtered_candidates = []
        artifacts_count = 0
        for candidate in candidates:
            if is_cutoff_artifact(candidate.scan_text, candidate.pg_text):
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
                artifacts_count += 1
            else:
                filtered_candidates.append(candidate)
        candidates = filtered_candidates
        if artifacts_count > 0:
            console.print(f"  Filtered {artifacts_count} word-boundary cutoff artifacts")

        # Step 6b: Filter absent-in-PG entries
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

        def is_long_mismatch(scan_text: str, pg_text: str) -> bool:
            return len(scan_text.strip()) > 40 or len(pg_text.strip()) > 40

        def is_html_artifact(scan_text: str, pg_text: str) -> bool:
            combined = scan_text + pg_text
            return any(marker in combined for marker in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])

        def is_all_caps_header(scan_text: str) -> bool:
            stripped = scan_text.strip()
            return stripped.isupper() and len(stripped) > 5

        def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if ' ' in shorter:
                return False
            if len(longer) - len(shorter) > 3:
                return False
            if not longer.endswith(shorter):
                return False
            if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
                return False
            if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
                return False
            if len(shorter) > 8:
                return False
            return True

        def is_quoted_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if not shorter:
                return False
            starts_with_quote = shorter[0] in '"\u201c\u201d\u2018\u00ab'
            if not starts_with_quote:
                return False
            if len(longer) - len(shorter) <= 20:
                return False
            return True

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

        total_filtered = sum(filter_counts.values())
        if total_filtered > 0:
            console.print(f"  Filtered {total_filtered} additional alignment artifacts:")
            for filter_name, count in filter_counts.items():
                if count > 0:
                    console.print(f"    - {filter_name}: {count}")
        candidates = filtered_candidates

        # Step 6d: Calculate pg_file_line for each candidate
        console.print(f"[bold blue]Step {step_num + 1}d:[/bold blue] Computing line numbers...")
        for candidate in candidates:
            pg_text = candidate.pg_text
            if '(absent in PG)' in pg_text or '(absent in scan)' in pg_text:
                pg_text = candidate.scan_text
            pos = parsed.body_text.find(pg_text)
            if pos >= 0:
                candidate.pg_file_line = parsed.body_text[:pos].count('\n') + 1
            else:
                candidate.pg_file_line = parsed.body_text[:candidate.pg_offset].count('\n') + 1
        console.print(f"  Computed line numbers for {len(candidates)} candidates")
        save_intermediate(intermed_dir, "05_candidates_filtered", candidates)
    else:
        if resume_from == "pre-verify":
            console.print(f"[bold blue]Step {step_num + 1}:[/bold blue] Filtering false positives...")
            console.print(f"  [dim]Skipped — loaded pre-filtered candidates from 05_candidates_filtered[/dim]")
        else:
            console.print(f"[bold blue]Step {step_num + 1}:[/bold blue] Filtering false positives...")
            console.print(f"  [dim]Skipped — loaded verified errors from 06_verified_errors[/dim]")

    # Step 7: Programmatic verification
    if resume_from != "pre-report":
        console.print(f"[bold blue]Step {step_num + 2}:[/bold blue] Programmatic verification...")
        prog_verifier = ProgrammaticVerifier(
            pg_text=parsed.body_text,
            alignments=alignments,
        )
        verified_errors = prog_verifier.verify_batch(candidates)

        # Save verified errors as intermediate
        save_intermediate(intermed_dir, "06_verified_errors", verified_errors)

        high = sum(1 for e in verified_errors if e.confidence >= 0.8)
        med = sum(1 for e in verified_errors if 0.5 <= e.confidence < 0.8)
        low = sum(1 for e in verified_errors if e.confidence < 0.5)
        console.print(f"  High confidence (≥0.8): {high}")
        console.print(f"  Medium confidence (0.5-0.8): {med}")
        console.print(f"  Low confidence (<0.5): {low}")
        console.print(f"  Total verified: {len(verified_errors)}")
    else:
        console.print(f"[bold blue]Step {step_num + 2}:[/bold blue] Programmatic verification...")
        high = sum(1 for e in verified_errors if e.confidence >= 0.8)
        med = sum(1 for e in verified_errors if 0.5 <= e.confidence < 0.8)
        low = sum(1 for e in verified_errors if e.confidence < 0.5)
        console.print(f"  [dim]Skipped — resumed {len(verified_errors)} verified errors from 06_verified_errors[/dim]")
        console.print(f"  High confidence (≥0.8): {high}")
        console.print(f"  Medium confidence (0.5-0.8): {med}")
        console.print(f"  Low confidence (<0.5): {low}")
        console.print(f"  Total verified: {len(verified_errors)}")

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
        alignments=alignments,
    )
    json_path, email_path = generator.save_reports(report, args.output)
    generator.print_summary(report)

    console.print(f"[bold green]Reports saved:[/bold green]")
    console.print(f"  JSON: {json_path}")
    console.print(f"  Errata Email: {email_path}")

    # Step: Substantive errata report (LLM analysis)
    if args.substantive_report:
        console.print(f"[bold blue]Step {step_num + 4}:[/bold blue] Generating substantive errata report...")
        substantive_gen = SubstantiveErrataGenerator(
            api_key=args.substantive_key or os.environ.get("OPENROUTER_API_KEY", ""),
            model=args.substantive_model,
            scan_id=scan_id,
        )
        substantive_path = await substantive_gen.generate(
            report=report,
            email_content=email_path.read_text(),
            json_content=json_path.read_text(),
            output_path=email_path,
        )
        if substantive_path:
            console.print(f"  [bold green]Substantive report:[/bold green] {substantive_path}")
        else:
            console.print(f"  [yellow]Substantive report skipped[/yellow] (no API key or call failed)")

    return report


async def run_verify_edition(args: argparse.Namespace) -> int:
    """Run the verify-edition subcommand."""
    from gerrata.edition.verifier import EditionVerifier

    verifier = EditionVerifier(
        cache_dir=Path(args.cache_dir) if args.cache_dir else Path("./cache"),
        verbose=args.verbose,
    )

    result = await verifier.verify(
        pg_id=args.pg_id,
        scan_id=args.scan_id,
        approaches=args.approach,
        pg_file=args.pg_file or None,
        vision_url=args.vision_url or None,
        vision_key=args.vision_key or None,
        vision_model=args.vision_model or None,
    )

    # Print console output
    console_text = EditionVerifier.format_console(result)
    print(console_text)
    print()

    # Save JSON report
    result.save(args.output, args.pg_id)
    print(f"JSON report saved to {Path(args.output) / f'{args.pg_id}_edition_verification.json'}")

    return EditionVerifier.exit_code(result.overall.get("result", "unable_to_determine"))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Handle verify-edition subcommand
    if args.command == "verify-edition":
        if args.pg_id is None:
            parser.error("pg_id is required for verify-edition")
        setup_logging(args.verbose)
        return asyncio.run(run_verify_edition(args))

    # Default: main pipeline
    if args.pg_id is None:
        parser.print_help()
        return 2

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
