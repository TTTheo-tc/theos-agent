"""Tests for SSRF protection."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.agent.tools.web_ssrf import async_ssrf_safe_request, validate_url_target


def test_allows_public_url():
    validate_url_target("https://example.com")


def test_blocks_localhost():
    with pytest.raises(ValueError, match="private"):
        validate_url_target("http://127.0.0.1/admin")


def test_blocks_private_10():
    with pytest.raises(ValueError, match="private"):
        validate_url_target("http://10.0.0.1/internal")


def test_blocks_private_192():
    with pytest.raises(ValueError, match="private"):
        validate_url_target("http://192.168.1.1/router")


def test_blocks_metadata_endpoint():
    with pytest.raises(ValueError, match="private"):
        validate_url_target("http://169.254.169.254/latest/meta-data/")


def test_blocks_non_http_scheme():
    with pytest.raises(ValueError, match="http"):
        validate_url_target("ftp://example.com/file")


def test_allowed_domains_filter():
    validate_url_target("https://api.example.com", allowed_domains=["api.example.com"])
    with pytest.raises(ValueError, match="not in allowed"):
        validate_url_target("https://evil.com", allowed_domains=["api.example.com"])


def test_blocked_domains_precedence():
    with pytest.raises(ValueError, match="blocked"):
        validate_url_target("https://internal.corp", blocked_domains=["internal.corp"])


def test_wildcard_allows_all():
    validate_url_target("https://anything.com", allowed_domains=["*"])


async def test_async_ssrf_safe_request_blocks_private_redirect():
    """async_ssrf_safe_request raises on redirect to private IP."""
    redirect_resp = httpx.Response(
        302,
        headers={"location": "http://127.0.0.1/internal"},
        request=httpx.Request("GET", "https://example.com"),
    )
    final_resp = httpx.Response(
        200,
        request=httpx.Request("GET", "http://127.0.0.1/internal"),
    )

    call_count = 0

    async def mock_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return redirect_resp
        return final_resp

    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = mock_request

    with pytest.raises(ValueError, match="private"):
        await async_ssrf_safe_request(client, "GET", "https://example.com")


async def test_async_ssrf_safe_request_follows_public_redirect():
    """async_ssrf_safe_request follows redirect to public URL."""
    redirect_resp = httpx.Response(
        302,
        headers={"location": "https://example.com/page2"},
        request=httpx.Request("GET", "https://example.com"),
    )
    final_resp = httpx.Response(
        200,
        request=httpx.Request("GET", "https://example.com/page2"),
    )

    call_count = 0

    async def mock_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return redirect_resp
        return final_resp

    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = mock_request

    # Patch DNS lookup to avoid real resolution
    with patch("src.agent.tools.web_ssrf.validate_url_target"):
        resp = await async_ssrf_safe_request(client, "GET", "https://example.com")
        assert resp.status_code == 200


async def test_async_ssrf_safe_request_no_redirect():
    """async_ssrf_safe_request returns directly when no redirect."""
    final_resp = httpx.Response(
        200,
        request=httpx.Request("GET", "https://example.com"),
    )

    async def mock_request(method, url, **kwargs):
        return final_resp

    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = mock_request

    resp = await async_ssrf_safe_request(client, "GET", "https://example.com")
    assert resp.status_code == 200


async def test_async_ssrf_safe_request_strips_headers_on_cross_origin_redirect():
    """Cross-origin redirects must not forward caller-provided sensitive headers."""
    redirect_resp = httpx.Response(
        302,
        headers={"location": "https://other.example/path"},
        request=httpx.Request("GET", "https://example.com/start"),
    )
    final_resp = httpx.Response(
        200,
        request=httpx.Request("GET", "https://other.example/path"),
    )

    calls: list[tuple[str, str, dict]] = []

    async def mock_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if len(calls) == 1:
            return redirect_resp
        return final_resp

    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = mock_request

    with patch("src.agent.tools.web_ssrf.validate_url_target"):
        resp = await async_ssrf_safe_request(
            client,
            "GET",
            "https://example.com/start",
            headers={
                "Authorization": "Bearer secret",
                "X-Api-Key": "secret-2",
                "User-Agent": "TheOS",
                "Accept": "application/json",
            },
        )

    assert resp.status_code == 200
    assert len(calls) == 2
    assert calls[1][2]["headers"] == {
        "User-Agent": "TheOS",
        "Accept": "application/json",
    }


async def test_async_ssrf_safe_request_preserves_method_and_body_for_307():
    """307 redirects must preserve method and body."""
    redirect_resp = httpx.Response(
        307,
        headers={"location": "https://example.com/next"},
        request=httpx.Request("POST", "https://example.com/start"),
    )
    final_resp = httpx.Response(
        200,
        request=httpx.Request("POST", "https://example.com/next"),
    )

    calls: list[tuple[str, str, dict]] = []

    async def mock_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if len(calls) == 1:
            return redirect_resp
        return final_resp

    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = mock_request

    with patch("src.agent.tools.web_ssrf.validate_url_target"):
        resp = await async_ssrf_safe_request(
            client,
            "POST",
            "https://example.com/start",
            json={"hello": "world"},
            headers={"User-Agent": "TheOS"},
        )

    assert resp.status_code == 200
    assert len(calls) == 2
    assert calls[1][0] == "POST"
    assert calls[1][2]["json"] == {"hello": "world"}
