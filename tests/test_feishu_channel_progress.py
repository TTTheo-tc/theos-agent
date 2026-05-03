"""Tests for Feishu progress rewriting behavior."""

from __future__ import annotations

import json
from types import SimpleNamespace

from src.bus.events import OutboundMessage
from src.bus.queue import MessageBus
from src.channels.feishu import FeishuChannel


def _make_channel() -> FeishuChannel:
    config = SimpleNamespace(
        app_id="app",
        app_secret="secret",
        react_emoji="THUMBSUP",
    )
    return FeishuChannel(config, MessageBus())


def test_tool_hint_progress_is_suppressed() -> None:
    channel = _make_channel()
    msg = OutboundMessage(
        channel="feishu",
        chat_id="oc_x",
        content='pdf("https://arxiv.org/pdf/2207.05844")',
        metadata={"_progress": True, "_tool_hint": True, "message_id": "om_1"},
    )
    assert channel.transform_progress_message(msg) is None


def test_non_keepalive_progress_is_suppressed() -> None:
    channel = _make_channel()
    msg = OutboundMessage(
        channel="feishu",
        chat_id="oc_x",
        content="我先找到 Wayformer 这篇论文，然后给你做详细分析。",
        metadata={"_progress": True, "message_id": "om_1"},
    )
    assert channel.transform_progress_message(msg) is None


def test_long_running_progress_becomes_generic_keepalive_once() -> None:
    channel = _make_channel()
    msg = OutboundMessage(
        channel="feishu",
        chat_id="oc_x",
        content="⏳ Running `pdf`... (10s)",
        metadata={"_progress": True, "message_id": "om_1"},
    )

    transformed = channel.transform_progress_message(msg)
    assert transformed is not None
    assert transformed.content == "⏳ 正在处理中，请稍候..."
    assert transformed.metadata.get("_generic_keepalive") is True
    assert "_progress" not in transformed.metadata

    # Same source message should not emit duplicate keepalives.
    assert channel.transform_progress_message(msg) is None


def test_send_falls_back_to_text_when_interactive_card_fails() -> None:
    channel = _make_channel()
    calls: list[tuple[str, str, str, dict]] = []

    def _fake_send(receive_id_type: str, receive_id: str, msg_type: str, content: str) -> bool:
        calls.append((receive_id_type, receive_id, msg_type, json.loads(content)))
        return msg_type == "text"

    channel._send_message_sync = _fake_send  # type: ignore[method-assign]
    ok = channel._send_content_with_fallback_sync("open_id", "ou_test", "卡片失败后应该退回纯文本")

    assert ok is True
    assert [call[2] for call in calls] == ["interactive", "text"]
    assert calls[0][0] == "open_id"
    assert calls[1][3] == {"text": "卡片失败后应该退回纯文本"}
