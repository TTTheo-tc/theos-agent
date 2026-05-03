"""Tests for ChannelManager outbound failure observability."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bus.events import OutboundMessage
from src.bus.queue import MessageBus
from src.channels.manager import ChannelManager


def _make_manager_with_failing_channel(bus: MessageBus) -> tuple[ChannelManager, MagicMock]:
    """Create a ChannelManager with a mock channel that always fails on send."""
    config = MagicMock()
    config.channels.send_progress = True
    config.channels.send_tool_hints = False

    manager = ChannelManager.__new__(ChannelManager)
    manager.config = config
    manager.bus = bus
    manager.dashboard = None
    manager._dispatch_task = None
    manager._restart_cb = None
    manager._inflight_sends = 0

    mock_channel = MagicMock()
    mock_channel.send = AsyncMock(side_effect=RuntimeError("network error"))
    manager.channels = {"test_channel": mock_channel}

    return manager, mock_channel


def _make_manager_with_channel(
    bus: MessageBus,
    *,
    supports_internal_progress: bool = True,
    send_progress: bool = True,
    send_tool_hints: bool = False,
) -> tuple[ChannelManager, MagicMock]:
    """Create a ChannelManager with a mock channel for outbound routing tests."""
    config = MagicMock()
    config.channels.send_progress = send_progress
    config.channels.send_tool_hints = send_tool_hints

    manager = ChannelManager.__new__(ChannelManager)
    manager.config = config
    manager.bus = bus
    manager.dashboard = None
    manager._dispatch_task = None
    manager._restart_cb = None
    manager._inflight_sends = 0

    mock_channel = MagicMock()
    mock_channel.send = AsyncMock()
    mock_channel.supports_internal_progress = supports_internal_progress
    mock_channel.transform_progress_message = MagicMock(return_value=None)
    manager.channels = {"test_channel": mock_channel}

    return manager, mock_channel


@pytest.mark.asyncio
async def test_send_failure_logs_structured_context() -> None:
    """Failed send should log routing metadata without leaking content."""
    bus = MessageBus()
    manager, mock_channel = _make_manager_with_failing_channel(bus)

    msg = OutboundMessage(
        channel="test_channel",
        chat_id="chat_42",
        content="secret-token-12345",
    )
    await bus.publish_outbound(msg)

    warning_logger = MagicMock()
    with patch("src.channels.manager.logger.opt", return_value=warning_logger) as mock_opt:
        dispatch_task = asyncio.create_task(manager._dispatch_outbound())
        await asyncio.sleep(0.05)
        dispatch_task.cancel()
        try:
            await dispatch_task
        except asyncio.CancelledError:
            pass

    mock_opt.assert_called_once_with(exception=True)
    warning_logger.warning.assert_called_once()
    fmt, channel, chat_id, has_media, content_chars = warning_logger.warning.call_args[0]
    assert fmt == "Outbound send failed | channel={} chat_id={} has_media={} content_chars={}"
    assert channel == "test_channel"
    assert chat_id == "chat_42"
    assert has_media is False
    assert content_chars == len(msg.content)
    assert "secret-token-12345" not in str(warning_logger.warning.call_args)

    mock_channel.send.assert_awaited_once_with(msg)


@pytest.mark.asyncio
async def test_dispatcher_does_not_retry_on_failure() -> None:
    """After a send failure, the dispatcher should move on, not retry."""
    bus = MessageBus()
    manager, mock_channel = _make_manager_with_failing_channel(bus)

    msg = OutboundMessage(
        channel="test_channel",
        chat_id="chat_42",
        content="test",
    )
    await bus.publish_outbound(msg)

    dispatch_task = asyncio.create_task(manager._dispatch_outbound())
    await asyncio.sleep(0.05)
    dispatch_task.cancel()
    try:
        await dispatch_task
    except asyncio.CancelledError:
        pass

    # Should only have been called once — no retry
    assert mock_channel.send.await_count == 1


@pytest.mark.asyncio
async def test_progress_suppressed_when_channel_disables_internal_progress() -> None:
    """Channels can drop internal progress entirely."""
    bus = MessageBus()
    manager, mock_channel = _make_manager_with_channel(bus, supports_internal_progress=False)
    mock_channel.transform_progress_message.return_value = None

    msg = OutboundMessage(
        channel="test_channel",
        chat_id="chat_42",
        content='pdf("https://arxiv.org/pdf/2207.05844")',
        metadata={"_progress": True, "_tool_hint": True},
    )
    await bus.publish_outbound(msg)

    dispatch_task = asyncio.create_task(manager._dispatch_outbound())
    await asyncio.sleep(0.05)
    dispatch_task.cancel()
    try:
        await dispatch_task
    except asyncio.CancelledError:
        pass

    mock_channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_can_be_rewritten_to_generic_keepalive() -> None:
    """Channels can rewrite internal progress into a safe user-visible keepalive."""
    bus = MessageBus()
    manager, mock_channel = _make_manager_with_channel(bus, supports_internal_progress=False)
    rewritten = OutboundMessage(
        channel="test_channel",
        chat_id="chat_42",
        content="⏳ 正在处理中，请稍候...",
        metadata={"_generic_keepalive": True},
    )
    mock_channel.transform_progress_message.return_value = rewritten

    msg = OutboundMessage(
        channel="test_channel",
        chat_id="chat_42",
        content="⏳ Running `pdf`... (10s)",
        metadata={"_progress": True},
    )
    await bus.publish_outbound(msg)

    dispatch_task = asyncio.create_task(manager._dispatch_outbound())
    await asyncio.sleep(0.05)
    dispatch_task.cancel()
    try:
        await dispatch_task
    except asyncio.CancelledError:
        pass

    mock_channel.send.assert_awaited_once_with(rewritten)


@pytest.mark.asyncio
async def test_rewritten_keepalive_respects_send_progress_flag() -> None:
    """Rewritten keepalives should not bypass the global send_progress setting."""
    bus = MessageBus()
    manager, mock_channel = _make_manager_with_channel(
        bus,
        supports_internal_progress=False,
        send_progress=False,
    )
    mock_channel.transform_progress_message.return_value = OutboundMessage(
        channel="test_channel",
        chat_id="chat_42",
        content="⏳ 正在处理中，请稍候...",
        metadata={"_generic_keepalive": True},
    )

    msg = OutboundMessage(
        channel="test_channel",
        chat_id="chat_42",
        content="⏳ Running `pdf`... (10s)",
        metadata={"_progress": True},
    )
    await bus.publish_outbound(msg)

    dispatch_task = asyncio.create_task(manager._dispatch_outbound())
    await asyncio.sleep(0.05)
    dispatch_task.cancel()
    try:
        await dispatch_task
    except asyncio.CancelledError:
        pass

    mock_channel.transform_progress_message.assert_not_called()
    mock_channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_delivered_when_channel_supports_internal_progress() -> None:
    """Existing progress behavior stays intact for channels that support it."""
    bus = MessageBus()
    manager, mock_channel = _make_manager_with_channel(bus, supports_internal_progress=True)

    msg = OutboundMessage(
        channel="test_channel",
        chat_id="chat_42",
        content="正在整理资料...",
        metadata={"_progress": True},
    )
    await bus.publish_outbound(msg)

    dispatch_task = asyncio.create_task(manager._dispatch_outbound())
    await asyncio.sleep(0.05)
    dispatch_task.cancel()
    try:
        await dispatch_task
    except asyncio.CancelledError:
        pass

    mock_channel.send.assert_awaited_once_with(msg)
