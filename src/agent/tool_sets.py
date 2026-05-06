"""Centralized tool registration helpers.

Provides ``register_standard_tools`` to avoid duplicating registration logic
across AgentLoop, SubagentManager, and GenVerEngine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.agent.tools.registry import ToolRegistry
from src.agent.tools.tool_profiles import ALWAYS_ON_TOOLS, expand_groups, resolve_profile

if TYPE_CHECKING:
    from src.agent.tools.base import Tool
    from src.agent.tools.registration import ToolRegistrationConfig


_FEISHU_TOOLS = {
    "feishu_read": "FeishuReadTool",
    "feishu_search": "FeishuSearchTool",
    "feishu_list": "FeishuListTool",
    "feishu_spaces": "FeishuSpacesTool",
    "feishu_calendar": "FeishuCalendarTool",
    "feishu_edit": "FeishuEditTool",
    "feishu_create": "FeishuCreateTool",
    "feishu_send": "FeishuSendTool",
    "feishu_comments": "FeishuCommentsTool",
    "feishu_download": "FeishuDownloadTool",
    "feishu_info": "FeishuInfoTool",
    "feishu_sheet": "FeishuSheetTool",
    "feishu_task": "FeishuTaskTool",
    "feishu_perm": "FeishuPermTool",
    "feishu_chat": "FeishuChatTool",
    "feishu_file": "FeishuFileTool",
    "feishu_contact": "FeishuContactTool",
}


@dataclass
class _RegistrationState:
    registry: ToolRegistry
    config: "ToolRegistrationConfig"
    exec_config: object
    profile_set: set[str] | None
    expanded_allowed: set[str] | None
    deny_tools: set[str]

    @classmethod
    def build(cls, registry: ToolRegistry, config: "ToolRegistrationConfig") -> "_RegistrationState":
        from src.config.schema import ExecToolConfig

        return cls(
            registry=registry,
            config=config,
            exec_config=config.exec_config or ExecToolConfig(),
            profile_set=resolve_profile(config.profile) if config.profile is not None else None,
            expanded_allowed=expand_groups(config.allowed_tools),
            deny_tools=config.deny_tools or set(),
        )

    @property
    def mode(self) -> str:
        return self.config.mode

    @property
    def is_team_mode(self) -> bool:
        return self.mode == "team"

    def should(self, name: str) -> bool:
        if name in self.deny_tools:
            return False
        if self.profile_set is not None and name not in self.profile_set:
            return False
        if self.expanded_allowed is not None and name not in self.expanded_allowed:
            return False
        return True

    def register(self, tool: "Tool") -> None:
        self.registry.register(tool, deferred=tool.name not in ALWAYS_ON_TOOLS)

    def register_always(self, tool: "Tool") -> None:
        self.registry.register(tool)


def register_standard_tools(
    registry: ToolRegistry,
    config: "ToolRegistrationConfig",
) -> None:
    """Register the standard tool set on *registry*.

    Parameters
    ----------
    config
        A :class:`ToolRegistrationConfig` holding all registration parameters:

        * ``mode`` — ``"single"`` (full toolset), ``"team"`` (orchestrator),
          ``"subagent"`` (filtered by *allowed_tools*), or ``"verifier"``
          (read-only fs + bash).
        * ``profile`` — Named tool profile (overrides *mode*-based selection).
        * ``allowed_tools`` — When *mode* is ``"subagent"``, only tools whose
          names are in this set are registered.  ``None`` means register all
          applicable tools.  Supports group references (e.g. ``"group:fs"``).
    """
    state = _RegistrationState.build(registry, config)
    if state.mode == "verifier":
        _register_verifier_tools(state)
        return

    _register_filesystem_tools(state)
    _register_notebook_tools(state)
    _register_todo_tools(state)
    _register_exec_tools(state)
    _register_web_and_discovery_tools(state)
    _register_plan_mode_tools(state)
    _register_memory_tools(state)
    _register_analysis_tools(state)
    _register_browser_tools(state)
    _register_communication_tools(state)
    _register_nested_subagent_tools(state)
    _register_feishu_tools(state)
    _register_session_tools(state)


def _register_verifier_tools(state: _RegistrationState) -> None:
    from src.agent.tools.fs_list import ListDirTool
    from src.agent.tools.fs_read import ReadFileTool
    from src.agent.tools.fs_search import GlobTool, GrepTool
    from src.agent.tools.shell import ExecTool

    config = state.config
    state.register_always(ExecTool(working_dir=str(config.workspace), timeout=300))
    state.register_always(
        ReadFileTool(workspace=config.workspace, allowed_dir=config.allowed_dir)
    )
    state.register_always(GlobTool(workspace=config.workspace, allowed_dir=config.allowed_dir))
    state.register_always(GrepTool(workspace=config.workspace, allowed_dir=config.allowed_dir))
    state.register_always(ListDirTool(workspace=config.workspace, allowed_dir=config.allowed_dir))


def _register_filesystem_tools(state: _RegistrationState) -> None:
    config = state.config

    if state.should("read_file"):
        from src.agent.tools.fs_read import ReadFileTool

        state.register(ReadFileTool(workspace=config.workspace, allowed_dir=config.allowed_dir))

    if state.is_team_mode:
        if state.should("write_docs"):
            from src.agent.tools.fs_write import DocWriteFileTool

            state.register(
                DocWriteFileTool(workspace=config.workspace, allowed_dir=config.allowed_dir)
            )
    else:
        if state.should("write_file"):
            from src.agent.tools.fs_write import WriteFileTool

            state.register(
                WriteFileTool(
                    workspace=config.workspace,
                    allowed_dir=config.allowed_dir,
                    neuro_symbolic_config=config.neuro_symbolic_config,
                )
            )
        if state.should("edit_file"):
            from src.agent.tools.fs_edit import EditFileTool

            state.register(
                EditFileTool(
                    workspace=config.workspace,
                    allowed_dir=config.allowed_dir,
                    neuro_symbolic_config=config.neuro_symbolic_config,
                )
            )

    if state.should("list_dir"):
        from src.agent.tools.fs_list import ListDirTool

        state.register(ListDirTool(workspace=config.workspace, allowed_dir=config.allowed_dir))
    if state.should("glob"):
        from src.agent.tools.fs_search import GlobTool

        state.register(GlobTool(workspace=config.workspace, allowed_dir=config.allowed_dir))
    if state.should("grep"):
        from src.agent.tools.fs_search import GrepTool

        state.register(GrepTool(workspace=config.workspace, allowed_dir=config.allowed_dir))

    if not state.is_team_mode and state.should("multi_edit"):
        from src.agent.tools.fs_edit import MultiEditTool

        state.register(MultiEditTool(workspace=config.workspace, allowed_dir=config.allowed_dir))

    if not state.is_team_mode and state.should("apply_patch"):
        from src.agent.tools.apply_patch import ApplyPatchTool

        state.register(ApplyPatchTool(workspace=config.workspace, allowed_dir=config.allowed_dir))


def _register_notebook_tools(state: _RegistrationState) -> None:
    config = state.config

    if state.should("notebook_read"):
        from src.agent.tools.notebook import NotebookReadTool

        state.register(NotebookReadTool(workspace=config.workspace, allowed_dir=config.allowed_dir))
    if not state.is_team_mode and state.should("notebook_edit"):
        from src.agent.tools.notebook import NotebookEditTool

        state.register(NotebookEditTool(workspace=config.workspace, allowed_dir=config.allowed_dir))


def _register_todo_tools(state: _RegistrationState) -> None:
    if state.mode == "subagent" or not state.should("todo"):
        return

    from src.agent.tools.todo import (
        TaskCreateTool,
        TaskGetTool,
        TaskListTool,
        TaskUpdateTool,
        TodoTool,
    )

    workspace = state.config.workspace
    state.register(TodoTool(workspace=workspace))
    state.register(TaskCreateTool(workspace=workspace))
    state.register(TaskListTool(workspace=workspace))
    state.register(TaskUpdateTool(workspace=workspace))
    state.register(TaskGetTool(workspace=workspace))


def _register_exec_tools(state: _RegistrationState) -> None:
    config = state.config
    exec_config = state.exec_config

    if state.should("bash"):
        from src.agent.tools.shell import ExecTool, SafeExecTool

        exec_kwargs = dict(
            working_dir=str(config.workspace),
            timeout=exec_config.timeout,
            restrict_to_workspace=config.allowed_dir is not None,
            path_append=exec_config.path_append,
            env_passthrough=exec_config.env_passthrough,
        )
        if state.is_team_mode:
            state.register(SafeExecTool(**exec_kwargs))
        else:
            state.register(ExecTool(**exec_kwargs))

    if not state.is_team_mode and state.should("process"):
        from src.agent.tools.process import ProcessTool

        state.register(ProcessTool(working_dir=str(config.workspace)))

    if not state.is_team_mode and state.should("gateway_restart"):
        from src.agent.tools.gateway_restart import GatewayRestartTool

        state.register(GatewayRestartTool())


def _register_web_and_discovery_tools(state: _RegistrationState) -> None:
    config = state.config

    if state.should("web_search"):
        from src.agent.tools.web_search import WebSearchTool

        state.register(
            WebSearchTool(
                api_key=config.brave_api_key,
                max_results=config.web_search_max_results,
                provider=config.web_search_provider,
                tavily_api_key=config.tavily_api_key,
            )
        )
    if state.should("web_fetch"):
        from src.agent.tools.web_fetch import WebFetchTool

        state.register(
            WebFetchTool(
                max_chars=config.web_fetch_max_chars,
                extractor=config.web_fetch_extractor,
                jina_api_key=config.web_fetch_jina_api_key,
                firecrawl_enabled=config.web_fetch_firecrawl_enabled,
                firecrawl_api_key=config.web_fetch_firecrawl_api_key,
                firecrawl_api_url=config.web_fetch_firecrawl_api_url,
                allowed_domains=config.web_fetch_allowed_domains,
                blocked_domains=config.web_fetch_blocked_domains,
            )
        )
    if state.should("http_request"):
        from src.agent.tools.web_http import HttpRequestTool

        state.register(HttpRequestTool())
    if state.should("image_search"):
        from src.agent.tools.web_image_search import ImageSearchTool

        state.register(ImageSearchTool())

    if state.should("capability_search"):
        from src.agent.tools.capability_search import CapabilitySearchTool

        state.register(CapabilitySearchTool(workspace=config.workspace, manager=config.mcp_manager))
    if state.should("tool_search"):
        from src.agent.tools.tool_search import ToolSearchTool

        state.register_always(ToolSearchTool(registry=state.registry))
    if state.should("skill_search"):
        from src.agent.tools.skill_search import SkillSearchTool

        state.register(SkillSearchTool(workspace=config.workspace))
    if config.mcp_manager is not None and state.should("mcp_search"):
        from src.agent.tools.mcp_search import MCPToolSearch

        state.register(MCPToolSearch(workspace=config.workspace, manager=config.mcp_manager))


def _register_plan_mode_tools(state: _RegistrationState) -> None:
    if state.should("enter_plan_mode"):
        from src.agent.tools.plan_mode import EnterPlanModeTool

        state.register_always(EnterPlanModeTool(registry=state.registry))
    if state.should("exit_plan_mode"):
        from src.agent.tools.plan_mode import ExitPlanModeTool

        state.register_always(ExitPlanModeTool(registry=state.registry))


def _register_memory_tools(state: _RegistrationState) -> None:
    config = state.config
    if config.memory_search_enabled and config.memory_index_resolver is not None:
        if state.should("memory_search"):
            from src.agent.tools.memory_search import MemorySearchTool

            state.register(
                MemorySearchTool(
                    index_resolver=config.memory_index_resolver,
                    workspace_resolver=config.structured_workspace_resolver,
                    default_max_results=config.memory_search_max_results,
                    default_min_score=config.memory_search_min_score,
                    recall_telemetry_enabled=config.memory_recall_telemetry_enabled,
                )
            )
        if state.should("memory_get"):
            from src.agent.tools.memory_search import MemoryGetTool

            state.register(MemoryGetTool(index_resolver=config.memory_index_resolver))

    if config.structured_memory_enabled and config.structured_workspace_resolver is not None:
        if state.should("structured_memory_search"):
            from src.agent.tools.structured_memory import StructuredMemorySearchTool

            state.register(
                StructuredMemorySearchTool(
                    workspace_resolver=config.structured_workspace_resolver,
                    default_max_results=config.memory_search_max_results,
                    recall_telemetry_enabled=config.memory_recall_telemetry_enabled,
                )
            )
        if state.should("research_note_get"):
            from src.agent.tools.structured_memory import ResearchNoteGetTool

            state.register(ResearchNoteGetTool(workspace_resolver=config.structured_workspace_resolver))
        if state.should("task_memory_get"):
            from src.agent.tools.structured_memory import TaskMemoryGetTool

            state.register(TaskMemoryGetTool(workspace_resolver=config.structured_workspace_resolver))
        if state.should("domain_rule_get"):
            from src.agent.tools.structured_memory import DomainRuleGetTool

            state.register(
                DomainRuleGetTool(
                    workspace_resolver=config.structured_workspace_resolver,
                    recall_telemetry_enabled=config.memory_recall_telemetry_enabled,
                )
            )


def _register_analysis_tools(state: _RegistrationState) -> None:
    config = state.config

    if state.mode in ("single", "team") and state.should("stock_analysis"):
        if config.stock_config and config.stock_config.enabled:
            from src.agent.tools.stock import StockAnalysisTool

            state.register(
                StockAnalysisTool(
                    stock_config=config.stock_config,
                    provider_keys=config.provider_keys or {},
                    brave_api_key=config.brave_api_key,
                    channel_env=config.channel_env,
                )
            )

    if state.mode in ("single", "team") and state.should("vendor_study"):
        from src.agent.tools.vendor_study import VendorStudyTool

        study_tool = VendorStudyTool()
        if study_tool.study_guide_path.exists():
            state.register(study_tool)

    if config.provider is not None and state.should("image_analyze"):
        from src.agent.tools.image import ImageAnalyzeTool

        state.register(ImageAnalyzeTool(provider=config.provider))

    if state.should("pdf"):
        from src.agent.tools.pdf import PdfTool

        state.register(PdfTool(provider=config.provider))

    if state.should("tts"):
        from src.agent.tools.tts import TtsTool

        state.register(TtsTool(workspace=config.workspace))


def _register_browser_tools(state: _RegistrationState) -> None:
    if not state.should("browser"):
        return

    from src.agent.tools.browser import BrowserTool
    from src.security.autonomy import AutonomyLevel

    config = state.config
    browser_config = config.browser_config
    if browser_config is not None and not getattr(browser_config, "enabled", True):
        return

    readonly = bool(config.autonomy_level and config.autonomy_level == AutonomyLevel.READONLY)
    state.register(BrowserTool(workspace=config.workspace, config=browser_config, readonly=readonly))


def _register_communication_tools(state: _RegistrationState) -> None:
    if state.mode not in ("single", "team"):
        return

    config = state.config
    if config.bus_publish is not None and state.should("message"):
        from src.agent.tools.message import MessageTool

        state.register(MessageTool(send_callback=config.bus_publish))
    if config.subagent_manager is not None and state.should("agent"):
        from src.agent.tools.spawn import AgentTool

        state.register(AgentTool(manager=config.subagent_manager))
    if config.executor is not None and state.should("subagent_wait"):
        from src.agent.tools.subagent_wait import SubagentWaitTool

        state.register(SubagentWaitTool(executor=config.executor))
    if config.executor is not None and state.should("subagent_kill"):
        from src.agent.tools.subagent_kill import SubagentKillTool

        state.register(SubagentKillTool(executor=config.executor))
    if config.cron_service is not None and state.should("cron"):
        from src.agent.tools.cron import CronTool

        state.register(CronTool(config.cron_service))


def _register_nested_subagent_tools(state: _RegistrationState) -> None:
    config = state.config
    if state.mode != "subagent" or config.executor is None:
        return

    if state.should("agent") and config.subagent_manager is not None:
        from src.agent.tools.spawn import AgentTool

        state.register(AgentTool(manager=config.subagent_manager))
    if state.should("subagent_wait"):
        from src.agent.tools.subagent_wait import SubagentWaitTool

        state.register(SubagentWaitTool(executor=config.executor))
    if state.should("subagent_kill"):
        from src.agent.tools.subagent_kill import SubagentKillTool

        state.register(SubagentKillTool(executor=config.executor))
    if state.should("subagents_list") and config.subagent_manager is not None:
        from src.agent.tools.sessions import SubagentsListTool

        state.register(SubagentsListTool(manager=config.subagent_manager))


def _register_feishu_tools(state: _RegistrationState) -> None:
    needed = [name for name in _FEISHU_TOOLS if state.should(name)]
    auth_needed = state.should("feishu_auth")
    if not needed and not auth_needed:
        return

    config = state.config
    app_id, app_secret, feishu_config = _resolve_feishu_credentials(config.channel_env)
    if not app_id or not app_secret:
        return

    import src.agent.tools.feishu as feishu_tools

    if needed:
        from src.feishu.client import FeishuClient

        client = FeishuClient(
            app_id=app_id,
            app_secret=app_secret,
            cache_dir=(config.channel_env or {}).get("feishu_cache_dir", "~/.theos/feishu_cache"),
            token_dir=(config.channel_env or {}).get("feishu_token_dir", "~/.theos/feishu_tokens"),
        )
        allow_from = _resolve_feishu_allow_from(feishu_config)

        for tool_name in needed:
            tool_cls = getattr(feishu_tools, _FEISHU_TOOLS[tool_name])
            if tool_name == "feishu_create":
                state.register(tool_cls(client=client, allow_from=allow_from))
            else:
                state.register(tool_cls(client=client))

    if auth_needed:
        token_dir = (config.channel_env or {}).get("feishu_token_dir", "~/.theos/feishu_tokens")
        state.register(
            feishu_tools.FeishuAuthTool(
                app_id=app_id,
                app_secret=app_secret,
                token_dir=token_dir,
            )
        )


def _resolve_feishu_credentials(channel_env: dict[str, str]) -> tuple[str, str, object | None]:
    app_id = (channel_env or {}).get("feishu_app_id", "")
    app_secret = (channel_env or {}).get("feishu_app_secret", "")
    feishu_config = None
    if not app_id or not app_secret:
        try:
            from src.config.loader import load_config

            feishu_config = load_config().channels.feishu
            app_id = app_id or feishu_config.app_id
            app_secret = app_secret or feishu_config.app_secret
        except Exception:
            pass
    return app_id, app_secret, feishu_config


def _resolve_feishu_allow_from(feishu_config: object | None) -> list[str]:
    try:
        return list(feishu_config.allow_from)  # type: ignore[union-attr]
    except Exception:
        pass

    try:
        from src.config.loader import load_config

        return list(load_config().channels.feishu.allow_from)
    except Exception:
        return []


def _register_session_tools(state: _RegistrationState) -> None:
    if state.mode not in ("single", "team"):
        return

    config = state.config
    if config.session_manager is not None:
        if state.should("sessions_list"):
            from src.agent.tools.sessions import SessionsListTool

            state.register(
                SessionsListTool(
                    session_manager=config.session_manager,
                    turn_store=config.turn_store,
                    subagent_store=config.subagent_store,
                )
            )
        if state.should("sessions_history"):
            from src.agent.tools.sessions import SessionsHistoryTool

            state.register(
                SessionsHistoryTool(
                    session_manager=config.session_manager,
                    turn_store=config.turn_store,
                    subagent_store=config.subagent_store,
                )
            )

    if config.bus is not None and state.should("sessions_send"):
        from src.agent.tools.sessions import SessionsSendTool

        state.register(SessionsSendTool(bus=config.bus))
    if config.subagent_manager is not None and state.should("subagents_list"):
        from src.agent.tools.sessions import SubagentsListTool

        state.register(SubagentsListTool(manager=config.subagent_manager))
