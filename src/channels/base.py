"""Base channel interface for chat platforms."""

from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

from src.bus.events import InboundMessage, OutboundMessage
from src.bus.queue import MessageBus


def identity_matches(sender_id: str, allowed_ids: list[str] | set[str] | tuple[str, ...]) -> bool:
    """Return true when sender_id or any composite part is in allowed_ids."""
    allowed = {str(item) for item in allowed_ids if item}
    if not allowed:
        return False

    sender_str = str(sender_id)
    if sender_str in allowed:
        return True
    if "|" not in sender_str:
        return False
    return any(part and part in allowed for part in sender_str.split("|"))


class BaseChannel(ABC):
    """
    Abstract base class for chat channel implementations.

    Each channel (Telegram, Discord, etc.) should implement this interface
    to integrate with the TheOS message bus.
    """

    name: str = "base"

    def __init__(self, config: Any, bus: MessageBus, owner_ids: list[str] | None = None):
        """
        Initialize the channel.

        Args:
            config: Channel-specific configuration.
            bus: The message bus for communication.
            owner_ids: Optional list of owner user IDs for sender_is_owner resolution.
        """
        self.config = config
        self.bus = bus
        self._running = False
        self._accept_inbound = True
        self._owner_ids: set[str] = {str(oid) for oid in (owner_ids or []) if oid}

    @abstractmethod
    async def start(self) -> None:
        """
        Start the channel and begin listening for messages.

        This should be a long-running async task that:
        1. Connects to the chat platform
        2. Listens for incoming messages
        3. Forwards messages to the bus via _handle_message()
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel and clean up resources."""
        pass

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """
        Send a message through this channel.

        Args:
            msg: The message to send.
        """
        pass

    def is_allowed(self, sender_id: str) -> bool:
        """
        Check if a sender is allowed to use this bot.

        Args:
            sender_id: The sender's identifier.

        Returns:
            True if allowed, False otherwise.
        """
        allow_list = getattr(self.config, "allow_from", [])

        # If no allow list, allow everyone
        if not allow_list:
            return True

        return identity_matches(sender_id, allow_list)

    def _is_owner_sender(self, sender_id: str) -> bool:
        """Check if sender is an owner (supports composite 'id|username' format)."""
        return identity_matches(sender_id, self._owner_ids)

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> None:
        """
        Handle an incoming message from the chat platform.

        This method checks permissions and forwards to the bus.

        Args:
            sender_id: The sender's identifier.
            chat_id: The chat/channel identifier.
            content: Message text content.
            media: Optional list of media URLs.
            metadata: Optional channel-specific metadata.
            session_key: Optional session key override (e.g. thread-scoped sessions).
        """
        if not self._accept_inbound:
            logger.info("Ignoring inbound message on paused channel {}", self.name)
            return

        if not self.is_allowed(sender_id):
            logger.warning(
                "Access denied for sender {} on channel {}. "
                "Add them to allowFrom list in config to grant access.",
                sender_id,
                self.name,
            )
            return

        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=metadata or {},
            session_key_override=session_key,
            sender_is_owner=self._is_owner_sender(sender_id),
        )

        await self.bus.publish_inbound(msg)

    def pause_inbound(self) -> None:
        """Stop accepting new inbound messages while allowing outbound sends."""
        self._accept_inbound = False

    def resume_inbound(self) -> None:
        """Resume accepting inbound messages."""
        self._accept_inbound = True

    @property
    def is_running(self) -> bool:
        """Check if the channel is running."""
        return self._running

    @property
    def supports_internal_progress(self) -> bool:
        """Whether this channel should surface agent/tool progress to end users."""
        return True

    def transform_progress_message(self, msg: OutboundMessage) -> OutboundMessage | None:
        """Rewrite or suppress internal progress for user delivery on this channel."""
        return msg
