"""SSRF protection for web tools — validates URLs against private IP blocklist."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Mapping
from urllib.parse import urlparse

import httpx

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_ip(addr: str) -> bool:
    """Check if an IP address is in a blocked private network."""
    try:
        ip = ipaddress.ip_address(addr)
        return any(ip in net for net in _BLOCKED_NETWORKS)
    except ValueError:
        return False


def validate_url_target(
    url: str,
    *,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> None:
    """Validate a URL for SSRF. Raises ValueError on violation.

    Checks: scheme (http/https only), blocked domains, allowed domains,
    DNS resolution against private IP blocklist.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http/https allowed, got {parsed.scheme!r}")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"No hostname in URL: {url}")

    # Blocked domains take precedence
    if blocked_domains:
        for bd in blocked_domains:
            if hostname == bd or hostname.endswith(f".{bd}"):
                raise ValueError(f"Host {hostname!r} is blocked")

    # Allowed domains check (skip if ["*"] or None)
    if allowed_domains and allowed_domains != ["*"]:
        matched = any(hostname == ad or hostname.endswith(f".{ad}") for ad in allowed_domains)
        if not matched:
            raise ValueError(f"Host {hostname!r} not in allowed domains")

    # DNS resolution — check all resolved IPs
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for info in infos:
            addr = info[4][0]
            if _is_private_ip(addr):
                raise ValueError(f"Host {hostname!r} resolves to private address {addr}")
    except socket.gaierror:
        pass  # DNS failure — let the HTTP client handle it


async def async_validate_url_target(
    url: str,
    *,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> None:
    """Async version of validate_url_target — runs DNS lookup in a thread."""
    await asyncio.to_thread(
        validate_url_target,
        url,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
    )


def _origin_key(url: str | httpx.URL) -> tuple[str, str, int | None]:
    parsed = httpx.URL(str(url))
    return parsed.scheme, parsed.host or "", parsed.port


def _cross_origin_redirect(source_url: str | httpx.URL, target_url: str | httpx.URL) -> bool:
    return _origin_key(source_url) != _origin_key(target_url)


def _sanitize_redirect_headers(
    headers: Mapping[str, str] | httpx.Headers | None,
    *,
    cross_origin: bool,
) -> dict[str, str] | httpx.Headers | None:
    if headers is None:
        return None

    if not cross_origin:
        return dict(headers.items()) if hasattr(headers, "items") else dict(headers)

    safe_names = {"user-agent", "accept", "accept-encoding", "accept-language", "range"}
    return {key: value for key, value in dict(headers.items()).items() if key.lower() in safe_names}


def _redirect_method_and_body(
    status_code: int,
    method: str,
    request_kwargs: dict,
) -> tuple[str, dict]:
    next_method = method
    next_kwargs = dict(request_kwargs)

    if status_code == 303 and method != "HEAD":
        next_method = "GET"
    elif status_code in {301, 302} and method not in {"GET", "HEAD"}:
        next_method = "GET"

    if next_method != method:
        next_kwargs.pop("json", None)
        next_kwargs.pop("content", None)

    return next_method, next_kwargs


async def async_ssrf_safe_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_redirects: int = 5,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
    **kwargs,
) -> httpx.Response:
    """Send an HTTP request with async SSRF validation on every redirect.

    Unlike the old sync event-hook approach, this manually follows redirects
    so that each target URL gets a non-blocking DNS check via
    ``async_validate_url_target``.

    The caller must create the ``AsyncClient`` with ``follow_redirects=False``.
    """
    request_kwargs = dict(kwargs)
    for _ in range(max_redirects + 1):
        resp = await client.request(method, url, **request_kwargs)
        if not resp.is_redirect:
            return resp
        location = resp.headers.get("location")
        if not location:
            return resp
        # Resolve relative redirects against the request URL
        next_url = str(resp.url.join(location))
        await async_validate_url_target(
            next_url,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
        )
        request_kwargs["headers"] = _sanitize_redirect_headers(
            request_kwargs.get("headers"),
            cross_origin=_cross_origin_redirect(resp.url, next_url),
        )
        method, request_kwargs = _redirect_method_and_body(resp.status_code, method, request_kwargs)
        url = next_url
    raise httpx.TooManyRedirects(f"Exceeded {max_redirects} redirects", request=resp.request)
