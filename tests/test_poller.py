import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.poller.base import BasePoller, PollerEvent
from src.poller.service import PollerService
from src.poller.x_poller import XPoller


class _Bus:
    def __init__(self) -> None:
        self.inbound = []

    async def publish_inbound(self, msg) -> None:
        self.inbound.append(msg)


class _RecordingPoller(BasePoller):
    name = "noop"

    def __init__(self) -> None:
        self.setup_called = False
        self.teardown_called = False
        self.setup_event = asyncio.Event()

    async def setup(self) -> None:
        self.setup_called = True
        self.setup_event.set()

    async def poll_once(self) -> list[PollerEvent]:
        return []

    async def teardown(self) -> None:
        self.teardown_called = True


class _FailingSetupPoller(BasePoller):
    name = "fail_setup"

    def __init__(self) -> None:
        self.setup_calls = 0

    async def setup(self) -> None:
        self.setup_calls += 1
        raise RuntimeError("setup failed")

    async def poll_once(self) -> list[PollerEvent]:
        return []

    async def teardown(self) -> None:
        pass


@pytest.mark.asyncio
async def test_default_event_handler_injects_owner_message() -> None:
    bus = _Bus()
    service = PollerService(bus=bus)
    event = PollerEvent(
        poller_name="x_monitor",
        message="new post",
        metadata={"tweet_id": "123"},
    )

    await service._default_on_event(event)

    assert len(bus.inbound) == 1
    msg = bus.inbound[0]
    assert msg.channel == "poller"
    assert msg.sender_id == "poller:x_monitor"
    assert msg.chat_id == "poller"
    assert msg.content == "new post"
    assert msg.metadata == {"poller_name": "x_monitor", "tweet_id": "123"}
    assert msg.sender_is_owner is True


@pytest.mark.asyncio
async def test_service_start_stop_tracks_registered_pollers() -> None:
    bus = _Bus()
    service = PollerService(bus=bus)
    poller = _RecordingPoller()
    service.register(poller)

    await service.start()
    await asyncio.wait_for(poller.setup_event.wait(), timeout=1)
    await service.stop()

    assert poller.setup_called is True
    assert poller.teardown_called is True


@pytest.mark.asyncio
async def test_service_start_is_idempotent() -> None:
    bus = _Bus()
    service = PollerService(bus=bus)
    poller = _RecordingPoller()
    service.register(poller)

    await service.start()
    await service.start()

    assert len(service._tasks) == 1

    await service.stop()


@pytest.mark.asyncio
async def test_service_start_allows_retry_after_setup_failure() -> None:
    bus = _Bus()
    service = PollerService(bus=bus)
    poller = _FailingSetupPoller()
    service.register(poller)

    await service.start()
    await asyncio.wait_for(service._tasks[0], timeout=1)
    await service.start()
    await asyncio.wait_for(service._tasks[0], timeout=1)

    assert poller.setup_calls == 2


@pytest.mark.asyncio
async def test_x_poller_poll_once_includes_notification_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    poller = XPoller(
        ["Theo"],
        state_path=tmp_path / "state.json",
        notify_channel="feishu",
        notify_chat_id="chat-1",
    )
    poller._api = object()
    tweet = SimpleNamespace(id=123, rawContent="hello world")
    saved = {"called": False}

    async def _check_user(username: str):
        assert username == "theo"
        return [tweet]

    def _save_state() -> None:
        saved["called"] = True

    monkeypatch.setattr(poller, "_check_user", _check_user)
    monkeypatch.setattr(poller, "_save_state", _save_state)

    events = await poller.poll_once()

    assert len(events) == 1
    event = events[0]
    assert event.poller_name == "x_monitor"
    assert event.message == "[X Monitor] New post from @theo\n\nhello world\n\nURL: https://x.com/theo/status/123"
    assert event.metadata == {
        "tweet_id": "123",
        "username": "theo",
        "tweet_url": "https://x.com/theo/status/123",
        "notify_channel": "feishu",
        "notify_chat_id": "chat-1",
    }
    assert saved["called"] is True


def test_x_poller_state_roundtrip(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    poller = XPoller(["Theo"], state_path=state_path)
    poller._seen = {"theo": {"2", "1"}}

    poller._save_state()

    loaded = XPoller(["Theo"], state_path=state_path)
    loaded._load_state()

    assert loaded._seen == {"theo": {"1", "2"}}
