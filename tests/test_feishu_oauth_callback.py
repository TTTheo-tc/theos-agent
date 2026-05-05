from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.feishu.oauth_callback import (
    build_callback_url,
    consume_oauth_state,
    create_oauth_app,
    register_oauth_state,
)


class _Bus:
    def __init__(self) -> None:
        self.messages = []

    async def publish_outbound(self, msg) -> None:
        self.messages.append(msg)


def test_build_callback_url_prefers_tailscale_for_bind_all():
    with patch("src.ui.tailscale.detect_tailscale_ip", return_value="100.68.1.2"):
        assert (
            build_callback_url("0.0.0.0", 18790) == "http://100.68.1.2:18790/feishu/oauth/callback"
        )


def test_build_callback_url_falls_back_to_localhost():
    with patch("src.ui.tailscale.detect_tailscale_ip", return_value=None):
        assert (
            build_callback_url("0.0.0.0", 18790) == "http://localhost:18790/feishu/oauth/callback"
        )


def test_register_and_consume_oauth_state_requires_matching_redirect_uri(tmp_path: Path):
    token_dir = str(tmp_path)
    state = "state-123"
    redirect_uri = "http://100.68.1.2:18790/feishu/oauth/callback"

    register_oauth_state(state, token_dir=token_dir, redirect_uri=redirect_uri)

    assert consume_oauth_state(state, token_dir=token_dir, redirect_uri=redirect_uri) is True
    assert consume_oauth_state(state, token_dir=token_dir, redirect_uri=redirect_uri) is False


def test_consume_oauth_state_rejects_wrong_redirect_uri(tmp_path: Path):
    token_dir = str(tmp_path)
    state = "state-456"
    register_oauth_state(
        state,
        token_dir=token_dir,
        redirect_uri="http://100.68.1.2:18790/feishu/oauth/callback",
    )

    assert (
        consume_oauth_state(
            state,
            token_dir=token_dir,
            redirect_uri="http://localhost:18790/feishu/oauth/callback",
        )
        is False
    )


@pytest.mark.asyncio
async def test_callback_rejects_missing_state(tmp_path: Path):
    app = create_oauth_app(
        app_id="app",
        app_secret="secret",
        token_dir=str(tmp_path),
        redirect_uri="http://100.68.1.2:18790/feishu/oauth/callback",
    )
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.get("/feishu/oauth/callback?code=abc")
        body = await resp.text()
        assert resp.status == 400
        assert "state" in body
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_callback_rejects_missing_code(tmp_path: Path):
    app = create_oauth_app(
        app_id="app",
        app_secret="secret",
        token_dir=str(tmp_path),
        redirect_uri="http://100.68.1.2:18790/feishu/oauth/callback",
    )
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.get("/feishu/oauth/callback?state=abc")
        body = await resp.text()
        assert resp.status == 400
        assert "no code parameter" in body
        assert resp.headers["Content-Security-Policy"] == "default-src 'none'; style-src 'unsafe-inline'"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_callback_rejects_invalid_state(tmp_path: Path):
    app = create_oauth_app(
        app_id="app",
        app_secret="secret",
        token_dir=str(tmp_path),
        redirect_uri="http://100.68.1.2:18790/feishu/oauth/callback",
    )
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.get("/feishu/oauth/callback?code=abc&state=bad-state")
        body = await resp.text()
        assert resp.status == 400
        assert "state 无效或已过期" in body
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_callback_accepts_registered_state(tmp_path: Path):
    redirect_uri = "http://100.68.1.2:18790/feishu/oauth/callback"
    register_oauth_state("good-state", token_dir=str(tmp_path), redirect_uri=redirect_uri)
    app = create_oauth_app(
        app_id="app",
        app_secret="secret",
        token_dir=str(tmp_path),
        redirect_uri=redirect_uri,
    )
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        with patch(
            "src.feishu.remote_auth.exchange_auth_code",
            return_value={"ok": True, "access_token_ttl": 7200, "refresh_token_ttl": 2592000},
        ):
            resp = await client.get("/feishu/oauth/callback?code=abc&state=good-state")
        body = await resp.text()
        assert resp.status == 200
        assert "授权成功" in body
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_callback_notifies_success_when_bus_configured(tmp_path: Path):
    redirect_uri = "http://100.68.1.2:18790/feishu/oauth/callback"
    register_oauth_state("notify-state", token_dir=str(tmp_path), redirect_uri=redirect_uri)
    bus = _Bus()
    app = create_oauth_app(
        app_id="app",
        app_secret="secret",
        token_dir=str(tmp_path),
        redirect_uri=redirect_uri,
        bus=bus,
        notify_chat_id="ou_owner",
    )
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        with patch(
            "src.feishu.remote_auth.exchange_auth_code",
            return_value={"ok": True, "access_token_ttl": 7200, "refresh_token_ttl": 2592000},
        ):
            resp = await client.get("/feishu/oauth/callback?code=abc&state=notify-state")
        assert resp.status == 200
        assert len(bus.messages) == 1
        msg = bus.messages[0]
        assert msg.channel == "feishu"
        assert msg.chat_id == "ou_owner"
        assert "飞书授权成功" in msg.content
    finally:
        await client.close()
