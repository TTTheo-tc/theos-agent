"""Tests for domain-scoped skill discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.skills import SkillsLoader
from src.agent.tools.skill_search import SkillSearchTool


def _write_skill(base: Path, name: str, description: str, metadata: str = "") -> None:
    skill_dir = base / name
    skill_dir.mkdir(parents=True)
    frontmatter = [
        "---",
        f"name: {name}",
        f"description: {description}",
    ]
    if metadata:
        frontmatter.append(f"metadata: {metadata}")
    frontmatter.append("---")
    (skill_dir / "SKILL.md").write_text(
        "\n".join(frontmatter) + "\n\n# Skill\n\nBody.\n",
        encoding="utf-8",
    )


def _write_domain(base: Path, category: str, domain: str, skills: list[str]) -> None:
    domain_dir = base / category
    domain_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {domain} Domain",
        "",
        "## Keywords",
        "test",
        "",
        "## Skills",
    ]
    lines.extend(f"- {name}: helper" for name in skills)
    lines.extend(["", "## Context", "context"])
    (domain_dir / f"{domain}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_loader(tmp_path: Path) -> SkillsLoader:
    workspace = tmp_path / "workspace"
    builtin = tmp_path / "builtin_skills"
    instinct = tmp_path / "instinct" / "domains"
    workspace.mkdir(parents=True)
    builtin.mkdir(parents=True)
    instinct.mkdir(parents=True)

    _write_skill(builtin, "github", "Manage GitHub PRs and CI")
    _write_skill(
        builtin,
        "summarize",
        "Summarize URLs and documents",
        metadata='{"theos":{"requires":{"bins":["__missing_summarize_bin__"]}}}',
    )
    _write_skill(builtin, "weather", "Look up weather reports")

    _write_domain(instinct, "coding", "github", ["github"])
    _write_domain(instinct, "web", "search", ["summarize"])
    _write_domain(instinct, "web", "weather", ["weather"])

    return SkillsLoader(
        workspace,
        builtin_skills_dir=builtin,
        instinct_domains_dir=instinct,
    )


def test_domain_skill_map_and_resolution(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)

    domain_map = loader.get_domain_skill_map()
    assert domain_map["coding/github"] == ["github"]
    assert domain_map["web/search"] == ["summarize"]

    assert loader.resolve_domain_labels("coding/github") == ["coding/github"]
    assert loader.resolve_domain_labels("web") == ["web/search", "web/weather"]
    assert loader.resolve_domain_labels("github") == ["coding/github"]


@pytest.mark.asyncio
async def test_skill_search_tool_filters_by_exact_domain(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    tool = SkillSearchTool(loader.workspace)
    tool._skills = loader

    raw = await tool.execute(query="github pr", domain="coding/github")
    payload = json.loads(raw)

    assert payload["resolved_domains"] == ["coding/github"]
    assert payload["count"] == 1
    assert payload["matches"][0]["name"] == "github"
    assert "coding/github" in payload["matches"][0]["domains"]


@pytest.mark.asyncio
async def test_skill_search_tool_supports_category_scope_and_unavailable_toggle(
    tmp_path: Path,
) -> None:
    loader = _make_loader(tmp_path)
    tool = SkillSearchTool(loader.workspace)
    tool._skills = loader

    raw_without = await tool.execute(domain="web")
    payload_without = json.loads(raw_without)
    returned_without = {item["name"] for item in payload_without["matches"]}
    assert returned_without == {"weather"}

    raw_with = await tool.execute(domain="web", include_unavailable=True)
    payload_with = json.loads(raw_with)
    returned_with = {item["name"] for item in payload_with["matches"]}
    assert returned_with == {"summarize", "weather"}
    summarize = next(item for item in payload_with["matches"] if item["name"] == "summarize")
    assert summarize["available"] is False
    assert summarize["missing_requirements"] == "CLI: __missing_summarize_bin__"


@pytest.mark.asyncio
async def test_skill_search_tool_returns_error_for_unknown_domain(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    tool = SkillSearchTool(loader.workspace)
    tool._skills = loader

    raw = await tool.execute(query="anything", domain="paper")
    payload = json.loads(raw)

    assert payload["error"] == "Unknown domain: paper"
    assert "coding/github" in payload["available_domains"]
