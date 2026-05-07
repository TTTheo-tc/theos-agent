from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.feishu.token import save_refresh_token
from src.feishu.token_refresh import (
    _send_reauth_expired,
    _send_reauth_warning,
    refresh_feishu_token,
)


class _Bus:
    def __init__(self) -> None:
        self.messages = []

    async def publish_outbound(self, msg) -> None:
        self.messages.append(msg)


def _config(owner_ids: list[str]):
    return SimpleNamespace(channels=SimpleNamespace(owner_ids=owner_ids))


def test_refresh_feishu_token_persists_new_token_pair(tmp_path):
    save_refresh_token("old-refresh", int(time.time()) + 3600, token_dir=str(tmp_path))

    with patch(
        "src.feishu.token.refresh_token_from_api",
        return_value={
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 7200,
            "refresh_token_expires_in": 2592000,
        },
    ) as refresh:
        result = refresh_feishu_token("app", "secret", token_dir=str(tmp_path))

    assert result == {
        "ok": True,
        "access_token_ttl": 7200,
        "refresh_token_ttl": 2592000,
    }
    refresh.assert_called_once_with("old-refresh", app_id="app", app_secret="secret")
    access_data = json.loads((tmp_path / "access_token.json").read_text(encoding="utf-8"))
    refresh_data = json.loads((tmp_path / "refresh_token.json").read_text(encoding="utf-8"))
    assert access_data["access_token"] == "new-access"
    assert refresh_data["refresh_token"] == "new-refresh"


def test_refresh_feishu_token_rejects_incomplete_refresh_response(tmp_path):
    save_refresh_token("old-refresh", int(time.time()) + 3600, token_dir=str(tmp_path))

    with patch(
        "src.feishu.token.refresh_token_from_api",
        return_value={"access_token": "new-access"},
    ):
        result = refresh_feishu_token("app", "secret", token_dir=str(tmp_path))

    assert result["ok"] is False
    assert "refresh_token" in result["error"]


def test_refresh_feishu_token_requires_refresh_ttls(tmp_path):
    save_refresh_token("old-refresh", int(time.time()) + 3600, token_dir=str(tmp_path))

    with patch(
        "src.feishu.token.refresh_token_from_api",
        return_value={"access_token": "new-access", "refresh_token": "new-refresh"},
    ):
        result = refresh_feishu_token("app", "secret", token_dir=str(tmp_path))

    assert result["ok"] is False
    assert "expires_in" in result["error"]
    assert "refresh_token_expires_in" in result["error"]


@pytest.mark.asyncio
async def test_send_reauth_warning_publishes_owner_message():
    bus = _Bus()

    _send_reauth_warning(_config(["ou_owner"]), bus, 3.25)
    await asyncio.sleep(0)

    assert len(bus.messages) == 1
    msg = bus.messages[0]
    assert msg.channel == "feishu"
    assert msg.chat_id == "ou_owner"
    assert "3.2 天后过期" in msg.content


@pytest.mark.asyncio
async def test_send_reauth_expired_publishes_owner_message():
    bus = _Bus()

    _send_reauth_expired(_config(["ou_owner"]), bus)
    await asyncio.sleep(0)

    assert len(bus.messages) == 1
    msg = bus.messages[0]
    assert msg.channel == "feishu"
    assert msg.chat_id == "ou_owner"
    assert "refresh_token 已过期" in msg.content


@pytest.mark.asyncio
async def test_reauth_notifications_skip_missing_owner():
    bus = _Bus()

    _send_reauth_warning(_config([]), bus, 1.0)
    _send_reauth_expired(_config([]), bus)
    await asyncio.sleep(0)

    assert bus.messages == []
