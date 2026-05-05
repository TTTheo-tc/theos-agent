"""Proxy environment helpers shared by CLI and config loading."""

from __future__ import annotations

import os

HTTP_PROXY_ENV_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")
ALL_PROXY_ENV_KEYS = ("ALL_PROXY", "all_proxy")
NO_PROXY_ENV_KEYS = ("NO_PROXY", "no_proxy")
PROXY_ENV_KEYS = HTTP_PROXY_ENV_KEYS + ALL_PROXY_ENV_KEYS + NO_PROXY_ENV_KEYS


def is_socks_proxy(value: str) -> bool:
    """Return True when *value* uses a SOCKS proxy scheme."""
    return value.lower().startswith(
        ("socks://", "socks4://", "socks4a://", "socks5://", "socks5h://")
    )


def first_supported_proxy_env(
    keys: tuple[str, ...] = HTTP_PROXY_ENV_KEYS + ALL_PROXY_ENV_KEYS,
) -> str | None:
    """Return the first configured non-SOCKS proxy from the selected env keys."""
    for key in keys:
        value = os.environ.get(key)
        if value and not is_socks_proxy(value):
            return value
    return None


def has_supported_http_proxy_env() -> bool:
    """Return True when HTTP proxy env has a non-SOCKS value."""
    return first_supported_proxy_env(HTTP_PROXY_ENV_KEYS) is not None


def apply_http_proxy_env(proxy: str | None) -> bool:
    """Expose an HTTP proxy through common env vars, skipping SOCKS values."""
    if not proxy or is_socks_proxy(proxy):
        return False
    for key in HTTP_PROXY_ENV_KEYS:
        os.environ.setdefault(key, proxy)
    return True
