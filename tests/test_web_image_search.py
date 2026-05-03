"""Tests for ImageSearchTool (DDG images)."""

from __future__ import annotations

from unittest.mock import patch

from src.agent.tools.web_image_search import ImageSearchTool


async def test_image_search_returns_results():
    tool = ImageSearchTool()
    mock_results = [
        {
            "title": "Cat photo",
            "image": "https://img.com/cat.jpg",
            "thumbnail": "https://img.com/cat_t.jpg",
            "url": "https://source.com",
        },
    ]
    with patch("src.agent.tools.web_image_search.DDGS") as mock_ddgs:
        mock_ddgs.return_value.images.return_value = mock_results
        result = await tool.execute(query="cats")
    assert "Cat photo" in result
    assert "https://img.com/cat.jpg" in result


async def test_image_search_accepts_size_filter():
    tool = ImageSearchTool()
    with patch("src.agent.tools.web_image_search.DDGS") as mock_ddgs:
        mock_ddgs.return_value.images.return_value = []
        await tool.execute(query="landscape", size="Large")
    mock_ddgs.return_value.images.assert_called_once()
    call_kwargs = mock_ddgs.return_value.images.call_args
    assert "Large" in str(call_kwargs)


async def test_image_search_tool_name():
    tool = ImageSearchTool()
    assert tool.name == "image_search"
