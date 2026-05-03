"""Tests for plan mode infrastructure in ToolRegistry and plan mode tools."""

from typing import Any

import pytest

from src.agent.tools.base import Tool
from src.agent.tools.registry import ToolRegistry


class _DummyTool(Tool):
    def __init__(self, tool_name: str, desc: str = "A test tool."):
        self._name = tool_name
        self._desc = desc

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._desc

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    async def execute(self, **kwargs: Any) -> str:
        return f"executed {self._name}"


# ---------------------------------------------------------------------------
# TestPlanModeBasics
# ---------------------------------------------------------------------------


class TestPlanModeBasics:
    def test_plan_mode_default_off(self):
        reg = ToolRegistry()
        assert reg.plan_mode is False

    def test_enter_plan_mode(self):
        reg = ToolRegistry()
        reg.enter_plan_mode()
        assert reg.plan_mode is True

    def test_exit_plan_mode(self):
        reg = ToolRegistry()
        reg.enter_plan_mode()
        reg.exit_plan_mode()
        assert reg.plan_mode is False

    def test_exit_without_enter_is_noop(self):
        reg = ToolRegistry()
        reg.exit_plan_mode()  # should not raise
        assert reg.plan_mode is False


# ---------------------------------------------------------------------------
# TestPlanModeDefinitions
# ---------------------------------------------------------------------------


class TestPlanModeDefinitions:
    def _make_registry(self) -> ToolRegistry:
        reg = ToolRegistry()
        reg.register(_DummyTool("read_file"))
        reg.register(_DummyTool("write_file"))
        reg.register(_DummyTool("bash"))
        reg.register(_DummyTool("exit_plan_mode"))
        reg.register(_DummyTool("grep"))
        return reg

    def test_plan_mode_filters_out_write_tools(self):
        reg = self._make_registry()
        reg.enter_plan_mode()
        defs = reg.get_definitions()
        names = {d["function"]["name"] for d in defs}
        assert "write_file" not in names
        assert "bash" not in names

    def test_plan_mode_keeps_read_tools(self):
        reg = self._make_registry()
        reg.enter_plan_mode()
        defs = reg.get_definitions()
        names = {d["function"]["name"] for d in defs}
        assert "read_file" in names
        assert "grep" in names

    def test_plan_mode_keeps_exit_plan_mode(self):
        reg = self._make_registry()
        reg.enter_plan_mode()
        defs = reg.get_definitions()
        names = {d["function"]["name"] for d in defs}
        assert "exit_plan_mode" in names

    def test_exit_plan_mode_restores_all_definitions(self):
        reg = self._make_registry()
        defs_before = {d["function"]["name"] for d in reg.get_definitions()}
        reg.enter_plan_mode()
        reg.exit_plan_mode()
        defs_after = {d["function"]["name"] for d in reg.get_definitions()}
        assert defs_before == defs_after


# ---------------------------------------------------------------------------
# TestPlanModeExecution
# ---------------------------------------------------------------------------


class TestPlanModeExecution:
    async def test_blocks_write_file_execute(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("write_file"))
        reg.enter_plan_mode()
        result = await reg.execute("write_file", {"x": "hello"})
        assert "plan mode" in result.lower()

    async def test_allows_read_file_execute(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("read_file"))
        reg.enter_plan_mode()
        result = await reg.execute("read_file", {"x": "hello"})
        assert result == "executed read_file"

    async def test_allows_exit_plan_mode_execute(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("exit_plan_mode"))
        reg.enter_plan_mode()
        result = await reg.execute("exit_plan_mode", {})
        assert result == "executed exit_plan_mode"

    async def test_blocks_deferred_write_tool_in_plan_mode(self):
        """A deferred tool not in PLAN_MODE_TOOLS should be blocked even after auto-activation."""
        reg = ToolRegistry()
        reg.register(_DummyTool("write_file"), deferred=True)
        reg.enter_plan_mode()
        result = await reg.execute("write_file", {"x": "hello"})
        assert "plan mode" in result.lower()

    async def test_allows_deferred_read_tool_in_plan_mode(self):
        """A deferred tool in PLAN_MODE_TOOLS should auto-activate and execute."""
        reg = ToolRegistry()
        reg.register(_DummyTool("read_file"), deferred=True)
        reg.enter_plan_mode()
        result = await reg.execute("read_file", {"x": "hello"})
        assert result == "executed read_file"


# ---------------------------------------------------------------------------
# TestPlanModeTools — tests for EnterPlanModeTool / ExitPlanModeTool
# ---------------------------------------------------------------------------


class TestPlanModeTools:
    @pytest.mark.asyncio
    async def test_enter_plan_mode_tool(self):
        from src.agent.tools.plan_mode import EnterPlanModeTool

        reg = ToolRegistry()
        tool = EnterPlanModeTool(registry=reg)
        result = await tool.execute()
        assert "plan mode" in result.lower()
        assert reg.plan_mode is True

    @pytest.mark.asyncio
    async def test_exit_plan_mode_tool(self):
        from src.agent.tools.plan_mode import ExitPlanModeTool

        reg = ToolRegistry()
        tool = ExitPlanModeTool(registry=reg)
        reg.enter_plan_mode()
        result = await tool.execute()
        assert reg.plan_mode is False
        assert "restored" in result.lower()

    @pytest.mark.asyncio
    async def test_enter_when_already_in_plan_mode(self):
        from src.agent.tools.plan_mode import EnterPlanModeTool

        reg = ToolRegistry()
        tool = EnterPlanModeTool(registry=reg)
        reg.enter_plan_mode()
        result = await tool.execute()
        assert "already" in result.lower()

    @pytest.mark.asyncio
    async def test_exit_when_not_in_plan_mode(self):
        from src.agent.tools.plan_mode import ExitPlanModeTool

        reg = ToolRegistry()
        tool = ExitPlanModeTool(registry=reg)
        result = await tool.execute()
        assert "not in" in result.lower()

    def test_enter_plan_mode_tool_properties(self):
        from src.agent.tools.plan_mode import EnterPlanModeTool

        reg = ToolRegistry()
        tool = EnterPlanModeTool(registry=reg)
        assert tool.name == "enter_plan_mode"
        assert tool.parallel_safe is True
        assert tool.parameters["type"] == "object"

    def test_exit_plan_mode_tool_properties(self):
        from src.agent.tools.plan_mode import ExitPlanModeTool

        reg = ToolRegistry()
        tool = ExitPlanModeTool(registry=reg)
        assert tool.name == "exit_plan_mode"
        assert tool.parallel_safe is True
        assert tool.parameters["type"] == "object"
