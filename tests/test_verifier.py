"""Tests for LLM vision verifier."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from gerrata.models import CandidateError, Error, ErrorCategory, Verdict
from gerrata.verifier.vision import VisionVerifier, DEFAULT_SYSTEM_PROMPT


@pytest.fixture
def verifier():
    return VisionVerifier(
        api_url="https://api.example.com/v1/chat/completions",
        api_key="test-key",
        model="test-model",
    )


@pytest.fixture
def unconfigured_verifier():
    return VisionVerifier()


@pytest.fixture
def sample_error():
    return CandidateError(
        pg_text="tne letter",
        scan_text="the letter",
        pg_offset=100,
        scan_page=5,
        diff_description="PG has 'tne' where scan has 'the'",
        category=ErrorCategory.OCR_SCANNO,
    )


class TestVisionVerifier:
    def test_is_configured(self, verifier):
        assert verifier.is_configured()

    def test_not_configured_empty(self):
        v = VisionVerifier()
        assert not v.is_configured()

    def test_not_configured_no_model(self):
        v = VisionVerifier(api_url="http://example.com")
        assert not v.is_configured()

    @pytest.mark.asyncio
    async def test_unconfigured_returns_unable(self, unconfigured_verifier, sample_error):
        result = await unconfigured_verifier.verify_error(sample_error)
        assert result.verdict == Verdict.UNABLE_TO_VERIFY
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_no_image_returns_unable(self, verifier, sample_error):
        result = await verifier.verify_error(sample_error, scan_image_path=None)
        assert result.verdict == Verdict.UNABLE_TO_VERIFY

    @pytest.mark.asyncio
    async def test_missing_image_returns_unable(self, verifier, sample_error):
        result = await verifier.verify_error(
            sample_error, scan_image_path=Path("/nonexistent/image.png")
        )
        assert result.verdict == Verdict.UNABLE_TO_VERIFY

    @pytest.mark.asyncio
    async def test_verify_error_scan_correct(self, verifier, sample_error):
        """Test successful verification where scan is correct."""
        from PIL import Image
        import io

        img = Image.new("RGB", (100, 100), color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        test_image = Path("/tmp/test_gerrata_verify.png")
        test_image.write_bytes(buf.getvalue())

        mock_response_data = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "verdict": "scan_correct",
                        "confidence": 0.9,
                        "reasoning": "The scan clearly shows 'the letter', not 'tne letter'",
                        "suggested_fix": "tne → the",
                    })
                }
            }]
        }

        try:
            with patch.object(verifier, '_parse_response') as mock_parse:
                mock_parse.return_value = Error(
                    candidate=sample_error,
                    verdict=Verdict.SCAN_CORRECT,
                    confidence=0.9,
                    reasoning="Test",
                    suggested_fix="tne → the",
                )
                with patch("gerrata.verifier.vision.httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.post = AsyncMock()
                    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

                    result = await verifier.verify_error(sample_error, scan_image_path=test_image)

            assert isinstance(result, Error)
            assert result.verdict == Verdict.SCAN_CORRECT
            assert result.confidence == 0.9
        finally:
            test_image.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_verify_error_edition_variant(self, verifier, sample_error):
        """Test edition variant classification."""
        from PIL import Image
        import io

        img = Image.new("RGB", (100, 100), color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        test_image = Path("/tmp/test_gerrata_verify2.png")
        test_image.write_bytes(buf.getvalue())

        try:
            with patch.object(verifier, '_parse_response') as mock_parse:
                mock_parse.return_value = Error(
                    candidate=sample_error,
                    verdict=Verdict.EDITION_VARIANT,
                    confidence=0.85,
                    reasoning="Test edition variant",
                )
                with patch("gerrata.verifier.vision.httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.post = AsyncMock()
                    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

                    result = await verifier.verify_error(sample_error, scan_image_path=test_image)

            assert result.verdict == Verdict.EDITION_VARIANT
            assert result.confidence == 0.85
        finally:
            test_image.unlink(missing_ok=True)

    def test_build_prompt(self, verifier, sample_error):
        prompt = verifier._build_prompt(sample_error, "Some context around the error.")
        assert "tne letter" in prompt
        # scan_text is intentionally excluded to prevent hallucination —
        # the model reads the page image directly.
        assert "the letter" not in prompt
        assert "Some context" in prompt
        assert "transcri" in prompt.lower()

    def test_extract_json_direct(self, verifier):
        text = '{"verdict": "scan_correct", "confidence": 0.8}'
        result = verifier._extract_json(text)
        assert result["verdict"] == "scan_correct"
        assert result["confidence"] == 0.8

    def test_extract_json_code_block(self, verifier):
        text = '```json\n{"verdict": "pg_correct", "confidence": 0.7}\n```'
        result = verifier._extract_json(text)
        assert result["verdict"] == "pg_correct"

    def test_extract_json_inline(self, verifier):
        text = 'I think this is an edition variant. {"verdict": "edition_variant", "confidence": 0.6}'
        result = verifier._extract_json(text)
        assert result["verdict"] == "edition_variant"

    def test_extract_json_empty(self, verifier):
        result = verifier._extract_json("No JSON here")
        assert result == {}

    def test_system_prompt_frames_verifier_as_transcriber(self):
        assert "transcri" in DEFAULT_SYSTEM_PROMPT.lower()
        assert "character by character" in DEFAULT_SYSTEM_PROMPT.lower()
        assert "unable_to_verify" in DEFAULT_SYSTEM_PROMPT.lower()

    def test_derive_verdict_from_transcription(self, verifier, sample_error):
        """Verdicts are derived mechanically from transcription vs PG text."""
        # Exact match
        v, c, r, f = verifier._derive_verdict_from_transcription("hello world", "hello world")
        assert v == Verdict.PG_CORRECT

        # Case-insensitive match
        v, c, r, f = verifier._derive_verdict_from_transcription("Hello World", "hello world")
        assert v == Verdict.PG_CORRECT

        # Whitespace-only difference
        v, c, r, f = verifier._derive_verdict_from_transcription("hello  world", "hello world")
        assert v == Verdict.PG_CORRECT

        # Minor difference (>90% similar)
        v, c, r, f = verifier._derive_verdict_from_transcription("the letter", "the latter")
        assert v == Verdict.EDITION_VARIANT
        assert r != ""

        # Moderate difference (50-90%)
        v, c, r, f = verifier._derive_verdict_from_transcription("tne letter", "the letter")
        assert v in (Verdict.SCAN_CORRECT, Verdict.EDITION_VARIANT)
        assert f != ""  # suggested_fix should be populated

        # Major difference (<50%)
        v, c, r, f = verifier._derive_verdict_from_transcription("the letter", "completely different")
        assert v == Verdict.AMBIGUOUS

    @pytest.mark.asyncio
    async def test_verify_batch_per_page_groups_by_page(self, verifier):
        """Test that batch verification groups items by page correctly."""
        from PIL import Image
        import io

        # Create test errors on different pages
        errors = [
            CandidateError(
                pg_text="error1",
                scan_text="fix1",
                pg_offset=100,
                scan_page=0,
                diff_description="Error on page 0",
            ),
            CandidateError(
                pg_text="error2",
                scan_text="fix2",
                pg_offset=200,
                scan_page=1,
                diff_description="Error on page 1",
            ),
            CandidateError(
                pg_text="error3",
                scan_text="fix3",
                pg_offset=300,
                scan_page=0,  # Same page as first error
                diff_description="Error on page 0",
            ),
        ]

        # Create a test image
        img = Image.new("RGB", (100, 100), color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        test_image = Path("/tmp/test_gerrata_batch.png")
        test_image.write_bytes(buf.getvalue())

        try:
            # Mock the page batch verification to return simple errors
            async def mock_verify_page(image_path, items):
                from gerrata.models import Error, Verdict
                return [
                    Error(
                        candidate=error,
                        verdict=Verdict.SCAN_CORRECT,
                        confidence=0.9,
                        reasoning=f"Mock result for page {error.scan_page}",
                    )
                    for _, error, _ in items
                ]

            with patch.object(verifier, '_verify_page_batch', side_effect=mock_verify_page):
                results = await verifier.verify_batch_per_page(
                    errors,
                    get_image_path=lambda e: test_image,
                    get_pg_context=lambda e: "context",
                )

            assert len(results) == 3
            # Verify order is preserved
            assert results[0].candidate.pg_text == "error1"
            assert results[1].candidate.pg_text == "error2"
            assert results[2].candidate.pg_text == "error3"
            # All should have been verified
            assert all(r.confidence == 0.9 for r in results)

        finally:
            test_image.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_verify_batch_per_page_concurrency_preserves_order(self, verifier):
        """Test that concurrent verification preserves result order."""
        from PIL import Image
        import io

        # Create test errors on different pages to test concurrent processing
        errors = []
        for i in range(10):
            errors.append(CandidateError(
                pg_text=f"error{i}",
                scan_text=f"fix{i}",
                pg_offset=100 + i * 100,
                scan_page=i % 3,  # Distribute across 3 pages
                diff_description=f"Error {i}",
            ))

        # Create a test image
        img = Image.new("RGB", (100, 100), color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        test_image = Path("/tmp/test_gerrata_concurrency.png")
        test_image.write_bytes(buf.getvalue())

        try:
            # Create verifier with concurrency=3
            concurrent_verifier = VisionVerifier(
                api_url="https://api.example.com/v1/chat/completions",
                api_key="test-key",
                model="test-model",
                concurrency=3,
            )

            # Mock the page batch verification to return simple errors
            async def mock_verify_page(image_path, items):
                from gerrata.models import Error, Verdict
                # Simulate variable delay to ensure concurrent execution
                import asyncio
                await asyncio.sleep(0.01 * len(items))
                return [
                    Error(
                        candidate=error,
                        verdict=Verdict.SCAN_CORRECT,
                        confidence=0.9,
                        reasoning=f"Mock result for {error.pg_text}",
                    )
                    for _, error, _ in items
                ]

            with patch.object(concurrent_verifier, '_verify_page_batch', side_effect=mock_verify_page):
                results = await concurrent_verifier.verify_batch_per_page(
                    errors,
                    get_image_path=lambda e: test_image,
                    get_pg_context=lambda e: "context",
                )

            # Verify all results are present
            assert len(results) == 10
            # Verify order is preserved (most important test for concurrency)
            for i, result in enumerate(results):
                assert result.candidate.pg_text == f"error{i}", f"Order not preserved at index {i}"
                assert result.confidence == 0.9

        finally:
            test_image.unlink(missing_ok=True)
