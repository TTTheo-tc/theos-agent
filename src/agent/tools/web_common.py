"""Shared helpers for web tools."""

from __future__ import annotations

import html
import re
from urllib.parse import urlparse

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5


def strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def normalize_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def validate_http_url(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"Only http/https allowed, got '{parsed.scheme or 'none'}'"
        if not parsed.netloc:
            return False, "Missing domain"
        return True, ""
    except Exception as exc:
        return False, str(exc)
