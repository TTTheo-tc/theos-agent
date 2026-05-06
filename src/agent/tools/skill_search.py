"""Domain-scoped skill discovery tool."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.agent.skills import SkillsLoader
from src.agent.tools.base import Tool


class SkillSearchTool(Tool):
    """Search available skills, optionally constrained by instinct domain."""

    def __init__(self, workspace: Path) -> None:
        self._skills = SkillsLoader(workspace)

    @property
    def name(self) -> str:
        return "skill_search"

    @property
    def description(self) -> str:
        return (
            "Search TheOS skills by intent, keyword, or instinct domain. "
            "Use this to discover which skills fit a task before choosing files, tools, or MCP."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What capability you are looking for, such as 'github pr', "
                        "'summarize links', or 'weather'. Optional if domain is provided."
                    ),
                },
                "domain": {
                    "type": "string",
                    "description": (
                        "Optional instinct domain scope. Accepts 'category/domain', "
                        "category only (for example 'coding'), or an unambiguous domain name."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Maximum number of matches to return (default: 5).",
                },
                "include_unavailable": {
                    "type": "boolean",
                    "description": "Include skills whose requirements are not currently met.",
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
        limit: int = 5,
        include_unavailable: bool = False,
        **kwargs: Any,
    ) -> str:
        del kwargs
        if error := _validate_search_request(query=query, domain=domain):
            return _json_response({"error": error})

        result = self._skills.search_skills(
            query=query,
            domain=domain,
            limit=limit,
            include_unavailable=include_unavailable,
        )
        return _json_response(result)


def _validate_search_request(*, query: str, domain: str | None) -> str | None:
    if not query.strip() and not (domain or "").strip():
        return "Provide at least one of 'query' or 'domain'."
    return None


def _json_response(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)
