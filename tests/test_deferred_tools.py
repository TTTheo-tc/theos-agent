"""Tests for deferred tool loading infrastructure in ToolRegistry."""

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
# TestDeferredRegistration
# ---------------------------------------------------------------------------


class TestDeferredRegistration:
    def test_register_deferred_not_in_definitions(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("alpha"), deferred=True)
        defs = reg.get_definitions()
        names = [d["function"]["name"] for d in defs]
        assert "alpha" not in names

    def test_register_deferred_in_get(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("alpha"), deferred=True)
        assert reg.get("alpha") is not None

    def test_register_deferred_in_has(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("alpha"), deferred=True)
        assert reg.has("alpha")

    def test_register_normal_in_definitions(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("beta"))
        defs = reg.get_definitions()
        names = [d["function"]["name"] for d in defs]
        assert "beta" in names

    def test_activate_moves_to_active(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("alpha"), deferred=True)
        assert reg.activate("alpha") is True
        defs = reg.get_definitions()
        names = [d["function"]["name"] for d in defs]
        assert "alpha" in names

    def test_activate_nonexistent_returns_false(self):
        reg = ToolRegistry()
        assert reg.activate("no_such_tool") is False

    def test_activate_already_active_tool_returns_false(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("beta"))
        assert reg.activate("beta") is False

    def test_activate_idempotent(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("alpha"), deferred=True)
        assert reg.activate("alpha") is True
        assert reg.activate("alpha") is False


# ---------------------------------------------------------------------------
# TestAutoActivationOnExecute
# ---------------------------------------------------------------------------


class TestAutoActivationOnExecute:
    async def test_execute_auto_activates_deferred(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("alpha"), deferred=True)
        result = await reg.execute("alpha", {"x": "hello"})
        assert result == "executed alpha"
        # Should now appear in definitions
        defs = reg.get_definitions()
        names = [d["function"]["name"] for d in defs]
        assert "alpha" in names

    async def test_execute_unknown_tool_error(self):
        reg = ToolRegistry()
        result = await reg.execute("nonexistent", {})
        assert "not found" in result


# ---------------------------------------------------------------------------
# TestRegistryContainment
# ---------------------------------------------------------------------------


class TestRegistryContainment:
    def test_tool_names_includes_both_pools(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("active_one"))
        reg.register(_DummyTool("deferred_one"), deferred=True)
        names = reg.tool_names
        assert "active_one" in names
        assert "deferred_one" in names

    def test_active_tool_names_excludes_deferred_pool(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("active_one"))
        reg.register(_DummyTool("deferred_one"), deferred=True)

        assert reg.active_tool_names() == ["active_one"]

    def test_active_tool_names_respects_plan_mode_filter(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("read_file"))
        reg.register(_DummyTool("write_file"))
        reg.enter_plan_mode()

        assert reg.active_tool_names() == ["read_file"]

    def test_len_counts_both_pools(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("a"))
        reg.register(_DummyTool("b"), deferred=True)
        assert len(reg) == 2

    def test_len_no_double_count_after_activate(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("a"), deferred=True)
        assert len(reg) == 1
        reg.activate("a")
        assert len(reg) == 1

    def test_contains_both_pools(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("a"))
        reg.register(_DummyTool("b"), deferred=True)
        assert "a" in reg
        assert "b" in reg
        assert "c" not in reg

    def test_unregister_removes_from_deferred(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("a"), deferred=True)
        reg.unregister("a")
        assert "a" not in reg
        assert not reg.has("a")


# ---------------------------------------------------------------------------
# TestSearchDeferred
# ---------------------------------------------------------------------------


class TestSearchDeferred:
    def test_search_full_name_match(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("web_search", desc="Search the web"), deferred=True)
        reg.register(_DummyTool("read_file", desc="Read a file"), deferred=True)
        results = reg.search_deferred("web_search")
        assert results[0]["name"] == "web_search"

    def test_search_partial_name_part(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("web_search", desc="Search the web"), deferred=True)
        reg.register(_DummyTool("web_fetch", desc="Fetch web pages"), deferred=True)
        reg.register(_DummyTool("read_file", desc="Read a file"), deferred=True)
        results = reg.search_deferred("web")
        names = [r["name"] for r in results]
        assert "web_search" in names
        assert "web_fetch" in names

    def test_search_description_match(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("alpha", desc="Search the web for things"), deferred=True)
        results = reg.search_deferred("web")
        assert len(results) >= 1
        assert results[0]["name"] == "alpha"

    def test_search_excludes_activated(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("alpha", desc="An alpha tool"), deferred=True)
        reg.register(_DummyTool("beta", desc="A beta tool"), deferred=True)
        reg.activate("alpha")
        results = reg.search_deferred("tool")
        names = [r["name"] for r in results]
        assert "alpha" not in names
        assert "beta" in names

    def test_search_excludes_active_pool(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("active_one", desc="An active tool"))
        reg.register(_DummyTool("deferred_one", desc="A deferred tool"), deferred=True)
        results = reg.search_deferred("tool")
        names = [r["name"] for r in results]
        assert "active_one" not in names
        assert "deferred_one" in names

    def test_search_max_results(self):
        reg = ToolRegistry()
        for i in range(20):
            reg.register(_DummyTool(f"tool_{i}", desc="A test tool"), deferred=True)
        results = reg.search_deferred("tool", max_results=5)
        assert len(results) <= 5

    def test_search_no_results(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("alpha"), deferred=True)
        results = reg.search_deferred("zzzznotfound")
        assert results == []


# ---------------------------------------------------------------------------
# TestDeferredSummary
# ---------------------------------------------------------------------------


class TestDeferredSummary:
    def test_summary_returns_unactivated(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("alpha", desc="Alpha desc"), deferred=True)
        reg.register(_DummyTool("beta", desc="Beta desc"), deferred=True)
        summary = reg.get_deferred_summary()
        names = [s["name"] for s in summary]
        assert "alpha" in names
        assert "beta" in names

    def test_summary_excludes_activated(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("alpha", desc="Alpha desc"), deferred=True)
        reg.register(_DummyTool("beta", desc="Beta desc"), deferred=True)
        reg.activate("alpha")
        summary = reg.get_deferred_summary()
        names = [s["name"] for s in summary]
        assert "alpha" not in names
        assert "beta" in names

    def test_summary_has_name_and_description(self):
        reg = ToolRegistry()
        reg.register(_DummyTool("alpha", desc="Alpha desc"), deferred=True)
        summary = reg.get_deferred_summary()
        assert summary[0]["name"] == "alpha"
        assert summary[0]["description"] == "Alpha desc"


# ---------------------------------------------------------------------------
# TestToolSearchTool
# ---------------------------------------------------------------------------


class TestToolSearchTool:
    @pytest.mark.asyncio
    async def test_search_returns_matching_tools(self):
        from src.agent.tools.tool_search import ToolSearchTool

        reg = ToolRegistry()
        reg.register(_DummyTool("feishu_read", "Read Feishu documents."), deferred=True)
        reg.register(_DummyTool("feishu_send", "Send Feishu messages."), deferred=True)
        reg.register(_DummyTool("stock_analysis", "Analyze stocks."), deferred=True)
        tool = ToolSearchTool(registry=reg)
        result = await tool.execute(query="feishu")
        assert "feishu_read" in result
        assert "feishu_send" in result
        assert "stock_analysis" not in result

    @pytest.mark.asyncio
    async def test_search_does_not_activate_matched_tools(self):
        from src.agent.tools.tool_search import ToolSearchTool

        reg = ToolRegistry()
        reg.register(_DummyTool("pdf", "Read PDF files."), deferred=True)
        tool = ToolSearchTool(registry=reg)
        result = await tool.execute(query="pdf")
        assert "select:pdf" in result
        defs = reg.get_definitions()
        names = [d["function"]["name"] for d in defs]
        assert "pdf" not in names

    @pytest.mark.asyncio
    async def test_search_with_no_matches(self):
        from src.agent.tools.tool_search import ToolSearchTool

        reg = ToolRegistry()
        reg.register(_DummyTool("bash", "Run commands."))
        tool = ToolSearchTool(registry=reg)
        result = await tool.execute(query="nonexistent_xyz")
        assert "no matching" in result.lower() or "0 tool" in result.lower()

    @pytest.mark.asyncio
    async def test_select_syntax_activates_by_name(self):
        from src.agent.tools.tool_search import ToolSearchTool

        reg = ToolRegistry()
        reg.register(_DummyTool("pdf", "Read PDFs."), deferred=True)
        reg.register(_DummyTool("tts", "Text to speech."), deferred=True)
        tool = ToolSearchTool(registry=reg)
        result = await tool.execute(query="select:pdf,tts")
        assert "pdf" in result
        assert "tts" in result
        defs = reg.get_definitions()
        names = [d["function"]["name"] for d in defs]
        assert "pdf" in names
        assert "tts" in names

    def test_tool_search_is_not_parallel_safe(self):
        from src.agent.tools.tool_search import ToolSearchTool

        reg = ToolRegistry()
        tool = ToolSearchTool(registry=reg)

        assert tool.parallel_safe is False

    @pytest.mark.asyncio
    async def test_select_memory_tool_returns_recall_policy(self):
        from src.agent.tools.tool_search import ToolSearchTool

        reg = ToolRegistry()
        reg.register(_DummyTool("structured_memory_search", "Search structured memory."), deferred=True)
        tool = ToolSearchTool(registry=reg)

        result = await tool.execute(query="select:structured_memory_search")

        assert "structured_memory_search" in result
        assert "historical recall" in result
        assert "`structured_memory_search` before answering" in result

    @pytest.mark.asyncio
    async def test_select_active_memory_tool_still_returns_recall_policy(self):
        from src.agent.tools.tool_search import ToolSearchTool

        reg = ToolRegistry()
        reg.register(_DummyTool("memory_search", "Search memory."))
        tool = ToolSearchTool(registry=reg)

        result = await tool.execute(query="select:memory_search")

        assert "Already active: memory_search" in result
        assert "historical recall" in result
        assert "`memory_search` before answering" in result

    def test_memory_recall_hint_ignores_non_memory_tools(self):
        from src.agent.tools.tool_search import _memory_recall_hint

        assert _memory_recall_hint(["pdf", "tts"]) == ""
        assert "`memory_search` before answering" in _memory_recall_hint(["pdf", "memory_search"])

    @pytest.mark.asyncio
    async def test_select_already_active_tool_is_not_counted_as_activated(self):
        from src.agent.tools.tool_search import ToolSearchTool

        reg = ToolRegistry()
        reg.register(_DummyTool("read_file", "Read files."))
        reg.register(_DummyTool("pdf", "Read PDFs."), deferred=True)
        tool = ToolSearchTool(registry=reg)

        result = await tool.execute(query="select:read_file,pdf")

        assert "**Activated 1 new tool(s):**" in result
        assert "- **pdf**: Read PDFs." in result
        assert "Already active: read_file" in result


# ---------------------------------------------------------------------------
# TestEndToEnd
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """Full registration -> search -> activate -> execute flow."""

    def test_full_registration_has_deferred_tools(self, tmp_path):
        from src.agent.tool_sets import register_standard_tools
        from src.agent.tools.registration import ToolRegistrationConfig

        reg = ToolRegistry()
        register_standard_tools(reg, ToolRegistrationConfig(workspace=tmp_path, mode="single"))

        defs = reg.get_definitions()
        def_names = {d["function"]["name"] for d in defs}
        deferred = reg.get_deferred_summary()
        deferred_names = {s["name"] for s in deferred}

        # Always-on tools in definitions
        assert "read_file" in def_names
        assert "bash" in deferred_names
        assert "tool_search" in def_names

        # Deferred tools not in definitions
        assert def_names.isdisjoint(deferred_names)

        # Total tools = always-on + deferred
        total = len(def_names) + len(deferred_names)
        assert total > 15, f"Expected >15 total tools, got {total}"

    @pytest.mark.asyncio
    async def test_search_then_execute_deferred_tool(self):
        """Search for a deferred tool, then execute it."""
        reg = ToolRegistry()
        reg.register(_DummyTool("always_tool"))
        reg.register(_DummyTool("deferred_tool", "A lazy tool."), deferred=True)

        from src.agent.tools.tool_search import ToolSearchTool

        search = ToolSearchTool(registry=reg)
        reg.register(search)

        # Before search: deferred_tool not in definitions
        defs_before = {d["function"]["name"] for d in reg.get_definitions()}
        assert "deferred_tool" not in defs_before

        # Search returns a candidate but does not activate it
        result = await search.execute(query="deferred")
        assert "select:deferred_tool" in result

        # After search: deferred_tool still not in definitions
        defs_after_search = {d["function"]["name"] for d in reg.get_definitions()}
        assert "deferred_tool" not in defs_after_search

        # Explicit selection activates it
        await search.execute(query="select:deferred_tool")
        defs_after_select = {d["function"]["name"] for d in reg.get_definitions()}
        assert "deferred_tool" in defs_after_select

        # Can execute it
        result = await reg.execute("deferred_tool", {"x": "test"})
        assert result == "executed deferred_tool"

    @pytest.mark.asyncio
    async def test_direct_execute_without_search(self):
        """Direct execution of deferred tool auto-activates it."""
        reg = ToolRegistry()
        reg.register(_DummyTool("lazy"), deferred=True)

        result = await reg.execute("lazy", {"x": "val"})
        assert result == "executed lazy"

        defs = {d["function"]["name"] for d in reg.get_definitions()}
        assert "lazy" in defs

    def test_profile_filtering_applies_to_deferred_too(self, tmp_path):
        """Profile restrictions should prevent tools from entering deferred pool."""
        from src.agent.tool_sets import register_standard_tools
        from src.agent.tools.registration import ToolRegistrationConfig

        reg = ToolRegistry()
        register_standard_tools(
            reg, ToolRegistrationConfig(workspace=tmp_path, mode="single", profile="readonly")
        )

        assert not reg.has("write_file")
        assert not reg.has("edit_file")
        assert reg.has("read_file")
        assert reg.has("grep")

    def test_readonly_profile_keeps_tool_search_for_deferred_access(self, tmp_path):
        """Profiles with deferred tools must expose tool_search to activate them."""
        from src.agent.tool_sets import register_standard_tools
        from src.agent.tools.registration import ToolRegistrationConfig

        reg = ToolRegistry()
        register_standard_tools(
            reg, ToolRegistrationConfig(workspace=tmp_path, mode="single", profile="readonly")
        )

        defs = {d["function"]["name"] for d in reg.get_definitions()}
        deferred = {s["name"] for s in reg.get_deferred_summary()}

        assert "tool_search" in defs
        assert "browser" in deferred
        assert "notebook_read" in deferred
