from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.feishu.token_refresh import _send_reauth_expired, _send_reauth_warning


class _Bus:
    def __init__(self) -> None:
        self.messages = []

    async def publish_outbound(self, msg) -> None:
        self.messages.append(msg)


def _config(owner_ids: list[str]):
    return SimpleNamespace(channels=SimpleNamespace(owner_ids=owner_ids))


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
