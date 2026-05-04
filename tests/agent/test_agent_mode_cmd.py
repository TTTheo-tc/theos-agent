"""Tests for /agent mode switching semantics."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agent.loop import AgentLoop
from src.agent.slash_commands import handle_agent_command
from src.bus.events import InboundMessage
from src.bus.queue import MessageBus
from src.config.schema import AgentRoleConfig, Config


def _make_provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return provider


def _make_config(workspace: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(workspace)
    cfg.agents.roles = {
        "executor": AgentRoleConfig(
            description="exec",
            prompt="do work",
            tools=["read_file", "write_file"],
        )
    }
    cfg.agents.mode = "team"
    cfg.tools.profile = "full"
    return cfg


def _make_single_config(workspace: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(workspace)
    cfg.agents.roles = {
        "executor": AgentRoleConfig(
            description="exec",
            prompt="do work",
            tools=["read_file", "write_file"],
        )
    }
    cfg.tools.profile = "full"
    return cfg


@pytest.mark.asyncio
async def test_agent_single_preserves_roles_and_restores_full_toolset(tmp_path: Path):
    config = _make_config(tmp_path)
    loop = AgentLoop(bus=MessageBus(), provider=_make_provider(), config=config)
    assert loop.mode == "team"
    assert loop.tools.has("write_docs") is True
    assert loop.tools.has("write_file") is False

    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/agent single")
    with (
        patch("src.config.loader.load_config", return_value=config),
        patch("src.config.loader.save_config"),
    ):
        resp = await handle_agent_command(loop, msg)

    assert "Switched to single-agent mode" in resp.content
    assert loop.mode == "single"
    assert "executor" in loop.roles
    assert loop.tools.has("write_file") is True
    assert loop.tools.has("write_docs") is False


@pytest.mark.asyncio
async def test_agent_team_requires_explicit_enable(tmp_path: Path):
    config = _make_single_config(tmp_path)
    loop = AgentLoop(bus=MessageBus(), provider=_make_provider(), config=config)

    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/agent team")
    with (
        patch("src.config.loader.load_config", return_value=config),
        patch("src.config.loader.save_config"),
    ):
        resp = await handle_agent_command(loop, msg)

    assert "Team mode is disabled" in resp.content
    assert loop.mode == "single"


@pytest.mark.asyncio
async def test_agent_team_switches_when_enabled(tmp_path: Path):
    config = _make_single_config(tmp_path)
    config.agents.team_enabled = True
    loop = AgentLoop(bus=MessageBus(), provider=_make_provider(), config=config)

    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/agent team")
    with (
        patch("src.config.loader.load_config", return_value=config),
        patch("src.config.loader.save_config"),
    ):
        resp = await handle_agent_command(loop, msg)

    assert "Switched to team mode" in resp.content
    assert loop.mode == "team"
    assert loop.team_enabled is True
    assert loop.tools.has("write_docs") is True
    assert loop.tools.has("write_file") is False


@pytest.mark.asyncio
async def test_agent_genver_requires_explicit_enable(tmp_path: Path):
    config = _make_single_config(tmp_path)
    config.agents.genver.generator_model = "generator-model"
    loop = AgentLoop(bus=MessageBus(), provider=_make_provider(), config=config)

    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/agent genver")
    with (
        patch("src.config.loader.load_config", return_value=config),
        patch("src.config.loader.save_config"),
    ):
        resp = await handle_agent_command(loop, msg)

    assert "GenVer mode is disabled" in resp.content
    assert loop.mode == "single"
    assert loop.genver_config is None


@pytest.mark.asyncio
async def test_agent_genver_switches_when_enabled(tmp_path: Path):
    config = _make_single_config(tmp_path)
    config.agents.genver_enabled = True
    config.agents.genver.generator_model = "generator-model"
    config.agents.genver.verifier_model = "verifier-model"
    loop = AgentLoop(bus=MessageBus(), provider=_make_provider(), config=config)

    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/agent genver")
    with (
        patch("src.config.loader.load_config", return_value=config),
        patch("src.config.loader.save_config"),
    ):
        resp = await handle_agent_command(loop, msg)

    assert "Generator-Verifier mode" in resp.content
    assert loop.mode == "genver"
    assert loop.genver_enabled is True
    assert loop.genver_config is config.agents.genver
