"""Image search tool using DuckDuckGo (free, no API key)."""

from __future__ import annotations

import asyncio
import json

from duckduckgo_search import DDGS

from src.agent.tools.base import Tool


class ImageSearchTool(Tool):
    name = "image_search"
    description = (
        "Search for images on the web. Returns image URLs, thumbnails, and titles. "
        "Supports size and type filters."
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Image search query"},
                "max_results": {
                    "type": "integer",
                    "description": "Number of results (1-10)",
                    "default": 5,
                },
                "size": {
                    "type": "string",
                    "description": "Image size filter",
                    "enum": ["Small", "Medium", "Large", "Wallpaper"],
                },
                "type_image": {
                    "type": "string",
                    "description": "Image type filter",
                    "enum": ["photo", "clipart", "gif", "transparent", "line"],
                },
            },
        }

    async def execute(self, **kwargs) -> str:
        query = kwargs.get("query", "")
        if not query:
            return "Error: query is required"
        max_results = min(kwargs.get("max_results", 5), 10)
        size = kwargs.get("size")
        type_image = kwargs.get("type_image")

        try:
            ddgs = DDGS(timeout=30)
            search_kwargs: dict = {"keywords": query, "max_results": max_results}
            if size:
                search_kwargs["size"] = size
            if type_image:
                search_kwargs["type_image"] = type_image

            results = await asyncio.to_thread(ddgs.images, **search_kwargs)

            if not results:
                return "No images found."

            items = []
            for r in results:
                items.append(
                    {
                        "title": r.get("title", ""),
                        "image_url": r.get("image", ""),
                        "thumbnail_url": r.get("thumbnail", ""),
                        "source_url": r.get("url", ""),
                    }
                )
            return json.dumps(items, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"Image search failed: {e}"
