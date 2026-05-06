"""
Provider Registry — single source of truth for LLM provider metadata.

Adding a new provider:
  1. Add a ProviderSpec to PROVIDERS below.
  2. Add a field to ProvidersConfig in config/schema.py.
  Done. Env vars, prefixing, config matching, status display all derive from here.

Order matters — it controls match priority and fallback. Gateways first.
Entries set only fields that differ from ProviderSpec defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderSpec:
    """One LLM provider's metadata. See PROVIDERS below for real examples.

    Placeholders in env_extras values:
      {api_key}  — the user's API key
      {api_base} — api_base from config, or this spec's default_api_base
    """

    # identity
    name: str  # config field name, e.g. "dashscope"
    keywords: tuple[str, ...]  # model-name keywords for matching (lowercase)
    env_key: str  # env var name for API key, e.g. "DASHSCOPE_API_KEY"
    display_name: str = ""  # shown in `theos status`

    # model prefixing
    model_prefix: str = ""  # prefix to strip before sending to API
    skip_prefixes: tuple[str, ...] = ()  # don't prefix if model already starts with these

    # extra env vars, e.g. (("ZHIPUAI_API_KEY", "{api_key}"),)
    env_extras: tuple[tuple[str, str], ...] = ()

    # gateway / local detection
    is_gateway: bool = False  # routes any model (OpenRouter, AiHubMix)
    is_local: bool = False  # local deployment (vLLM, Ollama)
    detect_by_key_prefix: str = ""  # match api_key prefix, e.g. "sk-or-"
    detect_by_base_keyword: str = ""  # match substring in api_base URL
    default_api_base: str = ""  # fallback base URL

    # gateway behavior
    strip_model_prefix: bool = False  # strip "provider/" before re-prefixing

    # per-model param overrides, e.g. (("kimi-k2.5", {"temperature": 1.0}),)
    model_overrides: tuple[tuple[str, dict[str, Any]], ...] = ()

    # OAuth-based providers (e.g., OpenAI Codex) don't use API keys
    is_oauth: bool = False  # if True, uses OAuth flow instead of API key

    # Backend implementation: "anthropic" | "openai_compat" | "codex"
    backend: str = "openai_compat"

    # Provider supports cache_control on content blocks (e.g. Anthropic prompt caching)
    supports_prompt_caching: bool = False

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()


def normalize_provider_name(name: str | None) -> str | None:
    """Normalize user-facing provider names to registry/config field names."""
    return name.strip().replace("-", "_") if name else name


def display_provider_name(name: str) -> str:
    """Return a user-facing provider name from a registry/config field name."""
    return name.replace("_", "-")


# ---------------------------------------------------------------------------
# PROVIDERS — the registry. Order = priority. Copy any entry as template.
# ---------------------------------------------------------------------------

PROVIDERS: tuple[ProviderSpec, ...] = (
    # === Custom (direct OpenAI-compatible endpoint) ========================
    ProviderSpec(
        name="custom",
        keywords=(),
        env_key="",
        display_name="Custom",
    ),
    # === Gateways (detected by api_key / api_base, not model name) =========
    # Gateways can route any model, so they win in fallback.
    # OpenRouter: global gateway, keys start with "sk-or-"
    ProviderSpec(
        name="openrouter",
        keywords=("openrouter",),
        env_key="OPENROUTER_API_KEY",
        display_name="OpenRouter",
        model_prefix="openrouter",  # claude-3 → openrouter/claude-3
        is_gateway=True,
        detect_by_key_prefix="sk-or-",
        detect_by_base_keyword="openrouter",
        default_api_base="https://openrouter.ai/api/v1",
        supports_prompt_caching=True,
    ),
    # AiHubMix: global gateway, OpenAI-compatible interface.
    # strip_model_prefix=True: it doesn't understand "anthropic/claude-3",
    # so we strip to bare "claude-3" then re-prefix as "openai/claude-3".
    ProviderSpec(
        name="aihubmix",
        keywords=("aihubmix",),
        env_key="OPENAI_API_KEY",  # OpenAI-compatible
        display_name="AiHubMix",
        model_prefix="openai",  # → openai/{model}
        is_gateway=True,
        detect_by_base_keyword="aihubmix",
        default_api_base="https://aihubmix.com/v1",
        strip_model_prefix=True,  # anthropic/claude-3 → claude-3 → openai/claude-3
    ),
    # SiliconFlow: OpenAI-compatible gateway, model names keep org prefix
    ProviderSpec(
        name="siliconflow",
        keywords=("siliconflow",),
        env_key="OPENAI_API_KEY",
        display_name="SiliconFlow",
        model_prefix="openai",
        is_gateway=True,
        detect_by_base_keyword="siliconflow",
        default_api_base="https://api.siliconflow.cn/v1",
    ),
    # VolcEngine: OpenAI-compatible gateway
    ProviderSpec(
        name="volcengine",
        keywords=("volcengine", "volces", "ark"),
        env_key="OPENAI_API_KEY",
        display_name="VolcEngine",
        model_prefix="volcengine",
        is_gateway=True,
        detect_by_base_keyword="volces",
        default_api_base="https://ark.cn-beijing.volces.com/api/v3",
    ),
    # === Standard providers (matched by model-name keywords) ===============
    # Anthropic: native SDK, no prefix needed.
    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        backend="anthropic",
        supports_prompt_caching=True,
    ),
    # OpenAI: native SDK, no prefix needed.
    ProviderSpec(
        name="openai",
        keywords=("openai", "gpt"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
    ),
    # OpenAI Codex: uses OAuth, not API key.
    ProviderSpec(
        name="openai_codex",
        keywords=("openai-codex", "codex"),
        env_key="",  # OAuth-based, no API key
        display_name="OpenAI Codex",
        detect_by_base_keyword="codex",
        default_api_base="https://chatgpt.com/backend-api",
        is_oauth=True,  # OAuth-based authentication
        backend="codex",
    ),
    # Github Copilot: uses OAuth, not API key.
    ProviderSpec(
        name="github_copilot",
        keywords=("github_copilot", "copilot"),
        env_key="",  # OAuth-based, no API key
        display_name="Github Copilot",
        model_prefix="github_copilot",  # github_copilot/model → github_copilot/model
        skip_prefixes=("github_copilot/",),
        default_api_base="https://api.githubcopilot.com",
        is_oauth=True,  # OAuth-based authentication
    ),
    # DeepSeek: OpenAI-compatible API at api.deepseek.com/v1.
    ProviderSpec(
        name="deepseek",
        keywords=("deepseek",),
        env_key="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        model_prefix="deepseek",  # deepseek-chat → deepseek/deepseek-chat
        skip_prefixes=("deepseek/",),  # avoid double-prefix
        default_api_base="https://api.deepseek.com",
    ),
    # Gemini: Google provides an OpenAI-compatible endpoint at v1beta/openai/.
    ProviderSpec(
        name="gemini",
        keywords=("gemini",),
        env_key="GEMINI_API_KEY",
        display_name="Gemini",
        model_prefix="gemini",  # gemini-pro → gemini/gemini-pro
        skip_prefixes=("gemini/",),  # avoid double-prefix
        default_api_base="https://generativelanguage.googleapis.com/v1beta/openai/",
    ),
    # Zhipu: OpenAI-compatible API at open.bigmodel.cn/api/paas/v4.
    ProviderSpec(
        name="zhipu",
        keywords=("zhipu", "glm", "zai"),
        env_key="ZAI_API_KEY",
        display_name="Zhipu AI",
        model_prefix="zai",  # glm-4 → zai/glm-4
        skip_prefixes=("zhipu/", "zai/", "openrouter/", "hosted_vllm/"),
        env_extras=(("ZHIPUAI_API_KEY", "{api_key}"),),
        default_api_base="https://open.bigmodel.cn/api/paas/v4",
    ),
    # DashScope: Qwen models, OpenAI-compatible API.
    ProviderSpec(
        name="dashscope",
        keywords=("qwen", "dashscope"),
        env_key="DASHSCOPE_API_KEY",
        display_name="DashScope",
        model_prefix="dashscope",  # qwen-max → dashscope/qwen-max
        skip_prefixes=("dashscope/", "openrouter/"),
        default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    # Moonshot: Kimi models, OpenAI-compatible API.
    # Kimi K2.5 API enforces temperature >= 1.0.
    ProviderSpec(
        name="moonshot",
        keywords=("moonshot", "kimi"),
        env_key="MOONSHOT_API_KEY",
        display_name="Moonshot",
        model_prefix="moonshot",  # kimi-k2.5 → moonshot/kimi-k2.5
        skip_prefixes=("moonshot/", "openrouter/"),
        default_api_base="https://api.moonshot.ai/v1",
        model_overrides=(("kimi-k2.5", {"temperature": 1.0}),),
    ),
    # MiniMax: OpenAI-compatible API at api.minimax.io/v1.
    ProviderSpec(
        name="minimax",
        keywords=("minimax",),
        env_key="MINIMAX_API_KEY",
        display_name="MiniMax",
        model_prefix="minimax",  # MiniMax-M2.1 → minimax/MiniMax-M2.1
        skip_prefixes=("minimax/", "openrouter/"),
        default_api_base="https://api.minimax.io/v1",
    ),
    # === Local deployment (matched by config key, NOT by api_base) =========
    # vLLM / any OpenAI-compatible local server.
    # Detected when config key is "vllm" (provider_name="vllm").
    ProviderSpec(
        name="vllm",
        keywords=("vllm",),
        env_key="HOSTED_VLLM_API_KEY",
        display_name="vLLM/Local",
        model_prefix="hosted_vllm",  # Llama-3-8B → hosted_vllm/Llama-3-8B
        is_local=True,
    ),
    # === Auxiliary (not a primary LLM provider) ============================
    # Groq: mainly used for Whisper voice transcription, also usable for LLM.
    # OpenAI-compatible API at api.groq.com/openai/v1.
    ProviderSpec(
        name="groq",
        keywords=("groq",),
        env_key="GROQ_API_KEY",
        display_name="Groq",
        model_prefix="groq",  # llama3-8b-8192 → groq/llama3-8b-8192
        skip_prefixes=("groq/",),  # avoid double-prefix
        default_api_base="https://api.groq.com/openai/v1",
    ),
)

_PROVIDER_BY_NAME: dict[str, ProviderSpec] = {spec.name: spec for spec in PROVIDERS}
CORE_PROVIDER_NAMES: frozenset[str] = frozenset({"custom", "anthropic", "openai"})


def core_providers() -> tuple[ProviderSpec, ...]:
    """Return the minimal provider set emphasized by core installs."""
    return tuple(spec for spec in PROVIDERS if spec.name in CORE_PROVIDER_NAMES)


def extended_providers() -> tuple[ProviderSpec, ...]:
    """Return installed metadata outside the minimal core provider set."""
    return tuple(spec for spec in PROVIDERS if spec.name not in CORE_PROVIDER_NAMES)


def oauth_providers() -> tuple[ProviderSpec, ...]:
    """Return OAuth-backed provider specs."""
    return tuple(spec for spec in PROVIDERS if spec.is_oauth)


def ordered_providers(*, core_first: bool = True) -> tuple[ProviderSpec, ...]:
    """Return provider specs in UI-friendly order while preserving registry entries."""
    if not core_first:
        return PROVIDERS
    return core_providers() + extended_providers()


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def iter_model_matches(
    model: str,
    *,
    include_gateways: bool = False,
    include_local: bool = False,
) -> tuple[ProviderSpec, ...]:
    """Return provider candidates matched by explicit model prefix, then keywords."""
    model_lower = model.lower()
    model_normalized = model_lower.replace("-", "_")
    model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
    normalized_prefix = model_prefix.replace("-", "_")
    specs = tuple(
        spec
        for spec in PROVIDERS
        if (include_gateways or not spec.is_gateway) and (include_local or not spec.is_local)
    )

    matches: list[ProviderSpec] = []
    seen: set[str] = set()

    def add_match(spec: ProviderSpec) -> None:
        if spec.name not in seen:
            matches.append(spec)
            seen.add(spec.name)

    # Prefer explicit provider prefix — prevents `github-copilot/...codex` matching openai_codex.
    for spec in specs:
        if model_prefix and normalized_prefix == spec.name:
            add_match(spec)

    for spec in specs:
        if any(
            kw in model_lower or kw.replace("-", "_") in model_normalized for kw in spec.keywords
        ):
            add_match(spec)
    return tuple(matches)


def find_by_model(model: str) -> ProviderSpec | None:
    """Match a standard provider by model-name keyword (case-insensitive).
    Skips gateways/local — those are matched by api_key/api_base instead."""
    matches = iter_model_matches(model)
    return matches[0] if matches else None


def find_gateway(
    provider_name: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
) -> ProviderSpec | None:
    """Detect gateway/local provider.

    Priority:
      1. provider_name — if it maps to a gateway/local spec, use it directly.
      2. api_key prefix — e.g. "sk-or-" → OpenRouter.
      3. api_base keyword — e.g. "aihubmix" in URL → AiHubMix.

    A standard provider with a custom api_base (e.g. DeepSeek behind a proxy)
    will NOT be mistaken for vLLM — the old fallback is gone.
    """
    # 1. Direct match by config key
    if provider_name:
        spec = find_by_name(provider_name)
        if spec and (spec.is_gateway or spec.is_local):
            return spec

    # 2. Auto-detect by api_key prefix / api_base keyword
    for spec in PROVIDERS:
        if spec.detect_by_key_prefix and api_key and api_key.startswith(spec.detect_by_key_prefix):
            return spec
        if spec.detect_by_base_keyword and api_base and spec.detect_by_base_keyword in api_base:
            return spec

    return None


def find_by_name(name: str) -> ProviderSpec | None:
    """Find a provider spec by config field name, e.g. "dashscope"."""
    normalized = normalize_provider_name(name)
    return _PROVIDER_BY_NAME.get(normalized or "")
