"""Tests for SubstantiveErrataGenerator."""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
from gerrata.reporter.substantive import (
    SubstantiveErrataGenerator,
    _estimate_tokens,
    _ia_page_url,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_error(
    pg_text: str = "teh",
    scan_text: str = "the",
    confidence: float = 0.8,
    page: int = 10,
    verdict: Verdict = Verdict.SCAN_CORRECT,
    category: ErrorCategory = ErrorCategory.OCR_SCANNO,
) -> Error:
    candidate = CandidateError(
        pg_text=pg_text,
        scan_text=scan_text,
        pg_offset=0,
        scan_page=page,
        category=category,
        severity=ErrorSeverity.HIGH,
    )
    return Error(
        candidate=candidate,
        verdict=verdict,
        confidence=confidence,
        reasoning="test reasoning",
    )


def _make_report(errors: list[Error] | None = None) -> Report:
    meta = PGMetadata(
        pg_id=2021,
        title="Test Book",
        author="Test Author",
    )
    return Report(
        metadata=meta,
        scan_source="https://archive.org/details/testscan",
        errors=errors or [],
    )


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestIAPageUrl:
    def test_basic(self):
        url = _ia_page_url("nostromotaleofse00conruoft", 48)
        assert url == "https://archive.org/details/nostromotaleofse00conruoft/page/n48/mode/1up"

    def test_page_zero(self):
        url = _ia_page_url("scan123", 0)
        assert url == "https://archive.org/details/scan123/page/n0/mode/1up"


class TestEstimateTokens:
    def test_empty(self):
        assert _estimate_tokens("") == 0

    def test_known(self):
        # 100 chars ≈ 25 tokens
        assert _estimate_tokens("a" * 100) == 25


class TestTruncateJson:
    """Test the static _truncate_json method."""

    def _make_json(self, errors: list[dict]) -> str:
        payload = {
            "metadata": {"title": "Test"},
            "errors": errors,
        }
        return json.dumps(payload)

    def test_keeps_full_high_confidence(self):
        errors = [
            {"pg_text": "teh", "scan_text": "the", "confidence": 0.9, "pg_sentence": "full sentence here", "reasoning": "long reasoning"},
            {"pg_text": "wrod", "scan_text": "word", "confidence": 0.6, "pg_sentence": "another sentence", "reasoning": "more reasoning"},
            {"pg_text": "foo", "scan_text": "bar", "confidence": 0.3, "pg_sentence": "low conf sentence", "reasoning": "low reasoning"},
        ]
        raw = self._make_json(errors)
        result = SubstantiveErrataGenerator._truncate_json(raw)
        data = json.loads(result)

        # Should still have all 3 entries
        assert len(data["errors"]) == 3

        # Low-confidence entry should be compact (no pg_sentence/reasoning)
        low_entry = data["errors"][2]
        assert "pg_sentence" not in low_entry
        assert "reasoning" not in low_entry
        assert low_entry["pg_text"] == "foo"
        assert low_entry["confidence"] == 0.3

        # High-confidence entries should keep full data
        high_entry = data["errors"][0]
        assert high_entry["pg_sentence"] == "full sentence here"
        assert high_entry["reasoning"] == "long reasoning"

    def test_drops_low_confidence_when_over_budget(self):
        # Build a massive payload to exceed a tiny budget
        errors = [
            {"pg_text": f"high_{i}", "scan_text": f"high_scan_{i}", "confidence": 0.9,
             "pg_sentence": "x" * 1000, "reasoning": "y" * 1000}
            for i in range(100)
        ]
        low_errors = [
            {"pg_text": f"low_{i}", "scan_text": f"low_scan_{i}", "confidence": 0.2,
             "pg_sentence": "z" * 1000, "reasoning": "w" * 1000}
            for i in range(100)
        ]
        raw = self._make_json(errors + low_errors)
        # Use a tiny budget to force low-confidence dropping
        result = SubstantiveErrataGenerator._truncate_json(raw, max_tokens=100)
        data = json.loads(result)

        # High/medium should be kept
        assert any(e.get("pg_sentence") == "x" * 1000 for e in data["errors"])
        # Low entries should be dropped (or compacted if they fit)
        assert len(data["errors"]) <= 100

    def test_handles_invalid_json(self):
        result = SubstantiveErrataGenerator._truncate_json("not json at all")
        assert result == "not json at all"

    def test_handles_empty_errors(self):
        raw = json.dumps({"metadata": {}, "errors": []})
        result = SubstantiveErrataGenerator._truncate_json(raw)
        assert json.loads(result)["errors"] == []


class TestBuildPrompt:
    """Test prompt construction."""

    def test_includes_book_info(self):
        gen = SubstantiveErrataGenerator(
            api_key="test-key",
            scan_id="testscan",
        )
        report = _make_report()
        sys_prompt, user_prompt = gen._build_prompt(report, "email body", "{}")

        assert "Test Book" in user_prompt
        assert "Test Author" in user_prompt
        assert "#2021" in user_prompt
        assert "testscan" in user_prompt

    def test_includes_email_content(self):
        gen = SubstantiveErrataGenerator(scan_id="s1")
        report = _make_report()
        _, user_prompt = gen._build_prompt(report, "SPECIAL_EMAIL_MARKER", "{}")
        assert "SPECIAL_EMAIL_MARKER" in user_prompt

    def test_system_prompt_has_classification_rules(self):
        gen = SubstantiveErrataGenerator(scan_id="s1")
        report = _make_report()
        sys_prompt, _ = gen._build_prompt(report, "email", "{}")

        assert "EXCLUDE" in sys_prompt
        assert "INCLUDE" in sys_prompt
        assert "Diacritic-only" in sys_prompt
        assert "Wrong word" in sys_prompt


class TestGenerate:
    """Test the async generate method."""

    @pytest.mark.asyncio
    async def test_skips_without_api_key(self):
        gen = SubstantiveErrataGenerator(api_key="", scan_id="test")
        report = _make_report()
        result = await gen.generate(report, "email", "{}", Path("/tmp/test.txt"))
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_without_scan_id(self):
        gen = SubstantiveErrataGenerator(api_key="key", scan_id="")
        report = _make_report()
        result = await gen.generate(report, "email", "{}", Path("/tmp/test.txt"))
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_path_on_success(self, tmp_path):
        gen = SubstantiveErrataGenerator(
            api_key="test-key",
            scan_id="testscan",
        )
        report = _make_report()
        fake_markdown = "# Substantive Errata: Test Book\n\nNo errors found."

        with patch.object(
            gen, "_call_llm", new_callable=AsyncMock, return_value=fake_markdown
        ):
            output = tmp_path / "test_errata_email.txt"
            output.write_text("placeholder")
            result = await gen.generate(report, "email", "{}", output)

        assert result is not None
        assert result.exists()
        content = result.read_text()
        assert "Substantive Errata" in content

    @pytest.mark.asyncio
    async def test_returns_none_on_llm_failure(self, tmp_path):
        gen = SubstantiveErrataGenerator(
            api_key="test-key",
            scan_id="testscan",
        )
        report = _make_report()

        with patch.object(
            gen, "_call_llm", new_callable=AsyncMock, side_effect=Exception("API down")
        ):
            output = tmp_path / "test_errata_email.txt"
            result = await gen.generate(report, "email", "{}", output)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self, tmp_path):
        gen = SubstantiveErrataGenerator(
            api_key="test-key",
            scan_id="testscan",
        )
        report = _make_report()

        with patch.object(
            gen, "_call_llm", new_callable=AsyncMock, return_value=""
        ):
            output = tmp_path / "test_errata_email.txt"
            result = await gen.generate(report, "email", "{}", output)

        assert result is None


class TestCallLLM:
    """Test the LLM API call."""

    @pytest.mark.asyncio
    async def test_makes_correct_request(self):
        gen = SubstantiveErrataGenerator(
            api_key="secret-key",
            model="test-model",
            api_url="https://example.com/api",
        )
        fake_response = {
            "choices": [{"message": {"content": "LLM output here"}}]
        }

        mock_response = AsyncMock()
        mock_response.json = lambda: fake_response  # json() is sync, not async
        mock_response.raise_for_status = lambda: None

        with patch("gerrata.reporter.substantive.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await gen._call_llm("system msg", "user msg")

        assert result == "LLM output here"

        # Verify the request was made correctly
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://example.com/api"
        payload = call_args[1]["json"]
        assert payload["model"] == "test-model"
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][0]["content"] == "system msg"
        assert payload["messages"][1]["role"] == "user"
        assert payload["messages"][1]["content"] == "user msg"

        # Verify auth header
        headers = call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer secret-key"


class TestImport:
    """Verify the module is importable."""

    def test_import(self):
        from gerrata.reporter.substantive import SubstantiveErrataGenerator
        assert SubstantiveErrataGenerator is not None

    def test_package_export(self):
        from gerrata.reporter import SubstantiveErrataGenerator
        assert SubstantiveErrataGenerator is not None
