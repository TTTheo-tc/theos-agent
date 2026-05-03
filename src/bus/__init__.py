"""Message bus module for decoupled channel-agent communication."""

from src.bus.events import InboundMessage, OutboundMessage
from src.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
