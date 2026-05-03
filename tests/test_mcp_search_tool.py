"""Tests for domain-aware MCP capability discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.skills import SkillsLoader
from src.agent.tools.mcp_search import MCPToolSearch


class _FakeMCPManager:
    def __init__(self, catalog: list[dict]):
        self._catalog = catalog

    def catalog_snapshot(self) -> list[dict]:
        return [entry.copy() for entry in self._catalog]


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


def _make_tool(tmp_path: Path) -> MCPToolSearch:
    workspace = tmp_path / "workspace"
    instinct = tmp_path / "instinct" / "domains"
    builtin = tmp_path / "builtin_skills"
    workspace.mkdir(parents=True)
    instinct.mkdir(parents=True)
    builtin.mkdir(parents=True)

    _write_domain(instinct, "coding", "github", "github, pr, ci, workflow, issue", ["github"])
    _write_domain(instinct, "feishu", "wiki", "wiki, doc, knowledge, space", ["reference"])

    manager = _FakeMCPManager(
        [
            {
                "server": "github",
                "transport": "stdio",
                "connected": True,
                "status": "connected",
                "error": None,
                "tool_count": 2,
                "tools": [
                    {
                        "server": "github",
                        "tool_name": "list_pull_requests",
                        "wrapper_name": "mcp_github_list_pull_requests",
                        "description": "List PRs and CI checks for a repository",
                        "parameters": {},
                    },
                    {
                        "server": "github",
                        "tool_name": "list_issues",
                        "wrapper_name": "mcp_github_list_issues",
                        "description": "List GitHub issues in a repository",
                        "parameters": {},
                    },
                ],
            },
            {
                "server": "feishu",
                "transport": "http",
                "connected": True,
                "status": "connected",
                "error": None,
                "tool_count": 1,
                "tools": [
                    {
                        "server": "feishu",
                        "tool_name": "search_wiki",
                        "wrapper_name": "mcp_feishu_search_wiki",
                        "description": "Search Feishu wiki docs and knowledge spaces",
                        "parameters": {},
                    }
                ],
            },
        ]
    )
    tool = MCPToolSearch(workspace=workspace, manager=manager)
    tool._skills = SkillsLoader(
        workspace,
        builtin_skills_dir=builtin,
        instinct_domains_dir=instinct,
    )
    return tool


@pytest.mark.asyncio
async def test_mcp_search_matches_domain_and_query(tmp_path: Path) -> None:
    tool = _make_tool(tmp_path)

    raw = await tool.execute(query="github pr", domain="coding/github")
    payload = json.loads(raw)

    assert payload["resolved_domains"] == ["coding/github"]
    assert payload["count"] >= 1
    assert payload["matches"][0]["server"] == "github"
    assert "coding/github" in payload["matches"][0]["matched_domains"]


@pytest.mark.asyncio
async def test_mcp_search_supports_server_filter(tmp_path: Path) -> None:
    tool = _make_tool(tmp_path)

    raw = await tool.execute(server="feishu")
    payload = json.loads(raw)

    assert payload["count"] == 1
    assert payload["matches"][0]["server"] == "feishu"
    assert payload["matches"][0]["tool_name"] == "search_wiki"


@pytest.mark.asyncio
async def test_mcp_search_returns_unknown_server_error(tmp_path: Path) -> None:
    tool = _make_tool(tmp_path)

    raw = await tool.execute(server="paper")
    payload = json.loads(raw)

    assert payload["error"] == "Unknown MCP server: paper"
    assert sorted(payload["available_servers"]) == ["feishu", "github"]


@pytest.mark.asyncio
async def test_mcp_search_returns_unknown_domain_error(tmp_path: Path) -> None:
    tool = _make_tool(tmp_path)

    raw = await tool.execute(domain="paper")
    payload = json.loads(raw)

    assert payload["error"] == "Unknown domain: paper"
    assert "coding/github" in payload["available_domains"]
