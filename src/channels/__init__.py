"""Chat channels module with plugin architecture."""

from src.channels.base import BaseChannel
from src.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
