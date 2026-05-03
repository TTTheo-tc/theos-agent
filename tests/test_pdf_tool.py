"""Tests for PdfTool."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pypdf import PdfWriter

from src.agent.tools.pdf import (
    DEFAULT_MAX_PDFS,
    PdfTool,
    _is_url,
    _load_pdf_bytes,
    parse_page_range,
)
from src.providers.base import LLMResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(content: str = "Analysis result.") -> AsyncMock:
    provider = AsyncMock()
    provider.chat.return_value = LLMResponse(content=content, tool_calls=[])
    return provider


def _make_pdf(tmp_path: Path, name: str = "test.pdf", num_pages: int = 3) -> Path:
    """Create a minimal valid PDF with pypdf."""
    writer = PdfWriter()
    for i in range(num_pages):
        writer.add_blank_page(width=72, height=72)
        # Add text annotation to make pages have some content
        writer.pages[i].merge_page(writer.pages[i])
    buf = io.BytesIO()
    writer.write(buf)
    p = tmp_path / name
    p.write_bytes(buf.getvalue())
    return p


def _make_pdf_with_text(
    tmp_path: Path, name: str = "text.pdf", texts: list[str] | None = None
) -> Path:
    """Create a PDF with actual text content using reportlab-free approach.

    Since we can't easily add text with pypdf alone, we create a PDF and
    test extraction behavior (which may return empty text for blank pages).
    """
    return _make_pdf(tmp_path, name, num_pages=len(texts) if texts else 1)


# ---------------------------------------------------------------------------
# Unit tests: parse_page_range
# ---------------------------------------------------------------------------


class TestParsePageRange:
    def test_single_page(self):
        assert parse_page_range("1", 10) == [0]

    def test_range(self):
        assert parse_page_range("1-3", 10) == [0, 1, 2]

    def test_comma_separated(self):
        assert parse_page_range("1,3,5", 10) == [0, 2, 4]

    def test_mixed(self):
        assert parse_page_range("1-3,5,7-8", 10) == [0, 1, 2, 4, 6, 7]

    def test_clamp_to_total(self):
        # Pages beyond total are excluded
        assert parse_page_range("1-100", 5) == [0, 1, 2, 3, 4]

    def test_out_of_range_single(self):
        assert parse_page_range("99", 5) == []

    def test_dedup(self):
        assert parse_page_range("1,1,2,2", 5) == [0, 1]

    def test_empty_string(self):
        assert parse_page_range("", 10) == []

    def test_invalid_segment(self):
        with pytest.raises(ValueError, match="Invalid page range"):
            parse_page_range("abc", 10)

    def test_spaces(self):
        assert parse_page_range(" 1 - 3 , 5 ", 10) == [0, 1, 2, 4]


# ---------------------------------------------------------------------------
# Unit tests: _is_url
# ---------------------------------------------------------------------------


class TestIsUrl:
    def test_http(self):
        assert _is_url("http://example.com/doc.pdf") is True

    def test_https(self):
        assert _is_url("https://example.com/doc.pdf") is True

    def test_local_path(self):
        assert _is_url("/tmp/doc.pdf") is False

    def test_relative_path(self):
        assert _is_url("docs/report.pdf") is False


# ---------------------------------------------------------------------------
# Unit tests: _load_pdf_bytes (local files)
# ---------------------------------------------------------------------------


class TestLoadPdfLocal:
    @pytest.mark.asyncio
    async def test_load_local(self, tmp_path: Path):
        p = _make_pdf(tmp_path)
        data, fname = await _load_pdf_bytes(str(p))
        assert data[:5] == b"%PDF-"
        assert fname == "test.pdf"

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            await _load_pdf_bytes("/nonexistent/doc.pdf")

    @pytest.mark.asyncio
    async def test_file_too_large(self, tmp_path: Path):
        p = tmp_path / "huge.pdf"
        p.write_bytes(b"%PDF-" + b"\x00" * (21 * 1024 * 1024))
        with pytest.raises(ValueError, match="too large"):
            await _load_pdf_bytes(str(p))

    @pytest.mark.asyncio
    async def test_tilde_expansion(self, tmp_path: Path):
        p = _make_pdf(tmp_path)
        with patch("src.agent.tools.pdf.Path.expanduser", return_value=p):
            data, fname = await _load_pdf_bytes("~/test.pdf")
            assert data[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# URL loading (mocked HTTP)
# ---------------------------------------------------------------------------


class TestLoadPdfUrl:
    @pytest.mark.asyncio
    async def test_load_url(self, tmp_path: Path):
        p = _make_pdf(tmp_path)
        pdf_bytes = p.read_bytes()

        mock_response = AsyncMock()
        mock_response.content = pdf_bytes
        mock_response.headers = {"content-type": "application/pdf"}
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.agent.tools.pdf.httpx.AsyncClient", return_value=mock_client):
            data, fname = await _load_pdf_bytes("https://example.com/reports/annual.pdf")
            assert data[:5] == b"%PDF-"
            assert fname == "annual.pdf"

    @pytest.mark.asyncio
    async def test_url_no_pdf_extension(self, tmp_path: Path):
        p = _make_pdf(tmp_path)
        pdf_bytes = p.read_bytes()

        mock_response = AsyncMock()
        mock_response.content = pdf_bytes
        mock_response.headers = {"content-type": "application/pdf"}
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.agent.tools.pdf.httpx.AsyncClient", return_value=mock_client):
            data, fname = await _load_pdf_bytes("https://example.com/download?id=123")
            assert fname == "document.pdf"


# ---------------------------------------------------------------------------
# Tool: schema and properties
# ---------------------------------------------------------------------------


class TestPdfToolSchema:
    def test_name(self):
        tool = PdfTool()
        assert tool.name == "pdf"

    def test_description_not_empty(self):
        tool = PdfTool()
        assert len(tool.description) > 20

    def test_parameters_schema(self):
        tool = PdfTool()
        params = tool.parameters
        assert params["type"] == "object"
        assert "pdf" in params["properties"]
        assert "pdfs" in params["properties"]
        assert "prompt" in params["properties"]
        assert "pages" in params["properties"]

    def test_to_schema(self):
        tool = PdfTool()
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "pdf"

    def test_risk_level(self):
        tool = PdfTool()
        assert tool.risk_level == "low"

    def test_owner_only_false(self):
        tool = PdfTool()
        assert tool.owner_only is False


# ---------------------------------------------------------------------------
# Tool: execute — text extraction only (no prompt)
# ---------------------------------------------------------------------------


class TestPdfToolExtractOnly:
    @pytest.mark.asyncio
    async def test_no_pdf_error(self):
        tool = PdfTool()
        result = await tool.execute()
        assert "No PDF provided" in result

    @pytest.mark.asyncio
    async def test_single_pdf(self, tmp_path: Path):
        p = _make_pdf(tmp_path, num_pages=2)
        tool = PdfTool()
        result = await tool.execute(pdf=str(p))
        assert "test.pdf" in result
        assert "2 pages" in result

    @pytest.mark.asyncio
    async def test_multiple_pdfs(self, tmp_path: Path):
        p1 = _make_pdf(tmp_path, "a.pdf", num_pages=1)
        p2 = _make_pdf(tmp_path, "b.pdf", num_pages=2)
        tool = PdfTool()
        result = await tool.execute(pdfs=[str(p1), str(p2)])
        assert "a.pdf" in result
        assert "b.pdf" in result

    @pytest.mark.asyncio
    async def test_pdf_and_pdfs_combined(self, tmp_path: Path):
        p1 = _make_pdf(tmp_path, "a.pdf")
        p2 = _make_pdf(tmp_path, "b.pdf")
        p3 = _make_pdf(tmp_path, "c.pdf")
        tool = PdfTool()
        result = await tool.execute(pdf=str(p1), pdfs=[str(p2), str(p3)])
        assert "a.pdf" in result
        assert "b.pdf" in result
        assert "c.pdf" in result

    @pytest.mark.asyncio
    async def test_dedup(self, tmp_path: Path):
        p = _make_pdf(tmp_path)
        tool = PdfTool()
        result = await tool.execute(pdf=str(p), pdfs=[str(p)])
        # Should only appear once
        assert result.count("test.pdf") == 1

    @pytest.mark.asyncio
    async def test_too_many_pdfs(self):
        tool = PdfTool()
        paths = [f"/fake/doc_{i}.pdf" for i in range(DEFAULT_MAX_PDFS + 5)]
        result = await tool.execute(pdfs=paths)
        assert "Too many PDFs" in result

    @pytest.mark.asyncio
    async def test_whitespace_sources_ignored(self, tmp_path: Path):
        p = _make_pdf(tmp_path)
        tool = PdfTool()
        result = await tool.execute(pdfs=["  ", str(p), ""])
        assert "test.pdf" in result

    @pytest.mark.asyncio
    async def test_all_pdfs_fail(self):
        tool = PdfTool()
        result = await tool.execute(pdfs=["/bad1.pdf", "/bad2.pdf"])
        assert "Error loading PDFs" in result

    @pytest.mark.asyncio
    async def test_partial_load_failure(self, tmp_path: Path):
        p = _make_pdf(tmp_path)
        tool = PdfTool()
        result = await tool.execute(pdfs=[str(p), "/nonexistent/bad.pdf"])
        assert "Failed to load 1 PDF" in result
        assert "test.pdf" in result


# ---------------------------------------------------------------------------
# Tool: execute — page range
# ---------------------------------------------------------------------------


class TestPdfToolPages:
    @pytest.mark.asyncio
    async def test_page_range(self, tmp_path: Path):
        p = _make_pdf(tmp_path, num_pages=5)
        tool = PdfTool()
        result = await tool.execute(pdf=str(p), pages="1-2")
        assert "extracted pages: 2" in result

    @pytest.mark.asyncio
    async def test_max_pages_limit(self, tmp_path: Path):
        p = _make_pdf(tmp_path, num_pages=10)
        tool = PdfTool()
        result = await tool.execute(pdf=str(p), max_pages=3)
        assert "extracted pages: 3" in result


# ---------------------------------------------------------------------------
# Tool: execute — with LLM analysis (prompt provided)
# ---------------------------------------------------------------------------


class TestPdfToolWithLLM:
    @pytest.mark.asyncio
    async def test_with_prompt(self, tmp_path: Path):
        provider = _make_provider("The document discusses quarterly earnings.")
        tool = PdfTool(provider=provider)
        p = _make_pdf(tmp_path)
        result = await tool.execute(pdf=str(p), prompt="Summarize this document")
        assert "quarterly earnings" in result
        # Verify provider was called
        call_args = provider.chat.call_args
        messages = call_args.kwargs["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        content = messages[0]["content"]
        # Should have extracted text block + prompt block
        assert any("Extracted PDF content" in c["text"] for c in content if c["type"] == "text")
        assert any("Summarize" in c["text"] for c in content if c["type"] == "text")

    @pytest.mark.asyncio
    async def test_no_provider_with_prompt(self, tmp_path: Path):
        tool = PdfTool(provider=None)
        p = _make_pdf(tmp_path)
        result = await tool.execute(pdf=str(p), prompt="Summarize")
        assert "No LLM provider" in result
        # Should still contain extracted text
        assert "test.pdf" in result

    @pytest.mark.asyncio
    async def test_provider_error(self, tmp_path: Path):
        provider = AsyncMock()
        provider.chat.side_effect = RuntimeError("Model unavailable")
        tool = PdfTool(provider=provider)
        p = _make_pdf(tmp_path)
        result = await tool.execute(pdf=str(p), prompt="Analyze")
        assert "Error calling LLM" in result
        assert "Model unavailable" in result
        # Should still contain extracted text
        assert "test.pdf" in result

    @pytest.mark.asyncio
    async def test_auth_error_is_rewritten(self, tmp_path: Path):
        provider = AsyncMock()
        provider.chat.side_effect = RuntimeError(
            "Error code: 401 - {'type': 'error', 'error': {'type': "
            "'authentication_error', 'message': 'invalid x-api-key'}}"
        )
        tool = PdfTool(provider=provider)
        p = _make_pdf(tmp_path)
        result = await tool.execute(pdf=str(p), prompt="Analyze")
        assert "credential is invalid or expired" in result
        assert "invalid x-api-key" not in result
        assert "authentication_error" not in result
        assert "test.pdf" in result

    @pytest.mark.asyncio
    async def test_max_tokens_passed(self, tmp_path: Path):
        provider = _make_provider("OK")
        tool = PdfTool(provider=provider)
        p = _make_pdf(tmp_path)
        await tool.execute(pdf=str(p), prompt="Analyze", max_tokens=2048)
        assert provider.chat.call_args.kwargs["max_tokens"] == 2048

    @pytest.mark.asyncio
    async def test_temperature_low(self, tmp_path: Path):
        provider = _make_provider("OK")
        tool = PdfTool(provider=provider)
        p = _make_pdf(tmp_path)
        await tool.execute(pdf=str(p), prompt="Analyze")
        assert provider.chat.call_args.kwargs["temperature"] == 0.3

    @pytest.mark.asyncio
    async def test_custom_model(self, tmp_path: Path):
        provider = _make_provider("OK")
        tool = PdfTool(provider=provider, model="openai/gpt-4o")
        p = _make_pdf(tmp_path)
        await tool.execute(pdf=str(p), prompt="Analyze")
        assert provider.chat.call_args.kwargs["model"] == "openai/gpt-4o"


# ---------------------------------------------------------------------------
# Tool: execute — no prompt returns raw text (no LLM call)
# ---------------------------------------------------------------------------


class TestPdfToolNoLLMCall:
    @pytest.mark.asyncio
    async def test_no_prompt_no_llm_call(self, tmp_path: Path):
        provider = _make_provider("Should not be called")
        tool = PdfTool(provider=provider)
        p = _make_pdf(tmp_path)
        await tool.execute(pdf=str(p))
        # Provider should NOT be called when no prompt
        provider.chat.assert_not_called()
