"""Tool registration configuration DTO."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class ToolRegistrationConfig:
    """All parameters needed by register_standard_tools(), as a typed object."""

    workspace: Path
    mode: str = "single"  # "single" | "team" | "subagent" | "verifier"
    profile: str | None = None
    allowed_dir: Path | None = None
    exec_config: Any = None
    brave_api_key: str | None = None
    web_search_max_results: int = 5
    web_search_provider: str = "duckduckgo"
    tavily_api_key: str | None = None
    web_fetch_extractor: str = "auto"
    web_fetch_jina_api_key: str | None = None
    web_fetch_firecrawl_enabled: bool = False
    web_fetch_firecrawl_api_key: str | None = None
    web_fetch_firecrawl_api_url: str = "https://api.firecrawl.dev/v1"
    web_fetch_allowed_domains: list[str] = field(default_factory=lambda: ["*"])
    web_fetch_blocked_domains: list[str] = field(default_factory=list)
    web_fetch_max_chars: int = 50000
    neuro_symbolic_config: Any = None
    bus_publish: Any = None
    bus: Any = None
    cron_service: Any = None
    executor: Any = None
    subagent_manager: Any = None
    session_manager: Any = None
    turn_store: Any = None
    subagent_store: Any = None
    allowed_tools: set[str] | None = None
    memory_index_resolver: Callable | None = None
    memory_search_enabled: bool = True
    memory_search_max_results: int = 6
    memory_search_min_score: float = 0.0
    structured_memory_enabled: bool = True
    structured_workspace_resolver: Callable | None = None
    stock_config: Any = None
    provider_keys: dict[str, str] = field(default_factory=dict)
    channel_env: dict[str, str] = field(default_factory=dict)
    provider: Any = None
    autonomy_level: Any = None  # AutonomyLevel | None
    browser_config: Any = None  # BrowserConfig | None
    mcp_manager: Any = None
    deny_tools: set[str] = field(default_factory=set)
