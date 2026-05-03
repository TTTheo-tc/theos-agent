"""Tests for session_key_override routing in system messages."""

from src.bus.events import InboundMessage


class TestSessionKeyOverride:
    def test_inbound_message_session_key_uses_override(self):
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="cli:direct",
            content="done",
            session_key_override="telegram:456",
        )
        assert msg.session_key == "telegram:456"

    def test_inbound_message_session_key_fallback(self):
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="cli:direct",
            content="done",
        )
        assert msg.session_key == "system:cli:direct"
