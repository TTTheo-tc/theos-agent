"""Tests for WebSearchTool with DDG/Brave/Tavily providers."""

from __future__ import annotations

from unittest.mock import patch

from src.agent.tools.web_search import WebSearchTool


async def test_ddg_search_returns_results():
    """DDG search works without API key."""
    tool = WebSearchTool(provider="duckduckgo")
    mock_results = [
        {"title": "Example", "href": "https://example.com", "body": "Example site"},
    ]
    with patch("src.agent.tools.web_search.DDGS") as mock_ddgs:  # noqa: N806
        mock_ddgs.return_value.text.return_value = mock_results
        result = await tool.execute(query="test")
    assert "Example" in result
    assert "https://example.com" in result


async def test_ddg_is_default_provider():
    """Default provider is DDG (zero-config usable)."""
    tool = WebSearchTool()
    assert tool._effective_provider == "duckduckgo"


async def test_fallback_to_ddg_when_no_keys():
    """Even if provider=brave, falls back to DDG if no Brave key."""
    tool = WebSearchTool(provider="brave")
    assert tool._effective_provider == "duckduckgo"


async def test_brave_used_when_key_present():
    """Brave used when API key is available."""
    tool = WebSearchTool(provider="brave", api_key="test-key")
    assert tool._effective_provider == "brave"


async def test_tavily_used_when_key_present():
    """Tavily used when API key is available."""
    tool = WebSearchTool(provider="tavily", tavily_api_key="test-key")
    assert tool._effective_provider == "tavily"
