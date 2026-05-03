"""Backward-compatible re-export of web tools.

Tools have been split into focused modules:
- web_search.py (WebSearchTool)
- web_fetch.py (WebFetchTool)
- web_http.py (HttpRequestTool)
- web_image_search.py (ImageSearchTool)
"""

from __future__ import annotations

from src.agent.tools.web_fetch import WebFetchTool
from src.agent.tools.web_http import HttpRequestTool
from src.agent.tools.web_image_search import ImageSearchTool
from src.agent.tools.web_search import WebSearchTool

__all__ = ["WebSearchTool", "WebFetchTool", "HttpRequestTool", "ImageSearchTool"]
