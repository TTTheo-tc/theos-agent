"""GenVer runner helpers — tool registry preparation for GenVer mode.

Keeps GenVer-specific tool wiring inside the genver package.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from src.agent.tool_sets import register_standard_tools
from src.agent.tools.explore import ExploreTool
from src.agent.tools.registration import ToolRegistrationConfig
from src.agent.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from src.agent.subagent import SubagentManager
    from src.config.schema import ExecToolConfig, GenVerConfig
    from src.providers.base import LLMProvider


def prepare_genver_tools(
    *,
    config: GenVerConfig,
    base_tools: ToolRegistry,
    workspace: Path,
    task_workspace: Path,
    provider: LLMProvider,
    default_model: str,
    restrict_to_workspace: bool,
    exec_config: ExecToolConfig | None = None,
    brave_api_key: str | None = None,
    web_search_max_results: int = 5,
    web_search_provider: str = "brave",
    tavily_api_key: str | None = None,
    neuro_symbolic_config: Any | None = None,
    cron_service: Any | None = None,
    memory_index_resolver: Callable[[str | None], Any | None] | None = None,
    memory_search_enabled: bool = True,
    memory_search_max_results: int = 6,
    memory_search_min_score: float = 0.0,
    structured_workspace_resolver: Callable[[str | None], Path] | None = None,
    stock_config: Any | None = None,
    provider_keys: dict[str, str] | None = None,
    channel_env: dict[str, str] | None = None,
    subagent_manager: SubagentManager | None = None,
) -> ToolRegistry:
    """Build the generator tool registry for a GenVer run.

    If *task_workspace* differs from *workspace*, creates a fresh registry
    scoped to the task workspace.  Otherwise returns *base_tools* as-is
    (with explore tool added if missing).
    """
    from src.agent.agentfs import AgentFS

    generator_tools = base_tools
    if task_workspace != workspace:
        generator_tools = ToolRegistry()
        allowed_dir = task_workspace if restrict_to_workspace else None
        reg_config = ToolRegistrationConfig(
            workspace=task_workspace,
            mode="single",
            allowed_dir=allowed_dir,
            exec_config=exec_config,
            brave_api_key=brave_api_key,
            web_search_max_results=web_search_max_results,
            web_search_provider=web_search_provider,
            tavily_api_key=tavily_api_key,
            neuro_symbolic_config=neuro_symbolic_config,
            cron_service=cron_service,
            executor=subagent_manager.executor if subagent_manager is not None else None,
            subagent_manager=subagent_manager,
            memory_index_resolver=memory_index_resolver,
            memory_search_enabled=memory_search_enabled,
            memory_search_max_results=memory_search_max_results,
            memory_search_min_score=memory_search_min_score,
            structured_workspace_resolver=structured_workspace_resolver,
            stock_config=stock_config,
            provider_keys=provider_keys or {},
            channel_env=channel_env or {},
            provider=provider,
        )
        register_standard_tools(generator_tools, reg_config)

    # Register explore tool if explorer model is configured
    agentfs = AgentFS(task_workspace, subdir=config.workspace_subdir)
    explorer_model = config.explorer_model or default_model
    if not generator_tools.has("explore"):
        generator_tools.register(
            ExploreTool(
                provider=provider,
                workspace=task_workspace,
                agentfs=agentfs,
                explorer_model=explorer_model,
                restrict_to_workspace=restrict_to_workspace,
            )
        )

    return generator_tools
