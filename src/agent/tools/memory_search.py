"""Memory search and get tools — agent-invocable memory retrieval."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from src.agent.tools.base import Tool

if TYPE_CHECKING:
    from src.agent.tools.context import ToolContext
    from src.memory.index import MemoryIndex


def _best_line_window(content: str, query: str, *, window_lines: int = 4) -> tuple[int, int] | None:
    """Return a best-effort 1-based line window hint for a section preview."""
    query_tokens = set(re.findall(r"\w+", query.lower()))
    if not query_tokens:
        return None

    lines = content.splitlines()
    best_idx = -1
    best_hits = 0
    for idx, line in enumerate(lines):
        line_tokens = set(re.findall(r"\w+", line.lower()))
        hits = len(query_tokens & line_tokens)
        if hits > best_hits:
            best_hits = hits
            best_idx = idx

    if best_idx < 0 or best_hits <= 0:
        return None

    start = best_idx + 1
    end = min(len(lines), start + max(1, int(window_lines)) - 1)
    return (start, end)


def _format_kg_results(kg_results: list[dict[str, Any]]) -> str:
    """Format KG search results into a readable string."""
    if not kg_results:
        return ""
    lines: list[str] = []
    for i, r in enumerate(kg_results, 1):
        node_type = r.get("node_type", "unknown")
        title = r.get("title", "")[:200]
        content = r.get("content", "")
        if len(content) > 400:
            content = content[:400] + "..."
        score = r.get("final_score", r.get("score", 0.0))
        importance = r.get("importance", 0.0)
        created = r.get("created_at", "")[:10]
        domains_raw = r.get("domains", "")
        domains = domains_raw if isinstance(domains_raw, str) else ",".join(domains_raw)

        line = (
            f"{i}. [KG:{node_type}] {title}" f" (score: {score:.2f}, importance: {importance:.2f})"
        )
        if created:
            line += f" ({created})"
        if domains:
            line += f" [{domains}]"
        line += f"\n   {content}"

        # Append related nodes if present
        related = r.get("related")
        if related:
            rel_lines = []
            for rn in related[:5]:
                rn_type = rn.get("node_type", "")
                rn_title = rn.get("title", "")[:80]
                rel_lines.append(f"     -> [{rn_type}] {rn_title}")
            line += "\n" + "\n".join(rel_lines)

        lines.append(line)
    return "\n\n".join(lines)


class MemorySearchTool(Tool):
    """Search long-term memory, conversation history, and knowledge graph."""

    accepts_context = True

    def __init__(
        self,
        *,
        index_resolver: Callable[[str | None], "MemoryIndex | None"] | None = None,
        workspace_resolver: Callable[[str | None], "Path | None"] | None = None,
        default_max_results: int = 6,
        default_min_score: float = 0.0,
    ) -> None:
        self._index_resolver = index_resolver
        self._workspace_resolver = workspace_resolver
        self._default_max_results = max(1, int(default_max_results))
        self._default_min_score = float(default_min_score)

    @property
    def name(self) -> str:
        return "memory_search"

    @property
    def description(self) -> str:
        return (
            "Search your long-term memory (MEMORY.md), conversation history (HISTORY.md), "
            "and structured knowledge graph for relevant information. Use this when you "
            "need to recall past decisions, conversations, facts, or preferences."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — use keywords or natural language",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return",
                },
                "min_score": {
                    "type": "number",
                    "description": "Minimum relevance score threshold",
                },
                "source": {
                    "type": "string",
                    "enum": ["markdown", "knowledge_graph", "all"],
                    "description": (
                        "Search source: markdown (MEMORY.md/HISTORY.md), "
                        "knowledge_graph (structured KG), or all"
                    ),
                    "default": "markdown",
                },
                "node_type": {
                    "type": "string",
                    "enum": [
                        "task",
                        "rule",
                        "research",
                        "pattern",
                        "decision",
                        "lesson",
                    ],
                    "description": ("Filter by node type (only for knowledge_graph source)"),
                },
                "min_importance": {
                    "type": "number",
                    "description": (
                        "Minimum importance score 0-1 (only for knowledge_graph source)"
                    ),
                },
                "include_related": {
                    "type": "boolean",
                    "description": "Include related nodes via graph edges",
                    "default": False,
                },
            },
            "required": ["query"],
        }

    def _resolve_index(self, session_key: str | None) -> "MemoryIndex | None":
        if self._index_resolver is None:
            return None
        return self._index_resolver(session_key)

    def _resolve_workspace(self, session_key: str | None) -> Path | None:
        if self._workspace_resolver is None:
            return None
        return self._workspace_resolver(session_key)

    async def execute(self, _context: "ToolContext | None" = None, **kwargs: Any) -> str:
        query = kwargs.get("query", "")
        if not query:
            return "Please provide a search query."

        max_results = max(1, int(kwargs.get("max_results", self._default_max_results)))
        min_score = float(kwargs.get("min_score", self._default_min_score))
        source = kwargs.get("source", "markdown")
        node_type = kwargs.get("node_type")
        min_importance = float(kwargs.get("min_importance", 0.0))
        include_related = bool(kwargs.get("include_related", False))

        session_key = _context.session_key if _context else None
        results_parts: list[str] = []
        md_results: list[dict[str, Any]] = []
        enriched: list[dict[str, Any]] = []

        # --- Markdown search (MEMORY.md / HISTORY.md) ---
        if source in ("markdown", "all"):
            index = self._resolve_index(session_key)
            if index:
                try:
                    md_results = await index.search(
                        query,
                        max_results=max_results,
                        source="all",
                        min_score=min_score,
                    )
                except Exception as exc:
                    md_results = []
                    results_parts.append(f"Markdown search error: {exc}")

                if md_results:
                    lines: list[str] = []
                    for i, r in enumerate(md_results, 1):
                        src = r["source"]
                        line_hint = _best_line_window(r.get("content", ""), query)
                        line_span = (
                            f" lines {line_hint[0]}-{line_hint[1]}" if line_hint is not None else ""
                        )
                        section = f" [{r['section']}{line_span}]" if r.get("section") else ""
                        ts = f" ({r['timestamp']})" if r.get("timestamp") else ""
                        content = r["content"]
                        if len(content) > 500:
                            content = content[:500] + "..."
                        lines.append(
                            f"{i}. [{src}]{section}{ts} (score: {r['score']})\n" f"   {content}"
                        )
                    results_parts.append("\n\n".join(lines))
            elif source == "markdown":
                return "Memory search is not available (index not initialized)."

        # --- Knowledge Graph search ---
        if source in ("knowledge_graph", "all"):
            workspace = self._resolve_workspace(session_key)
            if workspace:
                try:
                    from src.memory.structured import StructuredMemoryStore

                    store = StructuredMemoryStore(workspace)
                    try:
                        await store.ensure_kg()
                        kg_results = await store.search(
                            query,
                            max_results=max_results,
                        )
                        # Apply node_type filter
                        if node_type:
                            kg_results = [
                                r for r in kg_results if r.get("object_type") == node_type
                            ]
                        # Apply min_importance filter (requires fetching node)
                        if min_importance > 0 and store._kg:
                            filtered = []
                            for r in kg_results:
                                node = await store._kg.get_node(r.get("id", ""))
                                if node and node.get("importance", 0) >= min_importance:
                                    r["importance"] = node.get("importance", 0)
                                    r["node_type"] = node.get("node_type", "")
                                    r["content"] = node.get("content", "")
                                    filtered.append(r)
                            kg_results = filtered

                        # include_related: append related nodes for each result
                        if include_related and store._kg:
                            for r in kg_results:
                                rid = r.get("id", "")
                                if rid:
                                    edges = await store._kg.find_related(rid)
                                    r["related"] = edges[:5]

                        # Enrich results with KG fields for formatting
                        enriched = []
                        for r in kg_results:
                            entry = dict(r)
                            entry.setdefault("final_score", entry.get("score", 0))
                            entry.setdefault("node_type", entry.get("object_type", ""))
                            entry.setdefault("content", entry.get("summary", ""))
                            enriched.append(entry)

                        kg_text = _format_kg_results(enriched)
                        if kg_text:
                            results_parts.append(kg_text)
                    finally:
                        await store.close()
                except Exception as exc:
                    results_parts.append(f"Knowledge graph search error: {exc}")
            elif source == "knowledge_graph":
                return "Knowledge graph search is not available (workspace not resolved)."

        if not results_parts:
            return f"No memory results found for: {query}"

        # --- Recall telemetry (best-effort, non-blocking) ---
        if _context and results_parts:
            import asyncio

            from src.memory.recall_journal import append_recall_entries

            telemetry_results: list[dict] = []
            # Markdown results don't have stable IDs
            if source in ("markdown", "all") and md_results:
                for r in md_results:
                    telemetry_results.append(
                        {
                            "target_kind": "markdown_section",
                            "target_id": None,
                            "path": (
                                f"{r.get('source', '')}:{r.get('section', '')}"
                                + (
                                    f"@{hint[0]}-{hint[1]}"
                                    if (hint := _best_line_window(r.get("content", ""), query))
                                    else ""
                                )
                            ),
                            "score": r.get("score"),
                            "domains": [],
                            "content": r.get("content", ""),
                        }
                    )
            # KG results have stable IDs
            if source in ("knowledge_graph", "all") and enriched:
                for r in enriched:
                    telemetry_results.append(
                        {
                            "target_kind": r.get("node_type", ""),
                            "target_id": r.get("id"),
                            "path": "",
                            "score": r.get("final_score"),
                            "domains": r.get("domains", []),
                            "content": r.get("content", "") or r.get("title", ""),
                        }
                    )
            if telemetry_results:
                workspace_path = self._resolve_workspace(_context.session_key)
                if workspace_path:
                    asyncio.create_task(
                        append_recall_entries(
                            workspace=workspace_path,
                            session_key=_context.session_key,
                            tool=self.name,
                            query=query,
                            results=telemetry_results,
                        )
                    )

        return "\n\n".join(results_parts)


class MemoryGetTool(Tool):
    """Retrieve a specific memory section by title."""

    accepts_context = True

    def __init__(
        self,
        *,
        index_resolver: Callable[[str | None], "MemoryIndex | None"] | None = None,
    ) -> None:
        self._index_resolver = index_resolver

    @property
    def name(self) -> str:
        return "memory_get"

    @property
    def description(self) -> str:
        return (
            "Retrieve a specific section from long-term memory (MEMORY.md) by its title. "
            "Use this after memory_search to get the full content of a relevant section. "
            "Optionally narrow to a line range via `from_line` (1-based) and `lines` (count) "
            "to save context on long sections."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "The section title to retrieve (e.g. 'Projects', 'Decisions')",
                },
                "from_line": {
                    "type": "integer",
                    "description": "Optional: 1-based line number to start reading from within the section.",
                },
                "lines": {
                    "type": "integer",
                    "description": "Optional: number of lines to read. Defaults to all lines from from_line to end.",
                },
            },
            "required": ["section"],
        }

    def _resolve_index(self, session_key: str | None) -> "MemoryIndex | None":
        if self._index_resolver is None:
            return None
        return self._index_resolver(session_key)

    async def execute(self, _context: "ToolContext | None" = None, **kwargs: Any) -> str:
        index = self._resolve_index(_context.session_key if _context else None)
        if not index:
            return "Memory get is not available (index not initialized)."

        section = kwargs.get("section", "")
        if not section:
            return "Please provide a section title."

        try:
            content = await index.get_section(section)
        except Exception as exc:
            return f"Memory get error: {exc}"

        if not content:
            return f"Section '{section}' not found in memory."

        # Optional line range slicing (1-based from_line)
        from_line = kwargs.get("from_line")
        lines_count = kwargs.get("lines")
        if from_line is not None or lines_count is not None:
            all_lines = content.split("\n")
            start = max(0, int(from_line or 1) - 1)
            end = start + int(lines_count) if lines_count else len(all_lines)
            content = "\n".join(all_lines[start:end])

        return content
