"""Meta-tool for searching and activating deferred tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.agent.tools.base import Tool

if TYPE_CHECKING:
    from src.agent.tools.registry import ToolRegistry

_MEMORY_SEARCH_TOOLS = {"memory_search", "structured_memory_search"}


class ToolSearchTool(Tool):
    """Search the deferred tool pool and activate matched tools.

    Supports three query modes:
    - Empty query: lists all unactivated deferred tools.
    - ``select:name1,name2``: activates exact tool names.
    - Free-text keyword: searches deferred tools and returns candidates without
      changing the active tool surface.
    """

    def __init__(self, registry: "ToolRegistry") -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "tool_search"

    @property
    def description(self) -> str:
        return (
            "Search deferred tools by keyword or exact name. "
            "Keyword queries return candidates; use 'select:name1,name2' to "
            "activate specific tools."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query. Use 'select:name1,name2' for exact activation, "
                        "or keywords to search tool names and descriptions."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of results to return (default: 10).",
                },
            },
            "required": ["query"],
        }

    @property
    def parallel_safe(self) -> bool:
        return True

    async def execute(self, query: str = "", max_results: int = 10, **kwargs: Any) -> str:
        # Mode 1: empty query -> list all deferred tools
        if not query.strip():
            return self._format_summary()

        # Mode 2: select:name1,name2 -> activate exact names
        if query.startswith("select:"):
            names = [n.strip() for n in query[len("select:") :].split(",") if n.strip()]
            return self._activate_by_names(names)

        # Mode 3: keyword search -> return candidate deferred tools
        matches = self._registry.search_deferred(query, max_results=max_results)
        if not matches:
            return "No matching deferred tools found."

        return self._format_matches(matches)

    def _format_summary(self) -> str:
        """Format all unactivated deferred tools as markdown."""
        summary = self._registry.get_deferred_summary()
        if not summary:
            return "No deferred tools available."
        lines = [f"**{len(summary)} deferred tool(s) available:**\n"]
        for item in summary:
            lines.append(f"- **{item['name']}**: {item['description']}")
        return "\n".join(lines)

    def _activate_by_names(self, names: list[str]) -> str:
        """Activate tools by exact name and return results."""
        activated: list[dict[str, str]] = []
        not_found: list[str] = []

        for name in names:
            tool = self._registry.get(name)
            if tool is None:
                not_found.append(name)
                continue
            self._registry.activate(name)
            activated.append({"name": name, "description": tool.description})

        if not activated and not_found:
            return f"No matching deferred tools found. Not found: {', '.join(not_found)}"

        lines = [f"**Activated {len(activated)} tool(s):**\n"]
        for item in activated:
            lines.append(f"- **{item['name']}**: {item['description']}")
        memory_search_tools = [item["name"] for item in activated if item["name"] in _MEMORY_SEARCH_TOOLS]
        if memory_search_tools:
            search_phrase = " or ".join(f"`{name}`" for name in memory_search_tools)
            lines.append(
                "\nFor historical recall questions not covered by injected memory, "
                f"call {search_phrase} before answering."
            )
        if not_found:
            lines.append(f"\nNot found: {', '.join(not_found)}")
        return "\n".join(lines)

    def _format_matches(self, matches: list[dict[str, str]]) -> str:
        """Format search matches as markdown candidates."""
        names = ",".join(m["name"] for m in matches)
        lines = [f"**Found {len(matches)} deferred tool candidate(s):**\n"]
        for m in matches:
            lines.append(f"- **{m['name']}**: {m['description']}")
        lines.append("\nActivate one or more with " f"`select:{names}`.")
        return "\n".join(lines)
