"""Unified discovery tool for skills and MCP capabilities."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.agent.skills import SkillsLoader
from src.agent.tools.base import Tool
from src.agent.tools.mcp_search import MCPToolSearch

if TYPE_CHECKING:
    from src.agent.mcp_manager import MCPManager


class CapabilitySearchTool(Tool):
    """Search across native skills and discovered MCP capabilities."""

    def __init__(self, workspace: Path, manager: MCPManager | None = None) -> None:
        self._skills = SkillsLoader(workspace)
        self._mcp = (
            MCPToolSearch(workspace=workspace, manager=manager) if manager is not None else None
        )

    @property
    def name(self) -> str:
        return "capability_search"

    @property
    def description(self) -> str:
        return (
            "Search TheOS capabilities across native skills and MCP tools by intent, "
            "domain, or server. Use this first when you need to discover how to solve a task."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Capability intent such as 'github pr', 'wiki search', or 'weather'.",
                },
                "domain": {
                    "type": "string",
                    "description": (
                        "Optional instinct domain scope. Accepts 'category/domain', category only, "
                        "or an unambiguous domain name."
                    ),
                },
                "server": {
                    "type": "string",
                    "description": "Optional MCP server filter. Ignored for skill matches.",
                },
                "kinds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["skill", "mcp"],
                    },
                    "description": "Restrict search kinds. Defaults to both.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Maximum number of combined matches to return (default: 8).",
                },
                "include_unavailable": {
                    "type": "boolean",
                    "description": "Include unavailable skills in the result set.",
                    "default": False,
                },
            },
            "required": [],
        }

    @property
    def parallel_safe(self) -> bool:
        return True

    @property
    def dedupe_within_turn(self) -> bool:
        return True

    async def execute(
        self,
        query: str = "",
        domain: str | None = None,
        server: str | None = None,
        kinds: list[str] | None = None,
        limit: int = 8,
        include_unavailable: bool = False,
        **kwargs: Any,
    ) -> str:
        del kwargs
        result = self.search_capabilities(
            query=query,
            domain=domain,
            server=server,
            kinds=kinds,
            limit=limit,
            include_unavailable=include_unavailable,
        )
        return json.dumps(result, ensure_ascii=False)

    def search_capabilities(
        self,
        *,
        query: str = "",
        domain: str | None = None,
        server: str | None = None,
        kinds: list[str] | None = None,
        limit: int = 8,
        include_unavailable: bool = False,
    ) -> dict[str, Any]:
        """Search all enabled discovery surfaces and return a unified result set."""
        enabled_kinds = set(kinds) if kinds is not None else {"skill", "mcp"}
        if error := _validate_search_request(enabled_kinds, query=query, domain=domain, server=server):
            return {"error": error}

        entries: list[dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[str] = []

        if "skill" in enabled_kinds:
            entries.extend(
                self._skill_entries(
                    query=query,
                    domain=domain,
                    limit=limit,
                    include_unavailable=include_unavailable,
                    errors=errors,
                )
            )

        if "mcp" in enabled_kinds:
            if self._mcp is None:
                warnings.append("MCP discovery is unavailable because no MCP manager is attached.")
            else:
                entries.extend(
                    self._mcp_entries(
                        query=query,
                        domain=domain,
                        server=server,
                        limit=limit,
                        warnings=warnings,
                        errors=errors,
                    )
                )

        entries.sort(
            key=lambda item: (
                -item["score"],
                item["kind"],
                item["title"],
            )
        )
        matches = entries[: max(1, int(limit))]

        result = {
            "query": query,
            "domain": domain,
            "server": server,
            "kinds": sorted(enabled_kinds),
            "count": len(matches),
            "matches": [_public_entry(item) for item in matches],
        }
        if warnings:
            result["warnings"] = warnings
        if errors and len(errors) == len(enabled_kinds) and not entries:
            result["error"] = " | ".join(dict.fromkeys(errors))
        elif errors:
            result["partial_errors"] = list(dict.fromkeys(errors))
        return result

    def _skill_entries(
        self,
        *,
        query: str,
        domain: str | None,
        limit: int,
        include_unavailable: bool,
        errors: list[str],
    ) -> list[dict[str, Any]]:
        skill_result = self._skills.search_skills(
            query=query,
            domain=domain,
            limit=max(limit, 20),
            include_unavailable=include_unavailable,
        )
        if skill_result.get("error"):
            errors.append(skill_result["error"])

        return [
            _ranked_entry(
                kind="skill",
                score=self._score_skill_entry(item, query=query, domain=domain),
                title=item["name"],
                description=item["description"],
                match_reasons=item["match_reasons"],
                domains=item.get("domains", []),
                available=item["available"],
                details=item,
            )
            for item in skill_result.get("matches", [])
        ]

    def _mcp_entries(
        self,
        *,
        query: str,
        domain: str | None,
        server: str | None,
        limit: int,
        warnings: list[str],
        errors: list[str],
    ) -> list[dict[str, Any]]:
        if self._mcp is None:
            return []

        mcp_result = self._mcp.search_tools(
            query=query,
            domain=domain,
            server=server,
            limit=max(limit, 20),
        )
        if mcp_result.get("error"):
            errors.append(mcp_result["error"])
        if mcp_result.get("notice"):
            warnings.append(mcp_result["notice"])

        return [
            _ranked_entry(
                kind="mcp",
                score=self._score_mcp_entry(item, query=query, domain=domain, server=server),
                title=item["wrapper_name"],
                description=item["description"],
                match_reasons=item["match_reasons"],
                domains=item.get("matched_domains", []),
                available=item["connected"],
                details=item,
            )
            for item in mcp_result.get("matches", [])
        ]

    def _score_skill_entry(self, item: dict[str, Any], *, query: str, domain: str | None) -> int:
        """Estimate cross-surface ordering score for one skill result."""
        score = 0
        if domain:
            score += 20
        if query:
            reasons = item.get("match_reasons", [])
            score += 12 if "name match" in reasons else 0
            score += 8 if "description match" in reasons else 0
            score += 4 if "metadata match" in reasons else 0
        if item.get("available"):
            score += 5
        return score + 1

    def _score_mcp_entry(
        self,
        item: dict[str, Any],
        *,
        query: str,
        domain: str | None,
        server: str | None,
    ) -> int:
        """Estimate cross-surface ordering score for one MCP result."""
        score = 0
        if domain:
            score += 20
        if server:
            score += 10
        if query:
            reasons = item.get("match_reasons", [])
            score += 12 if "tool name match" in reasons else 0
            score += 8 if "description match" in reasons else 0
            score += 6 if "server match" in reasons else 0
        if item.get("connected"):
            score += 5
        return score


def _validate_search_request(
    enabled_kinds: set[str],
    *,
    query: str,
    domain: str | None,
    server: str | None,
) -> str | None:
    if not enabled_kinds:
        return "Provide at least one capability kind."
    invalid = sorted(kind for kind in enabled_kinds if kind not in {"skill", "mcp"})
    if invalid:
        return f"Unknown capability kinds: {', '.join(invalid)}"
    if not query.strip() and not (domain or "").strip() and not (server or "").strip():
        return "Provide at least one of 'query', 'domain', or 'server'."
    return None


def _ranked_entry(
    *,
    kind: str,
    score: int,
    title: str,
    description: str,
    match_reasons: list[str],
    domains: Iterable[str],
    available: bool,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "score": score,
        "title": title,
        "description": description,
        "match_reasons": match_reasons,
        "domains": list(domains),
        "available": available,
        "details": details,
    }


def _public_entry(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": item["kind"],
        "title": item["title"],
        "description": item["description"],
        "match_reasons": item["match_reasons"],
        "domains": item["domains"],
        "available": item["available"],
        "details": item["details"],
    }
