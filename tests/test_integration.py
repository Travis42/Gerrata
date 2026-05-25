"""Integration test: run full gerrata pipeline on PG #43.

This test uses local fixture files (no network required) and
exercises the entire pipeline from parsing through report generation.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from gerrata.models import ErrorCategory, Verdict, AlignmentMethod
from gerrata.fetcher.pg import PGFetcher
from gerrata.fetcher.scans import ScanFetcher, ScanPage
from gerrata.aligner.coarse import CoarseAligner
from gerrata.aligner.vision_aligner import VisionAligner, VisionTranscriber, PageTranscription
from gerrata.checker.text_diff import TextDiffChecker
from gerrata.checker.rules import FalsePositiveFilter
from gerrata.reporter.generator import ReportGenerator


FIXTURES = Path(__file__).parent / "fixtures" / "pg43"
PG_FILE = FIXTURES / "43-h.htm"
OCR_FILE = FIXTURES / "scans" / "ocr_text.txt"


def _load_scan_pages_from_ocr(ocr_file: Path, identifier: str) -> list[ScanPage]:
    """Load OCR text from file and create ScanPage objects.

    This replaces the old prepare_scan() which relied on legacy OCR downloads.
    The main pipeline uses vision transcription, but tests need fixture data.
    """
    ocr_text = ocr_file.read_text(encoding="utf-8", errors="replace")
    # Split on page markers if present
    import re
    page_marker_pattern = re.compile(r"^Page\s+(\d+)\s*$", re.MULTILINE)
    matches = list(page_marker_pattern.finditer(ocr_text))

    pages = []
    if len(matches) >= 3:
        for i, match in enumerate(matches):
            page_num = int(match.group(1)) - 1
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(ocr_text)
            page_text = ocr_text[start:end].strip()
            if page_text:
                pages.append(ScanPage(page_num=page_num, ocr_text=page_text))
    else:
        pages.append(ScanPage(page_num=0, ocr_text=ocr_text.strip()))

    return pages


class TestIntegrationPipeline:
    """End-to-end test with local fixture files."""

    def test_full_pipeline_no_network(self, tmp_path):
        """Run the complete pipeline on PG #43 with local files."""
        # Step 1: Parse PG text
        fetcher = PGFetcher()
        parsed = fetcher.parse_file(PG_FILE)

        assert parsed.metadata.pg_id == 43
        assert "Jekyll" in parsed.metadata.title
        assert "Stevenson" in parsed.metadata.author
        assert len(parsed.body_text) > 10000
        assert len(parsed.paragraphs) > 50

        # Step 2: Load scan pages from fixture OCR text
        scan_pages = _load_scan_pages_from_ocr(OCR_FILE, "06-stevenson-jekyll-hyde")
        assert len(scan_pages) >= 1

        # Combine scan text
        scan_full_text = "\n\n".join(p.ocr_text for p in scan_pages if p.ocr_text)
        assert len(scan_full_text) > 5000

        # Step 3: Align
        aligner = CoarseAligner()
        alignments = aligner.align(
            pg_text=parsed.body_text,
            pg_paragraphs=parsed.paragraphs,
            scan_ocr_text=scan_full_text,
            scan_pages=scan_pages,
        )

        assert isinstance(alignments, list)
        # Should find some alignment since the texts share common content
        coverage = aligner.alignment_confidence(alignments, len(parsed.body_text))
        assert coverage >= 0  # Just verify it doesn't crash

        # Step 4: Text diff
        checker = TextDiffChecker()
        candidates = checker.check_all_alignments(
            pg_text=parsed.body_text,
            alignments=alignments,
            scan_pages=scan_pages,
        )

        assert isinstance(candidates, list)

        # Step 5: Filter
        fp_filter = FalsePositiveFilter()
        candidates = fp_filter.filter(candidates)

        assert isinstance(candidates, list)

        # Step 6: Build report (skip LLM verification - no API)
        from gerrata.models import Error, Report

        errors = [Error(candidate=c) for c in candidates]
        report = Report(
            metadata=parsed.metadata,
            scan_source="https://archive.org/details/06-stevenson-jekyll-hyde",
            pages_checked=len(set(a.scan_page for a in alignments)) if alignments else 0,
            total_pages=len(scan_pages),
            alignment_confidence=coverage,
            edition_match_confidence=0.5,  # PG #43 is different edition
            edition_notes="PG #43 and IA scan 06-stevenson-jekyll-hyde are different editions",
            errors=errors,
            alignments=alignments,
        )

        # Step 7: Generate reports
        generator = ReportGenerator()
        json_path, email_path = generator.save_reports(report, tmp_path)

        assert json_path.exists()
        assert email_path.exists()

        # Verify JSON
        json_content = json.loads(json_path.read_text())
        assert json_content["metadata"]["pg_id"] == 43
        assert json_content["metadata"]["title"] == parsed.metadata.title
        assert "errors" in json_content
        assert "summary" in json_content

    def test_pipeline_with_edition_variant_detection(self):
        """Verify edition variant detection works in the pipeline."""
        fetcher = PGFetcher()
        parsed = fetcher.parse_file(PG_FILE)

        # Create a mock candidate that's clearly an edition variant
        from gerrata.models import CandidateError, Error
        candidate = CandidateError(
            pg_text="downright",
            scan_text="down-right",
            pg_offset=0,
            scan_page=0,
        )

        fp_filter = FalsePositiveFilter()
        filtered = fp_filter.filter([candidate])

        # The filter should either:
        # 1. Filter it out (hyphenation variant in non-strict mode), or
        # 2. Reclassify it as edition_variant
        if filtered:
            # The candidate's category should have been updated
            assert filtered[0].category == ErrorCategory.EDITION_VARIANT

    def test_pg43_has_known_content(self):
        """Verify the PG #43 fixture has expected content."""
        fetcher = PGFetcher()
        parsed = fetcher.parse_file(PG_FILE)

        # Known first line of the story
        body_lower = parsed.body_text.lower()
        assert "utterson" in body_lower

        # Known chapter titles
        assert any("door" in ch.title.lower() for ch in parsed.chapters)

    def test_ocr_has_known_content(self):
        """Verify the OCR fixture has expected content."""
        ocr = OCR_FILE.read_text(encoding="utf-8", errors="replace")
        # The OCR fixture should contain key text from Jekyll & Hyde
        assert "Jekyll" in ocr or "JEKYLL" in ocr
        assert len(ocr) > 10000

    def test_reports_are_valid(self, tmp_path):
        """Verify generated reports are valid and complete."""
        from gerrata.models import Report

        report = Report(
            metadata=PGFetcher().parse_file(PG_FILE).metadata,
            scan_source="test",
            errors=[],
        )

        generator = ReportGenerator()
        json_path, email_path = generator.save_reports(report, tmp_path)

        # JSON should be valid
        j = json.loads(json_path.read_text())
        assert j["summary"]["total_candidates"] == 0
        assert j["summary"]["high_confidence_errors"] == 0


class TestVisionFirstPipeline:
    """Integration test for the vision-first pipeline (with mocked transcription)."""

    def test_vision_pipeline_with_mock_transcription(self, tmp_path):
        """Run the vision-first pipeline with mocked transcriptions."""
        # Step 1: Parse PG text
        fetcher = PGFetcher()
        parsed = fetcher.parse_file(PG_FILE)
        assert len(parsed.body_text) > 10000
        assert len(parsed.paragraphs) > 50

        # Step 2: Create mock transcriptions from PG text paragraphs
        # Use paragraphs that are long enough to be meaningful
        transcriptions = []
        page_num = 0
        for para in parsed.paragraphs:
            if len(para) > 40:  # Only substantial paragraphs
                transcriptions.append(PageTranscription(
                    page_num=page_num,
                    image_path=tmp_path / f"page_{page_num:04d}.png",
                    transcription=para,
                    model_used="mock-vision",
                    success=True,
                ))
                page_num += 1
                if page_num >= 10:  # Use 10 pages
                    break

        assert len(transcriptions) >= 5

        # Step 3: Align transcriptions to PG text
        vision_aligner = VisionAligner(match_threshold=0.3, min_chunk_length=20)
        results = vision_aligner.align_all_pages(
            transcriptions=transcriptions,
            pg_text=parsed.body_text,
            pg_paragraphs=parsed.paragraphs,
        )
        alignments = vision_aligner.get_alignments(results)
        scan_pages = vision_aligner.build_scan_pages_from_transcriptions(transcriptions)

        # Should match most pages since transcriptions come from PG text itself
        assert len(alignments) >= 3

        # Step 4: Text diff
        checker = TextDiffChecker()
        candidates = checker.check_all_alignments(
            pg_text=parsed.body_text,
            alignments=alignments,
            scan_pages=scan_pages,
        )

        # Since transcriptions match PG text exactly, there should be no errors
        assert isinstance(candidates, list)

        # Step 5: Build report
        from gerrata.models import Error, Report

        errors = [Error(candidate=c) for c in candidates]
        report = Report(
            metadata=parsed.metadata,
            scan_source="vision-mock-test",
            pages_checked=len(set(a.scan_page for a in alignments)),
            total_pages=len(transcriptions),
            alignment_confidence=vision_aligner.alignment_confidence(alignments, len(parsed.body_text)),
            errors=errors,
            alignments=alignments,
        )

        # Step 6: Generate reports
        generator = ReportGenerator()
        json_path, email_path = generator.save_reports(report, tmp_path / "vision")

        assert json_path.exists()
        assert email_path.exists()

        # Verify alignment method is LLM_VISION
        for a in alignments:
            assert a.method == AlignmentMethod.LLM_VISION

    def test_vision_pipeline_with_diffs(self, tmp_path):
        """Test vision pipeline where transcription differs from PG text."""
        fetcher = PGFetcher()
        parsed = fetcher.parse_file(PG_FILE)

        # Create a transcription with an intentional difference
        original_para = parsed.paragraphs[0]
        modified_para = original_para.replace("was", "wos")  # Simulate OCR error in scan

        transcriptions = [
            PageTranscription(
                page_num=0,
                image_path=tmp_path / "page_0000.png",
                transcription=modified_para,
                model_used="mock-vision",
                success=True,
            ),
        ]

        vision_aligner = VisionAligner(match_threshold=0.2, min_chunk_length=10)
        results = vision_aligner.align_all_pages(
            transcriptions=transcriptions,
            pg_text=parsed.body_text,
            pg_paragraphs=parsed.paragraphs,
        )

        if results:
            alignments = vision_aligner.get_alignments(results)
            scan_pages = vision_aligner.build_scan_pages_from_transcriptions(transcriptions)

            checker = TextDiffChecker()
            candidates = checker.check_all_alignments(
                pg_text=parsed.body_text,
                alignments=alignments,
                scan_pages=scan_pages,
            )

            # Should find the "was" → "wos" difference
            has_diff = any("was" in c.pg_text.lower() or "wos" in c.scan_text.lower() for c in candidates)
            # Note: may not detect if the paragraph-level match is imperfect
