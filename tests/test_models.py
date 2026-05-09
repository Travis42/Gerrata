"""Tests for gerrata.models dataclasses."""

from gerrata.models import (
    Alignment,
    AlignmentMethod,
    CandidateError,
    Error,
    ErrorCategory,
    ErrorSeverity,
    PGMetadata,
    Report,
    Verdict,
)


class TestAlignment:
    def test_basic_creation(self):
        a = Alignment(pg_start=100, pg_end=200, scan_page=5)
        assert a.pg_start == 100
        assert a.pg_end == 200
        assert a.scan_page == 5
        assert a.confidence == 1.0
        assert a.method == AlignmentMethod.LCS

    def test_pg_length(self):
        a = Alignment(pg_start=100, pg_end=350, scan_page=0)
        assert a.pg_length == 250

    def test_to_dict(self):
        a = Alignment(pg_start=0, pg_end=100, scan_page=3, confidence=0.9)
        d = a.to_dict()
        assert d["pg_start"] == 0
        assert d["pg_end"] == 100
        assert d["scan_page"] == 3
        assert d["confidence"] == 0.9
        assert d["method"] == "lcs"


class TestPGMetadata:
    def test_basic_creation(self):
        m = PGMetadata(pg_id=43, title="Jekyll & Hyde", author="Stevenson")
        assert m.pg_id == 43
        assert m.title == "Jekyll & Hyde"
        assert m.language == "en"

    def test_to_dict(self):
        m = PGMetadata(pg_id=43, title="Test", author="Author")
        d = m.to_dict()
        assert d["pg_id"] == 43
        assert d["title"] == "Test"


class TestCandidateError:
    def test_basic_creation(self):
        c = CandidateError(
            pg_text="tne letter",
            scan_text="the letter",
            pg_offset=500,
            scan_page=10,
            category=ErrorCategory.OCR_SCANNO,
        )
        assert c.category == ErrorCategory.OCR_SCANNO
        assert c.severity == ErrorSeverity.HIGH

    def test_to_dict(self):
        c = CandidateError(
            pg_text="test", scan_text="text", pg_offset=0, scan_page=0,
        )
        d = c.to_dict()
        assert d["category"] == "ocr_scanno"
        assert d["severity"] == "high"


class TestError:
    def test_scan_correct_verdict(self):
        candidate = CandidateError(
            pg_text="tne letter", scan_text="the letter",
            pg_offset=100, scan_page=5,
        )
        err = Error(candidate=candidate, verdict=Verdict.SCAN_CORRECT,
                    confidence=0.95, reasoning="Clear OCR scanno")
        assert err.category == ErrorCategory.OCR_SCANNO
        assert err.severity == ErrorSeverity.HIGH

    def test_edition_variant_verdict(self):
        candidate = CandidateError(
            pg_text="down-right", scan_text="downright",
            pg_offset=200, scan_page=10,
        )
        err = Error(candidate=candidate, verdict=Verdict.EDITION_VARIANT,
                    confidence=0.8)
        assert err.category == ErrorCategory.EDITION_VARIANT
        assert err.severity == ErrorSeverity.INFO

    def test_intentional_modernization_verdict(self):
        candidate = CandidateError(
            pg_text="someone", scan_text="some one",
            pg_offset=50, scan_page=2,
        )
        err = Error(candidate=candidate, verdict=Verdict.INTENTIONAL_MODERNIZATION,
                    confidence=0.7)
        assert err.category == ErrorCategory.MODERNIZATION
        assert err.severity == ErrorSeverity.INFO

    def test_missing_content_severity(self):
        candidate = CandidateError(
            pg_text="", scan_text="[entire paragraph missing]",
            pg_offset=500, scan_page=15,
            category=ErrorCategory.MISSING_CONTENT,
        )
        err = Error(candidate=candidate, verdict=Verdict.SCAN_CORRECT,
                    confidence=0.9)
        assert err.severity == ErrorSeverity.CRITICAL

    def test_to_dict(self):
        candidate = CandidateError(
            pg_text="tne", scan_text="the", pg_offset=0, scan_page=0,
        )
        err = Error(candidate=candidate, verdict=Verdict.SCAN_CORRECT,
                    confidence=0.9, suggested_fix="tne -> the")
        d = err.to_dict()
        assert d["verdict"] == "scan_correct"
        assert d["confidence"] == 0.9
        assert d["suggested_fix"] == "tne -> the"
        assert "category" in d
        assert "severity" in d


class TestReport:
    def _make_report(self, errors: list[Error]) -> Report:
        meta = PGMetadata(pg_id=43, title="Test", author="Author")
        return Report(metadata=meta, errors=errors)

    def test_empty_report(self):
        r = self._make_report([])
        assert r.total_candidates == 0
        assert len(r.high_confidence_errors) == 0
        assert len(r.medium_confidence_errors) == 0
        assert len(r.low_confidence_errors) == 0

    def test_confidence_buckets(self):
        c = CandidateError(pg_text="a", scan_text="b", pg_offset=0, scan_page=0)

        high = Error(candidate=c, verdict=Verdict.SCAN_CORRECT, confidence=0.9)
        med = Error(candidate=c, verdict=Verdict.SCAN_CORRECT, confidence=0.6)
        low = Error(candidate=c, verdict=Verdict.SCAN_CORRECT, confidence=0.3)
        variant = Error(candidate=c, verdict=Verdict.EDITION_VARIANT, confidence=0.9)

        r = self._make_report([high, med, low, variant])
        assert r.total_candidates == 4
        assert len(r.high_confidence_errors) == 1
        assert len(r.medium_confidence_errors) == 1
        assert len(r.low_confidence_errors) == 1
        assert len(r.edition_variants) == 1

    def test_to_dict(self):
        r = self._make_report([])
        d = r.to_dict()
        assert "metadata" in d
        assert "summary" in d
        assert d["summary"]["total_candidates"] == 0
        assert d["metadata"]["pg_id"] == 43

    def test_to_json(self):
        r = self._make_report([])
        j = r.to_json()
        import json
        parsed = json.loads(j)
        assert parsed["metadata"]["pg_id"] == 43

    def test_edition_match_fields(self):
        r = self._make_report([])
        r.edition_match_confidence = 0.6
        r.edition_notes = "Different edition: Collins vs Longmans"
        d = r.to_dict()
        assert d["edition_match_confidence"] == 0.6
        assert "Different edition" in d["edition_notes"]
