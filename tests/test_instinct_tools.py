"""Tests for instinct → deferred tool activation (I3+I4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.agent.context import ContextBuilder
from src.agent.loop_context import TurnContextAssembler
from src.agent.tools.base import Tool
from src.agent.tools.registry import ToolRegistry
from src.bus.events import InboundMessage


class TestExtractInstinctTools:
    def test_extracts_tools_from_sidecar(self):
        hook_ctx = '<!-- instinct-routing:{"domains":["feishu/wiki"],"skills":[],"tools":["feishu_read","feishu_create"],"selected_primary":"feishu/wiki"} -->'
        tools = TurnContextAssembler.extract_instinct_tools(hook_ctx)
        assert tools == ["feishu_read", "feishu_create"]

    def test_returns_empty_for_no_tools_key(self):
        hook_ctx = '<!-- instinct-routing:{"domains":["coding/general"],"skills":["reference"]} -->'
        tools = TurnContextAssembler.extract_instinct_tools(hook_ctx)
        assert tools == []

    def test_returns_empty_for_no_hook_ctx(self):
        assert TurnContextAssembler.extract_instinct_tools(None) == []
        assert TurnContextAssembler.extract_instinct_tools("") == []

    def test_returns_empty_for_malformed_json(self):
        hook_ctx = "<!-- instinct-routing:{bad json} -->"
        assert TurnContextAssembler.extract_instinct_tools(hook_ctx) == []

    def test_returns_empty_for_non_object_sidecar(self):
        hook_ctx = "<!-- instinct-routing:[] -->"
        assert TurnContextAssembler.extract_instinct_tools(hook_ctx) == []

    def test_returns_empty_for_empty_tools_list(self):
        hook_ctx = '<!-- instinct-routing:{"domains":["feishu/wiki"],"tools":[]} -->'
        assert TurnContextAssembler.extract_instinct_tools(hook_ctx) == []


class _DummyTool(Tool):
    """Minimal tool for testing registry activation."""

    def __init__(self, n: str):
        self._n = n

    @property
    def name(self) -> str:
        return self._n

    @property
    def description(self) -> str:
        return "test"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kw: Any) -> str:
        return "ok"


class TestToolActivation:
    def test_activator_called_for_routed_tools(self):
        """Verify the activation callback pattern works."""
        reg = ToolRegistry()
        reg.register(_DummyTool("feishu_read"), deferred=True)
        reg.register(_DummyTool("feishu_create"), deferred=True)

        # Simulate what build_turn_messages would do
        routed_tools = ["feishu_read", "feishu_create", "nonexistent"]
        for name in routed_tools:
            reg.activate(name)

        defs = {d["function"]["name"] for d in reg.get_definitions()}
        assert "feishu_read" in defs
        assert "feishu_create" in defs

    def test_activate_returns_false_for_unknown(self):
        reg = ToolRegistry()
        assert reg.activate("nonexistent") is False

    def test_activate_idempotent(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("feishu_read"), deferred=True)
        assert reg.activate("feishu_read") is True
        assert reg.activate("feishu_read") is False  # already activated

    @pytest.mark.asyncio
    async def test_build_turn_messages_activates_routed_tools(self, tmp_path: Path):
        assembler = TurnContextAssembler(tmp_path)
        hook_ctx = (
            '<!-- instinct-routing:{"domains":["feishu/wiki"],"skills":[],'
            '"tools":["feishu_read","feishu_create"],"selected_primary":"feishu/wiki"} -->'
        )
        hooks = AsyncMock()
        hooks.run_pre_chat = AsyncMock(return_value=hook_ctx)
        activated: list[str] = []

        messages, _, domains, primary, skills = await assembler.build_turn_messages(
            InboundMessage(channel="cli", sender_id="u1", chat_id="c1", content="wiki"),
            key="cli:c1",
            run_genver=False,
            task_workspace=tmp_path,
            ctx=ContextBuilder(tmp_path),
            history=[],
            hooks=hooks,
            model="test-model",
            memory_config=None,
            memory_search_enabled=False,
            build_structured_recall=AsyncMock(return_value=""),
            maybe_compact=AsyncMock(side_effect=lambda msgs: msgs),
            tool_activator=activated.append,
        )

        assert activated == ["feishu_read", "feishu_create"]
        assert domains == ["feishu/wiki"]
        assert primary == "feishu/wiki"
        assert skills == []
        assert "[Ephemeral Context" in messages[-1]["content"]

    @pytest.mark.asyncio
    async def test_build_turn_messages_samples_memory_tools_after_routing(self, tmp_path: Path):
        assembler = TurnContextAssembler(tmp_path)
        hooks = AsyncMock()
        hooks.run_pre_chat = AsyncMock(
            return_value='<!-- instinct-routing:{"tools":["structured_memory_search"]} -->'
        )
        active_memory_tools: set[str] = set()

        def _activate(name: str) -> bool:
            active_memory_tools.add(name)
            return True

        messages, *_ = await assembler.build_turn_messages(
            InboundMessage(channel="cli", sender_id="u1", chat_id="c1", content="recall"),
            key="cli:c1",
            run_genver=False,
            task_workspace=tmp_path,
            ctx=ContextBuilder(tmp_path),
            history=[],
            hooks=hooks,
            model="test-model",
            memory_config=None,
            memory_search_enabled=False,
            build_structured_recall=AsyncMock(return_value=""),
            maybe_compact=AsyncMock(side_effect=lambda msgs: msgs),
            memory_tool_names=lambda: active_memory_tools,
            tool_activator=_activate,
        )

        assert "structured_memory_search" in messages[0]["content"]
