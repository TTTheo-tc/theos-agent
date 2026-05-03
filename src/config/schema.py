"""Configuration schema using Pydantic."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings

from src.config.schema_channels import (  # noqa: F401 — re-exported for backward compat
    ChannelsConfig,
    DingTalkConfig,
    DiscordConfig,
    EmailConfig,
    FeishuConfig,
    MatrixConfig,
    MochatConfig,
    MochatGroupRule,
    MochatMentionConfig,
    QQConfig,
    SlackConfig,
    SlackDMConfig,
    TelegramConfig,
    WhatsAppConfig,
)
from src.security.autonomy import AutonomyLevel

DEFAULT_GENVER_VERIFIER_COMMANDS = [
    "python3 -m compileall src",
    "uv run ruff check .",
    "uv run pytest -x",
    "git diff --check",
    "git diff --stat",
]


class Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AgentDefaults(Base):
    """Default agent configuration."""

    workspace: str = "~/.theos/workspace"
    model: str = "anthropic/claude-opus-4-5"
    provider: str = (
        "auto"  # Provider name (e.g. "anthropic", "openrouter") or "auto" for auto-detection
    )
    max_tokens: int = 16384
    temperature: float = 0.1
    max_tool_iterations: int = 60
    memory_window: int = 100
    failover_models: list[str] = Field(default_factory=list)  # e.g. ["anthropic/claude-sonnet-4-6"]


class AgentRoleConfig(Base):
    """Configuration for a specialized agent role."""

    description: str = ""
    model: str = ""  # Empty inherits from defaults.model
    prompt: str = ""  # Role-specific system prompt
    max_iterations: int = 60
    tools: list[str] = Field(default_factory=list)  # Empty means all tools
    isolation: str | None = None  # "worktree" or None
    timeout_seconds: int | None = None


class GenVerConfig(Base):
    """Generator-Verifier mode configuration."""

    generator_model: str = ""  # Empty inherits from defaults.model
    verifier_model: str = ""
    explorer_model: str = ""
    max_retries: int = 3
    generator_max_iterations: int = 60
    verifier_max_iterations: int = 30
    verifier_commands: list[str] = Field(
        default_factory=lambda: list(DEFAULT_GENVER_VERIFIER_COMMANDS)
    )
    workspace_subdir: str = ".genver"
    # --- Phase pipeline fields ---
    phases: list[str] = Field(
        default_factory=lambda: ["clarify", "spec", "plan", "execute", "review", "report"]
    )
    max_review_rounds: int = 1
    auto_phase_selection: bool = True
    spec_max_iterations: int = 20
    plan_max_iterations: int = 20
    review_max_iterations: int = 30


class MemoryInjectionConfig(Base):
    """Controls how long-term memory is injected into the system prompt."""

    mode: Literal["full", "retrieval"] = "retrieval"
    max_context_tokens: int = 400
    fallback_to_full: bool = True


class MemorySearchHybridConfig(Base):
    """Hybrid search configuration (for future embedding support)."""

    enabled: bool = False
    vector_weight: float = 0.7
    text_weight: float = 0.3


class MemorySearchConfig(Base):
    """Memory search (FTS + optional hybrid) configuration."""

    enabled: bool = True
    max_results: int = 6
    min_score: float = 0.0
    hybrid: MemorySearchHybridConfig = Field(default_factory=MemorySearchHybridConfig)


class MemoryCompactionConfig(Base):
    """Emergency compaction when context window approaches limits."""

    enabled: bool = True
    threshold_ratio: float = 0.85
    max_retries: int = 2
    safety_margin: float = 1.2
    max_consecutive_failures: int = 3
    restore_max_files: int = 5
    restore_max_chars_per_file: int = 20_000


class MemoryFlushConfig(Base):
    """Pre-compaction memory flush settings."""

    enabled: bool = True
    soft_threshold_tokens: int = 4000


class MemoryGCConfig(Base):
    """Memory garbage collection / time decay."""

    enabled: bool = True
    max_age_days: int = 90
    max_sections: int = 20


class MemoryConfig(Base):
    """Top-level memory configuration."""

    injection: MemoryInjectionConfig = Field(default_factory=MemoryInjectionConfig)
    search: MemorySearchConfig = Field(default_factory=MemorySearchConfig)
    compaction: MemoryCompactionConfig = Field(default_factory=MemoryCompactionConfig)
    flush: MemoryFlushConfig = Field(default_factory=MemoryFlushConfig)
    gc: MemoryGCConfig = Field(default_factory=MemoryGCConfig)


class KnowledgeGraphConfig(Base):
    """Knowledge graph storage configuration."""

    enabled: bool = True
    half_life_task_days: float = Field(default=30.0, alias="halfLifeTaskDays")
    half_life_rule_days: float = Field(default=60.0, alias="halfLifeRuleDays")
    half_life_research_days: float = Field(default=90.0, alias="halfLifeResearchDays")
    max_nodes: int = Field(default=100_000, alias="maxNodes")


class EmbeddingConfig(Base):
    """Embedding provider configuration for vector search."""

    provider: str = "none"
    model: str = "text-embedding-3-small"
    base_url: str | None = Field(default=None, alias="baseUrl")
    api_key: str | None = Field(default=None, alias="apiKey")
    dimensions: int = 1536


class ResponseCacheConfig(Base):
    """Response cache configuration."""

    enabled: bool = False
    max_memory_entries: int = Field(default=256, alias="maxMemoryEntries")
    ttl_seconds: int = Field(default=3600, alias="ttlSeconds")
    max_db_entries: int = Field(default=5000, alias="maxDbEntries")


class EventStoreConfig(Base):
    """Event sourcing persistence configuration."""

    enabled: bool = False
    db_name: str = "theos.db"


class MemoryTiersConfig(Base):
    """Three-tier memory configuration."""

    enabled: bool = False
    immediate_queue_size: int = 50
    consolidation_threshold: int = 200


class ApprovalGateConfig(Base):
    """Human escape-hatch approval gate configuration."""

    enabled: bool = False
    auto_approve: list[str] = Field(default_factory=lambda: ["low"])
    timeout_seconds: int = 300


class NeuroSymbolicConfig(Base):
    """Neuro-symbolic file risk controller configuration."""

    enabled: bool = False
    whitelist_patterns: list[str] = Field(default_factory=list)
    blacklist_patterns: list[str] = Field(default_factory=list)  # empty = use defaults


class OrchestratorConfig(Base):
    """Orchestrator state machine configuration."""

    enabled: bool = False  # Off by default; toggle to wrap AgentLoop with lifecycle management
    max_retries: int = 3
    review_mode: str = "auto"  # auto | always | never
    event_log_enabled: bool = True
    event_store: EventStoreConfig = Field(default_factory=EventStoreConfig)
    memory_tiers: MemoryTiersConfig = Field(default_factory=MemoryTiersConfig)
    approval_gate: ApprovalGateConfig = Field(default_factory=ApprovalGateConfig)
    neuro_symbolic: NeuroSymbolicConfig = Field(default_factory=NeuroSymbolicConfig)
    cache_keepalive_threshold_s: int = 240  # Send keepalive if tool takes longer than this


class ReflectorConfig(Base):
    """LLM-powered post-task reflector configuration."""

    enabled: bool = True
    model: str = "minimax/MiniMax-M2.5"


class SubagentPolicyConfig(Base):
    """Policy limits for team/delegation subagent spawning."""

    max_concurrent: int = 3
    max_children_per_agent: int = 3
    max_depth: int = 2
    timeout_seconds: int = 900
    loop_warn_threshold: int = 3
    loop_hard_limit: int = 5
    keep_completed: int = 20


class AgentsConfig(Base):
    """Agent configuration."""

    mode: Literal["auto", "single", "team", "genver"] = "auto"
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)
    roles: dict[str, AgentRoleConfig] = Field(default_factory=dict)
    genver: GenVerConfig = Field(default_factory=GenVerConfig)
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    reflector: ReflectorConfig = Field(default_factory=ReflectorConfig)
    subagents: SubagentPolicyConfig = Field(default_factory=SubagentPolicyConfig)


class ProviderConfig(Base):
    """LLM provider configuration."""

    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)
    models: list[str] = Field(default_factory=list)  # Model names (used by custom provider)


class ProvidersConfig(Base):
    """Configuration for LLM providers."""

    custom: ProviderConfig = Field(default_factory=ProviderConfig)  # Any OpenAI-compatible endpoint
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)  # Alibaba DashScope
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)  # AiHubMix API gateway
    siliconflow: ProviderConfig = Field(default_factory=ProviderConfig)  # SiliconFlow API gateway
    volcengine: ProviderConfig = Field(default_factory=ProviderConfig)  # VolcEngine API gateway
    openai_codex: ProviderConfig = Field(default_factory=ProviderConfig)  # OpenAI Codex (OAuth)
    github_copilot: ProviderConfig = Field(default_factory=ProviderConfig)  # Github Copilot (OAuth)


class HeartbeatConfig(Base):
    """Heartbeat service configuration."""

    enabled: bool = True
    interval_s: int = 30 * 60  # 30 minutes


class XPollerConfig(Base):
    """X/Twitter poller configuration."""

    enabled: bool = False
    usernames: list[str] = Field(default_factory=list)
    interval_s: float = 60.0  # 1 minute default
    auth_token: str = ""
    ct0: str = ""
    notify_channel: str = "feishu"
    notify_chat_id: str = ""


class PollersConfig(Base):
    """Poller services configuration."""

    x: XPollerConfig = Field(default_factory=XPollerConfig)


class UIConfig(Base):
    """Dashboard UI server configuration."""

    enabled: bool = True
    port: int = 8080
    host: str = "127.0.0.1"


class GatewayConfig(Base):
    """Gateway/server configuration."""

    host: str = "0.0.0.0"
    port: int = 18790
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    pollers: PollersConfig = Field(default_factory=PollersConfig)
    ui: UIConfig = Field(default_factory=UIConfig)


class WebSearchConfig(Base):
    """Web search tool configuration."""

    provider: str = "duckduckgo"  # "duckduckgo" | "brave" | "tavily"
    api_key: str = ""  # Brave Search API key (legacy, also used when provider=brave)
    tavily_api_key: str = ""  # Tavily API key (used when provider=tavily)
    max_results: int = 5


class WebFetchConfig(Base):
    """Web fetch tool configuration."""

    extractor: str = "auto"  # "auto" | "readability" | "jina"
    jina_api_key: str = ""
    firecrawl_enabled: bool = False
    firecrawl_api_key: str = ""
    firecrawl_api_url: str = "https://api.firecrawl.dev/v1"
    allowed_domains: list[str] = Field(default_factory=lambda: ["*"])
    blocked_domains: list[str] = Field(default_factory=list)
    max_chars: int = 50000


class WebToolsConfig(Base):
    """Web tools configuration."""

    search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    fetch: WebFetchConfig = Field(default_factory=WebFetchConfig)


class ExecToolConfig(Base):
    """Shell exec tool configuration."""

    timeout: int = 120
    path_append: str = ""
    env_passthrough: list[str] = Field(default_factory=list)


class MCPServerConfig(Base):
    """MCP server connection configuration (stdio or HTTP)."""

    command: str = ""  # Stdio: command to run (e.g. "npx")
    args: list[str] = Field(default_factory=list)  # Stdio: command arguments
    env: dict[str, str] = Field(default_factory=dict)  # Stdio: extra env vars
    url: str = ""  # HTTP: streamable HTTP endpoint URL
    headers: dict[str, str] = Field(default_factory=dict)  # HTTP: Custom HTTP Headers
    tool_timeout: int = 30  # Seconds before a tool call is cancelled


class StockConfig(Base):
    """Stock analysis configuration (daily_stock_analysis integration)."""

    enabled: bool = False
    stock_list: list[str] = Field(default_factory=list)  # e.g. ["600519", "AAPL", "HK00700"]
    tushare_token: str = ""  # Tushare Pro API token (optional, enhances A-share data)
    tavily_api_key: str = ""  # Tavily search API key (optional, news search)
    model: str = ""  # LLM model override (empty = use DSA defaults)
    schedule: str = "0 18 * * 1-5"  # Cron expression for daily analysis


class BrowserConfig(Base):
    """Browser automation tool configuration."""

    enabled: bool = True
    allowed_domains: list[str] = Field(default_factory=list, alias="allowedDomains")
    default_viewport_width: int = Field(1280, alias="defaultViewportWidth")
    default_viewport_height: int = Field(720, alias="defaultViewportHeight")
    navigate_timeout_ms: int = Field(60_000, alias="navigateTimeoutMs")
    action_timeout_ms: int = Field(30_000, alias="actionTimeoutMs")


class ToolsConfig(Base):
    """Tools configuration."""

    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    stock: StockConfig = Field(default_factory=StockConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    restrict_to_workspace: bool = False  # If true, restrict all tool access to workspace directory
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class AutonomyConfig(Base):
    """Autonomy level and guardrails for agent tool execution."""

    level: AutonomyLevel = Field(AutonomyLevel.SUPERVISED, alias="level")
    workspace_only: bool = Field(True, alias="workspaceOnly")
    forbidden_paths: list[str] = Field(
        default_factory=lambda: ["/etc", "/sys", "/proc", "/boot", "~/.ssh"],
        alias="forbiddenPaths",
    )
    allowed_commands: list[str] = Field(default_factory=list, alias="allowedCommands")
    max_actions_per_hour: int = Field(0, alias="maxActionsPerHour")
    max_cost_per_day: float = Field(0.0, alias="maxCostPerDay")
    auto_approve: list[str] = Field(default_factory=list, alias="autoApprove")
    always_ask: list[str] = Field(
        default_factory=lambda: ["bash", "write_file", "edit_file"],
        alias="alwaysAsk",
    )


class SecurityConfig(Base):
    """Security and isolation configuration."""

    network_isolated: bool = True
    group_memory_enabled: bool = (
        False  # Keep legacy global memory behavior unless explicitly enabled
    )
    scrub_tool_args_log: bool = Field(True, alias="scrubToolArgsLog")
    scrub_session_history: bool = Field(True, alias="scrubSessionHistory")
    leak_sensitivity: float = Field(0.7, alias="leakSensitivity")
    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig)


class Config(BaseSettings):
    """Root configuration for TheOS."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    knowledge_graph: KnowledgeGraphConfig = Field(
        default_factory=KnowledgeGraphConfig, alias="knowledgeGraph"
    )
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    response_cache: ResponseCacheConfig = Field(
        default_factory=ResponseCacheConfig, alias="responseCache"
    )
    hooks: str | None = None  # path to hooks directory (pre-chat / post-chat executables)
    proxy: str | None = None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890"

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path."""
        return Path(self.agents.defaults.workspace).expanduser()

    def _match_provider(
        self, model: str | None = None
    ) -> tuple["ProviderConfig | None", str | None]:
        """Match provider config and its registry name. Returns (config, spec_name)."""
        from src.providers.registry import PROVIDERS

        forced = self.agents.defaults.provider
        if forced != "auto":
            p = getattr(self.providers, forced, None)
            return (p, forced) if p else (None, None)

        model_lower = (model or self.agents.defaults.model).lower()
        model_normalized = model_lower.replace("-", "_")
        model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
        normalized_prefix = model_prefix.replace("-", "_")

        def _kw_matches(kw: str) -> bool:
            kw = kw.lower()
            return kw in model_lower or kw.replace("-", "_") in model_normalized

        # Explicit provider prefix wins — prevents `github-copilot/...codex` matching openai_codex.
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and model_prefix and normalized_prefix == spec.name:
                if spec.is_oauth or p.api_key:
                    return p, spec.name

        # Match by keyword (order follows PROVIDERS registry)
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and any(_kw_matches(kw) for kw in spec.keywords):
                if spec.is_oauth or p.api_key:
                    return p, spec.name

        # Fallback: gateways first, then others (follows registry order)
        # OAuth providers are NOT valid fallbacks — they require explicit model selection
        for spec in PROVIDERS:
            if spec.is_oauth:
                continue
            p = getattr(self.providers, spec.name, None)
            if p and p.api_key:
                return p, spec.name
        return None, None

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """Get matched provider config (api_key, api_base, extra_headers). Falls back to first available."""
        p, _ = self._match_provider(model)
        return p

    def get_provider_name(self, model: str | None = None) -> str | None:
        """Get the registry name of the matched provider (e.g. "deepseek", "openrouter")."""
        _, name = self._match_provider(model)
        return name

    def get_provider_keys(self) -> dict[str, str]:
        """Get all configured provider API keys (resolved), keyed by provider name."""
        from src.security.secret_refs import resolve_secret_ref

        result: dict[str, str] = {}
        for name in ("anthropic", "openai", "deepseek", "gemini", "aihubmix"):
            p = getattr(self.providers, name, None)
            if p and p.api_key:
                key = resolve_secret_ref(p.api_key)
                if key:
                    result[name] = key
        return result

    def get_api_key(self, model: str | None = None) -> str | None:
        """Get API key for the given model. Falls back to first available key."""
        from src.security.secret_refs import resolve_secret_ref

        p = self.get_provider(model)
        return resolve_secret_ref(p.api_key) if p else None

    def get_api_base(self, model: str | None = None) -> str | None:
        """Get API base URL for the given model. Applies default URLs for known gateways."""
        from src.providers.registry import find_by_name
        from src.security.secret_refs import resolve_secret_ref

        p, name = self._match_provider(model)
        if p and p.api_base:
            return resolve_secret_ref(p.api_base)
        if name:
            spec = find_by_name(name)
            if spec and spec.default_api_base:
                return spec.default_api_base
        return None

    model_config = ConfigDict(env_prefix="ARIESCLAW_", env_nested_delimiter="__")
