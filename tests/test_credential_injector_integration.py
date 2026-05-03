"""Integration coverage for HTTP tool credential injection."""

from __future__ import annotations

import json
from dataclasses import dataclass

from src.agent.tools.mcp import _prepare_http_endpoint, _prepare_stdio_env
from src.agent.tools.web_http import HttpRequestTool
from src.agent.tools.web_search import WebSearchTool
from src.security.credential_injector import (
    CredentialInjector,
    CredentialMapping,
    CredentialRegistry,
    InjectionMethod,
    SecretResolver,
)


class _StaticResolver(SecretResolver):
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def resolve(self, name: str) -> str | None:
        return self._secrets.get(name)


def _injector() -> CredentialInjector:
    registry = CredentialRegistry()
    registry.add_mapping(
        CredentialMapping(
            secret_name="brave",
            method=InjectionMethod.HEADER,
            host_patterns=["api.search.brave.com"],
            header_name="X-Subscription-Token",
        )
    )
    return CredentialInjector(
        registry, _StaticResolver({"brave": "brave-secret", "mcp_token": "xyz"})
    )


@dataclass
class _FakeResponse:
    request_headers: dict[str, str]
    request_params: dict[str, str]

    status_code: int = 200
    is_redirect: bool = False
    headers: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "web": {
                "results": [
                    {
                        "title": "Result",
                        "url": "https://example.com/post",
                        "description": "Snippet",
                    }
                ]
            }
        }


class _FakeAsyncClient:
    def __init__(self, **kwargs) -> None:
        self.last_response: _FakeResponse | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, params: dict[str, str], headers: dict[str, str], timeout: float):
        self.last_response = _FakeResponse(headers, params)
        return self.last_response

    async def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        json: dict | None = None,
        content: str | None = None,
    ):
        self.last_response = _FakeResponse(headers, params or {})
        self.last_response.url = url  # type: ignore[attr-defined]
        self.last_response.text = '{"ok": true}' if json is not None else (content or "ok")
        return self.last_response


async def test_web_search_uses_credential_injector(monkeypatch) -> None:
    captured: dict[str, dict[str, str]] = {}

    class _CapturingClient(_FakeAsyncClient):
        async def get(
            self,
            url: str,
            params: dict[str, str],
            headers: dict[str, str],
            timeout: float,
        ):
            captured["headers"] = headers
            captured["params"] = params
            return await super().get(url, params, headers, timeout)

    monkeypatch.setattr("src.agent.tools.web_search.httpx.AsyncClient", _CapturingClient)
    tool = WebSearchTool(api_key="placeholder", provider="brave", credential_injector=_injector())
    result = await tool.execute(query="test query")

    assert "Result" in result
    assert captured["headers"]["X-Subscription-Token"] == "brave-secret"
    assert captured["params"]["q"] == "test query"


async def test_http_request_uses_credential_injector(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _CapturingClient(_FakeAsyncClient):
        async def request(
            self,
            method: str,
            url: str,
            headers: dict[str, str],
            params: dict[str, str] | None = None,
            json: dict | None = None,
            content: str | None = None,
        ):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params or {}
            captured["json"] = json
            captured["content"] = content
            return await super().request(method, url, headers, params, json, content)

    monkeypatch.setattr("src.agent.tools.web_http.httpx.AsyncClient", _CapturingClient)
    tool = HttpRequestTool(credential_injector=_injector())

    output = await tool.execute(
        url="https://api.search.brave.com/custom?token=secret://mcp_token",
        method="POST",
        headers={"Authorization": "Bearer secret://mcp_token"},
        params={"q": "hello"},
        json_body={"token": "secret://mcp_token"},
    )
    payload = json.loads(output)

    assert payload["status"] == 200
    assert captured["method"] == "POST"
    assert captured["headers"]["Authorization"] == "Bearer xyz"
    assert captured["headers"]["X-Subscription-Token"] == "brave-secret"
    assert captured["json"] == {"token": "xyz"}
    assert captured["url"] == "https://api.search.brave.com/custom?token=xyz"


async def test_http_request_blocks_leaky_body(monkeypatch) -> None:
    monkeypatch.setattr("src.agent.tools.web_http.httpx.AsyncClient", _FakeAsyncClient)
    tool = HttpRequestTool(credential_injector=_injector())

    output = await tool.execute(
        url="https://example.com/api",
        method="POST",
        body="api_key=sk-ant-leaked123",
    )
    payload = json.loads(output)

    assert "blocked by safety checks" in payload["error"]


def test_mcp_http_endpoint_resolves_secret_refs() -> None:
    prepared_url, prepared_headers = _prepare_http_endpoint(
        "https://mcp.example.com/stream?token=secret://mcp_token",
        {"Authorization": "Bearer secret://mcp_token"},
        _injector(),
    )

    assert prepared_url == "https://mcp.example.com/stream?token=xyz"
    assert prepared_headers["Authorization"] == "Bearer xyz"


def test_mcp_stdio_env_resolves_secret_refs(monkeypatch) -> None:
    monkeypatch.setenv("MCP_TOKEN", "xyz")
    prepared_env = _prepare_stdio_env({"API_KEY": "secret://mcp_token", "MODE": "stdio"})

    assert prepared_env == {"API_KEY": "xyz", "MODE": "stdio"}
