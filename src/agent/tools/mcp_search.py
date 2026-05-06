"""Domain-aware MCP capability discovery tool."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.agent.skills import SkillsLoader
from src.agent.tools.base import Tool

if TYPE_CHECKING:
    from src.agent.mcp_manager import MCPManager


class MCPToolSearch(Tool):
    """Search discovered MCP tools by query, server, and instinct domain."""

    def __init__(self, workspace: Path, manager: "MCPManager") -> None:
        self._skills = SkillsLoader(workspace)
        self._manager = manager

    @property
    def name(self) -> str:
        return "mcp_search"

    @property
    def description(self) -> str:
        return (
            "Search MCP capabilities by intent, server name, or instinct domain. "
            "Use this to discover external tool surfaces before calling specific mcp_* tools."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Capability query such as 'github PRs', 'wiki search', or 'calendar'. "
                        "Optional if domain or server is provided."
                    ),
                },
                "domain": {
                    "type": "string",
                    "description": (
                        "Optional instinct domain scope. Accepts 'category/domain', "
                        "category only, or an unambiguous domain name."
                    ),
                },
                "server": {
                    "type": "string",
                    "description": "Optional MCP server name filter.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Maximum number of MCP tools to return (default: 5).",
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
        limit: int = 5,
        **kwargs: Any,
    ) -> str:
        result = self.search_tools(
            query=query,
            domain=domain,
            server=server,
            limit=limit,
        )
        return json.dumps(result, ensure_ascii=False)

    def search_tools(
        self,
        *,
        query: str = "",
        domain: str | None = None,
        server: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Search the current MCP capability catalog and return structured results."""
        if not query.strip() and not (domain or "").strip() and not (server or "").strip():
            return {"error": "Provide at least one of 'query', 'domain', or 'server'."}

        servers = self._manager.catalog_snapshot()
        if not servers:
            return _empty_result(query=query, domain=domain, server=server, notice="No MCP servers configured.")

        servers, server_error = self._filter_servers(
            servers, query=query, domain=domain, server=server
        )
        if server_error is not None:
            return server_error

        domain_catalog = self._skills.get_domain_catalog()
        resolved_domains = self._skills.resolve_domain_labels(domain) if domain else []
        if domain and not resolved_domains:
            return _empty_result(
                query=query,
                domain=domain,
                server=server,
                error=f"Unknown domain: {domain}",
                available_domains=sorted(domain_catalog),
            )

        domain_terms = self._domain_terms(domain_catalog, resolved_domains)
        query_tokens = self._tokenize(query)
        matches = self._matches(
            servers,
            query=query,
            query_tokens=query_tokens,
            domain=domain,
            domain_terms=domain_terms,
            resolved_domains=resolved_domains,
        )

        matches.sort(
            key=lambda item: (
                -item["score"],
                item["tool"]["server"],
                item["tool"]["tool_name"],
            )
        )

        snapshots = self._manager.catalog_snapshot()
        return _result(
            query=query,
            domain=domain,
            server=server,
            resolved_domains=resolved_domains,
            configured_servers=len(snapshots),
            connected_servers=sum(1 for entry in snapshots if entry["connected"]),
            matches=matches[: max(1, int(limit))],
        )

    def _filter_servers(
        self,
        servers: list[dict[str, Any]],
        *,
        query: str,
        domain: str | None,
        server: str | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        server_filter = (server or "").strip().lower()
        if not server_filter:
            return servers, None

        filtered = [entry for entry in servers if entry["server"].lower() == server_filter]
        if filtered:
            return filtered, None

        return [], _empty_result(
            query=query,
            domain=domain,
            server=server,
            error=f"Unknown MCP server: {server}",
            available_servers=sorted(entry["server"] for entry in self._manager.catalog_snapshot()),
        )

    def _matches(
        self,
        servers: list[dict[str, Any]],
        *,
        query: str,
        query_tokens: list[str],
        domain: str | None,
        domain_terms: dict[str, set[str]],
        resolved_domains: list[str],
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for entry in servers:
            for tool in entry.get("tools", []):
                match = self._match_tool(
                    entry,
                    tool,
                    query=query,
                    query_tokens=query_tokens,
                    domain_terms=domain_terms,
                    resolved_domains=resolved_domains,
                )
                if (query or domain) and match["score"] <= 0:
                    continue
                matches.append(match)
        return matches

    def _match_tool(
        self,
        entry: dict[str, Any],
        tool: dict[str, Any],
        *,
        query: str,
        query_tokens: list[str],
        domain_terms: dict[str, set[str]],
        resolved_domains: list[str],
    ) -> dict[str, Any]:
        score, reasons, matched_domains = self._score_tool(
            tool,
            server_name=entry["server"],
            transport=entry["transport"],
            query=query,
            query_tokens=query_tokens,
            domain_terms=domain_terms,
            resolved_domains=resolved_domains,
        )
        return {
            "score": score,
            "match_reasons": reasons,
            "matched_domains": matched_domains,
            "tool": {
                "wrapper_name": tool["wrapper_name"],
                "tool_name": tool["tool_name"],
                "server": entry["server"],
                "transport": entry["transport"],
                "description": tool["description"],
                "connected": entry["connected"],
            },
        }

    def _score_tool(
        self,
        tool: dict[str, Any],
        *,
        server_name: str,
        transport: str,
        query: str,
        query_tokens: list[str],
        domain_terms: dict[str, set[str]],
        resolved_domains: list[str],
    ) -> tuple[int, list[str], list[str]]:
        """Score one MCP capability against query/domain constraints."""
        blob = " ".join(
            [
                server_name,
                transport,
                tool["tool_name"],
                tool["wrapper_name"],
                tool["description"],
            ]
        ).lower()

        score = 0
        reasons: list[str] = []

        q = query.strip().lower()
        if q:
            if q in tool["tool_name"].lower() or q in tool["wrapper_name"].lower():
                score += 8
                reasons.append("tool name match")
            if q in tool["description"].lower():
                score += 6
                reasons.append("description match")
            if q in server_name.lower():
                score += 5
                reasons.append("server match")

            for token in query_tokens:
                if token in tool["tool_name"].lower() or token in tool["wrapper_name"].lower():
                    score += 3
                elif token in tool["description"].lower():
                    score += 2
                elif token in server_name.lower() or token in transport.lower():
                    score += 1

        matched_domains: list[str] = []
        if resolved_domains:
            for label in resolved_domains:
                terms = domain_terms.get(label, set())
                overlap = sum(1 for term in terms if term and term in blob)
                if overlap > 0:
                    score += overlap * 2
                    matched_domains.append(label)
            if matched_domains:
                reasons.append("domain term overlap")

        if not q and matched_domains:
            score += 1
        return score, list(dict.fromkeys(reasons)), matched_domains

    def _domain_terms(
        self,
        domain_catalog: dict[str, dict[str, Any]],
        resolved_domains: list[str],
    ) -> dict[str, set[str]]:
        """Expand domain metadata into searchable keyword terms."""
        result: dict[str, set[str]] = {}
        for label in resolved_domains:
            entry = domain_catalog.get(label, {})
            terms: set[str] = set()
            terms.update(self._tokenize(label.replace("/", " ")))
            for keyword in entry.get("keywords", []):
                terms.update(self._tokenize(keyword))
            for skill in entry.get("skills", []):
                terms.update(self._tokenize(skill))
            result[label] = {term for term in terms if len(term) >= 2}
        return result

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize mixed-language capability text into simple ASCII-ish terms."""
        return [token for token in re.split(r"[^a-zA-Z0-9_\-/]+", text.lower()) if token]


def _empty_result(
    *,
    query: str,
    domain: str | None,
    server: str | None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "query": query,
        "domain": domain,
        "server": server,
        "count": 0,
        "matches": [],
        **extra,
    }


def _result(
    *,
    query: str,
    domain: str | None,
    server: str | None,
    resolved_domains: list[str],
    configured_servers: int,
    connected_servers: int,
    matches: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    public_matches = [_public_match(item) for item in matches]
    return {
        "query": query,
        "domain": domain,
        "server": server,
        "resolved_domains": resolved_domains,
        "configured_servers": configured_servers,
        "connected_servers": connected_servers,
        "count": len(public_matches),
        "matches": public_matches,
    }


def _public_match(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **item["tool"],
        "match_reasons": item["match_reasons"],
        "matched_domains": item["matched_domains"],
    }
