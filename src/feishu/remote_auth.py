"""Remote Feishu OAuth authorization flow.

Allows re-authorization via Feishu chat when the user is away from their
computer.  Two-step flow:

1. ``generate_auth_url()`` — build the OAuth URL and send it to the user.
2. ``exchange_auth_code()`` — user sends back the code; exchange for tokens.
"""

from __future__ import annotations

import secrets
import time
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
from loguru import logger

# The scopes needed for feishu tools (same as feishu-auth command)
FEISHU_OAUTH_SCOPES = " ".join(
    [
        "offline_access",
        "wiki:wiki",
        "wiki:wiki:readonly",
        "wiki:node:read",
        "wiki:node:create",
        "wiki:node:update",
        "wiki:space:read",
        "docx:document",
        "docx:document:readonly",
        "docs:document.content:read",
        "docs:document.comment:read",
        "docx:document.block:convert",
        "drive:drive:readonly",
        "drive:file:download",
        "search:docs:read",
        "im:message",
        "contact:user.base:readonly",
        "calendar:calendar:readonly",
        "calendar:calendar.event:read",
    ]
)

# Default redirect URI — matches the gateway OAuth callback route.
# Must be registered in the Feishu developer console.
DEFAULT_REDIRECT_URI = "http://localhost:18790/feishu/oauth/callback"


def get_gateway_redirect_uri() -> str | None:
    """Return a remotely reachable gateway OAuth callback URL, else ``None``.

    Resolution order:
    1. ``channels.feishu.oauth_redirect_uri`` (explicit config)
    2. Derive from ``gateway.host:gateway.port`` (prefer Tailscale for 0.0.0.0)
    3. Return ``None`` when only localhost is available
    """
    try:
        from src.config.loader import load_config
        from src.feishu.oauth_callback import build_callback_url, is_local_callback_uri

        config = load_config()
        fs = config.channels.feishu
        if fs.oauth_redirect_uri:
            return None if is_local_callback_uri(fs.oauth_redirect_uri) else fs.oauth_redirect_uri
        gw = config.gateway
        callback_uri = build_callback_url(gw.host, gw.port)
        return None if is_local_callback_uri(callback_uri) else callback_uri
    except Exception:
        pass
    return None


def callback_health_url(redirect_uri: str) -> str:
    """Return the health endpoint for a callback URL."""
    parsed = urlparse(redirect_uri)
    return urlunparse(parsed._replace(path="/health", query="", fragment=""))


def is_callback_server_alive(redirect_uri: str, timeout_s: float = 2.0) -> bool:
    """Return True when the gateway OAuth callback server responds to /health."""
    try:
        resp = httpx.get(callback_health_url(redirect_uri), timeout=timeout_s)
        if resp.status_code != 200:
            return False
        data = resp.json()
        return isinstance(data, dict) and data.get("status") == "ok"
    except Exception:
        return False


def generate_auth_url(
    app_id: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    scope: str = FEISHU_OAUTH_SCOPES,
) -> tuple[str, str]:
    """Generate the Feishu OAuth authorization URL.

    Returns ``(url, state)`` — the caller must verify ``state`` when the
    callback arrives to prevent CSRF attacks.
    """
    state = secrets.token_urlsafe(32)
    query = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    if scope:
        query["scope"] = scope

    url = "https://accounts.feishu.cn/open-apis/authen/v1/authorize?" + urlencode(query)
    return url, state


def exchange_auth_code(
    code: str,
    app_id: str,
    app_secret: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    token_dir: str = "~/.theos/feishu_tokens",
) -> dict:
    """Exchange an authorization code for access + refresh tokens.

    Returns ``{"ok": True, "access_token_ttl": ..., "refresh_token_ttl": ...}``
    on success, or ``{"ok": False, "error": "..."}`` on failure.
    """
    from src.feishu.token import save_access_token, save_refresh_token

    token_url = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
    payload = {
        "grant_type": "authorization_code",
        "client_id": app_id,
        "client_secret": app_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }

    try:
        res = httpx.post(token_url, data=payload, timeout=60.0)
    except Exception as e:
        return {"ok": False, "error": f"Network error: {e}"}

    if res.status_code != 200:
        return {"ok": False, "error": f"HTTP {res.status_code}: {res.text[:200]}"}

    resp = res.json()
    if resp.get("code") is not None and resp.get("code") != 0:
        return {"ok": False, "error": f"API error: {resp}"}

    data = resp.get("data") or resp
    epoch = int(time.time())

    access_token = data.get("access_token")
    if not access_token:
        return {"ok": False, "error": f"No access_token in response: {resp}"}

    save_access_token(
        access_token,
        epoch + data.get("expires_in", 7200),
        token_dir=token_dir,
    )

    rt_ttl = data.get("refresh_token_expires_in", 2592000)
    if "refresh_token" in data:
        save_refresh_token(
            data["refresh_token"],
            epoch + rt_ttl,
            token_dir=token_dir,
        )

    at_ttl = data.get("expires_in", 7200)
    logger.info(
        "Remote auth: tokens saved. access_token TTL={}s, refresh_token TTL={}s ({:.1f}d)",
        at_ttl,
        rt_ttl,
        rt_ttl / 86400,
    )
    return {
        "ok": True,
        "access_token_ttl": at_ttl,
        "refresh_token_ttl": rt_ttl,
    }
