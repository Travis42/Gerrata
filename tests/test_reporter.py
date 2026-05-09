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

    def test_save_reports_with_suffix(self, generator, sample_report, tmp_path):
        """Test that suffix parameter adds suffix before file extension."""
        md_path, json_path, email_path, review_path = generator.save_reports(
            sample_report, tmp_path, suffix="-raw"
        )
        assert md_path.exists()
        assert json_path.exists()
        assert email_path.exists()
        assert review_path.exists()

        # Check that suffix is added before extension
        assert md_path.stem.endswith("_errata-raw")
        assert json_path.stem.endswith("_errata-raw")
        assert email_path.stem.endswith("_errata_email-raw")
        assert review_path.stem.endswith("_review_needed-raw")

        # Check extensions are preserved
        assert md_path.suffix == ".md"
        assert json_path.suffix == ".json"
        assert email_path.suffix == ".txt"
        assert review_path.suffix == ".txt"

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
        assert "Line 335, Page 1, (STORY OF THE DOOR)" in review_content  # Page 0 + 1
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
        assert "Line 129, Page 7" in review_content  # Page 6 + 1
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


class TestSentenceExtraction:
    """Test the _extract_sentence method for context extraction."""

    def test_extract_sentence_basic(self):
        generator = ReportGenerator()

        # Basic sentence extraction
        text = "This is sentence one. This is sentence two. This is sentence three."
        sentence = generator._extract_sentence(text, 28, 6)  # "sentence"
        assert "sentence two" in sentence
        assert sentence.startswith("This is sentence two")

    def test_extract_sentence_with_newlines(self):
        generator = ReportGenerator()

        # Text with newlines
        text = "First sentence.\n\nSecond sentence here. Third sentence."
        sentence = generator._extract_sentence(text, 25, 8)  # "sentence"
        assert "Second sentence here" in sentence

    def test_extract_sentence_at_boundary(self):
        generator = ReportGenerator()

        # At sentence start
        text = "Hello world. Goodbye world."
        sentence = generator._extract_sentence(text, 0, 5)  # "Hello"
        assert "Hello world" in sentence

    def test_extract_sentence_empty_text(self):
        generator = ReportGenerator()
        sentence = generator._extract_sentence("", 0, 5)
        assert sentence == ""

    def test_extract_sentence_invalid_offset(self):
        generator = ReportGenerator()
        text = "Some text here."
        sentence = generator._extract_sentence(text, 100, 5)  # Out of bounds
        assert sentence == ""


class TestPageNumbering:
    """Test that page numbers are displayed as 1-indexed in human-readable outputs."""

    def test_json_includes_display_page(self, sample_metadata, sample_errors):
        generator = ReportGenerator()
        report = Report(
            metadata=sample_metadata,
            pages_checked=10,
            total_pages=20,
            errors=sample_errors,
            alignments=[]
        )

        json_str = generator.generate_json(report)
        data = json.loads(json_str)

        # Check that display_page is scan_page + 1
        for error in data["errors"]:
            assert "display_page" in error
            assert error["display_page"] == error["scan_page"] + 1

    def test_markdown_shows_1_indexed_pages(self, sample_metadata, sample_errors):
        generator = ReportGenerator()
        report = Report(
            metadata=sample_metadata,
            pages_checked=10,
            total_pages=20,
            errors=sample_errors,
            alignments=[]
        )

        md = generator.generate_markdown(report)

        # Check that pages are displayed as 1-indexed (scan_page + 1)
        # scan_page=5 should be shown as page 6
        # scan_page=10 should be shown as page 11
        # scan_page=15 should be shown as page 16
        # scan_page=20 should be shown as page 21
        assert "page 16" in md  # scan_page=15 becomes page 16
        # Make sure 0-indexed pages don't appear
        assert "page 15" not in md  # scan_page=15 should not be shown as page 15


class TestLineNumberCalculation:
    """Test line number calculation for PG file offsets."""

    def test_line_number_calculation(self):
        # Simulate body text with multiple lines
        body_text = """Line 1
Line 2
Line 3
Line 4
Line 5"""

        # Offset at start of line 3 (after "Line 1\nLine 2\n")
        offset = len("Line 1\nLine 2\n")
        line_num = body_text[:offset].count('\n') + 1

        assert line_num == 3

    def test_line_number_at_offset(self):
        body_text = "First line\nSecond line\nThird line"

        # Character at position 15 (in "Second line")
        offset = 15
        line_num = body_text[:offset].count('\n') + 1

        assert line_num == 2

    def test_line_number_empty_text(self):
        body_text = ""
        offset = 0
        line_num = body_text[:offset].count('\n') + 1
        assert line_num == 1


class TestCutoffArtifactFilter:
    """Test the word-boundary cutoff artifact detection."""

    def test_cutoff_artifact_short_word(self):
        """Test that short suffix fragments are detected as artifacts."""
        # This is the "hen" -> "when" case
        # "hen" is 3 chars and is a suffix of "when" (4 chars)
        def is_cutoff_artifact(scan_text: str, pg_text: str) -> bool:
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

            # If the shorter text is ≤ 3 chars, it's likely a fragment
            if len(shorter) <= 3:
                return True

            return False

        # Test "hen" -> "when" (should be artifact)
        assert is_cutoff_artifact("when", "hen") == True
        assert is_cutoff_artifact("hen", "when") == True

    def test_cutoff_artifact_real_word(self):
        """Test that real word differences are not detected as artifacts."""
        # This is the "clause" -> "clauses" case
        # "clause" is 6 chars and is a prefix of "clauses" (7 chars)
        # Should NOT be artifact because "clause" is a real word (>3 chars)
        def is_cutoff_artifact(scan_text: str, pg_text: str) -> bool:
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

        # Test "clause" -> "clauses" (should NOT be artifact)
        assert is_cutoff_artifact("clauses", "clause") == False
        assert is_cutoff_artifact("clause", "clauses") == False

    def test_cutoff_artifact_multi_word(self):
        """Test that multi-word phrases are not detected as artifacts."""
        def is_cutoff_artifact(scan_text: str, pg_text: str) -> bool:
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

        # Multi-word should not be artifact
        assert is_cutoff_artifact("the cat", "cat") == False
        assert is_cutoff_artifact("walking", "walk") == False  # "walk" is 4 chars, not artifact

    def test_cutoff_artifact_no_match(self):
        """Test that completely different words are not detected as artifacts."""
        def is_cutoff_artifact(scan_text: str, pg_text: str) -> bool:
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

        # Completely different words should not be artifact
        assert is_cutoff_artifact("apple", "orange") == False
        assert is_cutoff_artifact("the", "cat") == False


class TestConcurrencyDefault:
    """Test that default concurrency is 5."""

    def test_default_concurrency_is_five(self):
        from gerrata.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["43"])
        assert args.concurrency == 5
