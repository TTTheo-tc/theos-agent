import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from src.bus.queue import MessageBus


def _load_telegram_with_stubs(monkeypatch):
    """Import TelegramChannel without requiring optional Telegram dependencies."""
    monkeypatch.delitem(sys.modules, "src.channels.telegram", raising=False)

    telegram_mod = ModuleType("telegram")

    class BotCommand:
        def __init__(self, command: str, description: str) -> None:
            self.command = command
            self.description = description

    class ReplyParameters:
        def __init__(self, message_id: str, allow_sending_without_reply: bool) -> None:
            self.message_id = message_id
            self.allow_sending_without_reply = allow_sending_without_reply

    telegram_mod.BotCommand = BotCommand
    telegram_mod.ReplyParameters = ReplyParameters
    telegram_mod.Update = type("Update", (), {})

    ext_mod = ModuleType("telegram.ext")
    ext_mod.Application = type("Application", (), {})
    ext_mod.CommandHandler = type("CommandHandler", (), {})
    ext_mod.ContextTypes = SimpleNamespace(DEFAULT_TYPE=object)
    ext_mod.MessageHandler = type("MessageHandler", (), {})
    ext_mod.filters = SimpleNamespace(
        TEXT=object(),
        PHOTO=object(),
        VOICE=object(),
        AUDIO=object(),
        COMMAND=object(),
        Document=SimpleNamespace(ALL=object()),
    )

    request_mod = ModuleType("telegram.request")
    request_mod.HTTPXRequest = type("HTTPXRequest", (), {})

    for name, module in {
        "telegram": telegram_mod,
        "telegram.ext": ext_mod,
        "telegram.request": request_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    return importlib.import_module("src.channels.telegram")


@pytest.mark.asyncio
async def test_telegram_clears_typing_when_publish_is_rejected(monkeypatch) -> None:
    telegram = _load_telegram_with_stubs(monkeypatch)
    channel = telegram.TelegramChannel(
        SimpleNamespace(token="token", proxy=None, reply_to_message=False),
        MessageBus(),
    )
    started: list[str] = []
    stopped: list[str] = []

    def _fake_start_typing(chat_id: str) -> None:
        started.append(chat_id)

    def _fake_stop_typing(chat_id: str) -> None:
        stopped.append(chat_id)

    async def _fake_handle_message(**kwargs) -> bool:
        del kwargs
        return False

    channel._start_typing = _fake_start_typing  # type: ignore[method-assign]
    channel._stop_typing = _fake_stop_typing  # type: ignore[method-assign]
    channel._handle_message = _fake_handle_message  # type: ignore[method-assign]

    message = SimpleNamespace(
        chat_id=123,
        text="hello",
        caption=None,
        photo=None,
        voice=None,
        audio=None,
        document=None,
        message_id=456,
        chat=SimpleNamespace(type="private"),
    )
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=789, username="alice", first_name="Alice"),
    )

    await channel._on_message(update, SimpleNamespace())

    assert started == ["123"]
    assert stopped == ["123"]
