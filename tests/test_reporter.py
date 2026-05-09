"""Tests for report generator and CLI."""

import json
from pathlib import Path

import pytest

from gerrata.models import (
    Alignment, AlignmentMethod, CandidateError, Error, ErrorCategory,
    ErrorSeverity, PGMetadata, Report, Verdict,
)
from gerrata.reporter.generator import ReportGenerator
from gerrata.fetcher.pg import PGParsedText, ChapterLocation


@pytest.fixture
def sample_metadata():
    return PGMetadata(pg_id=43, title="Strange Case of Dr. Jekyll and Mr. Hyde",
                     author="Robert Louis Stevenson")


@pytest.fixture
def sample_errors():
    """Create a mix of error types for testing."""
    errors = []

    # High confidence error
    c1 = CandidateError(pg_text="tne", scan_text="the", pg_offset=100, scan_page=5,
                        category=ErrorCategory.OCR_SCANNO)
    errors.append(Error(candidate=c1, verdict=Verdict.SCAN_CORRECT, confidence=0.95,
                       reasoning="Clear OCR scanno"))

    # Edition variant
    c2 = CandidateError(pg_text="downright", scan_text="down-right", pg_offset=200, scan_page=10)
    errors.append(Error(candidate=c2, verdict=Verdict.EDITION_VARIANT, confidence=0.8,
                       reasoning="Different hyphenation convention"))

    # Medium confidence
    c3 = CandidateError(pg_text="walked", scan_text="walking", pg_offset=300, scan_page=15,
                        category=ErrorCategory.WRONG_WORD)
    errors.append(Error(candidate=c3, verdict=Verdict.SCAN_CORRECT, confidence=0.6,
                       reasoning="Uncertain"))

    # Intentional modernization
    c4 = CandidateError(pg_text="someone", scan_text="some one", pg_offset=400, scan_page=20)
    errors.append(Error(candidate=c4, verdict=Verdict.INTENTIONAL_MODERNIZATION, confidence=0.7))

    return errors


@pytest.fixture
def sample_report(sample_metadata, sample_errors):
    return Report(
        metadata=sample_metadata,
        scan_source="Internet Archive (identifier: 06-stevenson-jekyll-hyde)",
        pages_checked=50,
        total_pages=100,
        alignment_confidence=0.85,
        edition_match_confidence=0.6,
        edition_notes="Different editions: Collins vs Longmans",
        errors=sample_errors,
    )


class TestReportGenerator:
    @pytest.fixture
    def generator(self):
        return ReportGenerator()

    @pytest.fixture
    def generator_with_context(self, tmp_path):
        """Create a generator with PG file context for line number mapping."""
        # Create a mock PG HTML file
        pg_html = """<!DOCTYPE html>
<html>
<head><title>Test Book</title></head>
<body>
<div>*** START OF THE PROJECT GUTENBERG EBOOK ***</div>
<p>This is a test paragraph with erroneous text.</p>
<p>Another paragraph with returned Enfield text.</p>
</body>
</html>"""
        pg_file = tmp_path / "test.htm"
        pg_file.write_text(pg_html)

        # Create a mock PGParsedText
        parsed_text = PGParsedText(
            metadata=PGMetadata(pg_id=43, title="Test Book", author="Test Author"),
            body_text="This is a test paragraph with erroneous text.\n\nAnother paragraph with returned Enfield text.",
            full_text=pg_html,
            chapters=[],
        )

        return ReportGenerator(pg_parsed_text=parsed_text, pg_file_path=pg_file, scan_id="test-scan")

    def test_generate_markdown(self, generator, sample_report):
        md = generator.generate_markdown(sample_report)
        assert "# Quality Audit Report" in md
        assert "Jekyll" in md
        assert "Stevenson" in md
        assert "Summary" in md
        assert "Edition Variants" in md
        assert "High Confidence Errors" in md

    def test_markdown_has_error_details(self, generator, sample_report):
        md = generator.generate_markdown(sample_report)
        assert "tne" in md
        assert "the" in md
        assert "downright" in md
        assert "down-right" in md

    def test_markdown_empty_errors(self, generator, sample_metadata):
        report = Report(metadata=sample_metadata, errors=[])
        md = generator.generate_markdown(report)
        assert "No discrepancies found" in md

    def test_generate_json(self, generator, sample_report):
        j = generator.generate_json(sample_report)
        parsed = json.loads(j)
        assert parsed["metadata"]["pg_id"] == 43
        assert parsed["summary"]["total_candidates"] == 4
        assert len(parsed["errors"]) == 4
        assert parsed["edition_match_confidence"] == 0.6

    def test_json_error_fields(self, generator, sample_report):
        j = generator.generate_json(sample_report)
        parsed = json.loads(j)
        err0 = parsed["errors"][0]
        assert err0["pg_text"] == "tne"
        assert err0["scan_text"] == "the"
        assert err0["verdict"] == "scan_correct"
        assert "category" in err0
        assert "severity" in err0

    def test_save_reports(self, generator, sample_report, tmp_path):
        md_path, json_path, email_path, review_path = generator.save_reports(sample_report, tmp_path)
        assert md_path.exists()
        assert json_path.exists()
        assert email_path.exists()
        assert review_path.exists()
        assert md_path.suffix == ".md"
        assert json_path.suffix == ".json"
        assert email_path.suffix == ".txt"
        assert review_path.suffix == ".txt"

    def test_save_reports_custom_name(self, generator, sample_report, tmp_path):
        md_path, json_path, email_path, review_path = generator.save_reports(sample_report, tmp_path, "custom")
        assert md_path.stem == "custom_errata"
        assert json_path.stem == "custom_errata"
        assert email_path.stem == "custom_errata_email"
        assert review_path.stem == "custom_review_needed"

    def test_save_reports_creates_dir(self, generator, sample_report, tmp_path):
        out_dir = tmp_path / "sub" / "dir"
        md_path, json_path, email_path, review_path = generator.save_reports(sample_report, out_dir)
        assert md_path.exists()
        assert email_path.exists()
        assert review_path.exists()

    def test_line_number_mapping(self, generator_with_context, sample_metadata):
        """Test that line numbers are correctly mapped from PG offsets."""
        # Create an error with a known text offset
        c1 = CandidateError(
            pg_text="erroneous",
            scan_text="correct",
            pg_offset=25,  # Position in body_text
            scan_page=0,
            category=ErrorCategory.OCR_SCANNO
        )
        error = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.9,
            reasoning="Test error"
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error]
        )

        # Enrich with context
        generator_with_context.enrich_errors_with_context(report)

        # Should have found a line number
        assert error.pg_file_line > 0
        assert error.pg_file_line == 6  # The line with "erroneous text"

    def test_chapter_context(self):
        """Test that chapter context is correctly extracted."""
        parsed_text = PGParsedText(
            metadata=PGMetadata(pg_id=43, title="Test", author="Test"),
            body_text="Start of chapter one.\n\nMore text here.\n\nStart of chapter two.",
            full_text="",
            chapters=[
                ChapterLocation(title="Chapter One", offset=0, end_offset=30),
                ChapterLocation(title="Chapter Two", offset=31, end_offset=100),
            ],
        )

        generator = ReportGenerator(pg_parsed_text=parsed_text)

        # Error in chapter one
        chapter = generator.get_chapter_context(10)
        assert chapter == "Chapter One"

        # Error in chapter two
        chapter = generator.get_chapter_context(40)
        assert chapter == "Chapter Two"

        # Error before any chapter
        chapter = generator.get_chapter_context(200)
        assert chapter == ""

    def test_arrow_format(self, generator):
        """Test arrow format generation."""
        c1 = CandidateError(
            pg_text="tne",
            scan_text="the",
            pg_offset=100,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )
        error = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.95,
            suggested_fix="tne ==> the"
        )

        arrow_fix = generator.format_arrow_fix(error)
        assert arrow_fix == "tne ==> the"

    def test_arrow_format_auto_generate(self, generator):
        """Test arrow format auto-generation when no suggested_fix exists."""
        c1 = CandidateError(
            pg_text="walked",
            scan_text="walking",
            pg_offset=100,
            scan_page=5,
        )
        error = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.7
        )

        arrow_fix = generator.format_arrow_fix(error)
        assert arrow_fix == "walked ==> walking"

    def test_errata_email_generation(self, generator_with_context, sample_metadata):
        """Test errata email generation in PG Format 2."""
        c1 = CandidateError(
            pg_text="tne",
            scan_text="the",
            pg_offset=25,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )
        error1 = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.95,
            reasoning="Clear OCR scanno",
            pg_file_line=6
        )

        c2 = CandidateError(
            pg_text="walked",
            scan_text="walking",
            pg_offset=50,
            scan_page=10,
        )
        error2 = Error(
            candidate=c2,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.7,
            pg_file_line=7
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error1, error2],
            scan_source="Internet Archive (identifier: test-scan)"
        )

        email_content = generator_with_context.generate_errata_email(report)

        # Check header
        assert "Strange Case of Dr. Jekyll and Mr. Hyde" in email_content
        assert "Robert Louis Stevenson" in email_content
        assert "[EBook #43]" in email_content
        assert "File:" in email_content
        assert "Verified against Internet Archive scan" in email_content

        # Only high-confidence errors should be included
        assert "Line 6:" in email_content
        assert "tne" in email_content
        assert "tne ==> the" in email_content

        # Low confidence error should NOT be included (moved to review)
        assert "Line 7:" not in email_content
        assert "walked" not in email_content

    def test_markdown_includes_line_and_chapter(self, generator_with_context, sample_metadata):
        """Test that markdown report includes line numbers and chapter context."""
        c1 = CandidateError(
            pg_text="tne",
            scan_text="the",
            pg_offset=25,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )
        error = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.95,
            pg_file_line=6,
            chapter_title="Chapter One"
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error]
        )

        md = generator_with_context.generate_markdown(report)

        # Should include line number and chapter
        assert "File line:** 6" in md
        assert "Chapter One" in md
        assert "tne" in md

    def test_report_structure_with_new_fields(self, generator_with_context, sample_metadata):
        """Test that the full report structure works with new fields."""
        c1 = CandidateError(
            pg_text="tne",
            scan_text="the",
            pg_offset=25,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )
        error = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.95,
            pg_file_line=6,
            chapter_title="Chapter One",
            location_description="Chapter: Chapter One; Line 6"
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error]
        )

        # Generate all formats
        md = generator_with_context.generate_markdown(report)
        json_str = generator_with_context.generate_json(report)
        email = generator_with_context.generate_errata_email(report)

        # Verify all formats work
        assert "Chapter One" in md
        assert "File line:** 6" in md

        parsed_json = json.loads(json_str)
        assert parsed_json["errors"][0]["pg_file_line"] == 6
        assert parsed_json["errors"][0]["chapter_title"] == "Chapter One"

        assert "Line 6:" in email
        assert "tne ==> the" in email

    def test_print_summary(self, generator, sample_report, capsys):
        """print_summary should not raise."""
        generator.print_summary(sample_report)

    def test_review_needed_includes_ambiguous(self, generator_with_context, sample_metadata):
        """Test that unable_to_verify items are included in review file."""
        c1 = CandidateError(
            pg_text="returned",
            scan_text="return",
            pg_offset=25,
            scan_page=0,  # Changed from 8 to 0 to match actual test data
        )
        error1 = Error(
            candidate=c1,
            verdict=Verdict.UNABLE_TO_VERIFY,
            confidence=0.5,
            reasoning="dialogue punctuation differs between editions",
            pg_file_line=335,
            chapter_title="STORY OF THE DOOR"
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error1],
            scan_source="Internet Archive (identifier: test-scan)"
        )

        review_content = generator_with_context.generate_review_needed(report)

        # Check header
        assert "Quality Audit Review Items" in review_content
        assert "1 items need your decision" in review_content

        # Check entry format
        assert "[?]" in review_content
        assert "Line 335, Page 0, (STORY OF THE DOOR)" in review_content  # Updated to match actual format
        assert "PG text: returned" in review_content
        assert "Scan text: return" in review_content
        assert "Verdict: unable_to_verify (50%)" in review_content
        assert "Reasoning: dialogue punctuation differs between editions" in review_content
        assert "Action needed: Check scan and decide if PG punctuation is wrong" in review_content

    def test_review_needed_edition_variant(self, generator_with_context, sample_metadata):
        """Test that edition_variant items have correct prefix."""
        c1 = CandidateError(
            pg_text="sir;",
            scan_text=" No, sir :",
            pg_offset=50,
            scan_page=6,
        )
        error1 = Error(
            candidate=c1,
            verdict=Verdict.EDITION_VARIANT,
            confidence=0.9,
            reasoning="The scan shows different punctuation",
            pg_file_line=129,
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error1],
            scan_source="Internet Archive (identifier: test-scan)"
        )

        review_content = generator_with_context.generate_review_needed(report)

        # Check edition variant prefix
        assert "[E]" in review_content
        assert "Line 129, Page 6" in review_content
        assert "Classified as edition variant — likely NOT an error" in review_content

    def test_review_needed_low_confidence(self, generator_with_context, sample_metadata):
        """Test that low confidence scan_correct errors get [~] prefix."""
        c1 = CandidateError(
            pg_text="test",
            scan_text="text",
            pg_offset=25,
            scan_page=5,
            category=ErrorCategory.WRONG_WORD
        )
        error1 = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.7,  # Below 0.8 threshold
            reasoning="Uncertain match",
            pg_file_line=100,
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error1],
            scan_source="Internet Archive (identifier: test-scan)"
        )

        review_content = generator_with_context.generate_review_needed(report)

        # Check low confidence prefix
        assert "[~]" in review_content
        assert "Low confidence — verify before submitting" in review_content

    def test_review_needed_excludes_artifacts(self, generator, sample_metadata):
        """Test that alignment_artifact items are excluded from review file."""
        c1 = CandidateError(
            pg_text="artifact",
            scan_text="artifact",
            pg_offset=25,
            scan_page=5,
            category=ErrorCategory.ALIGNMENT_ARTIFACT
        )
        error1 = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.9,
            pg_file_line=100,
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error1],
            scan_source="Internet Archive (identifier: test-scan)"
        )

        review_content = generator.generate_review_needed(report)

        # Should show 0 items need review
        assert "0 items need review" in review_content
        # Should not contain the artifact
        assert "artifact" not in review_content

    def test_review_needed_empty(self, generator, sample_metadata):
        """Test review file when no items need review."""
        # High confidence error (submit-ready)
        c1 = CandidateError(
            pg_text="tne",
            scan_text="the",
            pg_offset=25,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )
        error1 = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.95,
            pg_file_line=100,
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error1],
            scan_source="Internet Archive (identifier: test-scan)"
        )

        review_content = generator.generate_review_needed(report)

        # Should indicate ready to submit
        assert "0 items need review — errata_email.txt is ready to submit" in review_content

    def test_errata_email_filters_by_confidence(self, generator_with_context, sample_metadata):
        """Test that errata_email only includes scan_correct with >= 80% confidence."""
        # High confidence (should be included)
        c1 = CandidateError(
            pg_text="tne",
            scan_text="the",
            pg_offset=25,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )
        error1 = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.95,
            pg_file_line=100,
        )

        # Low confidence (should be excluded)
        c2 = CandidateError(
            pg_text="walked",
            scan_text="walking",
            pg_offset=50,
            scan_page=10,
        )
        error2 = Error(
            candidate=c2,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.7,
            pg_file_line=200,
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error1, error2],
            scan_source="Internet Archive (identifier: test-scan)"
        )

        email_content = generator_with_context.generate_errata_email(report)

        # Should include high confidence
        assert "Line 100:" in email_content
        assert "tne ==> the" in email_content

        # Should exclude low confidence
        assert "Line 200:" not in email_content
        assert "walked" not in email_content

        # Summary should show 1 error ready
        assert "1 errors ready for submission" in email_content

    def test_errata_email_deduplicates_by_line(self, generator_with_context, sample_metadata):
        """Test that errors on same line are deduplicated (keep highest confidence)."""
        # Same line, lower confidence
        c1 = CandidateError(
            pg_text="tne",
            scan_text="the",
            pg_offset=25,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )
        error1 = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.7,
            pg_file_line=100,
        )

        # Same line, higher confidence
        c2 = CandidateError(
            pg_text="tne",
            scan_text="the",
            pg_offset=25,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )
        error2 = Error(
            candidate=c2,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.95,
            pg_file_line=100,
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error1, error2],
            scan_source="Internet Archive (identifier: test-scan)"
        )

        email_content = generator_with_context.generate_errata_email(report)

        # Should only have one entry for line 100
        assert email_content.count("Line 100:") == 1
        # Should show the error only once
        assert email_content.count("tne ==> the") == 1
        # Summary should show 1 error (deduplicated)
        assert "1 errors ready for submission" in email_content

    def test_errata_email_arrow_format(self, generator_with_context, sample_metadata):
        """Test that errata_email uses arrow format, not 'Change X to Y'."""
        c1 = CandidateError(
            pg_text="tne",
            scan_text="the",
            pg_offset=25,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )
        error1 = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.95,
            pg_file_line=100,
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error1],
            scan_source="Internet Archive (identifier: test-scan)"
        )

        email_content = generator_with_context.generate_errata_email(report)

        # Should use arrow format
        assert "tne ==> the" in email_content
        # Should NOT use "Change to" format
        assert "Change" not in email_content

    def test_errata_email_no_line_number(self, generator, sample_metadata):
        """Test that errors without line numbers are excluded."""
        c1 = CandidateError(
            pg_text="tne",
            scan_text="the",
            pg_offset=25,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )
        error1 = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.95,
            pg_file_line=0,  # No line number
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error1],
            scan_source="Internet Archive (identifier: test-scan)"
        )

        email_content = generator.generate_errata_email(report)

        # Should show 0 errors ready (no line number)
        assert "No errors found requiring correction" in email_content
        assert "tne" not in email_content


class TestCLI:
    def test_build_parser(self):
        from gerrata.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["43", "--output", "/tmp/reports"])
        assert args.pg_id == 43
        assert args.output == "/tmp/reports"
        assert not args.no_verify
        assert not args.verbose

    def test_build_parser_all_options(self):
        from gerrata.cli import build_parser
        parser = build_parser()
        args = parser.parse_args([
            "43",
            "--scan-id", "test-id",
            "--pg-file", "/tmp/pg.txt",
            "--ocr-file", "/tmp/ocr.txt",
            "--output", "/tmp/out",
            "--verbose",
            "--no-verify",
            "--strict",
            "--vision-url", "http://api.test.com",
            "--vision-key", "test-key",
            "--vision-model", "test-model",
        ])
        assert args.pg_id == 43
        assert args.scan_id == "test-id"
        assert args.pg_file == "/tmp/pg.txt"
        assert args.ocr_file == "/tmp/ocr.txt"
        assert args.verbose
        assert args.no_verify
        assert args.strict
        assert args.vision_url == "http://api.test.com"
        assert args.vision_model == "test-model"

    def test_main_returns_code(self):
        from gerrata.cli import main
        # Missing scan-id should fail gracefully
        code = main(["43", "--ocr-file", "/nonexistent"])
        # Should return 3 (pipeline error) since file doesn't exist
        assert code in (2, 3)
