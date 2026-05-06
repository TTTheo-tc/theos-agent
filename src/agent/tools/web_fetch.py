"""WebFetchTool — fetch URL content with Jina Reader + Firecrawl fallback."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

import httpx

from src.agent.tools.base import Tool
from src.agent.tools.web_common import (
    MAX_REDIRECTS,
    USER_AGENT,
    normalize_text,
    strip_tags,
    validate_http_url,
)
from src.agent.tools.web_ssrf import async_ssrf_safe_request, async_validate_url_target
from src.security.credential_injector import (
    CredentialInjector,
    EncryptedSecretResolver,
    build_default_registry,
)


class WebFetchTool(Tool):
    """Fetch URL and extract content, optionally processed with a prompt."""

    name = "web_fetch"
    description = (
        "Fetch URL content and extract readable text. "
        "Optionally provide a prompt to focus extraction on specific information."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Fully-formed valid URL to fetch",
            },
            "prompt": {
                "type": "string",
                "description": "What information to extract from the page",
            },
            "extractMode": {
                "type": "string",
                "enum": ["markdown", "text"],
                "description": "Output format (default: markdown)",
            },
            "maxChars": {
                "type": "integer",
                "minimum": 100,
                "description": "Maximum characters to return",
            },
        },
        "required": ["url"],
    }

    def __init__(
        self,
        max_chars: int = 50000,
        credential_injector: CredentialInjector | None = None,
        extractor: str = "auto",
        jina_api_key: str | None = None,
        firecrawl_enabled: bool = False,
        firecrawl_api_key: str | None = None,
        firecrawl_api_url: str = "https://api.firecrawl.dev/v1",
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
    ):
        self.max_chars = max_chars
        self._credential_injector = credential_injector or CredentialInjector(
            build_default_registry(),
            EncryptedSecretResolver(),
        )
        self._extractor = extractor
        self._jina_api_key = jina_api_key or os.environ.get("JINA_API_KEY", "") or None
        self._firecrawl_enabled = firecrawl_enabled
        self._firecrawl_api_key = firecrawl_api_key
        self._firecrawl_api_url = firecrawl_api_url
        self._allowed_domains = allowed_domains
        self._blocked_domains = blocked_domains

    async def execute(
        self,
        url: str,
        prompt: str | None = None,
        extract_mode: str = "markdown",
        max_chars: int | None = None,
        **kwargs: Any,
    ) -> str:
        max_chars = max_chars or self.max_chars

        # URL scheme/format validation
        is_valid, error_msg = validate_http_url(url)
        if not is_valid:
            return json.dumps(
                {"error": f"URL validation failed: {error_msg}", "url": url},
                ensure_ascii=False,
            )

        # SSRF validation — block private/internal targets
        try:
            await async_validate_url_target(
                url,
                allowed_domains=self._allowed_domains,
                blocked_domains=self._blocked_domains,
            )
        except ValueError as e:
            return json.dumps({"error": f"URL blocked: {e}", "url": url}, ensure_ascii=False)

        try:
            # Choose extractor
            use_jina = self._extractor == "jina" or (
                self._extractor == "auto" and self._jina_api_key
            )
            if use_jina:
                try:
                    text, extractor_name = await self._extract_jina(url)
                except Exception:
                    text, extractor_name = await self._extract_readability(
                        url, extract_mode, **kwargs
                    )
            else:
                text, extractor_name = await self._extract_readability(url, extract_mode, **kwargs)

            # Firecrawl fallback for JS-rendered pages
            if self._firecrawl_enabled and len(text.strip()) < 100:
                try:
                    fc_text = await self._fallback_firecrawl(url)
                    if fc_text and len(fc_text.strip()) >= 100:
                        text = fc_text
                        extractor_name = "firecrawl"
                except Exception:
                    pass  # keep primary result

            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]

            result: dict[str, Any] = {
                "url": url,
                "extractor": extractor_name,
                "truncated": truncated,
                "length": len(text),
                "text": text,
            }
            if prompt:
                result["prompt"] = prompt

            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e), "url": url}, ensure_ascii=False)

    async def _extract_jina(self, url: str) -> tuple[str, str]:
        """Extract content via Jina Reader API. Returns (text, extractor_name)."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._jina_api_key:
            headers["Authorization"] = f"Bearer {self._jina_api_key}"
        resp = await asyncio.to_thread(
            httpx.get, f"https://r.jina.ai/{url}", headers=headers, timeout=30
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        title = data.get("title", "")
        content = data.get("content", "")
        text = f"# {title}\n\n{content}" if title else content
        return text, "jina"

    async def _fallback_firecrawl(self, url: str) -> str | None:
        """Scrape JS-rendered page via Firecrawl API."""
        if not self._firecrawl_api_key:
            return None
        resp = await asyncio.to_thread(
            httpx.post,
            f"{self._firecrawl_api_url.rstrip('/')}/scrape",
            json={"url": url, "formats": ["markdown"]},
            headers={
                "Authorization": f"Bearer {self._firecrawl_api_key}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("markdown", "")

    async def _extract_readability(
        self, url: str, extract_mode: str = "markdown", **kwargs: Any
    ) -> tuple[str, str]:
        """Extract content via HTTP fetch + readability. Returns (text, extractor_name)."""
        try:
            from readability import Document
        except ImportError as exc:
            raise RuntimeError(
                "readability extraction requires the web extra. "
                "Install it with: pip install 'theos-agent[web]'"
            ) from exc

        prepared_url, headers = self._credential_injector.prepare_url_and_headers(
            url,
            {"User-Agent": USER_AGENT},
        )
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=30.0,
        ) as client:
            r = await async_ssrf_safe_request(
                client,
                "GET",
                prepared_url,
                max_redirects=MAX_REDIRECTS,
                allowed_domains=self._allowed_domains,
                blocked_domains=self._blocked_domains,
                headers=headers,
            )
            r.raise_for_status()

        ctype = r.headers.get("content-type", "")

        if "application/json" in ctype:
            return json.dumps(r.json(), indent=2, ensure_ascii=False), "json"

        if "text/html" in ctype or r.text[:256].lower().startswith(("<!doctype", "<html")):
            doc = Document(r.text)
            mode = kwargs.get("extractMode", extract_mode)
            content = (
                self._to_markdown(doc.summary())
                if mode == "markdown"
                else strip_tags(doc.summary())
            )
            text = f"# {doc.title()}\n\n{content}" if doc.title() else content
            return text, "readability"

        return r.text, "raw"

    def _to_markdown(self, html_content: str) -> str:
        text = re.sub(
            r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
            lambda m: f"[{strip_tags(m[2])}]({m[1]})",
            html_content,
            flags=re.I,
        )
        text = re.sub(
            r"<h([1-6])[^>]*>([\s\S]*?)</h\1>",
            lambda m: f'\n{"#" * int(m[1])} {strip_tags(m[2])}\n',
            text,
            flags=re.I,
        )
        text = re.sub(
            r"<li[^>]*>([\s\S]*?)</li>",
            lambda m: f"\n- {strip_tags(m[1])}",
            text,
            flags=re.I,
        )
        text = re.sub(r"</(p|div|section|article)>", "\n\n", text, flags=re.I)
        text = re.sub(r"<(br|hr)\s*/?>", "\n", text, flags=re.I)
        return normalize_text(strip_tags(text))
