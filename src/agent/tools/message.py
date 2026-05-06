"""Message tool for sending messages to users."""

from typing import Any, Awaitable, Callable

from src.agent.tools.base import ContextAwareTool
from src.bus.events import OutboundMessage


class MessageTool(ContextAwareTool):
    """Tool to send messages to users on chat channels."""

    @property
    def owner_only(self) -> bool:
        return True

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
    ):
        self._send_callback = send_callback
        self._messages_sent_in_turn: bool = False

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    def start_turn(self) -> None:
        """Reset per-turn send tracking."""
        self._messages_sent_in_turn = False

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return "Send a message to the user. Use this when you want to communicate something."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The message content to send"},
                "channel": {
                    "type": "string",
                    "description": "Optional: target channel (telegram, discord, etc.)",
                },
                "chat_id": {"type": "string", "description": "Optional: target chat/user ID"},
                "media": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: list of file paths to attach (images, audio, documents)",
                },
            },
            "required": ["content"],
        }

    async def execute(
        self,
        content: str,
        _context: Any = None,
        channel: str | None = None,
        chat_id: str | None = None,
        message_id: str | None = None,
        media: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        del kwargs
        from src.agent.tools.context import ToolContext

        ctx = _context or ToolContext()
        channel, chat_id, message_id = self._resolve_target(ctx, channel, chat_id, message_id)

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        if not self._send_callback:
            return "Error: Message sending not configured"

        msg = self._build_outbound_message(channel, chat_id, content, message_id, media)

        try:
            await self._send_callback(msg)
            self._mark_sent_if_current_target(channel, chat_id, ctx)
            return self._format_success(channel, chat_id, media)
        except Exception as e:
            return f"Error sending message: {str(e)}"

    @staticmethod
    def _resolve_target(
        ctx: Any,
        channel: str | None,
        chat_id: str | None,
        message_id: str | None,
    ) -> tuple[str | None, str | None, str | None]:
        return channel or ctx.channel, chat_id or ctx.chat_id, message_id or ctx.message_id

    @staticmethod
    def _build_outbound_message(
        channel: str,
        chat_id: str,
        content: str,
        message_id: str | None,
        media: list[str] | None,
    ) -> OutboundMessage:
        return OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=media or [],
            metadata={"message_id": message_id},
        )

    def _mark_sent_if_current_target(self, channel: str, chat_id: str, ctx: Any) -> None:
        if channel == ctx.channel and chat_id == ctx.chat_id:
            self._messages_sent_in_turn = True

    @staticmethod
    def _format_success(channel: str, chat_id: str, media: list[str] | None) -> str:
        media_info = f" with {len(media)} attachments" if media else ""
        return f"Message sent to {channel}:{chat_id}{media_info}"
