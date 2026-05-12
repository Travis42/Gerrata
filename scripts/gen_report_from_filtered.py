"""Generate report from already-verified errors (skip verification step).

Usage:
    python3 scripts/gen_report_from_verified.py <scan_id>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gerrata.models import CandidateError, Error, ErrorCategory, ErrorSeverity, Verdict, Report
from gerrata.checker.rules import FalsePositiveFilter
from gerrata.reporter.generator import ReportGenerator
from gerrata.fetcher.pg import PGFetcher
import re


def is_cutoff_artifact(scan_text, pg_text):
    s, p = scan_text.strip(), pg_text.strip()
    if ' ' in s or ' ' in p: return False
    if abs(len(s) - len(p)) != 1: return False
    longer, shorter = (s, p) if len(s) > len(p) else (p, s)
    if not longer.startswith(shorter) and not longer.endswith(shorter): return False
    return len(shorter) <= 3

def is_long_mismatch(scan_text, pg_text):
    return len(scan_text.strip()) > 40 or len(pg_text.strip()) > 40

def is_html_artifact(scan_text, pg_text):
    return any(m in scan_text + pg_text for m in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])

def is_all_caps_header(scan_text):
    s = scan_text.strip()
    return s.isupper() and len(s) > 5

def is_suffix_fragment(scan_text, pg_text):
    s, p = scan_text.strip(), pg_text.strip()
    shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
    if ' ' in shorter: return False
    if len(longer) - len(shorter) > 3: return False
    if not longer.endswith(shorter): return False
    if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4: return False
    if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4: return False
    return len(shorter) <= 8

def is_quoted_fragment(scan_text, pg_text):
    s = scan_text.strip()
    if not (s.startswith('"') or s.startswith('"') or s.startswith('"')): return False
    if len(s) > 15 or ' ' in s: return False
    return True


def apply_cli_filters(candidates):
    filtered, artifacts = [], 0
    for c in candidates:
        if is_cutoff_artifact(c.scan_text, c.pg_text):
            c.category = ErrorCategory.ALIGNMENT_ARTIFACT
            artifacts += 1
        filtered.append(c)
    candidates = filtered

    absent, filtered = 0, []
    for c in candidates:
        if '(absent in PG)' in c.pg_text or '(absent in scan)' in c.scan_text:
            absent += 1; continue
        filtered.append(c)
    candidates = filtered

    additional, filtered = 0, []
    for c in candidates:
        if any(fn(c.scan_text, c.pg_text) for fn in [is_long_mismatch, is_html_artifact]):
            additional += 1; continue
        if is_all_caps_header(c.scan_text): additional += 1; continue
        if is_suffix_fragment(c.scan_text, c.pg_text): additional += 1; continue
        if is_quoted_fragment(c.scan_text, c.pg_text): additional += 1; continue
        filtered.append(c)
    candidates = filtered

    print(f"  CLI filters: cutoff={artifacts}, absent={absent}, additional={additional}")
    return candidates


def main():
    scan_id = sys.argv[1]
    cache_dir = Path(f"./cache/{scan_id}")

    # Load raw candidates
    with open(cache_dir / "04_candidates_raw.json") as f:
        raw = [CandidateError(**e) for e in json.load(f)]
    print(f"Raw candidates: {len(raw)}")

    # FP filter
    fp = FalsePositiveFilter()
    candidates = fp.filter(raw)
    print(f"After FP filter: {len(candidates)}")

    # Check for novels
    for c in candidates:
        if 'H.T' in c.pg_text:
            print(f"  ✅ novels error survived FP filter: pg={c.pg_text!r}")

    # CLI filters
    candidates = apply_cli_filters(candidates)
    print(f"After CLI filters: {len(candidates)}")

    for c in candidates:
        if 'H.T' in c.pg_text:
            print(f"  ✅ novels error survived CLI filters: pg={c.pg_text!r}")

    # For testing: create unverified Error objects
    errors = [Error(candidate=c) for c in candidates]

    # Generate report
    pg_fetcher = PGFetcher()
    parsed = pg_fetcher.parse_file(Path("./cache/1342.htm"))

    with open(cache_dir / "03_alignments.json") as f:
        alignments = json.load(f)
    with open(cache_dir / "03_scan_pages.json") as f:
        scan_pages = json.load(f)

    report = Report(
        metadata=parsed.metadata,
        scan_source=f"https://archive.org/details/{scan_id}",
        date=1342,
        pages_checked=len(set(a.get("scan_page", 0) for a in alignments)),
        total_pages=len(scan_pages),
        alignment_confidence=0.67,
        avg_page_confidence=0.0,
        errors=errors,
        alignments=[],
    )

    gen = ReportGenerator()
    gen.enrich_errors_with_context(report)
    email = gen.generate_errata_email(report)
    json_out = gen.generate_json(report)

    report_dir = Path("./reports")
    email_path = report_dir / "gutenberg1342-pride-andprejudice_errata_email.txt"
    json_path = report_dir / "gutenberg1342-pride-andprejudice_errata.json"

    with open(email_path, 'w') as f:
        f.write(email)
    with open(json_path, 'w') as f:
        json.dump(json_out, f, indent=2, default=str)

    print(f"\nReports saved to {report_dir}/")

    if 'H.T' in email or 'Feb 94' in email or 'H.T' in json.dumps(json_out):
        print("✅ novels\" H.T Feb 94 is in the report!")
    else:
        print("❌ novels\" H.T Feb 94 is NOT in the report")


if __name__ == "__main__":
    main()
