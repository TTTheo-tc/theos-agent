"""Event types for the message bus."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class InboundMessage:
    """Message received from a chat channel."""

    channel: str  # telegram, discord, slack, whatsapp
    sender_id: str  # User identifier
    chat_id: str  # Chat/channel identifier
    content: str  # Message text
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data
    session_key_override: str | None = None  # Optional override for thread-scoped sessions
    sender_is_owner: bool | None = None  # Computed at entry layer; None = use fallback

    @property
    def session_key(self) -> str:
        """Unique key for session identification."""
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """Response message from the agent to a channel.

    Metadata schema (stable contract):
        _progress: bool   — Streaming progress update, not a final reply.
        _tool_hint: bool  — Tool execution hint (e.g., "reading file.py...").
        _genver_ask: bool — GenVer question to the user.
        usage: dict       — Token usage stats (set on final reply only).

    Channel adapter behavior:
        - _progress=True messages → typing indicators or streaming text.
        - _tool_hint=True messages → subtle status updates.
        - Final replies (no _progress) → main response delivery.
    """

    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
