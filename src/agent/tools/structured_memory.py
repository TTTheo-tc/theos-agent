"""Structured memory query tools."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

from src.agent.tools.base import Tool
from src.memory.structured import StructuredMemoryStore

if TYPE_CHECKING:
    from pathlib import Path

    from src.agent.tools.context import ToolContext


class StructuredMemorySearchTool(Tool):
    """Search structured task, rule, and research-note objects."""

    accepts_context = True

    def __init__(
        self,
        *,
        workspace_resolver: Callable[[str | None], "Path"] | None = None,
        default_max_results: int = 6,
    ) -> None:
        self._workspace_resolver = workspace_resolver
        self._default_max_results = max(1, int(default_max_results))

    @property
    def name(self) -> str:
        return "structured_memory_search"

    @property
    def description(self) -> str:
        return (
            "Search structured memory objects such as task memories, domain rules, "
            "and research notes. Use this when you want object-level knowledge "
            "instead of raw MEMORY.md snippets."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "object_type": {
                    "type": "string",
                    "enum": ["all", "task", "rule", "research_note"],
                    "description": "Structured object type filter",
                },
                "domain": {
                    "type": "string",
                    "description": "Preferred domain such as 'paper/reading' or 'finance/general'",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of search results",
                    "minimum": 1,
                },
            },
            "required": ["query"],
        }

    def _resolve_workspace(self, session_key: str | None) -> "Path | None":
        if self._workspace_resolver is None:
            return None
        return self._workspace_resolver(session_key)

    async def execute(self, _context: "ToolContext | None" = None, **kwargs: Any) -> str:
        workspace = self._resolve_workspace(_context.session_key if _context else None)
        if workspace is None:
            return "Structured memory search is not available (workspace not resolved)."

        query = kwargs.get("query", "")
        if not query:
            return "Please provide a search query."

        store = StructuredMemoryStore(workspace)
        try:
            await store.ensure_kg()
            results = await store.search(
                query,
                object_type=kwargs.get("object_type", "all"),
                max_results=kwargs.get("max_results", self._default_max_results),
                prefer_domain=kwargs.get("domain"),
            )
        finally:
            await store.close()
        if not results:
            return f"No structured memory results found for: {query}"

        lines = []
        for i, result in enumerate(results, 1):
            domains = (
                f" domains={','.join(result.get('domains', []))}" if result.get("domains") else ""
            )
            lines.append(
                f"{i}. [{result['object_type']}] {result['id']} (score: {result['score']})\n"
                f"   {result['title']}{domains}\n"
                f"   {result['summary']}"
            )

        # Recall telemetry
        if _context and results:
            import asyncio

            from src.memory.recall_journal import append_recall_entries

            asyncio.create_task(
                append_recall_entries(
                    workspace=workspace,
                    session_key=_context.session_key,
                    tool=self.name,
                    query=query,
                    results=[
                        {
                            "target_kind": r.get("object_type", ""),
                            "target_id": r.get("id"),
                            "path": "",
                            "score": r.get("score"),
                            "domains": r.get("domains", []),
                            "content": r.get("summary") or r.get("title", ""),
                        }
                        for r in results
                    ],
                )
            )

        return "\n\n".join(lines)


class ResearchNoteGetTool(Tool):
    """Retrieve a structured research note by ID."""

    accepts_context = True

    def __init__(self, *, workspace_resolver: Callable[[str | None], "Path"] | None = None) -> None:
        self._workspace_resolver = workspace_resolver

    @property
    def name(self) -> str:
        return "research_note_get"

    @property
    def description(self) -> str:
        return (
            "Retrieve a structured research note by ID. Use this after "
            "`structured_memory_search` when you find a relevant research_note result."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "The research note ID returned by structured_memory_search",
                }
            },
            "required": ["note_id"],
        }

    def _resolve_workspace(self, session_key: str | None) -> "Path | None":
        if self._workspace_resolver is None:
            return None
        return self._workspace_resolver(session_key)

    async def execute(self, _context: "ToolContext | None" = None, **kwargs: Any) -> str:
        workspace = self._resolve_workspace(_context.session_key if _context else None)
        if workspace is None:
            return "Research note retrieval is not available (workspace not resolved)."

        note_id = kwargs.get("note_id", "")
        if not note_id:
            return "Please provide a research note ID."

        store = StructuredMemoryStore(workspace)
        try:
            await store.ensure_kg()
            note = await store.get_research_note(note_id)
        finally:
            await store.close()
        if note is None:
            return f"Research note '{note_id}' not found."
        return json.dumps(note, ensure_ascii=False, indent=2)


class TaskMemoryGetTool(Tool):
    """Retrieve a structured task memory by ID."""

    accepts_context = True

    def __init__(self, *, workspace_resolver: Callable[[str | None], "Path"] | None = None) -> None:
        self._workspace_resolver = workspace_resolver

    @property
    def name(self) -> str:
        return "task_memory_get"

    @property
    def description(self) -> str:
        return (
            "Retrieve a structured task memory by ID. Use this after "
            "`structured_memory_search` when you find a relevant task result."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task memory ID returned by structured_memory_search",
                }
            },
            "required": ["task_id"],
        }

    def _resolve_workspace(self, session_key: str | None) -> "Path | None":
        if self._workspace_resolver is None:
            return None
        return self._workspace_resolver(session_key)

    async def execute(self, _context: "ToolContext | None" = None, **kwargs: Any) -> str:
        workspace = self._resolve_workspace(_context.session_key if _context else None)
        if workspace is None:
            return "Task memory retrieval is not available (workspace not resolved)."

        task_id = kwargs.get("task_id", "")
        if not task_id:
            return "Please provide a task memory ID."

        store = StructuredMemoryStore(workspace)
        try:
            await store.ensure_kg()
            task = await store.get_task_memory(task_id)
        finally:
            await store.close()
        if task is None:
            return f"Task memory '{task_id}' not found."
        return json.dumps(task, ensure_ascii=False, indent=2)


class DomainRuleGetTool(Tool):
    """Retrieve a structured domain rule by ID."""

    accepts_context = True

    def __init__(self, *, workspace_resolver: Callable[[str | None], "Path"] | None = None) -> None:
        self._workspace_resolver = workspace_resolver

    @property
    def name(self) -> str:
        return "domain_rule_get"

    @property
    def description(self) -> str:
        return (
            "Retrieve a structured domain rule by ID. Use this after "
            "`structured_memory_search` when you find a relevant rule result."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "rule_id": {
                    "type": "string",
                    "description": "The domain rule ID returned by structured_memory_search",
                }
            },
            "required": ["rule_id"],
        }

    def _resolve_workspace(self, session_key: str | None) -> "Path | None":
        if self._workspace_resolver is None:
            return None
        return self._workspace_resolver(session_key)

    async def execute(self, _context: "ToolContext | None" = None, **kwargs: Any) -> str:
        workspace = self._resolve_workspace(_context.session_key if _context else None)
        if workspace is None:
            return "Domain rule retrieval is not available (workspace not resolved)."

        rule_id = kwargs.get("rule_id", "")
        if not rule_id:
            return "Please provide a domain rule ID."

        store = StructuredMemoryStore(workspace)
        try:
            await store.ensure_kg()
            rule = await store.get_domain_rule(rule_id)
        finally:
            await store.close()
        if rule is None:
            return f"Domain rule '{rule_id}' not found."

        # Recall telemetry
        if _context and rule is not None:
            import asyncio

            from src.memory.recall_journal import append_recall_entries

            asyncio.create_task(
                append_recall_entries(
                    workspace=workspace,
                    session_key=_context.session_key,
                    tool=self.name,
                    query=rule_id,
                    results=[
                        {
                            "target_kind": "kg_rule",
                            "target_id": rule_id,
                            "path": "",
                            "score": None,
                            "domains": [],
                            "content": rule.get("content") or rule.get("title", ""),
                        }
                    ],
                )
            )

        return json.dumps(rule, ensure_ascii=False, indent=2)
