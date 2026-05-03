"""Tests for owner-only tool restriction."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.agent.loop import AgentLoop
from src.agent.tools.base import Tool
from src.agent.tools.context import ToolContext
from src.agent.tools.registry import ToolRegistry
from src.bus.queue import MessageBus
from src.config.schema import Config
from src.config.schema_channels import ChannelsConfig


class OwnerOnlyTool(Tool):
    @property
    def name(self) -> str:
        return "secret"

    @property
    def description(self) -> str:
        return "owner only tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def owner_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        return "executed"


class PublicTool(Tool):
    @property
    def name(self) -> str:
        return "public"

    @property
    def description(self) -> str:
        return "public tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "executed"


@pytest.mark.asyncio
async def test_owner_only_tool_blocked_for_non_owner():
    reg = ToolRegistry()
    reg.register(OwnerOnlyTool())
    ctx = ToolContext(channel="telegram", chat_id="123", sender_is_owner=False)
    result = await reg.execute("secret", {}, context=ctx)
    assert "restricted to the bot owner" in result


@pytest.mark.asyncio
async def test_owner_only_tool_allowed_for_owner():
    reg = ToolRegistry()
    reg.register(OwnerOnlyTool())
    ctx = ToolContext(channel="telegram", chat_id="123", sender_is_owner=True)
    result = await reg.execute("secret", {}, context=ctx)
    assert result == "executed"


@pytest.mark.asyncio
async def test_owner_only_tool_allowed_without_context():
    """No context = backward compat, should allow."""
    reg = ToolRegistry()
    reg.register(OwnerOnlyTool())
    result = await reg.execute("secret", {})
    assert result == "executed"


@pytest.mark.asyncio
async def test_public_tool_allowed_for_non_owner():
    reg = ToolRegistry()
    reg.register(PublicTool())
    ctx = ToolContext(channel="telegram", chat_id="123", sender_is_owner=False)
    result = await reg.execute("public", {}, context=ctx)
    assert result == "executed"


@pytest.mark.asyncio
async def test_default_owner_only_is_false():
    tool = PublicTool()
    assert tool.owner_only is False


@pytest.mark.asyncio
async def test_default_context_sender_is_owner():
    """Default ToolContext should have sender_is_owner=True (CLI compat)."""
    ctx = ToolContext()
    assert ctx.sender_is_owner is True


def _make_loop(tmp_path, *, owner_ids: list[str] | None = None) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        config=cfg,
        channels_config_override=ChannelsConfig(owner_ids=owner_ids or []),
    )


def test_agent_loop_uses_explicit_owner_ids(tmp_path):
    loop = _make_loop(tmp_path, owner_ids=["alice"])
    assert loop._is_owner("alice", "telegram") is True
    assert loop._is_owner("bob", "telegram") is False


def test_agent_loop_non_cli_without_owner_ids_is_not_owner(tmp_path):
    loop = _make_loop(tmp_path)
    assert loop._is_owner("alice", "telegram") is False
    assert loop._is_owner("user", "cli") is True


def test_gateway_warns_when_channels_enabled_but_no_owner_ids():
    """Warning appears when gateway-enabled config has channels but no owner_ids."""
    from unittest.mock import patch

    from src.cli.gateway_cmd import _warn_missing_owner_ids
    from src.config.schema_channels import TelegramConfig

    config = MagicMock()
    config.channels = ChannelsConfig(
        telegram=TelegramConfig(enabled=True, token="fake"),
        owner_ids=[],
    )
    with patch("src.cli.gateway_cmd.logger") as mock_logger:
        _warn_missing_owner_ids(config)
        mock_logger.warning.assert_called_once()
        fmt, enabled = mock_logger.warning.call_args[0]
        assert "owner_ids" in fmt
        assert enabled == "telegram"


def test_gateway_no_warning_when_owner_ids_configured():
    """No warning when owner_ids is configured."""
    from unittest.mock import patch

    from src.cli.gateway_cmd import _warn_missing_owner_ids
    from src.config.schema_channels import TelegramConfig

    config = MagicMock()
    config.channels = ChannelsConfig(
        telegram=TelegramConfig(enabled=True, token="fake"),
        owner_ids=["alice"],
    )
    with patch("src.cli.gateway_cmd.logger") as mock_logger:
        _warn_missing_owner_ids(config)
        mock_logger.warning.assert_not_called()


def test_gateway_warning_follows_channel_registry():
    """Only channels present in the registry should count toward the warning."""
    from unittest.mock import patch

    from src.channels.registry import ChannelSpec
    from src.cli.gateway_cmd import _warn_missing_owner_ids
    from src.config.schema_channels import TelegramConfig

    config = MagicMock()
    config.channels = ChannelsConfig(
        telegram=TelegramConfig(enabled=True, token="fake"),
        owner_ids=[],
    )
    with (
        patch(
            "src.cli.gateway_cmd.CHANNELS",
            (
                ChannelSpec(
                    name="matrix",
                    config_attr="matrix",
                    module="src.channels.matrix",
                    class_name="MatrixChannel",
                ),
            ),
        ),
        patch("src.cli.gateway_cmd.logger") as mock_logger,
    ):
        _warn_missing_owner_ids(config)
        mock_logger.warning.assert_not_called()
