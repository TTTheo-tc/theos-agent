"""Tests for tool profiles and groups."""

from pathlib import Path

from src.agent.tool_sets import register_standard_tools
from src.agent.tools.registration import ToolRegistrationConfig
from src.agent.tools.registry import ToolRegistry
from src.agent.tools.tool_profiles import (
    ALWAYS_ON_TOOLS,
    PROFILES,
    TOOL_GROUPS,
    expand_groups,
    profile_allows_any,
    profile_allows_tool,
    resolve_profile,
)


def test_always_on_tools_is_a_set():
    assert isinstance(ALWAYS_ON_TOOLS, frozenset)


def test_always_on_tools_contains_core_tools():
    for name in (
        "read_file",
        "grep",
        "glob",
        "list_dir",
        "memory_search",
        "tool_search",
    ):
        assert name in ALWAYS_ON_TOOLS, f"{name} should be always-on"


def test_always_on_tools_excludes_deferred_tools():
    for name in (
        "feishu_read",
        "stock_analysis",
        "browser",
        "tts",
        "pdf",
        "image_analyze",
        "vendor_study",
        "http_request",
        "write_file",
        "edit_file",
        "bash",
        "web_search",
        "web_fetch",
        "message",
        "agent",
        "todo",
        "capability_search",
        "enter_plan_mode",
        "exit_plan_mode",
    ):
        assert name not in ALWAYS_ON_TOOLS, f"{name} should be deferred"


def test_tool_groups_exact_membership():
    assert TOOL_GROUPS == {
        "group:fs": {
            "read_file",
            "write_file",
            "edit_file",
            "multi_edit",
            "apply_patch",
            "glob",
            "grep",
            "list_dir",
        },
        "group:notebook": {"notebook_read", "notebook_edit"},
        "group:shell": {"bash", "process"},
        "group:web": {"web_search", "web_fetch", "http_request", "image_search", "browser"},
        "group:memory": {
            "memory_search",
            "memory_get",
            "structured_memory_search",
            "research_note_get",
            "task_memory_get",
            "domain_rule_get",
        },
        "group:discovery": {"capability_search", "skill_search", "mcp_search"},
        "group:comms": {
            "message",
            "agent",
            "cron",
            "sessions_list",
            "sessions_history",
            "sessions_send",
            "subagents_list",
        },
        "group:analysis": {"stock_analysis", "vendor_study", "image_analyze", "pdf", "tts"},
        "group:feishu": {
            "feishu_read",
            "feishu_search",
            "feishu_list",
            "feishu_spaces",
            "feishu_calendar",
            "feishu_edit",
            "feishu_create",
            "feishu_send",
            "feishu_comments",
            "feishu_download",
            "feishu_info",
            "feishu_auth",
            "feishu_sheet",
            "feishu_task",
            "feishu_perm",
            "feishu_chat",
            "feishu_file",
            "feishu_contact",
        },
    }


def test_expand_groups_none_passthrough():
    assert expand_groups(None) is None


def test_expand_groups_no_groups():
    names = {"read_file", "bash"}
    assert expand_groups(names) == names


def test_expand_groups_single_group():
    result = expand_groups({"group:fs"})
    assert result == TOOL_GROUPS["group:fs"]
    assert "read_file" in result
    assert "write_file" in result


def test_expand_groups_discovery_group():
    result = expand_groups({"group:discovery"})
    assert result == {"capability_search", "skill_search", "mcp_search"}


def test_expand_groups_mixed():
    result = expand_groups({"group:web", "bash"})
    assert "web_search" in result
    assert "web_fetch" in result
    assert "http_request" in result
    assert "bash" in result


def test_resolve_profile_full():
    assert resolve_profile("full") is None


def test_resolve_profile_minimal():
    result = resolve_profile("minimal")
    assert result is not None
    assert result == set(ALWAYS_ON_TOOLS)
    assert "read_file" in result
    assert "list_dir" in result
    assert "glob" in result
    assert "grep" in result
    assert "memory_search" in result
    assert "tool_search" in result
    assert "bash" not in result
    assert "web_search" not in result
    assert "write_file" not in result


def test_resolve_profile_coding():
    result = resolve_profile("coding")
    assert result is not None
    assert "read_file" in result
    assert "write_file" in result
    assert "bash" in result
    assert "web_search" in result
    assert "tool_search" in result
    assert "capability_search" in result
    assert "skill_search" in result
    assert "mcp_search" in result


def test_resolve_profile_readonly_keeps_tool_search():
    result = resolve_profile("readonly")
    assert result is not None
    assert "tool_search" in result
    assert "browser" in result
    assert "notebook_read" in result


def test_profiles_exact_membership():
    assert PROFILES["full"] is None
    assert PROFILES["minimal"] == set(ALWAYS_ON_TOOLS)
    assert PROFILES["coding"] == {
        "read_file",
        "write_file",
        "edit_file",
        "multi_edit",
        "apply_patch",
        "glob",
        "grep",
        "list_dir",
        "notebook_read",
        "notebook_edit",
        "bash",
        "process",
        "web_search",
        "web_fetch",
        "http_request",
        "image_search",
        "browser",
        "memory_search",
        "memory_get",
        "structured_memory_search",
        "research_note_get",
        "task_memory_get",
        "domain_rule_get",
        "tool_search",
        "capability_search",
        "skill_search",
        "mcp_search",
        "enter_plan_mode",
        "exit_plan_mode",
        "feishu_read",
        "feishu_search",
        "feishu_list",
        "feishu_spaces",
        "feishu_calendar",
        "feishu_info",
        "feishu_comments",
        "todo",
        "agent",
        "cron",
        "image_analyze",
        "pdf",
    }
    assert PROFILES["messaging"] == {
        "message",
        "web_search",
        "web_fetch",
        "memory_search",
        "memory_get",
        "structured_memory_search",
        "research_note_get",
        "task_memory_get",
        "domain_rule_get",
        "tool_search",
        "capability_search",
        "skill_search",
        "mcp_search",
        "enter_plan_mode",
        "exit_plan_mode",
        "feishu_read",
        "feishu_search",
        "feishu_list",
        "feishu_spaces",
        "feishu_calendar",
        "feishu_send",
    }
    assert PROFILES["readonly"] == {
        "read_file",
        "list_dir",
        "glob",
        "grep",
        "notebook_read",
        "web_search",
        "web_fetch",
        "browser",
        "tool_search",
        "capability_search",
        "skill_search",
        "mcp_search",
        "enter_plan_mode",
        "exit_plan_mode",
    }


def test_resolve_profile_unknown_raises():
    import pytest

    with pytest.raises(ValueError, match="Unknown tool profile"):
        resolve_profile("nonexistent")


def test_resolve_profile_extra_allow():
    result = resolve_profile("minimal", extra_allow={"group:memory"})
    assert result is not None
    assert "read_file" in result  # from minimal
    assert "memory_search" in result  # from extra_allow


def test_resolve_profile_extra_deny():
    result = resolve_profile("coding", extra_deny={"bash"})
    assert result is not None
    assert "bash" not in result
    assert "read_file" in result


def test_resolve_profile_extra_deny_group():
    result = resolve_profile("coding", extra_deny={"group:web"})
    assert result is not None
    assert "web_search" not in result
    assert "web_fetch" not in result
    assert "read_file" in result


def test_profile_allows_helpers():
    assert profile_allows_tool(None, "bash") is True
    assert profile_allows_tool("minimal", "read_file") is True
    assert profile_allows_tool("minimal", "bash") is False
    assert profile_allows_any("minimal", {"agent", "grep"}) is True
    assert profile_allows_any("minimal", {"agent", "bash"}) is False
    assert profile_allows_tool("unknown-profile", "bash") is True


def test_all_profiles_have_valid_tool_names():
    """Ensure profile tool names don't reference unknown groups."""
    for name, tools in PROFILES.items():
        if tools is None:
            continue
        for tool in tools:
            assert not tool.startswith(
                "group:"
            ), f"Profile {name!r} contains unexpanded group ref {tool!r}"


def test_register_standard_tools_profile_is_opt_in(tmp_path: Path):
    registry = ToolRegistry()
    register_standard_tools(registry, ToolRegistrationConfig(workspace=tmp_path, mode="single"))
    assert "write_file" in registry.tool_names


def test_register_standard_tools_explicit_profile_filters_tools(tmp_path: Path):
    registry = ToolRegistry()
    register_standard_tools(
        registry, ToolRegistrationConfig(workspace=tmp_path, mode="single", profile="minimal")
    )
    assert "read_file" in registry.tool_names
    assert "glob" in registry.tool_names
    assert "grep" in registry.tool_names
    assert "mcp_search" not in registry.tool_names
    assert "capability_search" not in registry.tool_names
    assert "skill_search" not in registry.tool_names
    assert "write_file" not in registry.tool_names


def test_register_standard_tools_registers_mcp_search_when_profile_allows(tmp_path: Path):
    class _FakeManager:
        def catalog_snapshot(self):
            return []

    registry = ToolRegistry()
    register_standard_tools(
        registry,
        ToolRegistrationConfig(
            workspace=tmp_path,
            mode="single",
            profile="readonly",
            mcp_manager=_FakeManager(),
        ),
    )
    assert "capability_search" in registry.tool_names
    assert "mcp_search" in registry.tool_names


def test_register_standard_tools_defers_non_always_on(tmp_path: Path):
    """Non-always-on tools should be in deferred pool, not in definitions."""
    registry = ToolRegistry()
    register_standard_tools(registry, ToolRegistrationConfig(workspace=tmp_path, mode="single"))

    defs = registry.get_definitions()
    def_names = {d["function"]["name"] for d in defs}

    # Always-on tools must be in definitions
    assert "read_file" in def_names
    assert "list_dir" in def_names
    assert "glob" in def_names
    assert "grep" in def_names
    assert "tool_search" in def_names

    # Deferred tools must NOT be in definitions
    deferred_summary = registry.get_deferred_summary()
    deferred_names = {s["name"] for s in deferred_summary}

    # At least some tools should be deferred
    assert len(deferred_names) > 0

    # Deferred tools should not appear in definitions
    assert def_names.isdisjoint(deferred_names)


def test_register_standard_tools_deferred_still_respects_should(tmp_path: Path):
    """Denied tools should not appear in deferred pool either."""
    registry = ToolRegistry()
    register_standard_tools(
        registry,
        ToolRegistrationConfig(
            workspace=tmp_path,
            mode="single",
            deny_tools={"http_request", "image_search"},
        ),
    )

    deferred_summary = registry.get_deferred_summary()
    deferred_names = {s["name"] for s in deferred_summary}
    assert "http_request" not in deferred_names
    assert "image_search" not in deferred_names
