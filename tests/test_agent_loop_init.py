"""AgentLoop Config-based construction + ownership propagation tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.agent.loop import AgentLoop
from src.agent.tools.registry import ToolRegistry
from src.bus.events import InboundMessage, OutboundMessage
from src.bus.queue import MessageBus
from src.config.schema import AgentRoleConfig, Config
from src.config.schema_channels import ChannelsConfig


def _make_test_config(workspace: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(workspace)
    return cfg


def _make_provider() -> MagicMock:
    p = MagicMock()
    p.get_default_model.return_value = "test-model"
    return p


def _make_loop(tmp_path: Path, **kwargs) -> AgentLoop:
    config = _make_test_config(tmp_path)
    with patch.object(AgentLoop, "_register_default_tools", return_value=None):
        return AgentLoop(
            bus=MessageBus(),
            provider=_make_provider(),
            config=config,
            **kwargs,
        )


# --- Test 1: Default Config construction ---


def test_default_config_construction(tmp_path: Path):
    """AgentLoop(bus, provider, Config()) sets correct defaults."""
    loop = _make_loop(tmp_path)
    assert loop.workspace == tmp_path
    assert loop.temperature == 0.1  # AgentDefaults default
    assert loop.max_iterations == 60  # AgentDefaults.max_tool_iterations default
    assert loop.memory_window == 100


# --- Test 2: Config values propagated ---


def test_config_values_propagated(tmp_path: Path):
    """Non-default Config values appear on AgentLoop attributes."""
    config = _make_test_config(tmp_path)
    config.agents.defaults.temperature = 0.5
    config.agents.defaults.max_tokens = 2048
    config.agents.defaults.max_tool_iterations = 10

    with patch.object(AgentLoop, "_register_default_tools", return_value=None):
        loop = AgentLoop(bus=MessageBus(), provider=_make_provider(), config=config)

    assert loop.temperature == 0.5
    assert loop.max_tokens == 2048
    assert loop.max_iterations == 10


def test_roles_do_not_force_multi_mode(tmp_path: Path):
    """Configured roles remain available without forcing orchestrator-only tools."""
    config = _make_test_config(tmp_path)
    config.agents.roles = {
        "executor": AgentRoleConfig(
            description="exec",
            prompt="do work",
            tools=["read_file", "write_file"],
        )
    }

    loop = AgentLoop(bus=MessageBus(), provider=_make_provider(), config=config)

    assert loop.mode == "single"
    assert "executor" in loop.roles
    assert loop.tools.has("write_file") is True
    assert loop.tools.has("write_docs") is False


# --- Test 3: channels_config_override ---


def test_channels_config_override(tmp_path: Path):
    """Override replaces config.channels."""
    override = ChannelsConfig(send_tool_hints=True, owner_ids=["alice"])
    loop = _make_loop(tmp_path, channels_config_override=override)

    assert loop.channels_config.send_tool_hints is True
    assert "alice" in loop._owner_ids


# --- Test 4: Runtime deps injection ---


def test_runtime_deps_injection(tmp_path: Path):
    """session_manager, dashboard, reflector are set when passed."""
    mock_sm = MagicMock()
    mock_dash = MagicMock()
    mock_refl = MagicMock()

    loop = _make_loop(
        tmp_path,
        session_manager=mock_sm,
        dashboard=mock_dash,
        reflector=mock_refl,
    )

    assert loop.sessions is mock_sm
    assert loop.dashboard is mock_dash
    assert loop.reflector is mock_refl


# --- Test 5: sender_is_owner respected by _resolve_sender_is_owner ---


def test_sender_is_owner_respected(tmp_path: Path):
    """Explicit sender_is_owner on InboundMessage is used by _resolve_sender_is_owner."""
    loop = _make_loop(tmp_path)

    msg_owner = InboundMessage(
        channel="telegram",
        sender_id="stranger",
        chat_id="c1",
        content="hi",
        sender_is_owner=True,
    )
    msg_not_owner = InboundMessage(
        channel="telegram",
        sender_id="stranger",
        chat_id="c1",
        content="hi",
        sender_is_owner=False,
    )
    msg_fallback = InboundMessage(
        channel="cli",
        sender_id="user",
        chat_id="c1",
        content="hi",  # sender_is_owner=None -> fallback
    )

    assert loop._resolve_sender_is_owner(msg_owner) is True
    assert loop._resolve_sender_is_owner(msg_not_owner) is False
    assert loop._resolve_sender_is_owner(msg_fallback) is True  # fallback: cli=owner


# --- Test 6: alias re-wrap preserves sender_is_owner ---
# This is verified by reading the code change -- no runtime test needed beyond
# the code review confirming the field is forwarded. Covered by test 5 + test 10.


@pytest.mark.asyncio
async def test_cli_model_alias_preserves_sender_is_owner(tmp_path: Path):
    """CLI bare model aliases must preserve sender_is_owner when re-wrapped."""
    loop = _make_loop(tmp_path)
    observed: dict[str, bool | None] = {}

    async def fake_handle(msg: InboundMessage):
        observed["sender_is_owner"] = msg.sender_is_owner
        return OutboundMessage(channel="cli", chat_id="direct", content="ok")

    msg = InboundMessage(
        channel="cli",
        sender_id="user",
        chat_id="direct",
        content="gpt-5",
        sender_is_owner=False,
    )

    with (
        patch("src.agent.slash_commands.is_model_alias", return_value=True),
        patch.object(loop, "_handle_model_command", side_effect=fake_handle),
    ):
        await loop._process_message(msg)

    assert observed["sender_is_owner"] is False


# --- Test 7: process_direct marks trusted callers ---


@pytest_asyncio.fixture()
async def loop_for_direct(tmp_path: Path):
    config = _make_test_config(tmp_path)
    provider = _make_provider()
    provider.chat = AsyncMock(
        return_value=MagicMock(
            content="ok",
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            has_tool_calls=False,
            reasoning_content=None,
        )
    )
    with patch.object(AgentLoop, "_register_default_tools", return_value=None):
        loop = AgentLoop(bus=MessageBus(), provider=provider, config=config)
    loop.tools = ToolRegistry()
    yield loop
    await loop.close_mcp()
    await loop._memory.close_dbs()


async def test_process_direct_sets_sender_is_owner(loop_for_direct: AgentLoop):
    """process_direct() creates InboundMessage with sender_is_owner=True."""
    captured = {}
    original = loop_for_direct._process_message

    async def spy(msg, **kwargs):
        captured["sender_is_owner"] = msg.sender_is_owner
        return await original(msg, **kwargs)

    with patch.object(loop_for_direct, "_process_message", side_effect=spy):
        await loop_for_direct.process_direct("hello")

    assert captured.get("sender_is_owner") is True


# --- Test 8: BaseChannel composite owner ids ---


def test_basechannel_composite_owner_ids():
    """'id|username' sender format resolves ownership correctly."""
    from src.channels.base import BaseChannel

    class _Stub(BaseChannel):
        async def start(self) -> None: ...
        async def stop(self) -> None: ...
        async def send(self, msg) -> None: ...

    channel = _Stub(config=MagicMock(), bus=MagicMock(), owner_ids=["12345"])

    assert channel._is_owner_sender("12345") is True
    assert channel._is_owner_sender("12345|alice") is True
    assert channel._is_owner_sender("99999|bob") is False
    assert channel._is_owner_sender("unknown") is False


# --- Test 9: _is_owner composite sender ids in AgentLoop ---


def test_agent_loop_composite_owner_ids(tmp_path: Path):
    """AgentLoop._is_owner handles 'id|username' composite format."""
    loop = _make_loop(tmp_path, channels_config_override=ChannelsConfig(owner_ids=["12345"]))

    assert loop._is_owner("12345", "telegram") is True
    assert loop._is_owner("12345|alice", "telegram") is True
    assert loop._is_owner("99999|bob", "telegram") is False


# --- Test 10: ChannelManager passes owner_ids to channels ---


def test_channel_manager_passes_owner_ids():
    """ChannelManager passes owner_ids when constructing channels."""
    from src.channels.manager import ChannelManager
    from src.channels.registry import ChannelSpec

    fake_channel_cls = MagicMock()
    fake_ch_config = MagicMock()
    fake_ch_config.enabled = True

    # Use a MagicMock for channels so getattr(config.channels, "fake") works
    mock_channels = MagicMock()
    mock_channels.owner_ids = ["alice", "bob"]
    mock_channels.fake = fake_ch_config

    mock_config = MagicMock()
    mock_config.channels = mock_channels

    spec = ChannelSpec(
        name="fake",
        config_attr="fake",
        module="src.channels.fake",
        class_name="FakeChannel",
    )

    with (
        patch("src.channels.registry.CHANNELS", (spec,)),
        patch("importlib.import_module") as mock_import,
    ):
        mock_module = MagicMock()
        mock_module.FakeChannel = fake_channel_cls
        mock_import.return_value = mock_module

        mgr = ChannelManager.__new__(ChannelManager)
        mgr.config = mock_config
        mgr.bus = MagicMock()
        mgr.dashboard = None
        mgr.channels = {}
        mgr._dispatch_task = None
        mgr._init_channels()

    # Verify owner_ids was passed to the channel constructor
    call_kwargs = fake_channel_cls.call_args
    assert call_kwargs.kwargs.get("owner_ids") == ["alice", "bob"]


# --- Test 11: system-message branch uses _resolve_sender_is_owner ---


def test_system_message_respects_sender_is_owner(tmp_path: Path):
    """System-message ToolContext uses _resolve_sender_is_owner."""
    loop = _make_loop(tmp_path)

    msg = InboundMessage(
        channel="system",
        sender_id="cron",
        chat_id="cli:direct",
        content="system task",
        sender_is_owner=True,
    )
    # _resolve_sender_is_owner should return True from the explicit field
    assert loop._resolve_sender_is_owner(msg, channel="cli") is True

    msg_no_flag = InboundMessage(
        channel="system",
        sender_id="cron",
        chat_id="telegram:123",
        content="system task",  # sender_is_owner=None -> fallback
    )
    # Fallback: sender_id="cron" is not in owner_ids, channel="telegram"
    assert loop._resolve_sender_is_owner(msg_no_flag, channel="telegram") is False
