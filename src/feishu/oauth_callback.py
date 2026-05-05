"""Lightweight HTTP server for Feishu OAuth callback.

Runs inside the gateway process on ``config.gateway.port`` (default 18790).
When the user authorizes on their phone, Feishu redirects to
``http://<host>:<port>/feishu/oauth/callback?code=...`` and this handler
automatically exchanges the code for tokens.
"""

from __future__ import annotations

import asyncio
import html
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web
from filelock import FileLock
from loguru import logger

if TYPE_CHECKING:
    from src.bus.queue import MessageBus

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_STATE_FILE = "oauth_pending_states.json"
_STATE_TTL_S = 15 * 60


def build_callback_url(host: str, port: int) -> str:
    """Return the full OAuth callback URL for the gateway."""
    # For redirect_uri registration, prefer a routable Tailscale address when
    # the gateway is listening on all interfaces.
    if host in {"0.0.0.0", "::", "", None}:
        try:
            from src.ui.tailscale import detect_magicdns_name, detect_tailscale_ip

            host = detect_magicdns_name() or detect_tailscale_ip() or "localhost"
        except Exception:
            host = "localhost"
    return f"http://{host}:{port}/feishu/oauth/callback"


def _state_store_path(token_dir: str) -> Path:
    token_path = Path(token_dir).expanduser()
    return token_path / _STATE_FILE


def _load_state_store(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("OAuth callback state store is unreadable — resetting {}", path)
        return {}


def _write_state_store(path: Path, data: dict[str, dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _prune_states(data: dict[str, dict[str, object]], now: float) -> dict[str, dict[str, object]]:
    pruned: dict[str, dict[str, object]] = {}
    for state, item in data.items():
        expires_at = item.get("expires_at")
        if isinstance(expires_at, (int, float)) and expires_at > now:
            pruned[state] = item
    return pruned


def register_oauth_state(
    state: str,
    *,
    token_dir: str,
    redirect_uri: str,
    ttl_s: int = _STATE_TTL_S,
) -> None:
    """Persist a pending OAuth state so gateway and CLI processes can share it."""
    if not state:
        raise ValueError("state is required")

    path = _state_store_path(token_dir)
    lock = FileLock(str(path) + ".lock", timeout=5)
    now = time.time()
    with lock:
        store = _prune_states(_load_state_store(path), now)
        store[state] = {
            "redirect_uri": redirect_uri,
            "expires_at": now + ttl_s,
        }
        _write_state_store(path, store)


def consume_oauth_state(
    state: str,
    *,
    token_dir: str,
    redirect_uri: str,
) -> bool:
    """Validate and consume a pending OAuth state."""
    if not state:
        return False

    path = _state_store_path(token_dir)
    lock = FileLock(str(path) + ".lock", timeout=5)
    now = time.time()
    with lock:
        store = _prune_states(_load_state_store(path), now)
        item = store.pop(state, None)
        if item is None:
            _write_state_store(path, store)
            return False
        expected_uri = item.get("redirect_uri")
        _write_state_store(path, store)
        return expected_uri == redirect_uri


def is_local_callback_uri(uri: str) -> bool:
    """Return True when *uri* points at a loopback-only callback host."""
    from urllib.parse import urlparse

    host = (urlparse(uri).hostname or "").lower()
    return host in _LOCAL_HOSTS


# ---------------------------------------------------------------------------
# aiohttp application
# ---------------------------------------------------------------------------

_SUCCESS_HTML = """\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>授权成功</title>
<style>body{font-family:system-ui;display:flex;justify-content:center;
align-items:center;height:100vh;margin:0;background:#f0f9ff}
.card{background:#fff;border-radius:12px;padding:40px;text-align:center;
box-shadow:0 2px 12px rgba(0,0,0,.08)}
.ok{font-size:48px;margin-bottom:16px}
h2{color:#059669;margin:0 0 8px}
p{color:#6b7280;margin:0}</style></head>
<body><div class="card"><div class="ok">✅</div>
<h2>授权成功</h2><p>Token 已自动保存，可以关闭此页面。</p></div></body></html>
"""

_ERROR_HTML = """\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>授权失败</title>
<style>body{{font-family:system-ui;display:flex;justify-content:center;
align-items:center;height:100vh;margin:0;background:#fef2f2}}
.card{{background:#fff;border-radius:12px;padding:40px;text-align:center;
box-shadow:0 2px 12px rgba(0,0,0,.08)}}
.err{{font-size:48px;margin-bottom:16px}}
h2{{color:#dc2626;margin:0 0 8px}}
p{{color:#6b7280;margin:0}}</style></head>
<body><div class="card"><div class="err">❌</div>
<h2>授权失败</h2><p>{error}</p></div></body></html>
"""


def create_oauth_app(
    app_id: str,
    app_secret: str,
    token_dir: str,
    redirect_uri: str,
    bus: "MessageBus | None" = None,
    notify_chat_id: str | None = None,
) -> web.Application:
    """Create an aiohttp app with the ``/feishu/oauth/callback`` route.

    Parameters
    ----------
    app_id, app_secret:
        Feishu app credentials.
    token_dir:
        Where to save tokens.
    redirect_uri:
        The redirect_uri that was used when generating the auth URL.
        Must match exactly for the code exchange to succeed.
    bus:
        Optional message bus — if provided, a success notification is
        published to *notify_chat_id*.
    notify_chat_id:
        Feishu open_id or chat_id to notify on success.
    """
    app = web.Application()

    _csp = "default-src 'none'; style-src 'unsafe-inline'"

    def error_response(error: object, *, status: int = 400) -> web.Response:
        return web.Response(
            text=_ERROR_HTML.format(error=html.escape(str(error))),
            content_type="text/html",
            headers={"Content-Security-Policy": _csp},
            status=status,
        )

    async def notify_success(at_ttl: int, rt_ttl: int) -> None:
        if not bus or not notify_chat_id:
            return

        from src.bus.events import OutboundMessage

        await bus.publish_outbound(
            OutboundMessage(
                channel="feishu",
                chat_id=notify_chat_id,
                content=(
                    f"✅ 飞书授权成功！\n"
                    f"- access_token TTL: {at_ttl}s ({at_ttl // 60}min)\n"
                    f"- refresh_token TTL: {rt_ttl}s ({rt_ttl / 86400:.1f}d)"
                ),
            )
        )

    async def handle_callback(request: web.Request) -> web.Response:
        code = request.query.get("code")
        if not code:
            error = request.query.get("error", "no code parameter")
            logger.warning("OAuth callback missing code: {}", error)
            return error_response(error)

        state = request.query.get("state", "")
        if not state:
            logger.warning("OAuth callback missing state parameter")
            return error_response("缺少 state 参数，请重新生成授权链接。")
        if not consume_oauth_state(state, token_dir=token_dir, redirect_uri=redirect_uri):
            logger.warning("OAuth callback rejected invalid or expired state")
            return error_response("state 无效或已过期，请重新生成授权链接。")
        logger.debug("OAuth callback: CSRF state verified")

        # Exchange code for tokens (sync, run in executor)
        from src.feishu.remote_auth import exchange_auth_code

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: exchange_auth_code(
                code=code,
                app_id=app_id,
                app_secret=app_secret,
                redirect_uri=redirect_uri,
                token_dir=token_dir,
            ),
        )

        if result["ok"]:
            at_ttl = result["access_token_ttl"]
            rt_ttl = result["refresh_token_ttl"]
            logger.info(
                "OAuth callback: tokens saved. AT TTL={}s, RT TTL={}s ({:.1f}d)",
                at_ttl,
                rt_ttl,
                rt_ttl / 86400,
            )

            await notify_success(at_ttl, rt_ttl)

            return web.Response(
                text=_SUCCESS_HTML,
                content_type="text/html",
                headers={"Content-Security-Policy": _csp},
            )
        else:
            error = result["error"]
            logger.error("OAuth callback: exchange failed: {}", error)
            return error_response(error)

    # Health check
    async def handle_health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app.router.add_get("/feishu/oauth/callback", handle_callback)
    app.router.add_get("/health", handle_health)

    return app


# Module-level reference so FeishuAuthTool can register state tokens
_running_app: web.Application | None = None


def _get_running_app() -> web.Application | None:
    """Return the running OAuth app (set by ``start_oauth_server``)."""
    return _running_app


async def start_oauth_server(
    app: web.Application,
    host: str = "0.0.0.0",
    port: int = 18790,
) -> web.AppRunner:
    """Start the OAuth callback server. Returns the runner for cleanup."""
    global _running_app
    _running_app = app
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("OAuth callback server listening on http://{}:{}", host, port)
    return runner
