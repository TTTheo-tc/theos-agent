"""Declarative tool profiles and groups.

Profiles define named sets of tools. Groups provide symbolic shorthand
(e.g. ``group:fs``) that expands to concrete tool names.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Tool groups — symbolic references that expand to concrete tool names
# ---------------------------------------------------------------------------

TOOL_GROUPS: dict[str, set[str]] = {
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
    "group:notebook": {
        "notebook_read",
        "notebook_edit",
    },
    "group:shell": {
        "bash",
        "process",
    },
    "group:web": {
        "web_search",
        "web_fetch",
        "http_request",
        "image_search",
        "browser",
    },
    "group:memory": {
        "memory_search",
        "memory_get",
        "structured_memory_search",
        "research_note_get",
        "task_memory_get",
        "domain_rule_get",
    },
    "group:discovery": {
        "capability_search",
        "skill_search",
        "mcp_search",
    },
    "group:comms": {
        "message",
        "agent",
        "cron",
        "sessions_list",
        "sessions_history",
        "sessions_send",
        "subagents_list",
    },
    "group:analysis": {
        "stock_analysis",
        "vendor_study",
        "image_analyze",
        "pdf",
        "tts",
    },
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

# ---------------------------------------------------------------------------
# Always-on tools — sent to the API on every call.
# Everything else is deferred and activated via tool_search or auto-activation.
# ---------------------------------------------------------------------------

ALWAYS_ON_TOOLS: frozenset[str] = frozenset(
    {
        # Core read-only filesystem
        "read_file",
        "list_dir",
        "glob",
        "grep",
        # Core memory
        "memory_search",
        # Discovery (always-on so model can find deferred tools)
        "tool_search",
    }
)


def expand_groups(names: set[str] | None) -> set[str] | None:
    """Expand symbolic group references to concrete tool names.

    Returns ``None`` if *names* is ``None`` (meaning "all tools").
    """
    if names is None:
        return None
    result: set[str] = set()
    for name in names:
        if name in TOOL_GROUPS:
            result.update(TOOL_GROUPS[name])
        else:
            result.add(name)
    return result


# ---------------------------------------------------------------------------
# Named profiles — each maps to a set of tool names (None = all tools)
# ---------------------------------------------------------------------------

PROFILES: dict[str, set[str] | None] = {
    "full": None,  # No restrictions
    "minimal": {
        "read_file",
        "list_dir",
        "glob",
        "grep",
        "memory_search",
        "tool_search",
    },
    "coding": {
        # fs
        "read_file",
        "write_file",
        "edit_file",
        "multi_edit",
        "apply_patch",
        "glob",
        "grep",
        "list_dir",
        # notebook
        "notebook_read",
        "notebook_edit",
        # shell
        "bash",
        "process",
        # web
        "web_search",
        "web_fetch",
        "http_request",
        "image_search",
        "browser",
        # memory
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
        # feishu (read-only)
        "feishu_read",
        "feishu_search",
        "feishu_list",
        "feishu_spaces",
        "feishu_calendar",
        "feishu_info",
        "feishu_comments",
        # misc
        "todo",
        "agent",
        "cron",
        "image_analyze",
        "pdf",
    },
    "messaging": {
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
        # feishu (knowledge retrieval + messaging)
        "feishu_read",
        "feishu_search",
        "feishu_list",
        "feishu_spaces",
        "feishu_calendar",
        "feishu_send",
    },
    "readonly": {
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
    },
}


def resolve_profile(
    profile: str,
    *,
    extra_allow: set[str] | None = None,
    extra_deny: set[str] | None = None,
) -> set[str] | None:
    """Resolve a profile name to a concrete tool name set.

    Parameters
    ----------
    profile
        A key in :data:`PROFILES`.
    extra_allow
        Additional tool names (or group refs) to add on top of the profile.
    extra_deny
        Tool names (or group refs) to remove from the resolved set.

    Returns ``None`` when the resolved set means "all tools" and no deny
    filter is applied.
    """
    base = PROFILES.get(profile)
    if base is None and profile not in PROFILES:
        raise ValueError(f"Unknown tool profile: {profile!r}. Available: {sorted(PROFILES)}")

    # Expand groups in base
    result = expand_groups(base)

    # Merge extra_allow
    if extra_allow:
        expanded_allow = expand_groups(extra_allow) or set()
        if result is None:
            # "full" profile + extra_allow is still "full"
            pass
        else:
            result = result | expanded_allow

    # Apply deny
    if extra_deny:
        expanded_deny = expand_groups(extra_deny) or set()
        if result is None:
            # Can't deny from "all" without enumerating — return None and let
            # the caller handle deny at registration time
            return None
        result = result - expanded_deny

    return result


def profile_allows_any(profile: str | None, names: set[str]) -> bool:
    """Return whether *profile* allows at least one tool in *names*.

    ``None`` and unknown profiles are treated as permissive for compatibility
    with existing callers that use profile checks as optional hints.
    """
    if profile is None:
        return True
    try:
        profile_set = resolve_profile(profile)
    except ValueError:
        return True
    return profile_set is None or any(name in profile_set for name in names)


def profile_allows_tool(profile: str | None, tool_name: str) -> bool:
    """Return whether *profile* allows a single tool name."""
    return profile_allows_any(profile, {tool_name})
