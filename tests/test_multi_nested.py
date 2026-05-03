"""Tests for nested subagent spawn behavior."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.delegation.types import SubagentResult, SubagentStatus
from src.agent.tool_sets import register_standard_tools
from src.agent.tools.context import ToolContext
from src.agent.tools.registration import ToolRegistrationConfig
from src.agent.tools.registry import ToolRegistry
from src.agent.tools.spawn import AgentTool
from src.agent.tools.subagent_kill import SubagentKillTool
from src.agent.tools.subagent_wait import SubagentWaitTool


class TestSpawnAuthorization:
    def _make_spawn_tool(self):
        from src.agent.subagent import SubagentManager

        manager = MagicMock(spec=SubagentManager)
        manager.executor = MagicMock()
        manager.spawn = AsyncMock(return_value="Subagent [test] started (id: abc).")
        return AgentTool(manager=manager)

    @pytest.mark.asyncio
    async def test_owner_can_spawn(self):
        tool = self._make_spawn_tool()
        registry = ToolRegistry()
        registry.register(tool)
        ctx = ToolContext(sender_is_owner=True)
        result = await registry.execute("agent", {"task": "t"}, context=ctx)
        assert "started" in result.lower()

    @pytest.mark.asyncio
    async def test_non_owner_without_opt_in_blocked(self):
        tool = self._make_spawn_tool()
        registry = ToolRegistry()
        registry.register(tool)
        ctx = ToolContext(sender_is_owner=False, allow_subagent_spawn=False)
        result = await registry.execute("agent", {"task": "t"}, context=ctx)
        assert "restricted" in result.lower()

    @pytest.mark.asyncio
    async def test_non_owner_with_opt_in_allowed(self):
        tool = self._make_spawn_tool()
        registry = ToolRegistry()
        registry.register(tool)
        ctx = ToolContext(
            sender_is_owner=False,
            allow_subagent_spawn=True,
            root_session_key="cli:direct",
            spawn_depth=1,
            subagent_task_id="parent-task",
        )
        result = await registry.execute("agent", {"task": "t"}, context=ctx)
        assert "started" in result.lower()

    @pytest.mark.asyncio
    async def test_spawn_routes_through_manager_facade(self):
        tool = self._make_spawn_tool()
        ctx = ToolContext(
            sender_is_owner=True,
            channel="cli",
            chat_id="direct",
            session_key="cli:direct",
            root_session_key="cli:root",
            subagent_task_id="parent-task",
            spawn_depth=1,
        )
        await tool.execute(task="t", role="explorer", _context=ctx)
        tool._manager.spawn.assert_awaited_once()
        kwargs = tool._manager.spawn.await_args.kwargs
        assert kwargs["root_session_key"] == "cli:root"
        assert kwargs["parent_task_id"] == "parent-task"
        assert kwargs["depth"] == 2

    @pytest.mark.asyncio
    async def test_other_owner_only_tools_still_blocked(self):
        registry = ToolRegistry()
        mock_tool = MagicMock()
        mock_tool.name = "sessions_send"
        mock_tool.owner_only = True
        mock_tool.requires_context = False
        mock_tool.validate_params.return_value = []
        mock_tool.execute = AsyncMock(return_value="sent")
        registry.register(mock_tool)
        ctx = ToolContext(sender_is_owner=False, allow_subagent_spawn=True)
        result = await registry.execute(
            "sessions_send", {"session_key": "k", "message": "m"}, context=ctx
        )
        assert "restricted" in result.lower()


class TestSubagentWaitTool:
    def test_schema(self):
        executor = MagicMock()
        tool = SubagentWaitTool(executor=executor)
        assert tool.name == "subagent_wait"
        params = tool.parameters
        assert "task_id" in params["required"]

    @pytest.mark.asyncio
    async def test_wait_completed(self):
        executor = MagicMock()
        executor.wait = AsyncMock(
            return_value=SubagentResult(
                task_id="t1",
                status=SubagentStatus.COMPLETED,
                role="explorer",
                parent_task_id=None,
                depth=1,
                result="found files",
                elapsed_seconds=1.2,
                tools_used=["glob"],
            )
        )
        tool = SubagentWaitTool(executor=executor)
        raw = await tool.execute(task_id="t1")
        data = json.loads(raw)
        assert data["status"] == "completed"
        assert data["result"] == "found files"
        assert data["elapsed_seconds"] == 1.2

    @pytest.mark.asyncio
    async def test_wait_unknown(self):
        executor = MagicMock()
        executor.wait = AsyncMock(return_value=None)
        tool = SubagentWaitTool(executor=executor)
        raw = await tool.execute(task_id="unknown")
        data = json.loads(raw)
        assert data["error"] is not None


class TestSubagentKillTool:
    def test_schema(self):
        executor = MagicMock()
        tool = SubagentKillTool(executor=executor)
        assert tool.name == "subagent_kill"
        params = tool.parameters
        assert "task_id" in params["required"]

    @pytest.mark.asyncio
    async def test_kill_success(self):
        executor = MagicMock()
        executor.kill = AsyncMock(return_value=True)
        tool = SubagentKillTool(executor=executor)
        raw = await tool.execute(task_id="t1")
        data = json.loads(raw)
        assert data["killed"] is True

    @pytest.mark.asyncio
    async def test_kill_unknown(self):
        executor = MagicMock()
        executor.kill = AsyncMock(return_value=False)
        tool = SubagentKillTool(executor=executor)
        raw = await tool.execute(task_id="nope")
        data = json.loads(raw)
        assert data["killed"] is False


class TestNestedToolRegistration:
    def test_subagent_mode_with_spawn_in_allowed(self, tmp_path):
        registry = ToolRegistry()
        executor = MagicMock()
        config = ToolRegistrationConfig(
            workspace=tmp_path,
            mode="subagent",
            allowed_tools={
                "read_file",
                "agent",
                "subagent_wait",
                "subagent_kill",
                "subagents_list",
            },
            executor=executor,
            subagent_manager=MagicMock(),
        )
        register_standard_tools(registry, config)
        names = registry.tool_names
        assert "agent" in names
        assert "subagent_wait" in names
        assert "subagent_kill" in names
        assert "subagents_list" in names

    def test_subagent_mode_without_spawn_excluded(self, tmp_path):
        registry = ToolRegistry()
        config = ToolRegistrationConfig(
            workspace=tmp_path,
            mode="subagent",
            allowed_tools={"read_file", "list_dir"},
        )
        register_standard_tools(registry, config)
        names = registry.tool_names
        assert "agent" not in names
        assert "subagent_wait" not in names
        assert "subagent_kill" not in names

    def test_spawn_not_auto_added_to_allowlist(self, tmp_path):
        registry = ToolRegistry()
        config = ToolRegistrationConfig(
            workspace=tmp_path,
            mode="subagent",
            allowed_tools={"read_file", "subagent_wait"},
            executor=MagicMock(),
        )
        register_standard_tools(registry, config)
        assert "agent" not in registry.tool_names
