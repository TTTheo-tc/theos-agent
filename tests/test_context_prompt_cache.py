"""Tests for cache-friendly prompt construction."""

from __future__ import annotations

import datetime as datetime_module
from datetime import datetime as real_datetime
from pathlib import Path

from src.agent.context import ContextBuilder
from src.config.schema import MemoryConfig, MemoryInjectionConfig


class _FakeDatetime(real_datetime):
    current = real_datetime(2026, 2, 24, 13, 59)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.current


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def test_system_prompt_stays_stable_when_clock_changes(tmp_path, monkeypatch) -> None:
    """System prompt should not change just because wall clock minute changes."""
    monkeypatch.setattr(datetime_module, "datetime", _FakeDatetime)

    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    _FakeDatetime.current = real_datetime(2026, 2, 24, 13, 59)
    prompt1 = builder.build_system_prompt()

    _FakeDatetime.current = real_datetime(2026, 2, 24, 14, 0)
    prompt2 = builder.build_system_prompt()

    assert prompt1 == prompt2


def test_runtime_context_merged_into_final_user_message(tmp_path) -> None:
    """Runtime metadata and current question should be merged into one user message."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="Return exactly: OK",
        channel="cli",
        chat_id="direct",
    )

    assert messages[0]["role"] == "system"
    assert "## Current Session" not in messages[0]["content"]

    # Only one final user message containing both runtime context and the question
    assert messages[-1]["role"] == "user"
    content = messages[-1]["content"]
    assert isinstance(content, str)
    assert ContextBuilder._RUNTIME_CONTEXT_TAG in content
    assert "Current Time:" in content
    assert "Channel: cli" in content
    assert "Chat ID: direct" in content
    assert "[Current Question]" in content
    assert "Return exactly: OK" in content


def test_requested_skills_are_loaded_into_system_prompt(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    skill_dir = workspace / "skills" / "paper-helper"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: paper-helper\ndescription: paper helper\n---\nUse this for paper reading.\n",
        encoding="utf-8",
    )

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt(skill_names=["paper-helper"])

    assert "# Routed Skills" in prompt
    assert "### Skill: paper-helper" in prompt
    assert "Use this for paper reading." in prompt


def test_genver_generator_prompt_omits_skills_summary_roles_and_active_skills(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    skill_dir = workspace / "skills" / "always-paper"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: always-paper\ndescription: always\nalways: true\n---\nAlways skill.\n",
        encoding="utf-8",
    )
    routed_skill_dir = workspace / "skills" / "paper-helper"
    routed_skill_dir.mkdir(parents=True)
    (routed_skill_dir / "SKILL.md").write_text(
        "---\nname: paper-helper\ndescription: routed\n---\nUse this for paper reading.\n",
        encoding="utf-8",
    )

    class _Role:
        description = "Executor"
        model = "test-model"

    builder = ContextBuilder(workspace, roles={"executor": _Role()})
    prompt = builder.build_system_prompt(
        skill_names=["paper-helper"],
        prompt_profile=ContextBuilder._GENVER_GENERATOR_PROFILE,
    )

    assert "# Routed Skills" in prompt
    assert "Use this for paper reading." in prompt
    assert "# Active Skills" not in prompt
    assert "# Skills" not in prompt
    assert "# Available Agent Roles" not in prompt


def test_system_prompt_only_loads_merged_bootstrap_files(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / "AGENTS.md").write_text("AGENTS block", encoding="utf-8")
    (workspace / "SOUL.md").write_text("SOUL block", encoding="utf-8")
    (workspace / "USER.md").write_text("USER block", encoding="utf-8")
    (workspace / "TOOLS.md").write_text("TOOLS block", encoding="utf-8")
    (workspace / "IDENTITY.md").write_text("IDENTITY block", encoding="utf-8")

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()

    assert "AGENTS block" in prompt
    assert "SOUL block" in prompt
    assert "USER block" not in prompt
    assert "TOOLS block" not in prompt
    assert "IDENTITY block" in prompt
    assert "## IDENTITY.md" not in prompt


def test_identity_is_loaded_from_workspace_markdown(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / "IDENTITY.md").write_text(
        "# theos\n\nRuntime: {runtime}\nWorkspace: {group_path}\nSkills: {global_path}/skills/",
        encoding="utf-8",
    )

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt()

    assert "Runtime:" in prompt
    assert str(workspace) in prompt
    assert f"{workspace}/skills/" in prompt


def test_context_builder_passes_group_workspace_and_memory_config_to_recall_service(
    tmp_path,
) -> None:
    workspace = _make_workspace(tmp_path)
    group_workspace = workspace / "groups" / "room-1"
    group_workspace.mkdir(parents=True)

    class _RecallProbe:
        def __init__(self) -> None:
            self.calls = []

        def get_memory_context(self, *, query=None, workspace=None, memory_config=None) -> str:
            self.calls.append(
                {
                    "query": query,
                    "workspace": workspace,
                    "memory_config": memory_config,
                }
            )
            return "probe memory"

    recall = _RecallProbe()
    builder = ContextBuilder(workspace, group_workspace=group_workspace, recall_service=recall)
    config = MemoryConfig(
        injection=MemoryInjectionConfig(
            mode="retrieval",
            max_context_tokens=64,
            fallback_to_full=False,
        )
    )

    prompt = builder.build_system_prompt(
        current_message="asyncio provider",
        memory_config=config,
    )

    assert "probe memory" in prompt
    assert recall.calls == [
        {
            "query": "asyncio provider",
            "workspace": group_workspace,
            "memory_config": config,
        }
    ]


def test_memory_tools_prompt_contains_mandatory_policy(tmp_path) -> None:
    """Prompt must contain mandatory recall policy, not just a soft hint."""
    workspace = _make_workspace(tmp_path)

    builder = ContextBuilder(workspace, group_workspace=workspace)
    prompt = builder.build_system_prompt(
        current_message="test",
        has_memory_tools=True,
    )
    assert "Mandatory recall policy" in prompt
    assert "MUST call" in prompt
    assert "Do NOT guess or fabricate" in prompt
