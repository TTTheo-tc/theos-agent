"""Shared helpers for media-oriented tools."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlparse


def is_http_url(source: str) -> bool:
    try:
        parsed = urlparse(source)
        return parsed.scheme in ("http", "https")
    except Exception:
        return False


def collect_sources(primary: str | None, additional: Sequence[str] | None) -> list[str]:
    sources: list[str] = []
    seen: set[str] = set()
    for raw in ([primary] if primary else []) + list(additional or []):
        source = raw.strip()
        if source and source not in seen:
            seen.add(source)
            sources.append(source)
    return sources
