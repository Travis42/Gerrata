#!/usr/bin/env python3
"""Re-filter saved Gerrata results using the same pipeline logic."""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from gerrata.models import CandidateError, Error, ErrorSeverity, Verdict, ErrorCategory, PGMetadata, Report
from gerrata.reporter.generator import ReportGenerator

def _next_version(path):
    if not path.exists():
        return path
    stem = path.stem
    ext = path.suffix
    n = 2
    while True:
        versioned = path.parent / f"{stem}-{n}{ext}"
        if not versioned.exists():
            return versioned
        n += 1


def main():
    input_json = sys.argv[1] if len(sys.argv) > 1 else None
    if not input_json:
        # Find the saved report
        candidates = [
            Path("reports/finished_full_drjekyl/gutenberg43-the-strange-case-of-dr-jekyll-and-mr-hyde_errata.json"),
        ]
        for c in candidates:
            if c.exists():
                input_json = str(c)
                break
        if not input_json:
            print("No input JSON found", file=sys.stderr)
            sys.exit(1)

    report_data = json.loads(Path(input_json).read_text(encoding='utf-8'))
    errors = report_data.get('errors', [])
    print(f"Loaded {len(errors)} errors from {input_json}")

    # Reconstruct Report
    meta = report_data.get('metadata', {})
    pg_metadata = PGMetadata(
        pg_id=meta.get('pg_id', 0),
        title=meta.get('title', ''),
        author=meta.get('author', ''),
    )

    report_errors = []
    for e in errors:
        candidate = CandidateError(
            pg_text=e.get('pg_text', ''),
            scan_text=e.get('scan_text', ''),
            pg_offset=e.get('pg_offset', 0),
            scan_page=e.get('scan_page', 0),
            category=ErrorCategory(e.get('category', 'ocr_scanno')),
            severity=ErrorSeverity(e.get('severity', 'high')),
        )
        report_errors.append(Error(
            candidate=candidate,
            verdict=Verdict(e.get('verdict', 'unable_to_verify')),
            confidence=e.get('confidence', 0.0),
            reasoning=e.get('reasoning', ''),
            suggested_fix=e.get('suggested_fix', ''),
            scan_image_path=e.get('scan_image_path'),
            pg_file_line=e.get('pg_file_line', 0),
            chapter_title=e.get('chapter_title', ''),
        ))

    report = Report(
        metadata=pg_metadata,
        scan_source=report_data.get('scan_source', ''),
        date=report_data.get('date', ''),
        pages_checked=report_data.get('pages_checked', 0),
        total_pages=report_data.get('total_pages', 0),
        alignment_confidence=report_data.get('alignment_confidence', 0.0),
        edition_match_confidence=report_data.get('edition_match_confidence', 0.0),
        edition_notes=report_data.get('edition_notes', ''),
        errors=report_errors,
    )

    # Auto-detect scan_id from scan_source URL
    scan_id = None
    source = report_data.get('scan_source', '')
    m = re.search(r'archive\.org/details/([a-zA-Z0-9_-]+)', source)
    if m:
        scan_id = m.group(1)

    # Load body text for context extraction
    body_text = ""
    html_path = Path("cache/43.htm")
    if html_path.exists():
        html = html_path.read_text(encoding='utf-8')
        body_text = re.sub(r'<[^>]+>', ' ', html)
        body_text = re.sub(r'\s+', ' ', body_text).strip()

    # Generate email using the same pipeline logic
    generator = ReportGenerator(
        scan_id=scan_id,
        body_text=body_text,
    )
    email_content = generator.generate_errata_email(report)

    # Save outputs
    output_dir = Path("reports/replay_output")
    output_dir.mkdir(parents=True, exist_ok=True)

    email_path = _next_version(output_dir / "gutenberg43-errata_email.txt")
    email_path.write_text(email_content, encoding='utf-8')

    # Also save filtered JSON (pass through existing errors, generate_report handles filtering)
    json_path = _next_version(output_dir / "gutenberg43-errata_filtered.json")
    json_path.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding='utf-8')

    print(f"Email saved to: {email_path}")
    print(f"JSON saved to: {json_path}")

if __name__ == '__main__':
    main()
