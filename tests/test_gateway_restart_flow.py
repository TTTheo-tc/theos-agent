from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.loop import AgentLoop
from src.bus.events import InboundMessage, OutboundMessage
from src.bus.queue import MessageBus
from src.channels.base import BaseChannel
from src.channels.manager import ChannelManager
from src.config.schema import Config
from src.session.manager import Session


def _make_test_config(tmp_path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    return cfg


def _make_provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return provider


def _make_loop(tmp_path) -> AgentLoop:
    config = _make_test_config(tmp_path)
    with patch.object(AgentLoop, "_register_default_tools", return_value=None):
        return AgentLoop(bus=MessageBus(), provider=_make_provider(), config=config)


class _StubChannel(BaseChannel):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send(self, msg: OutboundMessage) -> None: ...


@pytest.mark.asyncio
async def test_restart_slash_returns_restart_after_send_marker(tmp_path):
    loop = _make_loop(tmp_path)
    try:
        session = Session(key="feishu:chat")
        msg = InboundMessage(
            channel="feishu",
            sender_id="owner",
            chat_id="chat",
            content="/restart",
            sender_is_owner=True,
        )

        response = await loop._handle_slash_commands(msg, session, session.key)

        assert response is not None
        assert response.metadata["_restart_after_send"] is True
        assert "重启" in response.content
    finally:
        await loop.close()


@pytest.mark.asyncio
async def test_paused_channel_drops_inbound_messages():
    bus = MessageBus()
    channel = _StubChannel(config=SimpleNamespace(allow_from=[]), bus=bus, owner_ids=["owner"])
    channel.pause_inbound()

    published = await channel._handle_message("owner", "chat", "hello")

    assert published is False
    assert bus.inbound_size == 0


def _make_manager(bus: MessageBus) -> ChannelManager:
    manager = ChannelManager.__new__(ChannelManager)
    manager.config = SimpleNamespace(
        channels=SimpleNamespace(send_tool_hints=True, send_progress=True)
    )
    manager.bus = bus
    manager.dashboard = None
    manager.channels = {}
    manager._dispatch_task = None
    manager._restart_cb = None
    manager._inflight_sends = 0
    return manager


@pytest.mark.asyncio
async def test_dispatch_outbound_triggers_restart_only_after_send():
    bus = MessageBus()
    manager = _make_manager(bus)
    sent = asyncio.Event()
    restart_calls: list[str] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.set()

    channel = SimpleNamespace(send=AsyncMock(side_effect=_send))
    manager.channels = {"feishu": channel}
    manager.set_restart_callback(lambda: restart_calls.append("restart"))

    dispatch_task = asyncio.create_task(manager._dispatch_outbound())
    await bus.publish_outbound(
        OutboundMessage(
            channel="feishu",
            chat_id="chat",
            content="restarting",
            metadata={"_restart_after_send": True},
        )
    )

    await asyncio.wait_for(sent.wait(), timeout=1.0)
    await asyncio.sleep(0)

    dispatch_task.cancel()
    await dispatch_task

    assert restart_calls == ["restart"]


@pytest.mark.asyncio
async def test_wait_outbound_idle_waits_for_inflight_send():
    bus = MessageBus()
    manager = _make_manager(bus)
    send_started = asyncio.Event()
    release_send = asyncio.Event()

    async def _send(msg: OutboundMessage) -> None:
        send_started.set()
        await release_send.wait()

    channel = SimpleNamespace(send=AsyncMock(side_effect=_send))
    manager.channels = {"feishu": channel}

    dispatch_task = asyncio.create_task(manager._dispatch_outbound())
    await bus.publish_outbound(OutboundMessage(channel="feishu", chat_id="chat", content="x"))

    await asyncio.wait_for(send_started.wait(), timeout=1.0)
    assert await manager.wait_outbound_idle(timeout=0.05) is False

    release_send.set()
    assert await manager.wait_outbound_idle(timeout=1.0) is True

    dispatch_task.cancel()
    await dispatch_task
