"""Tool registry for dynamic tool management."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from src.agent.tools.base import Tool

if TYPE_CHECKING:
    from src.agent.approval import ApprovalGate
    from src.security.autonomy import AutonomyPolicy


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
        query_lower = query.lower()
        words = query_lower.split()
        scored: list[tuple[int, str, str]] = []

        for name, tool in self._deferred.items():
            if name in self._activated:
                continue
            score = 0
            name_lower = name.lower()
            desc_lower = tool.description.lower()
            name_parts = name_lower.split("_")

            if query_lower in name_lower:
                score += 10
            if query_lower in name_parts:
                score += 5
            if query_lower in desc_lower:
                score += 3
            for w in words:
                if w in name_lower:
                    score += 2
                if w in desc_lower:
                    score += 1
            if score > 0:
                scored.append((score, name, tool.description))

        scored.sort(key=lambda t: (-t[0], t[1]))
        return [{"name": n, "description": d} for _, n, d in scored[:max_results]]

    def get_deferred_summary(self) -> list[dict[str, str]]:
        """Return name and description for all unactivated deferred tools."""
        return [
            {"name": name, "description": tool.description}
            for name, tool in self._deferred.items()
            if name not in self._activated
        ]

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format.

        In plan mode, only tools in ``PLAN_MODE_TOOLS`` are returned.
        """
        tools = self._tools.values()
        if self._plan_mode:
            tools = [t for t in tools if t.name in self.PLAN_MODE_TOOLS]
        return [tool.to_schema() for tool in tools]

    @staticmethod
    def _assess_risk(tool: Tool, params: dict[str, Any]) -> str:
        """Return the dynamic risk level for a tool call."""
        if hasattr(tool, "assess_risk"):
            return tool.assess_risk(**params)
        return tool.risk_level

    async def execute(self, name: str, params: dict[str, Any], context: Any = None) -> str:
        """Execute a tool by name with given parameters."""
        from src.security.autonomy import READONLY_SAFE_TOOLS

        _hint = "\n\n[Analyze the error above and try a different approach.]"

        # Auto-activate deferred tools on direct call
        if name not in self._tools and name in self._deferred:
            self.activate(name)

        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"

        # Plan mode: block tools not in the allowed set
        if self._plan_mode and name not in self.PLAN_MODE_TOOLS:
            return (
                f"Error: Tool '{name}' is not available in plan mode. "
                "Use exit_plan_mode to return to normal mode."
            )

        try:
            # Owner-only check
            if tool.owner_only and context:
                from src.agent.tools.context import ToolContext

                if isinstance(context, ToolContext) and not context.sender_is_owner:
                    # Targeted exemption: agent tool with opt-in nested spawn
                    if name == "agent" and context.allow_subagent_spawn:
                        pass  # allowed
                    else:
                        return "⚠ This tool is restricted to the bot owner."

            risk = self._assess_risk(tool, params)

            # --- Autonomy policy checks ---
            if self._autonomy:
                err = self._autonomy.check_tool_allowed(name, risk)
                if err:
                    return f"⚠ {err}" + _hint

                # Path checks for file tools
                _path = params.get("path") or params.get("file_path") or params.get("filepath")
                if _path:
                    err = self._autonomy.check_path_allowed(str(_path))
                    if err:
                        return f"⚠ {err}" + _hint

                # Command checks for shell tools
                _cmd = params.get("command") or params.get("cmd")
                if _cmd and name in ("bash", "process"):
                    err = self._autonomy.check_command_allowed(str(_cmd))
                    if err:
                        return f"⚠ {err}" + _hint

                # Rate limit for non-readonly tools
                if name not in READONLY_SAFE_TOOLS:
                    err = self._autonomy.check_rate_limit()
                    if err:
                        return f"⚠ {err}" + _hint

            # Approval gate check. The gate owns auto-approval levels; autonomy
            # should not bypass it when both are configured.
            if self._approval_gate:
                from src.agent.approval import RiskLevel

                risk_enum = RiskLevel(risk)
                session_key = getattr(context, "session_key", None) if context else None
                response = await self._approval_gate.check(
                    name, params, risk_enum, session_key=session_key
                )
                if not response.approved:
                    return f"⚠ Operation blocked (risk: {risk}): {response.reason}"
                if response.modified_args is not None:
                    params = response.modified_args

            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + _hint
            if context and tool.requires_context:
                result = await tool.execute(**params, _context=context)
            else:
                result = await tool.execute(**params)
            if isinstance(result, str) and result.startswith("Error"):
                return result + _hint

            # Record successful write action for rate limiting
            if self._autonomy and name not in READONLY_SAFE_TOOLS:
                self._autonomy.record_action()

            return result
        except Exception as e:
            logger.opt(exception=True).warning("Tool {} failed", name)
            return f"Error executing {name}: {str(e)}" + _hint

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
