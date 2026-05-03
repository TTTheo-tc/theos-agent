"""Centralized tool registration helpers.

Provides ``register_standard_tools`` to avoid duplicating registration logic
across AgentLoop, SubagentManager, and GenVerEngine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.agent.tools.filesystem import (
    DocWriteFileTool,
    EditFileTool,
    GlobTool,
    GrepTool,
    ListDirTool,
    MultiEditTool,
    ReadFileTool,
    WriteFileTool,
)
from src.agent.tools.notebook import NotebookEditTool, NotebookReadTool
from src.agent.tools.registry import ToolRegistry
from src.agent.tools.shell import ExecTool, SafeExecTool
from src.agent.tools.todo import (
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskUpdateTool,
    TodoTool,
)
from src.agent.tools.tool_profiles import ALWAYS_ON_TOOLS, expand_groups, resolve_profile
from src.agent.tools.web_fetch import WebFetchTool
from src.agent.tools.web_http import HttpRequestTool
from src.agent.tools.web_image_search import ImageSearchTool
from src.agent.tools.web_search import WebSearchTool

if TYPE_CHECKING:
    from src.agent.tools.base import Tool
    from src.agent.tools.registration import ToolRegistrationConfig


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
    from src.config.schema import ExecToolConfig as _ECfg

    # Extract fields from config
    workspace = config.workspace
    mode = config.mode
    allowed_dir = config.allowed_dir
    profile = config.profile
    exec_config = config.exec_config
    brave_api_key = config.brave_api_key
    web_search_max_results = config.web_search_max_results
    web_search_provider = config.web_search_provider
    tavily_api_key = config.tavily_api_key
    neuro_symbolic_config = config.neuro_symbolic_config
    bus_publish = config.bus_publish
    bus = config.bus
    cron_service = config.cron_service
    executor = config.executor
    subagent_manager = config.subagent_manager
    session_manager = config.session_manager
    turn_store = config.turn_store
    subagent_store = config.subagent_store
    allowed_tools = config.allowed_tools
    memory_index_resolver = config.memory_index_resolver
    memory_search_enabled = config.memory_search_enabled
    memory_search_max_results = config.memory_search_max_results
    memory_search_min_score = config.memory_search_min_score
    structured_workspace_resolver = config.structured_workspace_resolver
    stock_config = config.stock_config
    provider_keys = config.provider_keys
    channel_env = config.channel_env
    provider = config.provider
    mcp_manager = config.mcp_manager
    is_team_mode = mode == "team"

    ec = exec_config or _ECfg()

    # Profiles are opt-in. Existing mode semantics stay authoritative.
    profile_set = resolve_profile(profile) if profile is not None else None

    # Expand group references in allowed_tools
    expanded_allowed = expand_groups(allowed_tools)

    deny_tools = config.deny_tools or set()

    def _should(name: str) -> bool:
        """Check if a tool should be registered based on profile, deny list, and allowed_tools."""
        if name in deny_tools:
            return False
        if profile_set is not None and name not in profile_set:
            return False
        if expanded_allowed is not None and name not in expanded_allowed:
            return False
        return True

    def _reg(tool: Tool) -> None:
        """Register tool, routing to deferred pool if not always-on."""
        registry.register(tool, deferred=tool.name not in ALWAYS_ON_TOOLS)

    # --- verifier: read-only fs + bash (autonomous verification agent) ---
    if mode == "verifier":
        registry.register(ExecTool(working_dir=str(workspace), timeout=300))
        registry.register(ReadFileTool(workspace=workspace, allowed_dir=allowed_dir))
        registry.register(GlobTool(workspace=workspace, allowed_dir=allowed_dir))
        registry.register(GrepTool(workspace=workspace, allowed_dir=allowed_dir))
        registry.register(ListDirTool(workspace=workspace, allowed_dir=allowed_dir))
        return

    # --- filesystem tools ---
    if _should("read_file"):
        _reg(ReadFileTool(workspace=workspace, allowed_dir=allowed_dir))

    if is_team_mode:
        if _should("write_docs"):
            _reg(DocWriteFileTool(workspace=workspace, allowed_dir=allowed_dir))
    else:
        if _should("write_file"):
            _reg(
                WriteFileTool(
                    workspace=workspace,
                    allowed_dir=allowed_dir,
                    neuro_symbolic_config=neuro_symbolic_config,
                )
            )
        if _should("edit_file"):
            _reg(
                EditFileTool(
                    workspace=workspace,
                    allowed_dir=allowed_dir,
                    neuro_symbolic_config=neuro_symbolic_config,
                )
            )

    if _should("list_dir"):
        _reg(ListDirTool(workspace=workspace, allowed_dir=allowed_dir))
    if _should("glob"):
        _reg(GlobTool(workspace=workspace, allowed_dir=allowed_dir))
    if _should("grep"):
        _reg(GrepTool(workspace=workspace, allowed_dir=allowed_dir))

    if not is_team_mode and _should("multi_edit"):
        _reg(MultiEditTool(workspace=workspace, allowed_dir=allowed_dir))

    if not is_team_mode and _should("apply_patch"):
        from src.agent.tools.apply_patch import ApplyPatchTool

        _reg(ApplyPatchTool(workspace=workspace, allowed_dir=allowed_dir))

    # --- notebook tools ---
    if _should("notebook_read"):
        _reg(NotebookReadTool(workspace=workspace, allowed_dir=allowed_dir))
    if not is_team_mode and _should("notebook_edit"):
        _reg(NotebookEditTool(workspace=workspace, allowed_dir=allowed_dir))

    # --- todo / tasks ---
    if mode != "subagent" and _should("todo"):
        _reg(TodoTool(workspace=workspace))
        _reg(TaskCreateTool(workspace=workspace))
        _reg(TaskListTool(workspace=workspace))
        _reg(TaskUpdateTool(workspace=workspace))
        _reg(TaskGetTool(workspace=workspace))

    # --- exec ---
    if _should("bash"):
        exec_kwargs = dict(
            working_dir=str(workspace),
            timeout=ec.timeout,
            restrict_to_workspace=allowed_dir is not None,
            path_append=ec.path_append,
            env_passthrough=ec.env_passthrough,
        )
        if is_team_mode:
            _reg(SafeExecTool(**exec_kwargs))
        else:
            _reg(ExecTool(**exec_kwargs))

    # --- process management ---
    if not is_team_mode and _should("process"):
        from src.agent.tools.process import ProcessTool

        _reg(ProcessTool(working_dir=str(workspace)))

    # --- gateway self-restart (owner-only, delayed kill) ---
    if not is_team_mode and _should("gateway_restart"):
        from src.agent.tools.gateway_restart import GatewayRestartTool

        _reg(GatewayRestartTool())

    # --- web ---
    if _should("web_search"):
        _reg(
            WebSearchTool(
                api_key=brave_api_key,
                max_results=web_search_max_results,
                provider=web_search_provider,
                tavily_api_key=tavily_api_key,
            )
        )
    if _should("web_fetch"):
        _reg(
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
    if _should("http_request"):
        _reg(HttpRequestTool())
    if _should("image_search"):
        _reg(ImageSearchTool())
    if mode != "verifier" and _should("capability_search"):
        from src.agent.tools.capability_search import CapabilitySearchTool

        _reg(CapabilitySearchTool(workspace=workspace, manager=mcp_manager))
    if mode != "verifier" and _should("tool_search"):
        from src.agent.tools.tool_search import ToolSearchTool

        registry.register(ToolSearchTool(registry=registry))  # always-on, never deferred
    if mode != "verifier" and _should("skill_search"):
        from src.agent.tools.skill_search import SkillSearchTool

        _reg(SkillSearchTool(workspace=workspace))
    if mode != "verifier" and mcp_manager is not None and _should("mcp_search"):
        from src.agent.tools.mcp_search import MCPToolSearch

        _reg(MCPToolSearch(workspace=workspace, manager=mcp_manager))

    # --- plan mode tools (always-on, never deferred) ---
    if _should("enter_plan_mode"):
        from src.agent.tools.plan_mode import EnterPlanModeTool

        registry.register(EnterPlanModeTool(registry=registry))
    if _should("exit_plan_mode"):
        from src.agent.tools.plan_mode import ExitPlanModeTool

        registry.register(ExitPlanModeTool(registry=registry))

    # --- memory tools ---
    if memory_search_enabled and memory_index_resolver is not None and mode not in ("verifier",):
        if _should("memory_search"):
            from src.agent.tools.memory_search import MemorySearchTool

            _reg(
                MemorySearchTool(
                    index_resolver=memory_index_resolver,
                    workspace_resolver=structured_workspace_resolver,
                    default_max_results=memory_search_max_results,
                    default_min_score=memory_search_min_score,
                )
            )
        if _should("memory_get"):
            from src.agent.tools.memory_search import MemoryGetTool

            _reg(MemoryGetTool(index_resolver=memory_index_resolver))
    if structured_workspace_resolver is not None and mode not in ("verifier",):
        if _should("structured_memory_search"):
            from src.agent.tools.structured_memory import StructuredMemorySearchTool

            _reg(
                StructuredMemorySearchTool(
                    workspace_resolver=structured_workspace_resolver,
                    default_max_results=memory_search_max_results,
                )
            )
        if _should("research_note_get"):
            from src.agent.tools.structured_memory import ResearchNoteGetTool

            _reg(ResearchNoteGetTool(workspace_resolver=structured_workspace_resolver))
        if _should("task_memory_get"):
            from src.agent.tools.structured_memory import TaskMemoryGetTool

            _reg(TaskMemoryGetTool(workspace_resolver=structured_workspace_resolver))
        if _should("domain_rule_get"):
            from src.agent.tools.structured_memory import DomainRuleGetTool

            _reg(DomainRuleGetTool(workspace_resolver=structured_workspace_resolver))

    # --- stock analysis ---
    if mode in ("single", "team") and _should("stock_analysis"):
        if stock_config and stock_config.enabled:
            from src.agent.tools.stock import StockAnalysisTool

            _reg(
                StockAnalysisTool(
                    stock_config=stock_config,
                    provider_keys=provider_keys or {},
                    brave_api_key=brave_api_key,
                    channel_env=channel_env,
                )
            )

    # --- vendor study ---
    if mode in ("single", "team") and _should("vendor_study"):
        from src.agent.tools.vendor_study import VendorStudyTool

        study_tool = VendorStudyTool()
        if study_tool.study_guide_path.exists():
            _reg(study_tool)

    # --- image analysis ---
    if provider is not None and _should("image_analyze"):
        from src.agent.tools.image import ImageAnalyzeTool

        _reg(ImageAnalyzeTool(provider=provider))

    # --- PDF analysis ---
    if _should("pdf"):
        from src.agent.tools.pdf import PdfTool

        _reg(PdfTool(provider=provider))

    # --- TTS ---
    if _should("tts"):
        from src.agent.tools.tts import TtsTool

        _reg(TtsTool(workspace=workspace))

    # --- browser automation ---
    if _should("browser"):
        from src.agent.tools.browser import BrowserTool
        from src.security.autonomy import AutonomyLevel

        browser_cfg = config.browser_config
        readonly = bool(config.autonomy_level and config.autonomy_level == AutonomyLevel.READONLY)
        _reg(BrowserTool(workspace=workspace, config=browser_cfg, readonly=readonly))

    # --- communication (only in loop modes) ---
    if mode in ("single", "team"):
        from src.agent.tools.subagent_kill import SubagentKillTool
        from src.agent.tools.subagent_wait import SubagentWaitTool

        if bus_publish is not None and _should("message"):
            from src.agent.tools.message import MessageTool

            _reg(MessageTool(send_callback=bus_publish))
        if subagent_manager is not None and _should("agent"):
            from src.agent.tools.spawn import AgentTool

            _reg(AgentTool(manager=subagent_manager))
        if executor is not None and _should("subagent_wait"):
            _reg(SubagentWaitTool(executor=executor))
        if executor is not None and _should("subagent_kill"):
            _reg(SubagentKillTool(executor=executor))
        if cron_service is not None and _should("cron"):
            from src.agent.tools.cron import CronTool

            _reg(CronTool(cron_service))

    # Nested subagent tools — registered when mode="subagent" and allowed_tools includes them
    if mode == "subagent" and executor is not None:
        from src.agent.tools.subagent_kill import SubagentKillTool
        from src.agent.tools.subagent_wait import SubagentWaitTool

        if _should("agent") and subagent_manager is not None:
            from src.agent.tools.spawn import AgentTool

            _reg(AgentTool(manager=subagent_manager))

        if _should("subagent_wait"):
            _reg(SubagentWaitTool(executor=executor))

        if _should("subagent_kill"):
            _reg(SubagentKillTool(executor=executor))

        if _should("subagents_list") and subagent_manager is not None:
            from src.agent.tools.sessions import SubagentsListTool

            _reg(SubagentsListTool(manager=subagent_manager))

    # --- feishu knowledge tools ---
    _feishu_tools = {
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
    _feishu_needed = [name for name in _feishu_tools if _should(name)]
    if _feishu_needed:
        # Read Feishu credentials: channel_env override > theos config
        _fid = (channel_env or {}).get("feishu_app_id", "")
        _fsecret = (channel_env or {}).get("feishu_app_secret", "")
        if not _fid or not _fsecret:
            try:
                from src.config.loader import load_config as _load_cfg

                _fs_cfg = _load_cfg().channels.feishu
                _fid = _fid or _fs_cfg.app_id
                _fsecret = _fsecret or _fs_cfg.app_secret
            except Exception:
                pass
        if _fid and _fsecret:
            import src.agent.tools.feishu as _feishu_mod
            from src.feishu.client import FeishuClient

            _feishu_client = FeishuClient(
                app_id=_fid,
                app_secret=_fsecret,
                cache_dir=(channel_env or {}).get("feishu_cache_dir", "~/.theos/feishu_cache"),
                token_dir=(channel_env or {}).get("feishu_token_dir", "~/.theos/feishu_tokens"),
            )
            # Read allow_from for auto-granting permissions on created docs
            _feishu_allow_from: list[str] = []
            try:
                _feishu_allow_from = _fs_cfg.allow_from  # type: ignore[union-attr]
            except Exception:
                try:
                    from src.config.loader import load_config as _load_cfg2  # noqa: PLC0415

                    _feishu_allow_from = _load_cfg2().channels.feishu.allow_from
                except Exception:
                    pass

            for tool_name in _feishu_needed:
                cls = getattr(_feishu_mod, _feishu_tools[tool_name])
                if tool_name == "feishu_create":
                    _reg(cls(client=_feishu_client, allow_from=_feishu_allow_from))
                else:
                    _reg(cls(client=_feishu_client))

            # Always register feishu_auth tool when feishu is configured
            if _should("feishu_auth"):
                _token_dir = (channel_env or {}).get(
                    "feishu_token_dir", "~/.theos/feishu_tokens"
                )
                _reg(
                    _feishu_mod.FeishuAuthTool(
                        app_id=_fid,
                        app_secret=_fsecret,
                        token_dir=_token_dir,
                    )
                )

    # --- session orchestration ---
    if mode in ("single", "team"):
        from src.agent.tools.sessions import (
            SessionsHistoryTool,
            SessionsListTool,
            SessionsSendTool,
            SubagentsListTool,
        )

        if session_manager is not None:
            if _should("sessions_list"):
                _reg(
                    SessionsListTool(
                        session_manager=session_manager,
                        turn_store=turn_store,
                        subagent_store=subagent_store,
                    )
                )
            if _should("sessions_history"):
                _reg(
                    SessionsHistoryTool(
                        session_manager=session_manager,
                        turn_store=turn_store,
                        subagent_store=subagent_store,
                    )
                )
        if bus is not None and _should("sessions_send"):
            _reg(SessionsSendTool(bus=bus))
        if subagent_manager is not None and _should("subagents_list"):
            _reg(SubagentsListTool(manager=subagent_manager))
