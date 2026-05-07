from types import SimpleNamespace

import pytest

from src.bus.queue import MessageBus

discord_module = pytest.importorskip(
    "src.channels.discord",
    reason="Discord channel optional dependencies are not installed",
    exc_type=ImportError,
)


@pytest.mark.asyncio
async def test_discord_clears_typing_when_publish_is_rejected() -> None:
    channel = discord_module.DiscordChannel(
        SimpleNamespace(token="token", gateway_url="ws://gateway", intents=0, allow_from=[]),
        MessageBus(),
    )
    started: list[str] = []
    stopped: list[str] = []

    async def _fake_start_typing(channel_id: str) -> None:
        started.append(channel_id)

    async def _fake_stop_typing(channel_id: str) -> None:
        stopped.append(channel_id)

    async def _fake_handle_message(**kwargs) -> bool:
        del kwargs
        return False

    channel._start_typing = _fake_start_typing  # type: ignore[method-assign]
    channel._stop_typing = _fake_stop_typing  # type: ignore[method-assign]
    channel._handle_message = _fake_handle_message  # type: ignore[method-assign]

    await channel._handle_message_create(
        {
            "author": {"id": "user_1", "bot": False},
            "channel_id": "channel_1",
            "content": "hello",
            "id": "message_1",
            "attachments": [],
        }
    )

    assert started == ["channel_1"]
    assert stopped == ["channel_1"]
