"""Tests for DreamToolRegistry — thin wrapper with policy gating."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.dream.output.artifacts import ArtifactTracker
from src.dream.output.dream_eval import DreamEval
from src.dream.sandbox.tool_policy import DreamToolPolicy
from src.dream.tool_registry import DreamToolRegistry


def _make_base_registry():
    """Build a mock ToolRegistry with read_file, bash, and write_file tools."""
    mock_tool = MagicMock()
    mock_tool.name = "read_file"
    mock_tool.parallel_safe = True
    mock_tool.dedupe_within_turn = False
    mock_tool.to_schema.return_value = {
        "type": "function",
        "function": {"name": "read_file", "parameters": {}},
    }

    bash_tool = MagicMock()
    bash_tool.name = "bash"
    bash_tool.parallel_safe = False
    bash_tool.dedupe_within_turn = False
    bash_tool.to_schema.return_value = {
        "type": "function",
        "function": {"name": "bash", "parameters": {}},
    }

    blocked_tool = MagicMock()
    blocked_tool.name = "write_file"
    blocked_tool.to_schema.return_value = {
        "type": "function",
        "function": {"name": "write_file", "parameters": {}},
    }

    base = MagicMock()
    base._tools = {"read_file": mock_tool, "bash": bash_tool, "write_file": blocked_tool}
    base.get.side_effect = lambda n: base._tools.get(n)
    base.execute = AsyncMock(return_value="tool result")
    return base


def _make_registry(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    policy = DreamToolPolicy(sandbox_root=sandbox, budget_usd=10.0)
    eval_tracker = DreamEval(session_id="test", topic="test")
    artifacts = ArtifactTracker(tmp_path)
    base = _make_base_registry()
    reg = DreamToolRegistry(
        base=base,
        policy=policy,
        eval_tracker=eval_tracker,
        artifacts=artifacts,
        sandbox_root=sandbox,
    )
    return reg, base, policy


class TestGetDefinitions:
    def test_only_allowed_tools(self, tmp_path):
        reg, _, _ = _make_registry(tmp_path)
        defs = reg.get_definitions()
        names = [d["function"]["name"] for d in defs]
        assert "read_file" in names
        assert "bash" in names
        assert "write_file" not in names

    def test_missing_tools_excluded(self, tmp_path):
        reg, _, _ = _make_registry(tmp_path)
        defs = reg.get_definitions()
        names = [d["function"]["name"] for d in defs]
        # memory_search is in ALLOWED_TOOLS but not in base registry
        assert "memory_search" not in names


class TestGet:
    def test_returns_allowed_tool(self, tmp_path):
        reg, _, _ = _make_registry(tmp_path)
        assert reg.get("read_file") is not None

    def test_returns_none_for_blocked(self, tmp_path):
        reg, _, _ = _make_registry(tmp_path)
        assert reg.get("write_file") is None

    def test_returns_none_for_unknown(self, tmp_path):
        reg, _, _ = _make_registry(tmp_path)
        assert reg.get("deploy_nuke") is None


class TestExecute:
    @pytest.mark.asyncio
    async def test_allowed_tool_executes(self, tmp_path):
        reg, base, _ = _make_registry(tmp_path)
        result = await reg.execute("bash", {"command": "ls"})
        assert result == "tool result"
        base.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bash_gets_sandbox_working_dir(self, tmp_path):
        """bash/python must execute in sandbox_root, not main workspace."""
        reg, base, _ = _make_registry(tmp_path)
        await reg.execute("bash", {"command": "echo hi"})
        call_args = base.execute.call_args
        passed_params = call_args[0][1]  # second positional arg is params
        assert "working_dir" in passed_params
        assert passed_params["working_dir"] == str(tmp_path / "sandbox")

    @pytest.mark.asyncio
    async def test_python_gets_sandbox_working_dir(self, tmp_path):
        reg, base, _ = _make_registry(tmp_path)
        await reg.execute("python", {"code": "print(1)"})
        call_args = base.execute.call_args
        passed_params = call_args[0][1]
        assert passed_params["working_dir"] == str(tmp_path / "sandbox")

    @pytest.mark.asyncio
    async def test_blocked_tool_rejected(self, tmp_path):
        reg, base, _ = _make_registry(tmp_path)
        result = await reg.execute("write_file", {"path": "/tmp/x"})
        assert "[Dream policy] Rejected:" in result
        base.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_policy_records_call(self, tmp_path):
        reg, _, policy = _make_registry(tmp_path)
        await reg.execute("bash", {"command": "ls"})
        assert policy.stats["total_calls"] == 1

    @pytest.mark.asyncio
    async def test_web_search_costs_tracked(self, tmp_path):
        reg, _, policy = _make_registry(tmp_path)
        await reg.execute("web_search", {"query": "test"})
        assert policy.stats["web_queries"] == 1
        assert policy.stats["cost_used"] == pytest.approx(0.01)

    @pytest.mark.asyncio
    async def test_artifact_detection(self, tmp_path):
        reg, _, _ = _make_registry(tmp_path)
        sandbox = tmp_path / "sandbox"
        # Create a file in sandbox to simulate tool output
        (sandbox / "output.txt").write_text("hello")
        await reg.execute("bash", {"command": "ls"})
        assert len(reg._artifacts.entries) >= 1
