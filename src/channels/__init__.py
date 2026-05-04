"""Chat channels module with plugin architecture."""

from src.channels.base import BaseChannel

__all__ = ["BaseChannel", "ChannelManager"]


def __getattr__(name: str):
    if name == "ChannelManager":
        from src.channels.manager import ChannelManager

        return ChannelManager
    raise AttributeError(name)
