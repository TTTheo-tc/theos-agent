import importlib
import sys
from types import ModuleType, SimpleNamespace

from src.bus.queue import MessageBus


def _load_slack_with_stubs(monkeypatch):
    """Import SlackChannel without requiring optional Slack dependencies."""
    monkeypatch.delitem(sys.modules, "src.channels.slack", raising=False)

    request_mod = ModuleType("slack_sdk.socket_mode.request")
    request_mod.SocketModeRequest = type("SocketModeRequest", (), {})
    response_mod = ModuleType("slack_sdk.socket_mode.response")

    class SocketModeResponse:
        def __init__(self, envelope_id=None):
            self.envelope_id = envelope_id

    response_mod.SocketModeResponse = SocketModeResponse
    websockets_mod = ModuleType("slack_sdk.socket_mode.websockets")
    websockets_mod.SocketModeClient = type("SocketModeClient", (), {})
    async_client_mod = ModuleType("slack_sdk.web.async_client")
    async_client_mod.AsyncWebClient = type("AsyncWebClient", (), {})
    slackify_mod = ModuleType("slackify_markdown")
    slackify_mod.slackify_markdown = lambda text: text

    for name, module in {
        "slack_sdk": ModuleType("slack_sdk"),
        "slack_sdk.socket_mode": ModuleType("slack_sdk.socket_mode"),
        "slack_sdk.socket_mode.request": request_mod,
        "slack_sdk.socket_mode.response": response_mod,
        "slack_sdk.socket_mode.websockets": websockets_mod,
        "slack_sdk.web": ModuleType("slack_sdk.web"),
        "slack_sdk.web.async_client": async_client_mod,
        "slackify_markdown": slackify_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    return importlib.import_module("src.channels.slack")


def test_slack_channel_respects_inbound_pause(monkeypatch) -> None:
    slack = _load_slack_with_stubs(monkeypatch)
    config = SimpleNamespace(
        dm=SimpleNamespace(enabled=True, policy="open", allow_from=[]),
        group_policy="open",
        group_allow_from=[],
    )
    channel = slack.SlackChannel(config, MessageBus())

    assert channel._is_allowed("alice", "C123", "im") is True
    channel.pause_inbound()
    assert channel._is_allowed("alice", "C123", "im") is False
