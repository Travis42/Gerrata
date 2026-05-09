"""gerrata CLI — automated post-publication quality audit for Project Gutenberg texts."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

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
        default="https://api.z.ai/api/paas/v4/chat/completions",
        help="Vision model API URL for page transcription (Step 3)",
    )
    parser.add_argument(
        "--vision-key",
        type=str,
        default=os.environ.get("ZAI_API_KEY", ""),
        help="Vision model API key for transcription (default: ZAI_API_KEY environment variable)",
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

    # Determine mode
    vision_mode = args.vision_transcribe

    # Initialize components
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    pg_fetcher = PGFetcher(cache_dir=cache_dir)
    scan_fetcher = ScanFetcher(cache_dir=cache_dir)

    scan_id = args.scan_id
    page_range = parse_page_range(args.page_range)

    # Step 1: Parse PG text
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
    console.print(f"  Mode: {'[green]vision-first[/green]' if vision_mode else '[yellow]OCR-based[/yellow]'}")

    # Step 2: Get page images (vision mode) or load OCR (OCR mode)
    alignments = []
    scan_pages = []

    if vision_mode:
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
        )

        transcriptions = await transcriber.transcribe_pages(page_images)
        successful = [t for t in transcriptions if t.success]
        console.print(f"  Transcribed {len(successful)}/{len(transcriptions)} pages")

        if not successful:
            console.print("[red]All transcriptions failed. Falling back to OCR mode.[/red]")
            vision_mode = False

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

    if vision_mode and successful:
        # Align transcriptions to PG text
        console.print("[bold blue]Step 4:[/bold blue] Aligning transcriptions to PG text...")
        vision_aligner = VisionAligner(match_threshold=0.35, min_match_chars=40)
        results = vision_aligner.align_all_pages(
            transcriptions=transcriptions,
            pg_text=parsed.body_text,
            pg_paragraphs=parsed.paragraphs,
        )
        alignments = vision_aligner.get_alignments(results)
        scan_pages = vision_aligner.build_scan_pages_from_transcriptions(transcriptions)

        alignment_confidence = vision_aligner.alignment_confidence(alignments, len(parsed.body_text))
        console.print(f"  Matched: {len(alignments)}/{len(transcriptions)} pages")
        console.print(f"  Coverage: {alignment_confidence:.0%}")
    elif vision_mode:
        # Vision mode was set but all transcriptions failed — already handled above
        pass
    else:
        alignment_confidence = CoarseAligner().alignment_confidence(alignments, len(parsed.body_text))
        console.print(f"  Alignments: {len(alignments)}")
        console.print(f"  Coverage: {alignment_confidence:.0%}")

    # Step 5: Text diff
    step_num = 5 if vision_mode else 4
    console.print(f"[bold blue]Step {step_num}:[/bold blue] Running text diff...")
    checker = TextDiffChecker()
    candidates = checker.check_all_alignments(
        pg_text=parsed.body_text,
        alignments=alignments,
        scan_pages=scan_pages,
    )
    console.print(f"  Raw candidates: {len(candidates)}")

    # Step 6: False positive filter
    console.print(f"[bold blue]Step {step_num + 1}:[/bold blue] Filtering false positives...")
    fp_filter = FalsePositiveFilter(strict=args.strict)
    candidates = fp_filter.filter(candidates)
    console.print(f"  After filtering: {len(candidates)}")

    # Step 7: LLM vision verification (if configured and in vision mode)
    verified_errors: list[Error] = []

    # Determine verify config (fallback to vision config if not set)
    verify_url = args.verify_url or args.vision_url
    verify_key = args.verify_key or args.vision_key
    verify_model = args.verify_model or args.vision_model or "zai/glm-4.6v"

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
        )

        # Use batch verification for better performance and rate limit handling
        def get_image_path(error):
            if scan_pages:
                page_idx = min(error.scan_page, len(scan_pages) - 1)
                if scan_pages[page_idx].image_path:
                    return Path(scan_pages[page_idx].image_path)
            return None

        def get_pg_context(error):
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
    )
    md_path, json_path, email_path, review_path = generator.save_reports(report, args.output)
    generator.print_summary(report)

    console.print(f"[bold green]Reports saved:[/bold green]")
    console.print(f"  Markdown: {md_path}")
    console.print(f"  JSON: {json_path}")
    console.print(f"  Errata Email: {email_path}")
    console.print(f"  Review Needed: {review_path}")

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
