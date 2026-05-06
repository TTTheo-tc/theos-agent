"""Web search tool with DuckDuckGo, Brave, and Tavily providers."""

from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None  # type: ignore[assignment]

from src.agent.tools.base import Tool
from src.agent.tools.web_common import normalize_text, strip_tags
from src.security.credential_injector import (
    CredentialInjector,
    EncryptedSecretResolver,
    build_default_registry,
)
from src.security.secret_refs import resolve_secret_ref


class WebSearchTool(Tool):
    """Search the web using DuckDuckGo, Brave, or Tavily Search API."""

    name = "web_search"
    description = "Search the web. Returns titles, URLs, and snippets."
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only include results from these domains",
            },
            "blocked_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exclude results from these domains",
            },
            "count": {
                "type": "integer",
                "description": "Number of results (1-10)",
                "minimum": 1,
                "maximum": 10,
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        api_key: str | None = None,
        max_results: int = 5,
        provider: str = "duckduckgo",
        tavily_api_key: str | None = None,
        credential_injector: CredentialInjector | None = None,
    ) -> None:
        self._init_api_key = api_key
        self._init_tavily_api_key = tavily_api_key
        self.max_results = max_results
        self._provider = provider.lower() if provider else "duckduckgo"
        self._credential_injector = credential_injector or CredentialInjector(
            build_default_registry(),
            EncryptedSecretResolver(),
        )

    @property
    def api_key(self) -> str:
        """Brave Search API key."""
        return resolve_secret_ref(self._init_api_key, default="") or os.environ.get(
            "BRAVE_API_KEY", ""
        )

    @property
    def tavily_api_key(self) -> str:
        """Tavily Search API key."""
        return resolve_secret_ref(self._init_tavily_api_key, default="") or os.environ.get(
            "TAVILY_API_KEY", ""
        )

    @property
    def _effective_provider(self) -> str:
        """Auto-detect provider: use configured provider, fallback if key missing."""
        if self._provider == "tavily" and self.tavily_api_key:
            return "tavily"
        if self._provider == "brave" and self.api_key:
            return "brave"
        if self._provider == "duckduckgo":
            return "duckduckgo"
        # Fallback: try whichever has a key, then DDG
        if self.tavily_api_key:
            return "tavily"
        if self.api_key:
            return "brave"
        return "duckduckgo"

    async def execute(
        self,
        query: str,
        count: int | None = None,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        del kwargs
        provider = self._effective_provider
        if provider == "tavily":
            return await self._search_tavily(query, count, allowed_domains, blocked_domains)
        if provider == "duckduckgo":
            return await self._search_duckduckgo(query, count, allowed_domains, blocked_domains)
        return await self._search_brave(query, count, allowed_domains, blocked_domains)

    async def _search_duckduckgo(
        self,
        query: str,
        count: int | None = None,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
    ) -> str:
        try:
            if DDGS is None:
                return (
                    "Error: DuckDuckGo web search requires the web extra. "
                    "Install it with: pip install 'theos-agent[web]'"
                )
            n = min(max(count or self.max_results, 1), 10)
            # Fetch extra results to allow post-filtering
            fetch_count = n * 3 if (allowed_domains or blocked_domains) else n
            ddgs = DDGS(timeout=10)
            results = await asyncio.to_thread(ddgs.text, query, max_results=fetch_count)

            # Domain filtering
            if allowed_domains or blocked_domains:
                filtered = []
                for item in results:
                    url = item.get("href", "")
                    domain = urlparse(url).netloc.lower()
                    if allowed_domains and not any(
                        domain == d or domain.endswith(f".{d}") for d in allowed_domains
                    ):
                        continue
                    if blocked_domains and any(
                        domain == d or domain.endswith(f".{d}") for d in blocked_domains
                    ):
                        continue
                    filtered.append(item)
                results = filtered

            if not results:
                return f"No results for: {query}"

            lines = [f"Results for: {query}\n"]
            for i, r in enumerate(results[:n], 1):
                title = r.get("title", "")
                url = r.get("href", "")
                body = normalize_text(strip_tags(r.get("body", "")))
                lines.append(f"{i}. {title}\n   {url}")
                if body:
                    lines.append(f"   {body}")
            return (
                "\n".join(lines)
                + "\n\nREMINDER: Include the sources above in your response using markdown hyperlinks."
            )
        except Exception as e:
            return f"Error: {e}"

    async def _search_tavily(
        self,
        query: str,
        count: int | None = None,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
    ) -> str:
        try:
            key = self.tavily_api_key
            if not key:
                return (
                    "Error: Tavily API key not configured. "
                    "Set it in ~/.theos/config.json under tools.web.search.tavilyApiKey, "
                    "or export TAVILY_API_KEY."
                )

            n = min(max(count or self.max_results, 1), 10)
            body: dict[str, Any] = {
                "query": query,
                "max_results": n,
                "include_answer": False,
            }
            if allowed_domains:
                body["include_domains"] = allowed_domains
            if blocked_domains:
                body["exclude_domains"] = blocked_domains

            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "https://api.tavily.com/search",
                    json=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {key}",
                    },
                    timeout=15.0,
                )
                r.raise_for_status()

            data = r.json()
            results = data.get("results", [])

            if not results:
                return f"No results for: {query}"

            lines = [f"Results for: {query}\n"]
            for i, item in enumerate(results[:n], 1):
                title = item.get("title", "")
                url = item.get("url", "")
                content = item.get("content", "")
                lines.append(f"{i}. {title}\n   {url}")
                if content:
                    # Tavily returns longer content; truncate for consistency
                    snippet = content[:300].rstrip()
                    if len(content) > 300:
                        snippet += "..."
                    lines.append(f"   {snippet}")
            return (
                "\n".join(lines)
                + "\n\nREMINDER: Include the sources above in your response using markdown hyperlinks."
            )
        except Exception as e:
            return f"Error: {e}"

    async def _search_brave(
        self,
        query: str,
        count: int | None = None,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
    ) -> str:
        try:
            n = min(max(count or self.max_results, 1), 10)
            # Fetch extra results to allow post-filtering
            fetch_count = n * 3 if (allowed_domains or blocked_domains) else n
            request_url = "https://api.search.brave.com/res/v1/web/search"
            headers = {"Accept": "application/json"}
            if self.api_key:
                headers["X-Subscription-Token"] = self.api_key
            params = {"q": query, "count": str(min(fetch_count, 20))}
            headers, params = self._credential_injector.prepare_request(
                request_url,
                headers,
                params,
            )
            if not headers.get("X-Subscription-Token"):
                return (
                    "Error: Brave Search API key not configured. "
                    "Set it in ~/.theos/config.json under tools.web.search.apiKey, "
                    "store a 'brave' auth profile, or export BRAVE_API_KEY."
                )

            async with httpx.AsyncClient() as client:
                r = await client.get(
                    request_url,
                    params=params,
                    headers=headers,
                    timeout=10.0,
                )
                r.raise_for_status()

            results = r.json().get("web", {}).get("results", [])

            # Domain filtering
            if allowed_domains or blocked_domains:
                filtered = []
                for item in results:
                    url = item.get("url", "")
                    domain = urlparse(url).netloc.lower()
                    if allowed_domains and not any(
                        domain == d or domain.endswith(f".{d}") for d in allowed_domains
                    ):
                        continue
                    if blocked_domains and any(
                        domain == d or domain.endswith(f".{d}") for d in blocked_domains
                    ):
                        continue
                    filtered.append(item)
                results = filtered

            if not results:
                return f"No results for: {query}"

            lines = [f"Results for: {query}\n"]
            for i, item in enumerate(results[:n], 1):
                lines.append(f"{i}. {item.get('title', '')}\n   {item.get('url', '')}")
                if desc := item.get("description"):
                    lines.append(f"   {desc}")
            return (
                "\n".join(lines)
                + "\n\nREMINDER: Include the sources above in your response using markdown hyperlinks."
            )
        except Exception as e:
            return f"Error: {e}"
