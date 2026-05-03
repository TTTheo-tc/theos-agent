"""Tests for /model slash command."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.loop import AgentLoop
from src.bus.events import InboundMessage


def _make_loop() -> AgentLoop:
    """Create an AgentLoop with minimal mocks."""
    bus = MagicMock()
    bus.consume_inbound = AsyncMock()
    bus.publish_outbound = AsyncMock()

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    from pathlib import Path
    from tempfile import mkdtemp

    from src.config.schema import Config

    ws = Path(mkdtemp())
    cfg = Config()
    cfg.agents.defaults.workspace = str(ws)
    cfg.agents.defaults.model = ""  # fall back to provider.get_default_model()
    loop = AgentLoop(bus=bus, provider=provider, config=cfg)
    return loop


def _msg(content: str) -> InboundMessage:
    return InboundMessage(channel="cli", sender_id="user", chat_id="direct", content=content)


async def test_model_show_current():
    loop = _make_loop()
    resp = await loop._handle_model_command(_msg("/model"))
    assert "test-model" in resp.content
    assert "Default model" in resp.content


async def test_model_switch_default():
    loop = _make_loop()
    with (
        patch("src.config.loader.load_config") as mock_load,
        patch("src.config.loader.save_config"),
    ):
        mock_cfg = MagicMock()
        mock_cfg.agents.defaults.model = "test-model"
        mock_load.return_value = mock_cfg
        resp = await loop._handle_model_command(_msg("/model new-model"))

    assert loop.model == "new-model"
    assert "new-model" in resp.content


async def test_model_switch_role():
    loop = _make_loop()
    from src.config.schema import AgentRoleConfig

    loop.roles = {"executor": AgentRoleConfig(model="old-model")}
    loop.subagents.roles = loop.roles

    with (
        patch("src.config.loader.load_config") as mock_load,
        patch("src.config.loader.save_config"),
    ):
        mock_cfg = MagicMock()
        mock_cfg.agents.roles = {"executor": AgentRoleConfig(model="old-model")}
        mock_load.return_value = mock_cfg
        resp = await loop._handle_model_command(_msg("/model executor new-model"))

    assert loop.roles["executor"].model == "new-model"
    assert "executor" in resp.content
    assert "new-model" in resp.content


@pytest.mark.asyncio
async def test_cli_bare_model_alias_switches_model():
    loop = _make_loop()
    msg = _msg("opus")

    with (
        patch("src.config.loader.load_config") as mock_load,
        patch("src.config.loader.save_config"),
    ):
        mock_cfg = MagicMock()
        mock_cfg.agents.defaults.model = "test-model"
        mock_load.return_value = mock_cfg
        resp = await loop._process_message(msg)

    assert loop.model == "anthropic/claude-opus-4-6"
    assert "Default model →" in resp.content
    assert "anthropic/claude-opus-4-6" in resp.content


async def test_model_hot_swap_propagates_to_executor():
    """Verify /model changes propagate through facade to executor's role resolver."""
    loop = _make_loop()
    from src.config.schema import AgentRoleConfig

    loop.roles = {"explorer": AgentRoleConfig(model="old/model")}
    loop.subagents.roles = loop.roles

    # Hot-swap default model on the facade
    loop.subagents.model = "new/hot-model"

    # _resolve_all_roles uses self.model as the default for roles without a model
    resolved = loop.subagents._resolve_all_roles()

    # Named role keeps its own explicit model
    assert resolved["explorer"].model == "old/model"

    # Add a role with no model override — it should pick up the hot-swapped default
    loop.roles["helper"] = AgentRoleConfig(model="")
    loop.subagents.roles = loop.roles
    resolved = loop.subagents._resolve_all_roles()
    assert resolved["helper"].model == "new/hot-model"
