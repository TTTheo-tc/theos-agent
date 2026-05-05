"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import time as _time
import uuid as _uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from src.agent.context import ContextBuilder
from src.agent.loop_context import _EPHEMERAL_CONTEXT_TAG, TurnContextAssembler
from src.agent.loop_core import run_tool_loop
from src.agent.loop_finalize import TurnFinalizer
from src.agent.loop_genver import GenVerHandler
from src.agent.loop_memory import MemoryHandler
from src.agent.tools.context import ToolContext
from src.agent.tools.message import MessageTool
from src.agent.tools.registry import ToolRegistry
from src.bus.events import InboundMessage, OutboundMessage
from src.bus.queue import MessageBus
from src.channels.base import identity_matches
from src.providers.base import LLMProvider
from src.session.group_dispatcher import PerGroupDispatcher
from src.session.manager import Session, SessionManager
from src.session.runtime_state import build_session_runtime_state
from src.session.turn_store import TurnStore
from src.utils.text import strip_think as _strip_think_fn
from src.utils.text import tool_hint as _tool_hint_fn

if TYPE_CHECKING:
    from src.agent.approval import ApprovalGate
    from src.agent.mcp_manager import MCPManager
    from src.agent.subagent import SubagentManager
    from src.config.schema import (
        AgentRoleConfig,
        AutonomyConfig,
        ChannelsConfig,
        Config,
        GenVerConfig,
        MemoryConfig,
    )
    from src.cron.service import CronService
    from src.safety.layer import SafetyLayer
    from src.security.autonomy import AutonomyPolicy
    from src.store.dashboard_writer import DashboardWriter


class _NoopHookRunner:
    """Minimal hook runner used when no hook directory is configured."""

    hooks_dir = None

    async def run_pre_chat(self, user_message: str, workspace: Path | None = None) -> str | None:
        return None

    async def run_post_chat(self, *args: Any, **kwargs: Any) -> None:
        return None


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    # Lazy-initialized safety layer (shared across all sessions)
    _safety: "SafetyLayer | None" = None
    _entropy_sensitivity: float = 0.0

    @classmethod
    def _get_safety(cls) -> "SafetyLayer":
        if cls._safety is None:
            from src.safety.layer import SafetyLayer

            cls._safety = SafetyLayer(
                entropy_sensitivity=cls._entropy_sensitivity,
            )
        return cls._safety

    _EPHEMERAL_CONTEXT_TAG = _EPHEMERAL_CONTEXT_TAG  # re-export module constant

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        config: "Config",
        *,
        cron_service: "CronService | None" = None,
        session_manager: SessionManager | None = None,
        dashboard: "DashboardWriter | None" = None,
        channels_config_override: "ChannelsConfig | None" = None,
        channel_env: dict[str, str] | None = None,
    ):
        from src.config.schema import ExecToolConfig
        from src.security.secret_refs import resolve_data_secret_refs, resolve_secret_ref

        self.dashboard = dashboard
        self.bus = bus
        self.provider = provider
        workspace = config.workspace_path
        self.workspace = workspace
        self.model = config.agents.defaults.model or provider.get_default_model()
        self.temperature = config.agents.defaults.temperature
        self.max_tokens = config.agents.defaults.max_tokens
        self.max_iterations = config.agents.defaults.max_tool_iterations
        self.memory_window = config.agents.defaults.memory_window
        self.brave_api_key = resolve_secret_ref(config.tools.web.search.api_key) or None
        self._web_search_max_results = config.tools.web.search.max_results
        self._web_search_provider = config.tools.web.search.provider
        self._tavily_api_key = resolve_secret_ref(config.tools.web.search.tavily_api_key) or None
        # Web fetch config
        self._web_fetch_extractor = config.tools.web.fetch.extractor
        self._web_fetch_jina_api_key = (
            resolve_secret_ref(config.tools.web.fetch.jina_api_key) or None
        )
        self._web_fetch_firecrawl_enabled = config.tools.web.fetch.firecrawl_enabled
        self._web_fetch_firecrawl_api_key = (
            resolve_secret_ref(config.tools.web.fetch.firecrawl_api_key) or None
        )
        self._web_fetch_firecrawl_api_url = config.tools.web.fetch.firecrawl_api_url
        self._web_fetch_allowed_domains = list(config.tools.web.fetch.allowed_domains)
        self._web_fetch_blocked_domains = list(config.tools.web.fetch.blocked_domains)
        self._web_fetch_max_chars = config.tools.web.fetch.max_chars
        self.exec_config = config.tools.exec or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = config.tools.restrict_to_workspace
        self.group_memory_enabled = config.security.group_memory_enabled
        # Wire leak_sensitivity into the shared SafetyLayer
        type(self)._entropy_sensitivity = config.security.leak_sensitivity
        self._stock_config = config.tools.stock
        self._browser_config = config.tools.browser
        self._tool_profile = config.tools.profile
        self._provider_keys = config.get_provider_keys()
        self._channel_env = channel_env or {}
        self._knowledge_graph_enabled = config.knowledge_graph.enabled
        self.learning_enabled = config.learning.enabled
        self._mcp_server_configs = resolve_data_secret_refs(config.tools.mcp_servers)
        self._mcp_server_count = len(self._mcp_server_configs)
        self._mcp: "MCPManager | None" = None
        self._subagents: "SubagentManager | None" = None
        self._subagent_policy = getattr(config.agents, "subagents", None)
        self._genver_handler: GenVerHandler | None = None

        channels_config = channels_config_override or resolve_data_secret_refs(config.channels)
        self.channels_config = channels_config
        self.team_enabled = config.agents.team_enabled or config.agents.mode == "team"
        self.genver_enabled = config.agents.genver_enabled or config.agents.mode == "genver"
        self.genver_config = (
            config.agents.genver
            if config.agents.mode == "genver" and self.genver_enabled
            else None
        )
        self._root_agent_mode = (
            "team" if config.agents.mode == "team" and self.team_enabled else "single"
        )

        self.roles = config.agents.roles or {}
        # Merge workspace agent definitions (*.md files in agents/ dir)
        from src.agent.definitions import load_agent_definitions

        workspace_defs = load_agent_definitions(workspace / "agents")
        for name, defn in workspace_defs.items():
            if name not in self.roles:
                self.roles[name] = defn

        self.sessions = session_manager or SessionManager(workspace, config=config)
        self.turns = TurnStore(workspace)
        interrupted = self.turns.mark_interrupted_inflight()
        if interrupted:
            logger.warning("Marked {} in-flight turn(s) as interrupted on startup", interrupted)
        # Approval gate (HumanEscapeHatch)
        orchestrator_config = config.agents.orchestrator
        _gate = None
        if orchestrator_config and orchestrator_config.approval_gate.enabled:
            from src.agent.approval import ApprovalGate, RiskLevel

            auto_levels = {RiskLevel(lv) for lv in orchestrator_config.approval_gate.auto_approve}
            _gate = ApprovalGate(
                auto_approve_levels=auto_levels,
                timeout=float(orchestrator_config.approval_gate.timeout_seconds),
            )
        self._approval_gate = _gate
        self._autonomy_policy = self._build_autonomy_policy(config.security.autonomy, workspace)
        self.tools = self._new_tool_registry()

        hooks_dir = Path(config.hooks).expanduser() if self.learning_enabled and config.hooks else None
        if hooks_dir:
            from src.hooks.runner import HookRunner

            self.hooks = HookRunner(hooks_dir)
        else:
            self.hooks = _NoopHookRunner()

        # Owner-only tools use explicit owner IDs, not channel allowlists.
        owner_ids = channels_config.owner_ids if channels_config else []
        self._owner_ids: set[str] = {str(owner_id) for owner_id in owner_ids if owner_id}

        self._running = False
        self._orchestrator_config = orchestrator_config
        self._memory_config: MemoryConfig | None = config.memory

        # Composed memory handler
        self._memory = MemoryHandler(
            workspace=workspace,
            memory_config=config.memory,
            orchestrator_config=orchestrator_config,
            group_memory_enabled=config.security.group_memory_enabled,
            groups_base_dir=workspace.parent / "groups",
            structured_memory_enabled=self._knowledge_graph_enabled,
        )
        # Context assembler — must be after _memory so recall service is available
        self._context = TurnContextAssembler(
            workspace=workspace,
            roles=self.roles,
            recall_service=self._memory.recall,
            learning_enabled=self.learning_enabled,
        )
        # Composed turn finalizer
        # Use lambda so tests can patch AgentLoop._get_safety after construction
        self._finalizer = TurnFinalizer(
            hooks=self.hooks,
            safety_fn=lambda: type(self)._get_safety(),
            sessions=self.sessions,
            provider=provider,
        )
        # Phase 3: Per-group queue dispatcher — unified TurnLifecycle entry point
        from src.orchestrator.policies import GenVerExecutionPolicy, OrchestratorPolicy
        from src.orchestrator.turn_lifecycle import TurnLifecycle

        policies: list = []
        if orchestrator_config and orchestrator_config.enabled:
            policies.append(GenVerExecutionPolicy(agent=self))
            orchestrator_policy = OrchestratorPolicy(
                max_retries=orchestrator_config.max_retries,
                review_mode=orchestrator_config.review_mode,
                event_log_enabled=orchestrator_config.event_log_enabled,
                event_store_config=orchestrator_config.event_store,
                agent=self,
            )
            policies.append(orchestrator_policy)
        self._lifecycle = TurnLifecycle(self, policies=policies)
        self._dispatcher = PerGroupDispatcher(self._lifecycle.handle_message)
        self._register_default_tools()

    @property
    def subagents(self) -> "SubagentManager":
        """Lazily create the subagent manager when a subagent feature is used."""
        if self._subagents is None:
            from src.agent.subagent import SubagentManager

            self._subagents = SubagentManager(
                provider=self.provider,
                workspace=self.workspace,
                bus=self.bus,
                model=self.model,
                roles=self.roles,
                policy=self._subagent_policy,
            )
        return self._subagents

    @property
    def _genver(self) -> GenVerHandler:
        """Lazily create GenVer state when GenVer is enabled or directly used."""
        if self._genver_handler is None:
            self._genver_handler = GenVerHandler(
                provider=self.provider,
                workspace=self.workspace,
                bus=self.bus,
                turn_store=self.turns,
            )
        return self._genver_handler

    def _get_mcp_manager(self) -> "MCPManager":
        if self._mcp is None:
            from src.agent.mcp_manager import MCPManager

            self._mcp = MCPManager(self._mcp_server_configs)
        return self._mcp

    @staticmethod
    def _build_autonomy_policy(
        autonomy_config: "AutonomyConfig",
        workspace: Path,
    ) -> "AutonomyPolicy | None":
        """Attach autonomy only when the config differs from conservative defaults."""
        from src.config.schema import AutonomyConfig
        from src.security.autonomy import AutonomyPolicy

        if autonomy_config.model_dump(mode="json", by_alias=True) == AutonomyConfig().model_dump(
            mode="json", by_alias=True
        ):
            return None
        return AutonomyPolicy(autonomy_config, workspace)

    def _new_tool_registry(
        self,
        approval_gate: "ApprovalGate | None" = None,
    ) -> ToolRegistry:
        """Create a root tool registry with shared runtime policies attached."""
        registry = ToolRegistry(approval_gate=approval_gate or self._approval_gate)
        if self._autonomy_policy is not None:
            registry.set_autonomy(self._autonomy_policy)
        return registry

    def _profile_allows_any(self, names: set[str]) -> bool:
        from src.agent.tools.tool_profiles import profile_allows_any

        return profile_allows_any(self._tool_profile, names)

    def _needs_subagents_for_registration(self) -> bool:
        if self._root_agent_mode == "team" or self.is_genver:
            return True
        return self._profile_allows_any(
            {"agent", "subagent_wait", "subagent_kill", "subagents_list"}
        )

    def _is_owner(self, sender_id: str, channel: str = "") -> bool:
        """Check if a sender is the bot owner (fallback for legacy paths)."""
        if channel == "cli" or sender_id == "user":
            return True
        if not self._owner_ids:
            return False
        return identity_matches(sender_id, self._owner_ids)

    def _resolve_sender_is_owner(self, msg: InboundMessage, channel: str | None = None) -> bool:
        """Resolve ownership from explicit InboundMessage field, falling back to _is_owner."""
        if msg.sender_is_owner is not None:
            return msg.sender_is_owner
        return self._is_owner(msg.sender_id, channel or msg.channel)

    def _get_context_for_session(self, session_key: str) -> ContextBuilder:
        """Return a ContextBuilder for the session (per-group when enabled)."""
        return self._context.get_for_session(
            session_key,
            group_memory_enabled=self.group_memory_enabled,
            group_workspace_resolver=self._memory.get_group_workspace,
        )

    def _get_context_for_workspace(self, *, session_key: str, workspace: Path) -> ContextBuilder:
        """Return a transient ContextBuilder bound to a task workspace when needed."""
        return self._context.get_for_workspace(
            session_key=session_key,
            workspace=workspace,
            group_memory_enabled=self.group_memory_enabled,
            group_workspace_resolver=self._memory.get_group_workspace,
        )

    def _register_default_tools(self) -> None:
        """Register the default set of tools.

        Root tool mode is controlled by ``self._root_agent_mode``.
        ``roles`` only provide delegation role configs; they do not by themselves
        force the root agent into orchestrator-only tools.
        """
        from src.agent.tool_sets import register_standard_tools
        from src.agent.tools.registration import ToolRegistrationConfig

        allowed_dir = self.workspace if self.restrict_to_workspace else None
        ns_config = self._orchestrator_config.neuro_symbolic if self._orchestrator_config else None
        subagents = self.subagents if self._needs_subagents_for_registration() else None
        mcp_manager = self._get_mcp_manager() if self._mcp_server_count else None

        config = ToolRegistrationConfig(
            workspace=self.workspace,
            mode=self._root_agent_mode,
            profile=self._tool_profile,
            allowed_dir=allowed_dir,
            exec_config=self.exec_config,
            brave_api_key=self.brave_api_key,
            web_search_max_results=self._web_search_max_results,
            web_search_provider=self._web_search_provider,
            tavily_api_key=self._tavily_api_key,
            web_fetch_extractor=self._web_fetch_extractor,
            web_fetch_jina_api_key=self._web_fetch_jina_api_key,
            web_fetch_firecrawl_enabled=self._web_fetch_firecrawl_enabled,
            web_fetch_firecrawl_api_key=self._web_fetch_firecrawl_api_key,
            web_fetch_firecrawl_api_url=self._web_fetch_firecrawl_api_url,
            web_fetch_allowed_domains=self._web_fetch_allowed_domains,
            web_fetch_blocked_domains=self._web_fetch_blocked_domains,
            web_fetch_max_chars=self._web_fetch_max_chars,
            neuro_symbolic_config=ns_config,
            bus_publish=self.bus.publish_outbound,
            bus=self.bus,
            cron_service=self.cron_service,
            executor=subagents.executor if subagents is not None else None,
            subagent_manager=subagents,
            session_manager=self.sessions,
            turn_store=self.turns,
            subagent_store=subagents.store if subagents is not None else None,
            memory_index_resolver=self._memory.resolve_index_for_tools,
            memory_search_enabled=self._memory.search_enabled(),
            memory_search_max_results=self._memory.search_max_results(),
            memory_search_min_score=self._memory.search_min_score(),
            memory_recall_telemetry_enabled=self._memory.recall_telemetry_enabled(),
            structured_memory_enabled=self._knowledge_graph_enabled,
            structured_workspace_resolver=lambda sk: self._memory.resolve_structured_workspace_for_tools(
                sk,
                genver_workspace_resolver=lambda k: (
                    self._genver_handler.get_active_workspace(k)
                    if self._genver_handler is not None
                    else None
                ),
            ),
            stock_config=self._stock_config,
            provider_keys=self._provider_keys,
            channel_env=self._channel_env,
            provider=self.provider,
            autonomy_level=self._autonomy_policy.level if self._autonomy_policy else None,
            browser_config=self._browser_config,
            mcp_manager=mcp_manager,
        )
        register_standard_tools(self.tools, config)

    def rebuild_tools(self) -> None:
        """Rebuild the root tool registry for the current mode.

        Preserves plan mode state across rebuilds so that ``/reboot``,
        ``/agent``, ``/model`` etc. don't silently exit plan mode.
        """
        was_plan_mode = self.tools.plan_mode
        self.tools = self._new_tool_registry(approval_gate=self.tools.approval_gate)
        self._register_default_tools()
        if was_plan_mode:
            self.tools.enter_plan_mode()

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if not self._mcp_server_count:
            return
        await self._get_mcp_manager().connect(self.tools)

    _strip_think = staticmethod(_strip_think_fn)
    _tool_hint = staticmethod(_tool_hint_fn)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        tool_context: Any = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        session_key: str = "",
        active_workspace: Path | None = None,
    ) -> tuple[str | None, list[str], list[dict], dict[str, int]]:
        """Run the agent iteration loop. Returns (final_content, tools_used, messages, usage)."""
        keepalive = 0
        if self._orchestrator_config:
            keepalive = self._orchestrator_config.cache_keepalive_threshold_s

        # In-loop compaction callback — reuses MemoryHandler.maybe_compact
        async def _compact(msgs: list[dict]) -> list[dict]:
            return await self._memory.maybe_compact(
                msgs,
                provider=self.provider,
                model=self.model,
                memory_window=self.memory_window,
                session_key=session_key,
                workspace=active_workspace,
            )

        return await run_tool_loop(
            provider=self.provider,
            messages=initial_messages,
            tools=self.tools,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            max_iterations=self.max_iterations,
            add_assistant=self._context.global_context.add_assistant_message,
            add_tool_result=self._context.global_context.add_tool_result,
            on_progress=on_progress,
            on_content_delta=on_content_delta,
            tool_context=tool_context,
            cache_keepalive_threshold_s=keepalive,
            maybe_compact=_compact,
        )

    @property
    def mode(self) -> str:
        """Derived mode: 'genver' if genver_config, else the explicit root mode."""
        if self.genver_config is not None:
            return "genver"
        return self._root_agent_mode

    @property
    def is_genver(self) -> bool:
        return self.genver_config is not None

    def reload_roles(self, roles: dict[str, AgentRoleConfig] | None = None) -> None:
        """Hot-reload agent roles from the given dict (or from config on disk).

        Updates self.roles, context builders, and subagent manager in-place.
        """
        if roles is None:
            from src.config.loader import load_config

            roles = load_config().agents.roles

        self.roles = roles or {}
        # Re-merge workspace agent definitions (hot reload picks up new files)
        from src.agent.definitions import load_agent_definitions

        for name, defn in load_agent_definitions(self.workspace / "agents").items():
            if name not in self.roles:
                self.roles[name] = defn
        # Rebuild global context and clear per-session cache
        self._context.rebuild_global(roles=self.roles)
        # Update subagent manager
        if self._subagents is not None:
            self._subagents.roles = self.roles
        if self.mode == "genver":
            mode = "genver"
        elif self.mode == "team":
            mode = "team-agent"
        else:
            mode = "single-agent"
        logger.info("Roles reloaded — {} mode ({})", mode, list(self.roles) or "no roles")

    async def _handle_agent_command(self, msg: InboundMessage) -> OutboundMessage | None:
        """Handle ``/agent [single|team|genver]`` — delegates to slash_commands module."""
        from src.agent.slash_commands import handle_agent_command

        return await handle_agent_command(self, msg)

    # Sentinel values so the CLI interactive loop can detect "needs setup"
    _AGENT_TEAM_NEEDS_SETUP = "__AGENT_TEAM_NEEDS_SETUP__"
    _AGENT_GENVER_NEEDS_SETUP = "__AGENT_GENVER_NEEDS_SETUP__"

    def apply_genver_config(self, genver_config: "GenVerConfig") -> None:
        """Apply a GenVerConfig and switch to genver mode."""
        from src.agent.slash_commands import apply_genver_config

        apply_genver_config(self, genver_config)

    async def _handle_model_command(self, msg: InboundMessage) -> OutboundMessage:
        """Handle ``/model [name]`` — delegates to slash_commands module."""
        from src.agent.slash_commands import handle_model_command

        return await handle_model_command(self, msg)

    def get_diagnostics(self) -> dict[str, Any]:
        """Return a snapshot of the agent's configuration for display/logging."""
        diag: dict[str, Any] = {
            "model": self.model,
            "mode": self.mode,
            "tools": len(self.tools),
            "tool_names": self.tools.tool_names,
            "max_iterations": self.max_iterations,
            "memory_window": self.memory_window,
            "mcp_servers": (
                self._mcp.server_count if self._mcp is not None else self._mcp_server_count
            ),
            "orchestrator": bool(self._lifecycle.policies),
            "learning": self.learning_enabled,
            "team_enabled": self.team_enabled,
            "genver_enabled": self.genver_enabled,
            "hooks": str(self.hooks.hooks_dir) if self.hooks.hooks_dir else None,
        }
        if self.is_genver and self.genver_config:
            diag["genver"] = {
                "generator": self.genver_config.generator_model,
                "verifier": self.genver_config.verifier_model,
                "explorer": self.genver_config.explorer_model,
                "max_retries": self.genver_config.max_retries,
            }
        if self.roles:
            diag["roles"] = {name: cfg.model for name, cfg in self.roles.items()}
        return diag

    async def run(self) -> None:
        """Run the agent loop, using PerGroupDispatcher for concurrency."""
        self._running = True
        await self._connect_mcp()
        diag = self.get_diagnostics()
        logger.info(
            "Agent loop started | model={} mode={} tools={} mcp={}",
            diag["model"],
            diag["mode"],
            diag["tools"],
            diag["mcp_servers"],
        )

        try:
            while self._running:
                try:
                    msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                pending = (
                    self._genver_handler.get_pending_question(msg.session_key)
                    if self._genver_handler is not None
                    else None
                )
                if pending:
                    if pending.done():
                        self._genver_handler.clear_pending_question(msg.session_key)
                    else:
                        pending_turn_id = self._genver_handler.get_pending_turn_id(
                            msg.session_key
                        )
                        if pending_turn_id:
                            session = self.sessions.get_or_create(msg.session_key)
                            reply_entry = {
                                "role": "user",
                                "content": msg.content,
                                "timestamp": datetime.now().isoformat(),
                                "turn_id": pending_turn_id,
                                "metadata": {"genver_reply": True},
                            }
                            session.messages.append(reply_entry)
                            session.updated_at = datetime.now()
                            self.sessions.save(session)
                            if self._memory.tiers_enabled():
                                self._memory.tiers.buffer_entry(msg.session_key, reply_entry)
                            self.turns.record(
                                msg.session_key,
                                pending_turn_id,
                                "inferring",
                                resumed_from="waiting_user",
                                answer_preview=msg.content[:200],
                            )
                        pending.set_result(
                            "abort" if msg.content.strip().lower() == "/stop" else msg.content
                        )
                        if msg.content.strip().lower() == "/stop":
                            await self._handle_stop(msg)
                        continue

                if msg.content.strip().lower() == "/stop":
                    await self._handle_stop(msg)
                elif msg.content.strip().lower().startswith("/agent"):
                    resp = await self._handle_agent_command(msg)
                    if resp:
                        await self.bus.publish_outbound(resp)
                elif msg.content.strip().lower().startswith("/model"):
                    resp = await self._handle_model_command(msg)
                    await self.bus.publish_outbound(resp)
                else:
                    await self._dispatcher.dispatch(msg)
        finally:
            self._dispatcher.cancel_all()

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Cancel active worker and subagents for the session."""
        cancelled = self._dispatcher.cancel_group(msg.session_key)
        sub_cancelled = (
            await self._subagents.cancel_by_session(msg.session_key)
            if self._subagents is not None
            else 0
        )

        total = (1 if cancelled else 0) + sub_cancelled
        content = f"⏹ Stopped {total} task(s)." if total else "No active task to stop."
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=content,
            )
        )

    async def drain_and_consolidate(self, session_key: str = "cli:direct") -> None:
        """Flush memory tiers and run consolidation on exit if enough messages accumulated.

        Call this before exiting to ensure short sessions still get consolidated.
        Low watermark: 5 unconsolidated messages (skip trivial 1-2 message sessions).
        """
        low_watermark = 5
        if not self._memory.memory_enabled():
            return
        # Flush any buffered immediate-queue entries to SQLite
        if self._memory.tiers_enabled():
            await self._memory.tiers.flush_immediate(session_key)
        session = self.sessions.get_or_create(session_key)
        unconsolidated = len(session.messages) - session.last_consolidated
        if unconsolidated < low_watermark:
            return
        if self._memory.is_consolidating(session.key):
            return
        logger.info(
            "Session-end consolidation: {} unconsolidated messages for {}",
            unconsolidated,
            session_key,
        )
        try:
            await self._memory.consolidate(
                session,
                provider=self.provider,
                model=self.model,
                memory_window=self.memory_window,
                archive_all=True,
            )
            self.sessions.save(session)
        except Exception:
            logger.opt(exception=True).warning(
                "Session-end consolidation failed for {}", session_key
            )

    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp is not None:
            await self._mcp.close()

    async def close(self) -> None:
        """Close all async resources: lifecycle policies, memory DBs, MCP."""
        await self._lifecycle.close()
        await self._memory.close_dbs()
        await self.close_mcp()

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(self._memory.close_dbs())
        logger.info("Agent loop stopping")

    # ------------------------------------------------------------------
    # _process_message sub-methods (intra-file extraction, Task 4)
    # ------------------------------------------------------------------

    async def _handle_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """Handle system-originated messages (cron, heartbeat)."""
        key = msg.session_key  # Uses session_key_override if present
        channel, chat_id = key.split(":", 1) if ":" in key else ("cli", key)
        logger.info("Processing system message from {}", msg.sender_id)
        session = self.sessions.get_or_create(key)
        await self._memory.ensure_db(key)
        ctx = self._get_context_for_session(key)
        tool_ctx = ToolContext(
            channel=channel,
            chat_id=chat_id,
            message_id=msg.metadata.get("message_id"),
            session_key=key,
            sender_id=msg.sender_id,
            sender_is_owner=self._resolve_sender_is_owner(msg, channel),
        )
        history = session.get_history(max_messages=self.memory_window)
        messages = ctx.build_messages(
            history=history,
            current_message=msg.content,
            channel=channel,
            chat_id=chat_id,
            model=self.model,
            memory_config=self._memory_config,
            has_memory_tools=self._memory.search_enabled(),
            prompt_profile=(ContextBuilder._GENVER_GENERATOR_PROFILE if self.is_genver else None),
        )
        initial_count = len(messages)
        final_content, _, all_msgs, usage = await self._run_agent_loop(
            messages,
            tool_context=tool_ctx,
            session_key=key,
            active_workspace=ctx.group_workspace,
        )
        self._finalizer.save_turn(
            session,
            all_msgs,
            initial_count,
            usage=usage,
            user_message=msg.content,
            memory_tiers=self._memory.tiers_or_none(),
        )
        self.sessions.save(session)
        return OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=final_content or "Background task completed.",
        )

    async def _init_session(
        self, msg: InboundMessage, session_key: str | None = None
    ) -> tuple[str, Session, str, float]:
        """Load or create session, emit dashboard events.

        Returns (key, session, agent_id, t0).
        """
        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)
        await self._memory.ensure_db(key)

        _agent_id = _uuid.uuid4().hex[:16]
        _t0 = _time.monotonic()
        # Dashboard writes — TURN START (Phase 1 of 2)
        # These fire-and-forget writes give the web dashboard real-time visibility
        # into agent activity. They must happen BEFORE inference, not after.
        # Phase 2 (turn-end writes) is in TurnFinalizer.finalize_turn().
        if self.dashboard:
            asyncio.ensure_future(
                self.dashboard.upsert_session(key, msg.channel, message_count=len(session.messages))
            )
            asyncio.ensure_future(self.dashboard.insert_agent(_agent_id, key, model=self.model))
            asyncio.ensure_future(self.dashboard.emit_event(key, "message_in", agent_id=_agent_id))

        return key, session, _agent_id, _t0

    def _record_turn_checkpoint(
        self,
        session_key: str,
        turn_id: str,
        status: str,
        **metadata: Any,
    ) -> None:
        """Write a durable turn checkpoint without failing the live request."""
        try:
            self.turns.record(session_key, turn_id, status, **metadata)
        except Exception:
            logger.opt(exception=True).warning(
                "Failed to record turn checkpoint {} for {}", status, session_key
            )

    def _format_resume_summary(self, session: Session, key: str) -> str:
        """Build a factual /resume summary from durable turn state."""
        runtime = build_session_runtime_state(
            key,
            turn_store=self.turns,
            subagent_store=self._subagents.store if self._subagents is not None else None,
            recent_background_limit=3,
        )
        checkpoint = runtime.latest_turn
        recent_background = runtime.recent_background
        active_background = runtime.active_background
        if checkpoint is None:
            lines = [
                f"Session `{key}` has no recoverable turn checkpoints.\n"
                f"Messages on record: {len(session.messages)}."
            ]
            if runtime.recoverable:
                lines.append("- recoverable: yes")
            if recent_background:
                lines.append("")
                lines.append("Recent background tasks:")
                for cp in recent_background:
                    label = cp.metadata.get("label") or cp.task_id
                    lines.append(f"- `{label}`: `{cp.status}`")
            if runtime.next_step:
                lines.append("")
                lines.append(f"Next step: {runtime.next_step}")
            return "\n".join(lines)

        lines = [
            f"Session `{key}` latest turn:",
            f"- turn_id: `{checkpoint.turn_id}`",
            f"- status: `{checkpoint.status}`",
            f"- timestamp: `{checkpoint.timestamp}`",
            f"- messages on record: {len(session.messages)}",
            f"- recoverable: {'yes' if runtime.recoverable else 'no'}",
        ]
        if interrupted_from := checkpoint.metadata.get("interrupted_from"):
            lines.append(f"- interrupted_from: `{interrupted_from}`")
        if question := checkpoint.metadata.get("question"):
            lines.append(f"- pending_question: {question}")
        if reason := checkpoint.metadata.get("reason"):
            lines.append(f"- reason: {reason}")
        if error := checkpoint.metadata.get("error"):
            lines.append(f"- error: {error}")
        if active_background:
            lines.append(f"- active_background_tasks: {len(active_background)}")
        if recent_background:
            lines.append("")
            lines.append("Recent background tasks:")
            for cp in recent_background:
                label = cp.metadata.get("label") or cp.task_id
                lines.append(f"- `{label}`: `{cp.status}`")
        next_step = runtime.next_step
        if next_step:
            lines.append("")
            lines.append(f"Next step: {next_step}")
        return "\n".join(lines)

    async def _handle_slash_commands(
        self, msg: InboundMessage, session: Session, key: str
    ) -> OutboundMessage | None:
        """Handle slash commands. Returns response if handled, None to continue."""
        cmd = msg.content.strip().lower()

        # Channel capability gate — reject commands not allowed for this caller
        from src.agent.slash_commands import is_command_allowed

        if cmd.startswith("/") and not is_command_allowed(cmd, msg):
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="This command is not available in this context.",
            )
        if cmd == "/new":
            lock = self._memory.get_consolidation_lock(session.key)
            self._memory.add_consolidating(session.key)
            try:
                async with lock:
                    snapshot = session.messages[session.last_consolidated :]
                    if snapshot:
                        temp = Session(key=session.key)
                        temp.messages = list(snapshot)
                        if not await self._memory.consolidate(
                            temp,
                            provider=self.provider,
                            model=self.model,
                            memory_window=self.memory_window,
                            archive_all=True,
                        ):
                            return OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content="Memory archival failed, session not cleared. Please try again.",
                            )
            except Exception:
                logger.opt(exception=True).warning("/new archival failed for {}", session.key)
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="Memory archival failed, session not cleared. Please try again.",
                )
            finally:
                self._memory.discard_consolidating(session.key)
                self._memory.pop_consolidation_lock(session.key)

            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content="New session started."
            )
        if cmd == "/reboot":
            self.rebuild_tools()
            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Agent rebooted. Tools reloaded, session cleared.",
            )
        if cmd == "/restart":
            if not msg.sender_is_owner:
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="Only the bot owner can restart the gateway.",
                )
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="♻ Gateway 正在重启，稍后恢复服务...",
                metadata={"_restart_after_send": True},
            )
        if cmd.startswith("/ui"):
            from src.agent.slash_commands import handle_ui_command

            return await handle_ui_command(self, msg)
        if cmd == "/help":
            from src.agent.slash_commands import format_help_message

            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=format_help_message(learning_enabled=self.learning_enabled),
            )
        if cmd.startswith("/resume"):
            parts = msg.content.strip().split(maxsplit=1)
            target_key = key
            if len(parts) > 1:
                requested_key = parts[1].strip()
                if (
                    requested_key
                    and requested_key != key
                    and not self._resolve_sender_is_owner(msg)
                ):
                    return OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="Only the bot owner can inspect another session.",
                    )
                if requested_key:
                    target_key = requested_key
            target_session = self.sessions.get_or_create(target_key)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self._format_resume_summary(target_session, target_key),
            )
        if cmd == "/plan":
            if self.tools.plan_mode:
                self.tools.exit_plan_mode()
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="Exited plan mode. Full tool access restored.",
                )
            self.tools.enter_plan_mode()
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=(
                    "Entered plan mode. Only read-only tools are available. "
                    "Use /plan again to exit."
                ),
            )
        if cmd.startswith("/instinct"):
            if not self.learning_enabled:
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=(
                        "Learning features are disabled. Set `learning.enabled=true` "
                        "in config and use a source/full build with instinct assets "
                        "to enable `/instinct`."
                    ),
                )
            from src.agent.instinct_commands import handle_instinct_command

            return await handle_instinct_command(self, msg)
        if cmd.startswith("/agent"):
            return await self._handle_agent_command(msg)
        if cmd.startswith("/model"):
            return await self._handle_model_command(msg)
        if msg.channel == "cli":
            from src.agent.slash_commands import is_model_alias

            if is_model_alias(cmd):
                alias_msg = InboundMessage(
                    channel=msg.channel,
                    sender_id=msg.sender_id,
                    chat_id=msg.chat_id,
                    content=f"/model {cmd}",
                    metadata=msg.metadata or {},
                    sender_is_owner=msg.sender_is_owner,
                )
                return await self._handle_model_command(alias_msg)
        return None  # Not a slash command

    async def _run_inference(
        self,
        msg: InboundMessage,
        initial_messages: list[dict],
        on_progress: Callable[[str], Awaitable[None]] | None,
        tool_ctx: ToolContext,
        run_genver: bool,
        key: str,
        ctx: "ContextBuilder",
        active_workspace: Path | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        turn_id: str | None = None,
    ) -> tuple[str | None, list[str], list[dict], dict[str, int], int]:
        """Run LLM inference (standard or genver) with retry on invalid_request.

        Returns (content, tools_used, all_msgs, usage, initial_count).
        Exceptions bubble up to TurnLifecycle.handle_message for unified fallback.
        """
        initial_count = len(initial_messages)

        from src.agent.loop_core import ProviderAuthError

        try:
            return await self._run_inference_inner(
                msg,
                initial_messages,
                on_progress,
                tool_ctx,
                run_genver,
                key,
                ctx,
                active_workspace,
                on_content_delta,
                initial_count,
                turn_id,
            )
        except ProviderAuthError as e:
            logger.error("Provider auth error — not saving to session: {}", e)
            # Return error to user but do NOT save as assistant message in session.
            # This prevents auth errors from polluting conversation history.
            return (
                str(e),
                [],
                initial_messages,
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                initial_count,
            )

    async def _run_inference_inner(
        self,
        msg: InboundMessage,
        initial_messages: list[dict],
        on_progress: Callable[[str], Awaitable[None]] | None,
        tool_ctx: ToolContext,
        run_genver: bool,
        key: str,
        ctx: "ContextBuilder",
        active_workspace: Path | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        initial_count: int = 0,
        turn_id: str | None = None,
    ) -> tuple[str | None, list[str], list[dict], dict[str, int], int]:
        """Inner inference — may raise ProviderAuthError."""
        if run_genver:
            content, tools_used, all_msgs, usage = await self._genver.run_loop(
                initial_messages,
                tools=self.tools,
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                restrict_to_workspace=self.restrict_to_workspace,
                exec_config=self.exec_config,
                brave_api_key=self.brave_api_key,
                web_search_max_results=self._web_search_max_results,
                web_search_provider=self._web_search_provider,
                tavily_api_key=self._tavily_api_key,
                orchestrator_config=self._orchestrator_config,
                cron_service=self.cron_service,
                stock_config=self._stock_config,
                provider_keys=self._provider_keys,
                channel_env=self._channel_env,
                memory_handler=self._memory,
                genver_config=self.genver_config,
                context_add_assistant=self._context.global_context.add_assistant_message,
                context_add_tool_result=self._context.global_context.add_tool_result,
                subagent_manager=self.subagents,
                on_progress=on_progress,
                tool_context=tool_ctx,
                session_key=key,
                turn_id=turn_id,
            )
        else:
            content, tools_used, all_msgs, usage = await self._run_agent_loop(
                initial_messages,
                on_progress=on_progress,
                tool_context=tool_ctx,
                on_content_delta=on_content_delta,
                session_key=key,
                active_workspace=active_workspace,
            )
            if TurnFinalizer._is_invalid_request_error(content, usage):
                logger.warning(
                    "Primary prompt rejected with invalid_request_error; "
                    "retrying with clean context"
                )
                retry_messages = ctx.build_messages(
                    history=[],
                    current_message=msg.content,
                    skill_names=[],
                    media=msg.media if msg.media else None,
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    model=self.model,
                    memory_config=None,
                    has_memory_tools=False,
                )
                retry_initial_count = len(retry_messages)
                retry_content, retry_tools, retry_all_msgs, retry_usage = (
                    await self._run_agent_loop(
                        retry_messages,
                        on_progress=on_progress,
                        tool_context=tool_ctx,
                        on_content_delta=on_content_delta,
                        session_key=key,
                        active_workspace=active_workspace,
                    )
                )
                if not TurnFinalizer._is_invalid_request_error(retry_content, retry_usage):
                    logger.info("Clean-context retry succeeded after invalid_request_error")
                    initial_count = retry_initial_count
                    content, tools_used, all_msgs, usage = (
                        retry_content,
                        retry_tools,
                        retry_all_msgs,
                        retry_usage,
                    )
                else:
                    logger.warning(
                        "Clean-context retry also failed with invalid_request_error; "
                        "keeping original failure"
                    )

        return content, tools_used, all_msgs, usage, initial_count

    # ------------------------------------------------------------------
    # _process_message — thin orchestration skeleton
    # ------------------------------------------------------------------

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        turn_id: str | None = None,
        run_genver_override: bool | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # 1. System messages (cron / heartbeat)
        if msg.channel == "system":
            return await self._handle_system_message(msg)

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        # 2. Session initialization + dashboard
        key, session, _agent_id, _t0 = await self._init_session(msg, session_key)
        active_turn_id = turn_id or _uuid.uuid4().hex[:12]

        # 3. Slash commands (/new, /reboot, /help, /agent, /model, aliases)
        slash_result = await self._handle_slash_commands(msg, session, key)
        if slash_result is not None:
            return slash_result

        # 4. Consolidation threshold check (with AutoDream-style gates)
        unconsolidated = len(session.messages) - session.last_consolidated
        consolidation_threshold = self.memory_window  # default fallback
        if self._orchestrator_config and self._orchestrator_config.memory_tiers.enabled:
            consolidation_threshold = self._orchestrator_config.memory_tiers.consolidation_threshold

        # Gate: MEMORY.md size cap — force consolidation when memory file
        # exceeds 25KB.  Only uses stat() (no file read) to avoid I/O on
        # every message.
        _memory_oversize = False
        try:
            _ws = self._memory.resolve_structured_workspace_for_tools(key)
            _mem_path = _ws / "memory" / "MEMORY.md"
            if _mem_path.exists() and _mem_path.stat().st_size > 25_000:
                _memory_oversize = True
        except Exception:
            pass  # non-fatal — skip gate

        should_consolidate = (
            self._memory.memory_enabled()
            and (unconsolidated >= consolidation_threshold or _memory_oversize)
            and not self._memory.is_consolidating(session.key)
        )

        if should_consolidate:
            self._memory.add_consolidating(session.key)
            lock = self._memory.get_consolidation_lock(session.key)

            async def _consolidate_and_unlock():
                try:
                    async with lock:
                        await self._memory.consolidate(
                            session,
                            provider=self.provider,
                            model=self.model,
                            memory_window=self.memory_window,
                        )
                finally:
                    self._memory.discard_consolidating(session.key)
                    self._memory.pop_consolidation_lock(session.key)
                    _task = asyncio.current_task()
                    if _task is not None:
                        self._memory.discard_consolidation_task(_task)

            _task = asyncio.create_task(_consolidate_and_unlock())
            self._memory.add_consolidation_task(_task)

        # 5. ToolContext + MessageTool turn reset
        tool_ctx = ToolContext(
            channel=msg.channel,
            chat_id=msg.chat_id,
            message_id=msg.metadata.get("message_id"),
            session_key=key,
            sender_id=msg.sender_id,
            sender_is_owner=self._resolve_sender_is_owner(msg),
        )
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        # 6. GenVer routing decision
        detected_genver = self.is_genver and GenVerHandler.should_run_for_request(msg.content)
        run_genver = detected_genver if run_genver_override is None else run_genver_override
        if self.is_genver and not run_genver:
            logger.info(
                "Bypassing GenVer for non-code request in session {}: {}",
                key,
                msg.content[:120],
            )

        if run_genver:
            from src.genver.workspace import resolve_task_workspace

            task_workspace = resolve_task_workspace(self.workspace, msg.content)
        else:
            task_workspace = self.workspace
        ctx = self._get_context_for_workspace(session_key=key, workspace=task_workspace)

        # Progress callback for streaming updates
        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content,
                    metadata=meta,
                )
            )

        # Streaming delta callback. For normal channel flows we batch deltas onto
        # the existing bus `_progress` path. For direct callers that provided an
        # explicit `on_progress` callback, send stream chunks to that callback
        # instead of silently routing them through the bus.
        _stream_buffer: list[str] = []
        _stream_last_flush = _time.monotonic()
        _stream_flush_interval = 0.15  # seconds — batch deltas within this window

        if on_progress is not None:

            async def _stream_delta(text: str) -> None:
                await on_progress(text)

            async def _flush_stream_buffer() -> None:
                return None

        else:

            async def _stream_delta(text: str) -> None:
                nonlocal _stream_last_flush
                _stream_buffer.append(text)
                now = _time.monotonic()
                if now - _stream_last_flush >= _stream_flush_interval:
                    chunk = "".join(_stream_buffer)
                    _stream_buffer.clear()
                    _stream_last_flush = now
                    meta = dict(msg.metadata or {})
                    meta["_progress"] = True
                    meta["_progress_kind"] = "stream"
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=chunk,
                            metadata=meta,
                        )
                    )

            # Flush remaining buffer after inference completes
            async def _flush_stream_buffer() -> None:
                if _stream_buffer:
                    chunk = "".join(_stream_buffer)
                    _stream_buffer.clear()
                    meta = dict(msg.metadata or {})
                    meta["_progress"] = True
                    meta["_progress_kind"] = "stream"
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=chunk,
                            metadata=meta,
                        )
                    )

        # 7. Safety inbound scan
        inbound_safety = self._get_safety().scan_inbound(msg.content)
        if inbound_safety.should_block:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=inbound_safety.block_message or "Input blocked by safety checks.",
            )
        persisted_user = self.sessions.persist_user_message(
            session,
            msg.content,
            turn_id=active_turn_id,
            metadata={"channel": msg.channel},
        )
        if persisted_user and self._memory.tiers_enabled():
            self._memory.tiers.buffer_entry(key, session.messages[-1])
        self._record_turn_checkpoint(
            key,
            active_turn_id,
            "accepted",
            channel=msg.channel,
            chat_id=msg.chat_id,
            sender_id=msg.sender_id,
            run_mode="genver" if run_genver else "normal",
            model=self.model,
        )

        try:
            self._record_turn_checkpoint(
                key,
                active_turn_id,
                "building_context",
                workspace=task_workspace,
            )
            history = session.get_history(
                max_messages=self.memory_window,
                exclude_turn_id=active_turn_id,
            )

            # 8. Context building (pre-chat hook + context + ephemeral merge + compaction)
            initial_messages, initial_count, routing_domains, selected_primary, routed_skills = (
                await self._build_turn_messages(
                    msg,
                    key=key,
                    run_genver=run_genver,
                    task_workspace=task_workspace,
                    ctx=ctx,
                    history=history,
                )
            )

            # 9. LLM inference (agent loop or genver, with invalid_request retry)
            self._record_turn_checkpoint(
                key,
                active_turn_id,
                "inferring",
                workspace=task_workspace,
            )
            final_content, tools_used, all_msgs, usage, initial_count = await self._run_inference(
                msg,
                initial_messages,
                on_progress=on_progress or _bus_progress,
                tool_ctx=tool_ctx,
                run_genver=run_genver,
                key=key,
                ctx=ctx,
                active_workspace=task_workspace if run_genver else ctx.group_workspace,
                on_content_delta=_stream_delta,
                turn_id=active_turn_id,
            )
            await _flush_stream_buffer()

            # 10. Finalization (save turn, hooks, dashboard)
            self._record_turn_checkpoint(key, active_turn_id, "finalizing")
            response = await self._finalizer.finalize_turn(
                msg,
                key=key,
                session=session,
                final_content=final_content,
                tools_used=tools_used,
                all_msgs=all_msgs,
                initial_count=initial_count,
                usage=usage,
                run_genver=run_genver,
                task_workspace=task_workspace,
                routing_domains=routing_domains,
                selected_primary=selected_primary,
                routed_skills=routed_skills,
                agent_id=_agent_id,
                t0=_t0,
                dashboard=self.dashboard,
                memory=self._memory,
                model=self.model,
                bus=self.bus,
                genver_last_handoff=self._genver.pop_handoff(key) if run_genver else None,
                tools=self.tools,
                workspace=self.workspace,
                memory_tiers=self._memory.tiers_or_none(),
                turn_id=active_turn_id,
                persisted_user_message=persisted_user,
            )
            self._record_turn_checkpoint(
                key,
                active_turn_id,
                "completed",
                usage=usage,
                tools_used=tools_used,
            )
            return response
        except asyncio.CancelledError:
            await _flush_stream_buffer()
            self._record_turn_checkpoint(key, active_turn_id, "interrupted", reason="cancelled")
            raise
        except Exception as exc:
            await _flush_stream_buffer()
            self._record_turn_checkpoint(key, active_turn_id, "failed", error=str(exc))
            raise

    async def _build_turn_messages(
        self,
        msg: InboundMessage,
        *,
        key: str,
        run_genver: bool,
        task_workspace: Path,
        ctx: "ContextBuilder",
        history: list[dict],
    ) -> tuple[list[dict], int, list[str], str | None, list[str]]:
        """Build initial LLM messages from pre-chat hooks and context.

        Returns (initial_messages, initial_count, routing_domains,
        selected_primary, routed_skills).
        """
        return await self._context.build_turn_messages(
            msg,
            key=key,
            run_genver=run_genver,
            task_workspace=task_workspace,
            ctx=ctx,
            history=history,
            hooks=self.hooks,
            model=self.model,
            memory_config=self._memory_config,
            memory_search_enabled=self._memory.search_enabled(),
            build_structured_recall=self._memory.build_structured_recall,
            maybe_compact=lambda messages: self._memory.maybe_compact(
                messages,
                provider=self.provider,
                model=self.model,
                memory_window=self.memory_window,
                session_key=key,
                workspace=task_workspace,
                persisted_history=history,
            ),
            tool_activator=self.tools.activate,
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            sender_is_owner=True,
        )
        response = await self._process_message(
            msg, session_key=session_key, on_progress=on_progress
        )
        self._last_usage = response.metadata.get("usage") if response else None
        return response.content if response else ""
