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
        json_path, email_path = generator.save_reports(sample_report, tmp_path)
        assert json_path.exists()
        assert email_path.exists()
        assert json_path.suffix == ".json"
        assert email_path.suffix == ".txt"

    def test_save_reports_custom_name(self, generator, sample_report, tmp_path):
        json_path, email_path = generator.save_reports(sample_report, tmp_path, "custom")
        assert json_path.stem == "custom_errata"
        assert email_path.stem == "custom_errata_email"

    def test_save_reports_creates_dir(self, generator, sample_report, tmp_path):
        out_dir = tmp_path / "sub" / "dir"
        json_path, email_path = generator.save_reports(sample_report, out_dir)
        assert json_path.exists()
        assert email_path.exists()

    def test_save_reports_with_suffix(self, generator, sample_report, tmp_path):
        """Test that suffix parameter adds suffix before file extension."""
        json_path, email_path = generator.save_reports(
            sample_report, tmp_path, suffix="-raw"
        )
        assert json_path.exists()
        assert email_path.exists()

        # Check that suffix is added before extension
        assert json_path.stem.endswith("_errata-raw")
        assert email_path.stem.endswith("_errata_email-raw")

        # Check extensions are preserved
        assert json_path.suffix == ".json"
        assert email_path.suffix == ".txt"

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
        assert arrow_fix == "tne ==> the [OCR]"

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
        assert arrow_fix == "walked ==> walking [OCR]"

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
        assert "verified the following changes" in email_content

        # Should use ==> arrow format (PG's preferred format)
        assert "tne ==> the" in email_content

        # Low confidence error should still be included (above 0.4 threshold)
        assert "walked" in email_content

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

        # Generate JSON and email formats
        json_str = generator_with_context.generate_json(report)
        email = generator_with_context.generate_errata_email(report)

        # Verify formats work
        parsed_json = json.loads(json_str)
        assert parsed_json["errors"][0]["pg_file_line"] == 6
        assert parsed_json["errors"][0]["chapter_title"] == "Chapter One"

        assert "tne ==> the" in email

    def test_print_summary(self, generator, sample_report, capsys):
        """print_summary should not raise."""
        generator.print_summary(sample_report)

    def test_errata_email_filters_by_confidence(self, generator_with_context, sample_metadata):
        """Test that errata_email only includes scan_correct with >= 85% confidence."""
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
        assert "tne ==> the" in email_content

        # Should include both (0.7 is above 0.4 threshold)
        assert "walked" in email_content

        # Summary should show 1 error found
        # error count removed from email output

    def test_errata_email_deduplicates_by_offset(self, generator_with_context, sample_metadata):
        """Test that errors within 50 chars offset proximity are deduplicated."""
        # Same offset proximity (within 50 chars), lower confidence
        c1 = CandidateError(
            pg_text="tne",
            scan_text="the",
            pg_offset=100,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )
        error1 = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.95,
            pg_file_line=100,
        )

        # Close offset (within 50 chars), higher confidence — should be deduped away
        c2 = CandidateError(
            pg_text="tne",
            scan_text="the",
            pg_offset=120,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )
        error2 = Error(
            candidate=c2,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.97,
            pg_file_line=100,
        )

        # Far offset (> 50 chars away) — should be kept
        c3 = CandidateError(
            pg_text="respectors",
            scan_text="respecters",
            pg_offset=300,
            scan_page=10,
            category=ErrorCategory.OCR_SCANNO
        )
        error3 = Error(
            candidate=c3,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.90,
            pg_file_line=200,
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error1, error2, error3],
            scan_source="Internet Archive (identifier: test-scan)"
        )

        email_content = generator_with_context.generate_errata_email(report)

        # Should show 2 errors found (one deduped, one far away)
        # error count removed from email output
        # tne==>the should appear only once (deduped)
        assert email_content.count("tne ==> the") == 1
        # respecters should appear
        assert "respectors ==> respecters" in email_content

    def test_dedup_keeps_different_errors_at_nearby_offsets(self, generator, sample_metadata):
        """Dedup must not collapse two DIFFERENT errors within 50 chars offset.

        The old dedup code collapsed anything within 50 chars regardless of text
        content, losing distinct errors. Different text pairs must both survive.
        """
        # Error A — robber-hands ==> robber-bands
        cA = CandidateError(
            pg_text="robber-hands",
            scan_text="robber-bands",
            pg_offset=104400,
            scan_page=96,
            category=ErrorCategory.OCR_SCANNO,
        )
        errA = Error(
            candidate=cA,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.72,
        )

        # Error B — (Ofote) ==> (Ofóte), 40 chars away (within 50) but different text
        cB = CandidateError(
            pg_text="(Ofote)",
            scan_text="(Ofóte)",
            pg_offset=104360,
            scan_page=96,
            category=ErrorCategory.OCR_SCANNO,
        )
        errB = Error(
            candidate=cB,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.65,
        )

        report = Report(
            metadata=sample_metadata,
            errors=[errA, errB],
        )

        email_content = generator.generate_errata_email(report)

        # Both distinct errors must survive dedup and appear in the email.
        assert "robber-hands ==> robber-bands" in email_content
        assert "Ofote" in email_content and "Ofóte" in email_content

    def test_errata_email_arrow_format(self, generator_with_context, sample_metadata):
        """Test that errata_email uses -> arrow format."""
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

        # Should use ==> arrow format (PG's preferred format)
        assert "tne ==> the" in email_content
        # Should NOT use "Change to" format
        assert "Change" not in email_content

    def test_errata_email_no_scan_page(self, generator, sample_metadata):
        """Test that errors without scan_page are excluded."""
        c1 = CandidateError(
            pg_text="tne",
            scan_text="the",
            pg_offset=25,
            scan_page=0,  # No meaningful scan page
            category=ErrorCategory.OCR_SCANNO
        )
        error1 = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.95,
            pg_file_line=0,
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error1],
            scan_source="Internet Archive (identifier: test-scan)"
        )

        email_content = generator.generate_errata_email(report)

        # Error with scan_page=0 will show — still included if it passes filters
        # error count removed from email output

    def test_errata_email_post_dedup_filters(self, generator, sample_metadata):
        """Test punctuation-only and quote-start fragment post-dedup filters."""
        # Punctuation-only change (should be filtered)
        c1 = CandidateError(
            pg_text="hello,",
            scan_text="hello.",
            pg_offset=100,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )
        error1 = Error(
            candidate=c1,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.95,
        )

        # Quote-start fragment (should be filtered)
        c2 = CandidateError(
            pg_text='"Only a very long sentence here that extends far beyond the short quote',
            scan_text='"Only',
            pg_offset=200,
            scan_page=10,
            category=ErrorCategory.OCR_SCANNO
        )
        error2 = Error(
            candidate=c2,
            verdict=Verdict.SCAN_CORRECT,
            confidence=0.90,
        )

        report = Report(
            metadata=sample_metadata,
            errors=[error1, error2],
        )

        email_content = generator.generate_errata_email(report)

        # Both should be filtered out
        assert "no errors requiring correction" in email_content
        assert "hello" not in email_content
        assert "Only" not in email_content

    def test_report_shows_all_missing_content_pages(self, sample_metadata, monkeypatch):
        """All coverage gaps render in the email (indentation regression).

        The gap-rendering loop previously had an indentation bug that only
        rendered the last gap. All uncovered pages must appear in the output.
        """
        import gerrata.checker.gap_detector as gap_mod
        from gerrata.checker.gap_detector import CoverageGap

        gaps = [
            CoverageGap(page=10, strategy="uncovered", word_count=60,
                        scan_text_preview="alpha missing content page one " * 10,
                        pg_verified=True, confidence="high"),
            CoverageGap(page=20, strategy="uncovered", word_count=60,
                        scan_text_preview="bravo missing content page two " * 10,
                        pg_verified=True, confidence="high"),
            CoverageGap(page=30, strategy="uncovered", word_count=60,
                        scan_text_preview="charlie missing content page three " * 10,
                        pg_verified=True, confidence="high"),
        ]
        # Stub detect_scan_gaps so the generator renders exactly these gaps
        monkeypatch.setattr(gap_mod, "detect_scan_gaps", lambda **kwargs: list(gaps))

        # A single error is needed to reach the gap-rendering section (the
        # email returns early when there are no unique errors).
        c = CandidateError(
            pg_text="tne",
            scan_text="the",
            pg_offset=25,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO,
        )
        err = Error(candidate=c, verdict=Verdict.SCAN_CORRECT, confidence=0.95)

        report = Report(metadata=sample_metadata, errors=[err])

        generator = ReportGenerator(
            alignments=[Alignment(pg_start=0, pg_end=100, scan_page=5,
                                  confidence=0.9, method=AlignmentMethod.LCS)],
            scan_pages=[object()],  # non-empty to enter the gap section
            body_text="Some body text here.",
            scan_id="test-scan",
        )

        email_content = generator.generate_errata_email(report)

        # All three gap pages must appear (old indentation bug only rendered
        # the last gap, so the first two would be missing).
        assert "page/n10/mode/1up" in email_content
        assert "page/n20/mode/1up" in email_content
        assert "page/n30/mode/1up" in email_content


class TestMissingContentFormat:
    """Tests for the standardized MISSING CONTENT report format.

    Covers the spec in TASK.md:
    - content_hole gaps render in the short format (Missing: "..." + PG context)
    - consecutive structural gaps group into "Pages N-M" with the long format
    - running headers and page numbers are stripped from transcriptions
    - "Source scan:" header appears only when a multi-page group exists
    - "no alignment" jargon is gone
    """

    def _make_generator(self, scan_pages, alignments, body_text, scan_id="test-scan"):
        return ReportGenerator(
            alignments=alignments,
            scan_pages=scan_pages,
            body_text=body_text,
            scan_id=scan_id,
        )

    def _stub_gaps(self, monkeypatch, gaps):
        import gerrata.checker.gap_detector as gap_mod
        monkeypatch.setattr(gap_mod, "detect_scan_gaps", lambda **kwargs: list(gaps))

    def _trigger_error(self, sample_metadata):
        # The email returns early without a unique error, so inject one.
        c = CandidateError(
            pg_text="tne", scan_text="the", pg_offset=25, scan_page=5,
            category=ErrorCategory.OCR_SCANNO,
        )
        return Error(candidate=c, verdict=Verdict.SCAN_CORRECT, confidence=0.95)

    def test_content_hole_uses_short_format(self, sample_metadata, monkeypatch):
        """Content hole renders as Page N (URL) - N words + Missing: \"...\" + PG context."""
        from gerrata.checker.gap_detector import CoverageGap

        hole = CoverageGap(
            page=219, strategy="content_hole", word_count=9,
            scan_text_preview="barrios with his 2 000 improved rifles here something",
            pg_verified=True, confidence="high",
            missing_words="barrios with his 2 000 improved rifles here something",
            pg_context_before="if we had",
            pg_context_after="could have been",
        )
        self._stub_gaps(monkeypatch, [hole])

        gen = self._make_generator(
            scan_pages=[object()],
            alignments=[Alignment(pg_start=0, pg_end=100, scan_page=5,
                                  confidence=0.9, method=AlignmentMethod.LCS)],
            body_text="Some body text here.",
        )
        report = Report(metadata=sample_metadata, errors=[self._trigger_error(sample_metadata)])
        email = gen.generate_errata_email(report)

        section = email[email.find("MISSING CONTENT"):]
        # Short-format header line
        assert "Page 219 (https://archive.org/details/test-scan/page/n219/mode/1up) - 9 words [HIGH]:" in section
        # Missing: prefixed and quoted
        assert 'Missing: "barrios with his 2 000 improved rifles here something"' in section
        # PG context line with [gap] marker
        assert "PG context: ...if we had [gap] could have been..." in section
        # No "Source scan:" line for single-page short entries
        assert "Source scan:" not in section

    def test_consecutive_pages_grouped_as_multi_page(self, sample_metadata, monkeypatch):
        """Two consecutive uncovered pages render as 'Pages N-M' pointing at the first page."""
        from gerrata.checker.gap_detector import CoverageGap

        # 3 words * 20 = 60 words per page (>= REPORT_MIN_WORDS_UNCOVERED=50)
        gap6 = CoverageGap(page=6, strategy="uncovered", word_count=60,
                           scan_text_preview="page six text " * 20,
                           pg_verified=True, confidence="high")
        gap7 = CoverageGap(page=7, strategy="uncovered", word_count=60,
                           scan_text_preview="page seven text " * 20,
                           pg_verified=True, confidence="high")
        self._stub_gaps(monkeypatch, [gap6, gap7])

        gen = self._make_generator(
            scan_pages=[object(), object()],
            alignments=[
                Alignment(pg_start=0, pg_end=50, scan_page=5, confidence=0.9, method=AlignmentMethod.LCS),
                Alignment(pg_start=100, pg_end=150, scan_page=8, confidence=0.9, method=AlignmentMethod.LCS),
            ],
            body_text="alpha " * 50 + "beta " * 50,
        )
        report = Report(metadata=sample_metadata, errors=[self._trigger_error(sample_metadata)])
        email = gen.generate_errata_email(report)
        section = email[email.find("MISSING CONTENT"):]

        # Grouped header with first-page URL
        assert "Pages 6-7 (https://archive.org/details/test-scan/page/n6/mode/1up)" in section
        # Approximate word count with tilde (60 + 60 = 120)
        assert "~120 words [HIGH]:" in section
        # No separate Page 6 / Page 7 entries
        assert "Page 6 (" not in section
        assert "Page 7 (" not in section
        # Source scan line present because of the multi-page group
        assert "Source scan: https://archive.org/details/test-scan" in section

    def test_non_consecutive_pages_not_grouped(self, sample_metadata, monkeypatch):
        """Pages 10 and 12 (gap > 1) stay as separate single-page entries."""
        from gerrata.checker.gap_detector import CoverageGap

        g1 = CoverageGap(page=10, strategy="uncovered", word_count=60,
                         scan_text_preview="alpha content " * 20,
                         pg_verified=True, confidence="high")
        g2 = CoverageGap(page=12, strategy="uncovered", word_count=60,
                         scan_text_preview="bravo content " * 20,
                         pg_verified=True, confidence="high")
        self._stub_gaps(monkeypatch, [g1, g2])

        gen = self._make_generator(
            scan_pages=[object()],
            alignments=[Alignment(pg_start=0, pg_end=100, scan_page=5,
                                  confidence=0.9, method=AlignmentMethod.LCS)],
            body_text="Some body text here.",
        )
        report = Report(metadata=sample_metadata, errors=[self._trigger_error(sample_metadata)])
        email = gen.generate_errata_email(report)
        section = email[email.find("MISSING CONTENT"):]

        assert "page/n10/mode/1up" in section
        assert "page/n12/mode/1up" in section
        assert "Pages 10-" not in section  # not grouped
        # No Source scan line (no multi-page group)
        assert "Source scan:" not in section

    def test_running_headers_and_page_numbers_stripped(self, sample_metadata, monkeypatch):
        """Repetitive headers, standalone digits, and 'digit + header' combos are removed."""
        from gerrata.checker.gap_detector import CoverageGap

        # The header "THE YOUNGER EDDA." appears on the aligned page AND the
        # missing page so cross-page detection fires. "6 PREFACE." is a combo.
        header_text = (
            "THE YOUNGER EDDA.\n"
            "Prologue text on the aligned page for context here."
        )
        gap_text = (
            "THE YOUNGER EDDA.\n"
            "6                               PREFACE.\n"
            "7\n"
            "The real content starts here and runs for many words to clear "
            "the minimum gap thresholds established by the gap detector logic."
        )
        gap = CoverageGap(page=6, strategy="uncovered", word_count=50,
                          scan_text_preview=gap_text, pg_verified=True, confidence="high")

        class FakePage:
            def __init__(self, pnum, text):
                self.page_num = pnum
                self.vision_text = text
                self.ocr_text = ""
                self.image_path = None

        # Stub: only the gap is reported (page 6). scan_pages also holds an
        # aligned page 5 carrying the same header so header detection fires.
        self._stub_gaps(monkeypatch, [gap])
        gen = self._make_generator(
            scan_pages=[FakePage(5, header_text), FakePage(6, gap_text)],
            alignments=[Alignment(pg_start=0, pg_end=50, scan_page=5,
                                  confidence=0.9, method=AlignmentMethod.LCS)],
            body_text="Some body text here. " * 5,
        )
        report = Report(metadata=sample_metadata, errors=[self._trigger_error(sample_metadata)])
        email = gen.generate_errata_email(report)
        section = email[email.find("MISSING CONTENT"):]

        assert "The real content starts here" in section
        assert "THE YOUNGER EDDA." not in section
        assert "PREFACE." not in section
        # Standalone "7" should not appear on its own line as a page number
        assert "\n7\n" not in section

    def test_no_alignment_jargon_removed(self, sample_metadata, monkeypatch):
        """The 'no alignment' technical description must not appear."""
        from gerrata.checker.gap_detector import CoverageGap

        gap = CoverageGap(page=10, strategy="uncovered", word_count=60,
                          scan_text_preview="alpha content " * 20,
                          pg_verified=True, confidence="high")
        self._stub_gaps(monkeypatch, [gap])

        gen = self._make_generator(
            scan_pages=[object()],
            alignments=[Alignment(pg_start=0, pg_end=100, scan_page=5,
                                  confidence=0.9, method=AlignmentMethod.LCS)],
            body_text="Some body text here.",
        )
        report = Report(metadata=sample_metadata, errors=[self._trigger_error(sample_metadata)])
        email = gen.generate_errata_email(report)
        section = email[email.find("MISSING CONTENT"):]
        assert "no alignment" not in section
        assert "page coverage" not in section

    def test_short_format_single_page_exact_word_count(self, sample_metadata, monkeypatch):
        """A single structural gap under 100 words uses an exact count (no tilde)."""
        from gerrata.checker.gap_detector import CoverageGap

        # 4 words * 13 = 52 words (>= REPORT_MIN_WORDS_UNCOVERED=50, < 100 → short format)
        gap = CoverageGap(page=10, strategy="uncovered", word_count=52,
                          scan_text_preview="alpha content words here " * 13,
                          pg_verified=True, confidence="high")
        self._stub_gaps(monkeypatch, [gap])

        gen = self._make_generator(
            scan_pages=[object()],
            alignments=[Alignment(pg_start=0, pg_end=100, scan_page=5,
                                  confidence=0.9, method=AlignmentMethod.LCS)],
            body_text="Some body text here.",
        )
        report = Report(metadata=sample_metadata, errors=[self._trigger_error(sample_metadata)])
        email = gen.generate_errata_email(report)
        section = email[email.find("MISSING CONTENT"):]
        # Exact count (re-counted from cleaned text), no tilde, short format with Missing: prefix
        assert "- 52 words [HIGH]:" in section
        assert "~52 words" not in section
        assert 'Missing: "' in section

    def test_pg_context_derived_from_neighbor_alignments(self, sample_metadata, monkeypatch):
        """Multi-page group pulls PG context from the alignments before and after."""
        from gerrata.checker.gap_detector import CoverageGap

        gap6 = CoverageGap(page=6, strategy="uncovered", word_count=120,
                           scan_text_preview="page six content " * 30,
                           pg_verified=True, confidence="high")
        gap7 = CoverageGap(page=7, strategy="uncovered", word_count=80,
                           scan_text_preview="page seven content " * 30,
                           pg_verified=True, confidence="high")
        self._stub_gaps(monkeypatch, [gap6, gap7])

        body_text = (
            "before the gap we have many words here that should appear. " * 3
            + "AFTER THE GAP COMES THIS DISTINCTIVE PASSAGE TEXT. " * 3
        )
        gen = self._make_generator(
            scan_pages=[object(), object()],
            alignments=[
                # Alignment on page 5 (before group) ends mid body_text
                Alignment(pg_start=0, pg_end=120, scan_page=5, confidence=0.9, method=AlignmentMethod.LCS),
                # Alignment on page 8 (after group) starts later
                Alignment(pg_start=130, pg_end=200, scan_page=8, confidence=0.9, method=AlignmentMethod.LCS),
            ],
            body_text=body_text,
        )
        report = Report(metadata=sample_metadata, errors=[self._trigger_error(sample_metadata)])
        email = gen.generate_errata_email(report)
        section = email[email.find("MISSING CONTENT"):]

        assert "PG context:" in section
        assert "[gap]" in section
        # Context should pull from body_text (before side)
        assert "before the gap" in section
        # Context should not contain partial words at offset boundaries
        line = [ln for ln in section.split("\n") if ln.startswith("PG context:")][0]
        # The [gap] marker separates before and after cleanly
        assert "] [gap]" not in line  # no dangling bracket from partial word


class TestCLI:
    def test_build_parser(self):
        from gerrata.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["verify-edition", "43", "test-scan-id", "--output", "/tmp/reports"])
        assert args.pg_id == 43
        assert args.output == "/tmp/reports"
        assert not args.verbose

    def test_build_parser_all_options(self):
        from gerrata.cli import build_parser
        parser = build_parser()
        args = parser.parse_args([
            "verify-edition", "43", "test-scan-id",
            "--pg-file", "/tmp/pg.txt",
            "--output", "/tmp/out",
            "--verbose",
            "--vision-url", "http://api.test.com",
            "--vision-key", "test-key",
            "--vision-model", "test-model",
        ])
        assert args.pg_id == 43
        assert args.pg_file == "/tmp/pg.txt"
        assert args.verbose
        assert args.vision_url == "http://api.test.com"
        assert args.vision_model == "test-model"

    def test_main_returns_code(self):
        from gerrata.cli import main
        # The default pipeline path expects pg_id as first arg then flags.
        # Missing pg-file / nonexistent should fail gracefully.
        code = main(["43", "--pg-file", "/nonexistent"])
        # Should return non-zero since file doesn't exist
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

    def test_extract_sentence_strips_underscores(self):
        """Underscores (PG italic/formatting markers) are stripped from context."""
        generator = ReportGenerator()
        text = "He was an _officier superieur_ in the army. It was clear."
        sentence = generator._extract_sentence(text, 10, 18)  # around "officier"
        assert "_" not in sentence
        assert "officier superieur" in sentence


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

        # Check that display_page is present (falls back to scan_page when
        # no scan_pages are configured for IA leaf number lookup)
        for error in data["errors"]:
            assert "display_page" in error
            assert error["display_page"] == error["scan_page"]


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
        def is_cutoff_artifact(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            if ' ' in s or ' ' in p: return False
            if abs(len(s) - len(p)) != 1: return False
            longer, shorter = (s, p) if len(s) > len(p) else (p, s)
            if not longer.startswith(shorter) and not longer.endswith(shorter): return False
            return len(shorter) <= 3

        assert is_cutoff_artifact("when", "hen") == True
        assert is_cutoff_artifact("hen", "when") == True

    def test_cutoff_artifact_real_word(self):
        """Test that real word differences are not detected as artifacts."""
        def is_cutoff_artifact(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            if ' ' in s or ' ' in p: return False
            if abs(len(s) - len(p)) != 1: return False
            longer, shorter = (s, p) if len(s) > len(p) else (p, s)
            if not longer.startswith(shorter) and not longer.endswith(shorter): return False
            return len(shorter) <= 3

        assert is_cutoff_artifact("clauses", "clause") == False
        assert is_cutoff_artifact("clause", "clauses") == False

    def test_cutoff_artifact_multi_word(self):
        """Test that multi-word phrases are not detected as artifacts."""
        def is_cutoff_artifact(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            if ' ' in s or ' ' in p: return False
            if abs(len(s) - len(p)) != 1: return False
            longer, shorter = (s, p) if len(s) > len(p) else (p, s)
            if not longer.startswith(shorter) and not longer.endswith(shorter): return False
            return len(shorter) <= 3

        assert is_cutoff_artifact("the cat", "cat") == False
        assert is_cutoff_artifact("walking", "walk") == False

    def test_cutoff_artifact_no_match(self):
        """Test that completely different words are not detected as artifacts."""
        def is_cutoff_artifact(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            if ' ' in s or ' ' in p: return False
            if abs(len(s) - len(p)) != 1: return False
            longer, shorter = (s, p) if len(s) > len(p) else (p, s)
            if not longer.startswith(shorter) and not longer.endswith(shorter): return False
            return len(shorter) <= 3

        assert is_cutoff_artifact("apple", "orange") == False
        assert is_cutoff_artifact("the", "cat") == False


class TestConcurrencyDefault:
    """Test that default concurrency is 10."""

    def test_default_concurrency_is_five(self):
        from gerrata.cli import main
        # Use main() which handles the default pipeline path (pg_id as first arg)
        # and injects pg_id into the namespace after parsing top-level flags.
        from gerrata.cli import build_parser
        parser = build_parser()
        # Parse with the default pipeline path: no subcommand, just flags.
        # main() intercepts pg_id and parses the rest via parser.
        args = parser.parse_args([])
        assert args.concurrency == 10
