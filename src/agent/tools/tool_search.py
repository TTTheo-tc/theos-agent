"""Meta-tool for searching and activating deferred tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.agent.tools.base import Tool

if TYPE_CHECKING:
    from src.agent.tools.registry import ToolRegistry

_MEMORY_SEARCH_TOOLS = {"memory_search", "structured_memory_search"}


def _memory_recall_hint(tool_names: list[str]) -> str:
    memory_tools = [name for name in tool_names if name in _MEMORY_SEARCH_TOOLS]
    if not memory_tools:
        return ""
    search_phrase = " or ".join(f"`{name}`" for name in memory_tools)
    return (
        "\nFor historical recall questions not covered by injected memory, "
        f"call {search_phrase} before answering."
    )


class ToolSearchTool(Tool):
    """Search the deferred tool pool and activate matched tools.

    Supports three query modes:
    - Empty query: lists all unactivated deferred tools.
    - ``select:name1,name2``: activates exact tool names.
    - Free-text keyword: searches deferred tools and returns candidates without
      changing the active tool surface.
    """

    def __init__(self, registry: ToolRegistry) -> None:
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

    async def execute(self, query: str = "", max_results: int = 10, **kwargs: Any) -> str:
        del kwargs
        # Mode 1: empty query -> list all deferred tools
        if not query.strip():
            return self._format_summary()

        # Mode 2: select:name1,name2 -> activate exact names
        if query.startswith("select:"):
            return self._activate_by_names(self._parse_selection(query))

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
        lines.extend(f"- **{item['name']}**: {item['description']}" for item in summary)
        return "\n".join(lines)

    @staticmethod
    def _parse_selection(query: str) -> list[str]:
        return [name.strip() for name in query[len("select:") :].split(",") if name.strip()]

    def _activate_by_names(self, names: list[str]) -> str:
        """Activate tools by exact name and return results."""
        activated: list[dict[str, str]] = []
        already_active: list[str] = []
        not_found: list[str] = []

        for name in names:
            tool = self._registry.get(name)
            if tool is None:
                not_found.append(name)
                continue
            if self._registry.activate(name):
                activated.append({"name": name, "description": tool.description})
            else:
                already_active.append(name)

        if not activated and not already_active and not_found:
            return f"No matching deferred tools found. Not found: {', '.join(not_found)}"

        return self._format_activation_result(activated, already_active, not_found)

    @staticmethod
    def _format_activation_result(
        activated: list[dict[str, str]],
        already_active: list[str],
        not_found: list[str],
    ) -> str:
        lines = [f"**Activated {len(activated)} new tool(s):**\n"]
        lines.extend(f"- **{item['name']}**: {item['description']}" for item in activated)
        memory_hint = _memory_recall_hint([*(item["name"] for item in activated), *already_active])
        if memory_hint:
            lines.append(memory_hint)
        if already_active:
            lines.append(f"\nAlready active: {', '.join(already_active)}")
        if not_found:
            lines.append(f"\nNot found: {', '.join(not_found)}")
        return "\n".join(lines)

    def _format_matches(self, matches: list[dict[str, str]]) -> str:
        """Format search matches as markdown candidates."""
        names = ",".join(m["name"] for m in matches)
        lines = [f"**Found {len(matches)} deferred tool candidate(s):**\n"]
        lines.extend(f"- **{m['name']}**: {m['description']}" for m in matches)
        lines.append("\nActivate one or more with " f"`select:{names}`.")
        return "\n".join(lines)
