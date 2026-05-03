"""Test message tool suppress logic for final replies."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.loop import AgentLoop
from src.agent.tools.message import MessageTool
from src.bus.events import InboundMessage, OutboundMessage
from src.bus.queue import MessageBus
from src.config.schema import Config
from src.config.schema_channels import ChannelsConfig
from src.providers.base import LLMResponse, ToolCallRequest


def _make_loop(tmp_path: Path, *, owner_ids: list[str] | None = None) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    cfg.agents.defaults.memory_window = 10
    cfg.tools.profile = "messaging"
    return AgentLoop(
        bus=bus,
        provider=provider,
        config=cfg,
        channels_config_override=ChannelsConfig(owner_ids=owner_ids or []),
    )


class TestMessageToolSuppressLogic:
    """Final reply suppressed only when message tool sends to the same target."""

    @pytest.mark.asyncio
    async def test_suppress_when_sent_to_same_target(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path, owner_ids=["user1"])
        tool_call = ToolCallRequest(
            id="call1",
            name="message",
            arguments={"content": "Hello", "channel": "feishu", "chat_id": "chat123"},
        )
        calls = iter(
            [
                LLMResponse(content="", tool_calls=[tool_call]),
                LLMResponse(content="Done", tool_calls=[]),
                LLMResponse(content="", tool_calls=[]),
            ]
        )
        loop.provider.chat = AsyncMock(side_effect=lambda *a, **kw: next(calls))
        loop.tools.get_definitions = MagicMock(return_value=[])

        sent: list[OutboundMessage] = []
        mt = loop.tools.get("message")
        if isinstance(mt, MessageTool):
            mt.set_send_callback(AsyncMock(side_effect=lambda m: sent.append(m)))

        msg = InboundMessage(channel="feishu", sender_id="user1", chat_id="chat123", content="Send")
        result = await loop._process_message(msg)

        assert len(sent) == 1
        assert result is None  # suppressed

    @pytest.mark.asyncio
    async def test_not_suppress_when_sent_to_different_target(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path, owner_ids=["user1"])
        tool_call = ToolCallRequest(
            id="call1",
            name="message",
            arguments={
                "content": "Email content",
                "channel": "email",
                "chat_id": "user@example.com",
            },
        )
        calls = iter(
            [
                LLMResponse(content="", tool_calls=[tool_call]),
                LLMResponse(content="I've sent the email.", tool_calls=[]),
                LLMResponse(content="", tool_calls=[]),
            ]
        )
        loop.provider.chat = AsyncMock(side_effect=lambda *a, **kw: next(calls))
        loop.tools.get_definitions = MagicMock(return_value=[])

        sent: list[OutboundMessage] = []
        mt = loop.tools.get("message")
        if isinstance(mt, MessageTool):
            mt.set_send_callback(AsyncMock(side_effect=lambda m: sent.append(m)))

        msg = InboundMessage(
            channel="feishu", sender_id="user1", chat_id="chat123", content="Send email"
        )
        result = await loop._process_message(msg)

        assert len(sent) == 1
        assert sent[0].channel == "email"
        assert result is not None  # not suppressed
        assert result.channel == "feishu"

    @pytest.mark.asyncio
    async def test_not_suppress_when_no_message_tool_used(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop.provider.chat = AsyncMock(return_value=LLMResponse(content="Hello!", tool_calls=[]))
        loop.tools.get_definitions = MagicMock(return_value=[])

        msg = InboundMessage(channel="feishu", sender_id="user1", chat_id="chat123", content="Hi")
        result = await loop._process_message(msg)

        assert result is not None
        assert "Hello" in result.content


class TestMessageToolTurnTracking:

    def test_messages_sent_in_turn_tracks_same_target(self) -> None:
        tool = MessageTool()
        assert not tool._messages_sent_in_turn
        tool._messages_sent_in_turn = True
        assert tool._messages_sent_in_turn

    def test_start_turn_resets(self) -> None:
        tool = MessageTool()
        tool._messages_sent_in_turn = True
        tool.start_turn()
        assert not tool._messages_sent_in_turn
