"""HttpRequestTool — general-purpose HTTP client with SSRF protection and safety checks."""

from __future__ import annotations

import json
from typing import Any

import httpx

from src.agent.tools.base import Tool
from src.agent.tools.web_common import MAX_REDIRECTS, USER_AGENT, validate_http_url
from src.agent.tools.web_ssrf import async_ssrf_safe_request, async_validate_url_target
from src.safety.layer import SafetyLayer
from src.security.credential_injector import (
    CredentialInjector,
    EncryptedSecretResolver,
    build_default_registry,
)


def _json_body_preview(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _resolve_request_data(value: Any, injector: CredentialInjector) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_request_data(item, injector) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_request_data(item, injector) for item in value]
    if isinstance(value, tuple):
        return tuple(_resolve_request_data(item, injector) for item in value)
    if isinstance(value, str):
        return injector.resolve_value(value)
    return value


class HttpRequestTool(Tool):
    """Make a direct HTTP request with secret injection and safety checks."""

    name = "http_request"
    description = (
        "Make an HTTP request to a specific URL. Supports headers, query params, "
        "JSON or text bodies, secret injection, and sanitized text responses."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Fully-formed http/https URL"},
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                "description": "HTTP method (default: GET)",
            },
            "headers": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Optional request headers",
            },
            "params": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Optional query parameters",
            },
            "json_body": {
                "type": "object",
                "description": "Optional JSON request body",
            },
            "body": {
                "type": "string",
                "description": "Optional plain-text request body",
            },
            "timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "description": "Request timeout in seconds (default: 30)",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 100,
                "description": "Maximum response characters to return",
            },
        },
        "required": ["url"],
    }

    def __init__(
        self,
        max_chars: int = 12000,
        credential_injector: CredentialInjector | None = None,
        safety: SafetyLayer | None = None,
    ) -> None:
        self.max_chars = max_chars
        self._credential_injector = credential_injector or CredentialInjector(
            build_default_registry(),
            EncryptedSecretResolver(),
        )
        self._safety = safety or SafetyLayer()

    async def execute(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json_body: Any | None = None,
        body: str | None = None,
        timeout_seconds: float = 30.0,
        max_chars: int | None = None,
        **kwargs: Any,
    ) -> str:
        is_valid, error_msg = validate_http_url(url)
        if not is_valid:
            return json.dumps(
                {"error": f"URL validation failed: {error_msg}", "url": url}, ensure_ascii=False
            )

        # SSRF: block requests to private/internal IPs
        try:
            await async_validate_url_target(url)
        except ValueError as e:
            return json.dumps({"error": f"URL blocked: {e}"}, ensure_ascii=False)

        method = (method or "GET").upper()
        max_chars = max_chars or self.max_chars
        request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
        request_params = _resolve_request_data(params or {}, self._credential_injector)
        request_json = (
            _resolve_request_data(json_body, self._credential_injector)
            if json_body is not None
            else None
        )
        request_body = (
            _resolve_request_data(body, self._credential_injector) if body is not None else None
        )

        if request_json is not None:
            body_check = self._safety.scan_http_body(_json_body_preview(request_json))
            if not body_check.clean:
                return json.dumps(
                    {
                        "error": "HTTP request body blocked by safety checks.",
                        "url": url,
                        "method": method,
                    },
                    ensure_ascii=False,
                )
        if request_body:
            body_check = self._safety.scan_http_body(request_body)
            if not body_check.clean:
                return json.dumps(
                    {
                        "error": "HTTP request body blocked by safety checks.",
                        "url": url,
                        "method": method,
                    },
                    ensure_ascii=False,
                )

        prepared_url, prepared_headers = self._credential_injector.prepare_url_and_headers(
            url,
            request_headers,
        )
        prepared_headers, prepared_params = self._credential_injector.prepare_request(
            prepared_url,
            prepared_headers,
            request_params,
        )

        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=float(timeout_seconds),
            ) as client:
                response = await async_ssrf_safe_request(
                    client,
                    method,
                    prepared_url,
                    max_redirects=MAX_REDIRECTS,
                    headers=prepared_headers,
                    params=prepared_params,
                    json=request_json,
                    content=request_body,
                )
                response.raise_for_status()

            ctype = response.headers.get("content-type", "")
            if "application/json" in ctype:
                raw_text = json.dumps(response.json(), ensure_ascii=False, indent=2)
            else:
                raw_text = response.text

            safe_text = self._safety.scan_external_content(raw_text)
            truncated = len(safe_text) > max_chars
            if truncated:
                safe_text = safe_text[:max_chars]

            return json.dumps(
                {
                    "url": url,
                    "finalUrl": str(response.url),
                    "method": method,
                    "status": response.status_code,
                    "contentType": ctype,
                    "truncated": truncated,
                    "length": len(safe_text),
                    "text": safe_text,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps(
                {"error": str(e), "url": url, "method": method},
                ensure_ascii=False,
            )
