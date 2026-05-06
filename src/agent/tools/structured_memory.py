"""Structured memory query tools."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from src.agent.tools.base import ContextAwareTool
from src.memory.structured import StructuredMemoryStore

if TYPE_CHECKING:
    from pathlib import Path

    from src.agent.tools.context import ToolContext

WorkspaceResolver = Callable[[str | None], "Path | None"] | None


def _resolve_workspace(
    workspace_resolver: WorkspaceResolver,
    context: "ToolContext | None",
    *,
    unavailable_message: str,
) -> tuple["Path | None", str | None]:
    if workspace_resolver is None:
        return None, unavailable_message
    workspace = workspace_resolver(context.session_key if context else None)
    if workspace is None:
        return None, unavailable_message
    return workspace, None


def _required_text_arg(kwargs: dict[str, Any], name: str, label: str) -> tuple[str, str | None]:
    value = kwargs.get(name, "")
    if not value:
        return "", f"Please provide a {label}."
    return str(value), None


async def _load_structured_record(
    workspace: "Path",
    loader: Callable[[StructuredMemoryStore], Awaitable[dict[str, Any] | None]],
) -> dict[str, Any] | None:
    store = StructuredMemoryStore(workspace)
    try:
        await store.ensure_kg()
        return await loader(store)
    finally:
        await store.close()


def _dump_json(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, indent=2)


class StructuredMemorySearchTool(ContextAwareTool):
    """Search structured task, rule, and research-note objects."""

    def __init__(
        self,
        *,
        workspace_resolver: WorkspaceResolver = None,
        default_max_results: int = 6,
        recall_telemetry_enabled: bool = False,
    ) -> None:
        self._workspace_resolver = workspace_resolver
        self._default_max_results = max(1, int(default_max_results))
        self._recall_telemetry_enabled = bool(recall_telemetry_enabled)

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

    async def execute(self, _context: "ToolContext | None" = None, **kwargs: Any) -> str:
        workspace, error = _resolve_workspace(
            self._workspace_resolver,
            _context,
            unavailable_message="Structured memory search is not available (workspace not resolved).",
        )
        if error:
            return error
        assert workspace is not None

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
        if self._recall_telemetry_enabled and _context and results:
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


class ResearchNoteGetTool(ContextAwareTool):
    """Retrieve a structured research note by ID."""

    def __init__(self, *, workspace_resolver: WorkspaceResolver = None) -> None:
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

    async def execute(self, _context: "ToolContext | None" = None, **kwargs: Any) -> str:
        workspace, error = _resolve_workspace(
            self._workspace_resolver,
            _context,
            unavailable_message="Research note retrieval is not available (workspace not resolved).",
        )
        if error:
            return error
        assert workspace is not None

        note_id, error = _required_text_arg(kwargs, "note_id", "research note ID")
        if error:
            return error

        note = await _load_structured_record(
            workspace,
            lambda store: store.get_research_note(note_id),
        )
        if note is None:
            return f"Research note '{note_id}' not found."
        return _dump_json(note)


class TaskMemoryGetTool(ContextAwareTool):
    """Retrieve a structured task memory by ID."""

    def __init__(self, *, workspace_resolver: WorkspaceResolver = None) -> None:
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

    async def execute(self, _context: "ToolContext | None" = None, **kwargs: Any) -> str:
        workspace, error = _resolve_workspace(
            self._workspace_resolver,
            _context,
            unavailable_message="Task memory retrieval is not available (workspace not resolved).",
        )
        if error:
            return error
        assert workspace is not None

        task_id, error = _required_text_arg(kwargs, "task_id", "task memory ID")
        if error:
            return error

        task = await _load_structured_record(
            workspace,
            lambda store: store.get_task_memory(task_id),
        )
        if task is None:
            return f"Task memory '{task_id}' not found."
        return _dump_json(task)


class DomainRuleGetTool(ContextAwareTool):
    """Retrieve a structured domain rule by ID."""

    def __init__(
        self,
        *,
        workspace_resolver: WorkspaceResolver = None,
        recall_telemetry_enabled: bool = False,
    ) -> None:
        self._workspace_resolver = workspace_resolver
        self._recall_telemetry_enabled = bool(recall_telemetry_enabled)

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

    async def execute(self, _context: "ToolContext | None" = None, **kwargs: Any) -> str:
        workspace, error = _resolve_workspace(
            self._workspace_resolver,
            _context,
            unavailable_message="Domain rule retrieval is not available (workspace not resolved).",
        )
        if error:
            return error
        assert workspace is not None

        rule_id, error = _required_text_arg(kwargs, "rule_id", "domain rule ID")
        if error:
            return error

        rule = await _load_structured_record(
            workspace,
            lambda store: store.get_domain_rule(rule_id),
        )
        if rule is None:
            return f"Domain rule '{rule_id}' not found."

        # Recall telemetry
        if self._recall_telemetry_enabled and _context and rule is not None:
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

        return _dump_json(rule)
