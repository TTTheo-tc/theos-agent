"""Tool registry for dynamic tool management."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.agent.tools.base import Tool

if TYPE_CHECKING:
    from src.agent.approval import ApprovalGate
    from src.security.autonomy import AutonomyPolicy

ERROR_HINT = "\n\n[Analyze the error above and try a different approach.]"


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    # Tools allowed in plan mode (read-only exploration + discovery + exit).
    # browser is intentionally excluded: its readonly behavior depends on
    # instance construction (BrowserTool(readonly=True)), not registry state,
    # so plan mode cannot guarantee it won't perform write actions.
    PLAN_MODE_TOOLS: frozenset[str] = frozenset(
        {
            "read_file",
            "list_dir",
            "glob",
            "grep",
            "web_search",
            "web_fetch",
            "memory_search",
            "capability_search",
            "tool_search",
            "notebook_read",
            "skill_search",
            "mcp_search",
            "enter_plan_mode",
            "exit_plan_mode",
        }
    )

    def __init__(self, approval_gate: ApprovalGate | None = None):
        self._tools: dict[str, Tool] = {}
        self._deferred: dict[str, Tool] = {}
        self._activated: set[str] = set()
        self._plan_mode: bool = False
        self._approval_gate = approval_gate
        self._autonomy: AutonomyPolicy | None = None

    def set_autonomy(self, policy: AutonomyPolicy) -> None:
        """Attach an autonomy policy for tool-level enforcement."""
        self._autonomy = policy

    def set_approval_gate(self, gate: ApprovalGate) -> None:
        """Attach an approval gate (replaces any existing)."""
        self._approval_gate = gate

    @property
    def approval_gate(self) -> ApprovalGate | None:
        """Return the attached approval gate, if any."""
        return self._approval_gate

    @property
    def autonomy_policy(self) -> AutonomyPolicy | None:
        """Return the attached autonomy policy, if any."""
        return self._autonomy

    # --- Plan mode ---------------------------------------------------------

    @property
    def plan_mode(self) -> bool:
        """Whether the registry is in plan (read-only) mode."""
        return self._plan_mode

    def enter_plan_mode(self) -> None:
        """Restrict the registry to read-only tools."""
        self._plan_mode = True

    def exit_plan_mode(self) -> None:
        """Restore full tool access."""
        self._plan_mode = False

    # --- Registration ------------------------------------------------------

    def register(self, tool: Tool, *, deferred: bool = False) -> None:
        """Register a tool.

        Args:
            tool: The tool instance to register.
            deferred: If True, the tool is placed in the deferred pool and will
                not appear in ``get_definitions()`` until explicitly activated.
        """
        if deferred:
            self._deferred[tool.name] = tool
        else:
            self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name (removes from both active and deferred pools)."""
        self._tools.pop(name, None)
        self._deferred.pop(name, None)
        self._activated.discard(name)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name (searches both active and deferred pools)."""
        return self._tools.get(name) or self._deferred.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered (in either pool)."""
        return name in self._tools or name in self._deferred

    def activate(self, name: str) -> bool:
        """Move a deferred tool into the active pool.

        Returns True if the tool was deferred and is now active, False otherwise.
        """
        if name not in self._deferred or name in self._activated:
            return False
        tool = self._deferred[name]
        self._tools[name] = tool
        self._activated.add(name)
        return True

    def _pending_deferred_items(self) -> Iterator[tuple[str, Tool]]:
        for name, tool in self._deferred.items():
            if name not in self._activated:
                yield name, tool

    def search_deferred(self, query: str, max_results: int = 10) -> list[dict[str, str]]:
        """Keyword search over unactivated deferred tools.

        Scoring:
            - Name contains full query: +10
            - Name part (split by ``_``) contains query: +5
            - Description contains query: +3
            - Individual query words in name: +2 each
            - Individual query words in description: +1 each

        Returns a list of ``{"name": ..., "description": ...}`` sorted by
        score descending, then alphabetically by name.
        """
        scored: list[tuple[int, str, str]] = []

        for name, tool in self._pending_deferred_items():
            score = self._score_deferred_match(name, tool.description, query)
            if score > 0:
                scored.append((score, name, tool.description))

        scored.sort(key=lambda t: (-t[0], t[1]))
        return [{"name": n, "description": d} for _, n, d in scored[:max_results]]

    @staticmethod
    def _score_deferred_match(name: str, description: str, query: str) -> int:
        query_lower = query.lower()
        words = query_lower.split()
        score = 0
        name_lower = name.lower()
        desc_lower = description.lower()
        name_parts = name_lower.split("_")

        if query_lower in name_lower:
            score += 10
        if query_lower in name_parts:
            score += 5
        if query_lower in desc_lower:
            score += 3
        for word in words:
            if word in name_lower:
                score += 2
            if word in desc_lower:
                score += 1
        return score

    def get_deferred_summary(self) -> list[dict[str, str]]:
        """Return name and description for all unactivated deferred tools."""
        return [
            {"name": name, "description": tool.description}
            for name, tool in self._pending_deferred_items()
        ]

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format.

        In plan mode, only tools in ``PLAN_MODE_TOOLS`` are returned.
        """
        return [tool.to_schema() for tool in self._definition_tools()]

    def active_tool_names(self) -> list[str]:
        """Return tool names currently exposed to the model."""
        return [tool.name for tool in self._definition_tools()]

    def _definition_tools(self) -> list[Tool]:
        tools = list(self._tools.values())
        if self._plan_mode:
            return [tool for tool in tools if tool.name in self.PLAN_MODE_TOOLS]
        return tools

    @staticmethod
    def _assess_risk(tool: Tool, params: dict[str, Any]) -> str:
        """Return the dynamic risk level for a tool call."""
        if hasattr(tool, "assess_risk"):
            return tool.assess_risk(**params)
        return tool.risk_level

    def _get_active_tool(self, name: str) -> Tool | None:
        if name not in self._tools and name in self._deferred:
            self.activate(name)
        return self._tools.get(name)

    def _missing_tool_error(self, name: str) -> str:
        return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"

    def _plan_mode_error(self, name: str) -> str | None:
        if self._plan_mode and name not in self.PLAN_MODE_TOOLS:
            return (
                f"Error: Tool '{name}' is not available in plan mode. "
                "Use exit_plan_mode to return to normal mode."
            )
        return None

    @staticmethod
    def _owner_only_error(tool: Tool, name: str, context: Any) -> str | None:
        if not tool.owner_only or not context:
            return None

        from src.agent.tools.context import ToolContext

        if not isinstance(context, ToolContext) or context.sender_is_owner:
            return None
        if name == "agent" and context.allow_subagent_spawn:
            return None
        return "⚠ This tool is restricted to the bot owner."

    def _autonomy_error(self, name: str, params: dict[str, Any], risk: str) -> str | None:
        from src.security.autonomy import READONLY_SAFE_TOOLS

        if not self._autonomy:
            return None

        err = self._autonomy.check_tool_allowed(name, risk)
        if err:
            return f"⚠ {err}" + ERROR_HINT

        path = params.get("path") or params.get("file_path") or params.get("filepath")
        if path:
            err = self._autonomy.check_path_allowed(str(path))
            if err:
                return f"⚠ {err}" + ERROR_HINT

        command = params.get("command") or params.get("cmd")
        if command and name in ("bash", "process"):
            err = self._autonomy.check_command_allowed(str(command))
            if err:
                return f"⚠ {err}" + ERROR_HINT

        if name not in READONLY_SAFE_TOOLS:
            err = self._autonomy.check_rate_limit()
            if err:
                return f"⚠ {err}" + ERROR_HINT
        return None

    async def _approved_params(
        self,
        name: str,
        params: dict[str, Any],
        risk: str,
        context: Any,
    ) -> tuple[dict[str, Any], bool, str | None]:
        if not self._approval_gate:
            return params, False, None

        from src.agent.approval import RiskLevel

        risk_enum = RiskLevel(risk)
        session_key = getattr(context, "session_key", None) if context else None
        response = await self._approval_gate.check(name, params, risk_enum, session_key=session_key)
        if not response.approved:
            return params, False, f"⚠ Operation blocked (risk: {risk}): {response.reason}"
        if response.modified_args is not None:
            return response.modified_args, True, None
        return params, False, None

    def _params_after_approval(
        self,
        tool: Tool,
        name: str,
        params: dict[str, Any],
        risk: str,
        approved_params: dict[str, Any],
        *,
        modified: bool,
    ) -> tuple[dict[str, Any], str | None]:
        if modified:
            params = approved_params
            risk = self._assess_risk(tool, params)
            if autonomy_error := self._autonomy_error(name, params, risk):
                return params, autonomy_error
        return approved_params, None

    @staticmethod
    def _validation_error(tool: Tool, name: str, params: dict[str, Any]) -> str | None:
        errors = tool.validate_params(params)
        if not errors:
            return None
        return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + ERROR_HINT

    @staticmethod
    async def _execute_tool(tool: Tool, params: dict[str, Any], context: Any) -> str:
        if context and tool.requires_context:
            return await tool.execute(**params, _context=context)
        return await tool.execute(**params)

    def _record_success(self, name: str) -> None:
        if not self._autonomy:
            return

        from src.security.autonomy import READONLY_SAFE_TOOLS

        if name not in READONLY_SAFE_TOOLS:
            self._autonomy.record_action()

    async def execute(self, name: str, params: dict[str, Any], context: Any = None) -> str:
        """Execute a tool by name with given parameters."""
        tool = self._get_active_tool(name)
        if not tool:
            return self._missing_tool_error(name)

        if plan_error := self._plan_mode_error(name):
            return plan_error

        try:
            if owner_error := self._owner_only_error(tool, name, context):
                return owner_error

            risk = self._assess_risk(tool, params)
            if autonomy_error := self._autonomy_error(name, params, risk):
                return autonomy_error

            approved_params, modified, approval_error = await self._approved_params(
                name, params, risk, context
            )
            if approval_error:
                return approval_error
            params, approved_error = self._params_after_approval(
                tool,
                name,
                params,
                risk,
                approved_params,
                modified=modified,
            )
            if approved_error:
                return approved_error

            if validation_error := self._validation_error(tool, name, params):
                return validation_error

            result = await self._execute_tool(tool, params, context)
            if isinstance(result, str) and result.startswith("Error"):
                return result + ERROR_HINT

            self._record_success(name)

            return result
        except Exception as e:
            logger.opt(exception=True).warning("Tool {} failed", name)
            return f"Error executing {name}: {str(e)}" + ERROR_HINT

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names (both active and deferred)."""
        all_names = dict.fromkeys(list(self._tools.keys()) + list(self._deferred.keys()))
        return list(all_names)

    def __len__(self) -> int:
        all_names = set(self._tools) | set(self._deferred)
        return len(all_names)

    def __contains__(self, name: str) -> bool:
        return name in self._tools or name in self._deferred
