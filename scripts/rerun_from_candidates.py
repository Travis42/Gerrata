"""Re-run gerrata pipeline from cached raw candidates through verification and report.

Usage:
    python3 scripts/rerun_from_candidates.py <scan_id> [--verify-model MODEL]
"""
import asyncio
import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gerrata.models import CandidateError, Error, ErrorCategory, ErrorSeverity, Verdict
from gerrata.checker.rules import FalsePositiveFilter
from gerrata.verifier.vision import VisionVerifier
from gerrata.reporter.generator import ReportGenerator
from gerrata.fetcher.pg import PGFetcher

import re


def is_cutoff_artifact(scan_text: str, pg_text: str) -> bool:
    s, p = scan_text.strip(), pg_text.strip()
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


def is_long_mismatch(scan_text: str, pg_text: str) -> bool:
    return len(scan_text.strip()) > 40 or len(pg_text.strip()) > 40


def is_html_artifact(scan_text: str, pg_text: str) -> bool:
    combined = scan_text + pg_text
    return any(m in combined for m in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])


def is_all_caps_header(scan_text: str) -> bool:
    stripped = scan_text.strip()
    return stripped.isupper() and len(stripped) > 5


def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
    s, p = scan_text.strip(), pg_text.strip()
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
    """Quoted dialogue fragment where alignment grabbed just the opening."""
    s, p = scan_text.strip(), pg_text.strip()
    if not (s.startswith('"') or s.startswith('"') or s.startswith('"')):
        return False
    if len(s) > 15:
        return False
    if ' ' in s:
        return False
    return True


def apply_cli_filters(candidates):
    """Reproduce the CLI-level filters from cli.py steps 6b-6d."""
    # Cutoff artifact filter
    filtered = []
    artifacts = 0
    for c in candidates:
        if is_cutoff_artifact(c.scan_text, c.pg_text):
            c.category = ErrorCategory.ALIGNMENT_ARTIFACT
            filtered.append(c)
            artifacts += 1
        else:
            filtered.append(c)
    candidates = filtered
    if artifacts:
        print(f"  Filtered {artifacts} word-boundary cutoff artifacts")

    # Absent-text filter
    absent = 0
    filtered = []
    for c in candidates:
        if '(absent in PG)' in c.pg_text or '(absent in scan)' in c.scan_text:
            absent += 1
            continue
        filtered.append(c)
    candidates = filtered
    if absent:
        print(f"  Filtered {absent} absent-text entries")

    # Additional filters
    additional = 0
    filtered = []
    for c in candidates:
        if is_long_mismatch(c.scan_text, c.pg_text):
            additional += 1
            continue
        if is_html_artifact(c.scan_text, c.pg_text):
            additional += 1
            continue
        if is_all_caps_header(c.scan_text):
            additional += 1
            continue
        if is_suffix_fragment(c.scan_text, c.pg_text):
            additional += 1
            continue
        if is_quoted_fragment(c.scan_text, c.pg_text):
            additional += 1
            continue
        filtered.append(c)
    candidates = filtered
    if additional:
        print(f"  Filtered {additional} additional artifacts")

    return candidates


async def main():
    scan_id = sys.argv[1]
    verify_model = sys.argv[2] if len(sys.argv) > 2 else "mistralai/mistral-small-3.2-24b-instruct"

    cache_dir = Path(f"./cache/{scan_id}")
    or_key = open("/root/.secrets/openrouter.key").read().strip()

    # Load raw candidates
    with open(cache_dir / "04_candidates_raw.json") as f:
        raw_candidates = [CandidateError(**e) for e in json.load(f)]
    print(f"Raw candidates: {len(raw_candidates)}")

    # Load alignments and scan pages
    with open(cache_dir / "03_scan_pages.json") as f:
        scan_pages_data = json.load(f)

    # Reconstruct scan pages
    from dataclasses import dataclass
    @dataclass
    class ScanPage:
        page_num: int
        ocr_text: str = ""
        vision_text: str = ""
        image_path: str = ""

    scan_pages = []
    for sp in scan_pages_data:
        scan_pages.append(ScanPage(
            page_num=sp.get("page_num", sp.get("page", 0)),
            ocr_text=sp.get("ocr_text", ""),
            vision_text=sp.get("vision_text", ""),
            image_path=sp.get("image_path", ""),
        ))

    # Load PG text for context
    with open(cache_dir / "01_pg_parsed.json") as f:
        pg_data = json.load(f)
    pg_text = pg_data.get("body_text", "")

    # Load alignments
    with open(cache_dir / "03_alignments.json") as f:
        alignments = json.load(f)

    # Step 5: FP filter (with the scanner metadata fix)
    print("Step 5: False positive filter...")
    fp_filter = FalsePositiveFilter()
    candidates = fp_filter.filter(raw_candidates)
    print(f"  After FP filter: {len(candidates)}")

    # Step 6: CLI-level filters
    print("Step 6: CLI-level filters...")
    candidates = apply_cli_filters(candidates)
    print(f"  After CLI filters: {len(candidates)}")

    # Check if novels error survived
    for c in candidates:
        if 'feb' in c.pg_text.lower() or 'H.T' in c.pg_text:
            print(f"\n  ✅ novels error survived: pg={c.pg_text!r} scan={c.scan_text!r}")

    # Step 7: Verification
    print(f"\nStep 7: LLM verification ({verify_model})...")
    verifier = VisionVerifier(
        api_url="https://openrouter.ai/api/v1/chat/completions",
        api_key=or_key,
        model=verify_model,
        concurrency=3,
    )

    def get_image_path(error):
        page_idx = min(error.scan_page, len(scan_pages) - 1)
        if scan_pages and scan_pages[page_idx].image_path:
            return Path(scan_pages[page_idx].image_path)
        return None

    def get_pg_context(error):
        pg_t = error.pg_text
        if '(absent in PG)' in pg_t or '(absent in scan)' in pg_t:
            pg_t = error.scan_text
        pos = pg_text.find(pg_t)
        if pos >= 0:
            start = max(0, pos - 200)
            end = min(len(pg_text), pos + len(pg_t) + 200)
            return pg_text[start:end]
        return pg_text[max(0, error.pg_offset - 200):error.pg_offset + 200]

    verified = await verifier.verify_batch_per_page(
        candidates,
        get_image_path=get_image_path,
        get_pg_context=get_pg_context,
    )
    print(f"  Verified: {len(verified)}")

    # Show verdicts
    from collections import Counter
    verdicts = Counter(e.verdict.value for e in verified if e.verdict != Verdict.PG_CORRECT)
    print(f"\n  Verdict distribution (excl pg_correct):")
    for v, c in verdicts.most_common():
        print(f"    {v}: {c}")

    # Step 8: Generate report
    print("\nStep 8: Generating report...")

    # Build report data structure matching what cli.py produces
    from gerrata.models import Report, PGMetadata
    from gerrata.fetcher.pg import PGFetcher

    pg_fetcher = PGFetcher()
    parsed = pg_fetcher.parse_file(Path(f"./cache/1342.htm"))

    alignment_conf = sum(a.get("confidence", 0) for a in alignments) / len(alignments) if alignments else 0.0

    report = Report(
        metadata=parsed.metadata,
        scan_source=f"https://archive.org/details/{scan_id}",
        date=1342,
        pages_checked=len(set(a.get("scan_page", 0) for a in alignments)),
        total_pages=len(scan_pages),
        alignment_confidence=alignment_conf,
        avg_page_confidence=0.0,
        errors=verified,
        alignments=[],
    )

    generator = ReportGenerator()
    generator.enrich_errors_with_context(report)
    email_text = generator.generate_errata_email(report)
    json_data = generator.generate_json(report)

    # Save
    report_dir = Path("./reports")
    report_dir.mkdir(exist_ok=True)
    email_path = report_dir / "gutenberg1342-pride-andprejudice_errata_email.txt"
    json_path = report_dir / "gutenberg1342-pride-andprejudice_errata.json"

    with open(email_path, 'w') as f:
        f.write(email_text)
    with open(json_path, 'w') as f:
        json.dump(json_data, f, indent=2, default=str)

    print(f"  Reports saved:")
    print(f"    Email: {email_path}")
    print(f"    JSON: {json_path}")

    # Check for novels error in final report
    if 'H.T' in email_text or 'Feb 94' in email_text or 'H.T' in json.dumps(json_data):
        print(f"\n  ✅ novels\" H.T Feb 94 error is in the final report!")
    else:
        print(f"\n  ❌ novels\" H.T Feb 94 error is NOT in the final report")


if __name__ == "__main__":
    asyncio.run(main())
