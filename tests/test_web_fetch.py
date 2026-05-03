"""Tests for WebFetchTool with Jina Reader and Firecrawl fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.agent.tools.web_fetch import WebFetchTool


async def test_jina_extractor_returns_content():
    """Jina Reader extracts content from URL."""
    tool = WebFetchTool(extractor="jina", jina_api_key="test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"data": {"title": "Test Page", "content": "Page content here"}}

    with patch("httpx.get", return_value=mock_resp) as mock_get:
        result = await tool.execute(url="https://example.com")

    assert "Test Page" in result
    assert "Page content here" in result
    mock_get.assert_called_once()
    assert "r.jina.ai" in mock_get.call_args[0][0]


async def test_firecrawl_fallback_on_short_content():
    """Firecrawl triggers when primary extraction < 100 chars."""
    tool = WebFetchTool(
        extractor="readability",
        firecrawl_enabled=True,
        firecrawl_api_key="fc-key",
        firecrawl_api_url="https://api.firecrawl.dev/v1",
    )

    # Primary extraction returns very short content (JS-only page)
    mock_readability = AsyncMock(return_value=("app", "readability"))

    # Firecrawl returns proper content (must be >= 100 chars to replace primary)
    fc_md = (
        "# Full Page Content\n\n"
        "Rendered by Firecrawl with enough content to exceed the 100-character "
        "threshold that gates the fallback replacement logic."
    )
    mock_firecrawl = AsyncMock(return_value=fc_md)

    with (
        patch.object(tool, "_extract_readability", mock_readability),
        patch.object(tool, "_fallback_firecrawl", mock_firecrawl),
    ):
        result = await tool.execute(url="https://spa-app.com")

    assert "Firecrawl" in result
    mock_firecrawl.assert_called_once_with("https://spa-app.com")


async def test_ssrf_blocks_private_url():
    """SSRF protection blocks internal URLs."""
    tool = WebFetchTool()
    result = await tool.execute(url="http://192.168.1.1/admin")
    assert "private" in result.lower() or "blocked" in result.lower()


async def test_readability_extractor_preserved():
    """Default readability extraction still works."""
    tool = WebFetchTool(extractor="readability")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/html"}
    mock_resp.text = (
        "<html><head><title>Test</title></head>" "<body><p>Hello world</p></body></html>"
    )
    mock_resp.url = "https://example.com"
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_resp):
        result = await tool.execute(url="https://example.com")

    assert "Hello world" in result or "example.com" in result
