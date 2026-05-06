"""Tests for unified capability discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.skills import SkillsLoader
from src.agent.tools.capability_search import CapabilitySearchTool


class _FakeMCPManager:
    def __init__(self, catalog: list[dict]):
        self._catalog = catalog

    def catalog_snapshot(self) -> list[dict]:
        return [entry.copy() for entry in self._catalog]


def _write_skill(base: Path, name: str, description: str) -> None:
    skill_dir = base / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# Skill\n\nBody.\n",
        encoding="utf-8",
    )


def _write_domain(base: Path, category: str, domain: str, keywords: str, skills: list[str]) -> None:
    domain_dir = base / category
    domain_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {domain} Domain",
        "",
        "## Keywords",
        keywords,
        "",
        "## Skills",
    ]
    lines.extend(f"- {name}: helper" for name in skills)
    lines.extend(["", "## Context", "context"])
    (domain_dir / f"{domain}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_tool(tmp_path: Path) -> CapabilitySearchTool:
    workspace = tmp_path / "workspace"
    builtin = tmp_path / "builtin_skills"
    instinct = tmp_path / "instinct" / "domains"
    workspace.mkdir(parents=True)
    builtin.mkdir(parents=True)
    instinct.mkdir(parents=True)

    _write_skill(builtin, "github", "Manage GitHub pull requests and CI")
    _write_skill(builtin, "weather", "Look up weather reports")
    _write_domain(instinct, "coding", "github", "github, pr, ci, issue, review", ["github"])
    _write_domain(instinct, "web", "weather", "weather, forecast, report", ["weather"])

    manager = _FakeMCPManager(
        [
            {
                "server": "github",
                "transport": "stdio",
                "connected": True,
                "status": "connected",
                "error": None,
                "tool_count": 1,
                "tools": [
                    {
                        "server": "github",
                        "tool_name": "list_pull_requests",
                        "wrapper_name": "mcp_github_list_pull_requests",
                        "description": "List PRs and CI checks for a repository",
                        "parameters": {},
                    }
                ],
            }
        ]
    )
    tool = CapabilitySearchTool(workspace=workspace, manager=manager)
    tool._skills = SkillsLoader(
        workspace,
        builtin_skills_dir=builtin,
        instinct_domains_dir=instinct,
    )
    if tool._mcp is not None:
        tool._mcp._skills = tool._skills
    return tool


@pytest.mark.asyncio
async def test_capability_search_combines_skill_and_mcp_matches(tmp_path: Path) -> None:
    tool = _make_tool(tmp_path)

    raw = await tool.execute(query="github pr", domain="coding/github")
    payload = json.loads(raw)

    assert payload["count"] >= 2
    kinds = {item["kind"] for item in payload["matches"]}
    assert {"skill", "mcp"} <= kinds


@pytest.mark.asyncio
async def test_capability_search_can_restrict_kinds(tmp_path: Path) -> None:
    tool = _make_tool(tmp_path)

    raw = await tool.execute(query="weather", kinds=["skill"])
    payload = json.loads(raw)

    assert payload["kinds"] == ["skill"]
    assert all(item["kind"] == "skill" for item in payload["matches"])


@pytest.mark.asyncio
async def test_capability_search_degrades_without_mcp_manager(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    builtin = tmp_path / "builtin_skills"
    instinct = tmp_path / "instinct" / "domains"
    workspace.mkdir(parents=True)
    builtin.mkdir(parents=True)
    instinct.mkdir(parents=True)
    _write_skill(builtin, "weather", "Look up weather reports")
    _write_domain(instinct, "web", "weather", "weather, forecast", ["weather"])

    tool = CapabilitySearchTool(workspace=workspace, manager=None)
    tool._skills = SkillsLoader(
        workspace,
        builtin_skills_dir=builtin,
        instinct_domains_dir=instinct,
    )

    raw = await tool.execute(query="weather")
    payload = json.loads(raw)

    assert payload["count"] == 1
    assert payload["matches"][0]["kind"] == "skill"
    assert "warnings" in payload


@pytest.mark.asyncio
async def test_capability_search_reports_invalid_kind(tmp_path: Path) -> None:
    tool = _make_tool(tmp_path)

    raw = await tool.execute(query="github", kinds=["plugin"])
    payload = json.loads(raw)

    assert payload["error"] == "Unknown capability kinds: plugin"


@pytest.mark.asyncio
async def test_capability_search_requires_kind(tmp_path: Path) -> None:
    tool = _make_tool(tmp_path)

    raw = await tool.execute(query="github", kinds=[])
    payload = json.loads(raw)

    assert payload["error"] == "Provide at least one capability kind."


@pytest.mark.asyncio
async def test_capability_search_requires_search_scope(tmp_path: Path) -> None:
    tool = _make_tool(tmp_path)

    raw = await tool.execute()
    payload = json.loads(raw)

    assert payload["error"] == "Provide at least one of 'query', 'domain', or 'server'."
