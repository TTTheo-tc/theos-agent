"""Provider detection, model fetching, and model-choice helpers for ``theos init``."""

from __future__ import annotations

import re
from pathlib import Path

import typer

from src.cli.display import console

# ---------------------------------------------------------------------------
# Provider constants
# ---------------------------------------------------------------------------

# Providers shown in init (API Key based, most common)
INIT_PROVIDERS = [
    ("anthropic", "Anthropic", "API Key"),
    ("openai", "OpenAI", "API Key"),
    ("deepseek", "DeepSeek", "API Key"),
    ("gemini", "Gemini", "API Key"),
    ("openrouter", "OpenRouter", "API Key"),
    ("minimax", "MiniMax", "API Key"),
    ("groq", "Groq", "API Key"),
]

# Fallback models per provider (used only when API fetch fails)
FALLBACK_MODELS: dict[str, list[tuple[str, str]]] = {
    "anthropic": [
        ("anthropic/claude-opus-4-6", "Claude Opus 4.6"),
        ("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("anthropic/claude-haiku-4-5", "Claude Haiku 4.5"),
    ],
    "openai": [
        ("openai/gpt-5.4", "GPT-5.4"),
        ("openai/gpt-5.4-pro", "GPT-5.4 Pro"),
        ("openai/gpt-5.2", "GPT-5.2"),
    ],
    "openai-codex": [
        ("openai-codex/gpt-5.4", "GPT-5.4"),
        ("openai-codex/gpt-5.4-pro", "GPT-5.4 Pro"),
    ],
    "deepseek": [
        ("deepseek/deepseek-chat", "DeepSeek Chat"),
        ("deepseek/deepseek-reasoner", "DeepSeek Reasoner"),
    ],
    "gemini": [
        ("gemini/gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
        ("gemini/gemini-3-flash", "Gemini 3 Flash"),
    ],
    "openrouter": [
        ("openrouter/anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6 (via OR)"),
        ("openrouter/deepseek/deepseek-chat", "DeepSeek Chat (via OR)"),
    ],
    "minimax": [
        ("minimax/MiniMax-M2.5", "MiniMax M2.5"),
        ("minimax/MiniMax-M2.5-highspeed", "MiniMax M2.5 Highspeed"),
    ],
    "groq": [
        ("groq/llama-4-maverick", "LLaMA 4 Maverick"),
        ("groq/llama-3.3-70b-versatile", "LLaMA 3.3 70B"),
    ],
}

# Known API base URLs for providers (OpenAI-compatible /v1/models endpoints)
PROVIDER_API_BASES: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "openai-codex": "https://api.openai.com",
    "deepseek": "https://api.deepseek.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "openrouter": "https://openrouter.ai/api",
    "minimax": "https://api.minimax.io",
    "groq": "https://api.groq.com/openai",
}

# Runtime cache: populated from curated fallback models during init flows
PROVIDER_TOP_MODELS: dict[str, list[tuple[str, str]]] = {}
PROVIDER_MODEL_SOURCE: dict[str, str] = {}
PROVIDER_DISCOVERED_MODELS: dict[str, set[str]] = {}

# Channels shown in init
INIT_CHANNELS = [
    ("WhatsApp", "whatsapp"),
    ("Telegram", "telegram"),
    ("Discord", "discord"),
    ("Slack", "slack"),
    ("Feishu / Lark", "feishu"),
    ("Email", "email"),
]


# ---------------------------------------------------------------------------
# Token / key helpers
# ---------------------------------------------------------------------------


def check_codex_token(creds_path: Path) -> str:
    """Return 'ok', 'expiring' (< 1h), or 'expired' by decoding the JWT access token."""
    import base64
    import json as _json
    import time

    warn_secs = 3600  # warn if less than 1 hour left

    try:
        data = _json.loads(creds_path.read_text())
        token = data.get("tokens", {}).get("access_token") or data.get("OPENAI_API_KEY", "")
        if not token or "." not in token:
            return "ok"  # not a JWT (plain API key), assume valid
        payload_b64 = token.split(".")[1]
        # Add padding
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        if not exp:
            return "ok"
        remaining = exp - time.time()
        if remaining < 0:
            return "expired"
        if remaining < warn_secs:
            return "expiring"
        return "ok"
    except Exception:
        return "ok"  # can't decode, assume valid


def _normalize_pasted_secret(raw: str, *, env_var: str | None = None) -> str:
    """Normalize pasted secret text into a single token string."""
    value = raw.strip()
    if not value:
        return ""

    if env_var:
        for prefix in (f"export {env_var}=", f"{env_var}="):
            if value.startswith(prefix):
                value = value[len(prefix) :].strip()
                break

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]

    return re.sub(r"\s+", "", value)


def _prompt_multiline_secret(
    prompt: str,
    *,
    env_var: str | None = None,
    help_text: str | None = None,
) -> str:
    """Read a possibly multi-line pasted secret and normalize it."""
    if help_text:
        console.print(help_text)
    console.print("  Paste and press Enter twice to finish. Empty first line skips.")

    first = console.input(f"{prompt} ")
    if not first.strip():
        return ""

    lines = [first]
    while True:
        line = console.input("  ... ")
        if not line.strip():
            break
        lines.append(line)

    return _normalize_pasted_secret("\n".join(lines), env_var=env_var)


def prompt_anthropic_key() -> str:
    """Prompt for a standard Anthropic API key."""
    console.print("\n  [bold]Anthropic[/bold]")
    console.print("  Get your key at: [cyan]https://console.anthropic.com/settings/keys[/cyan]")
    api_key = typer.prompt("  API Key", default="", show_default=False, prompt_suffix=" ").strip()
    if api_key.startswith("sk-ant-oat"):
        console.print(
            "  [red]\u2717[/red] Anthropic OAuth tokens are disabled in TheOS. "
            "Paste a standard Anthropic API key instead."
        )
        return ""
    return api_key


# ---------------------------------------------------------------------------
# Model helpers
# ---------------------------------------------------------------------------


def _display_model_label(model_id: str) -> str:
    """Render a stable, compact display label from the full model id."""
    return model_id.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Model fetching — per-provider handlers
# ---------------------------------------------------------------------------

# Models to skip across all providers (non-chat utility models)
_SKIP_PATTERNS = ("embed", "tts", "whisper", "dall-e", "moderation", "davinci", "babbage")


def _filter_chat_models(raw_ids: list[str]) -> list[str]:
    """Filter out non-chat models (embeddings, TTS, etc.)."""
    return [rid for rid in raw_ids if not any(s in rid.lower() for s in _SKIP_PATTERNS)]


def _fetch_openai_compatible(
    provider_key: str,
    api_key: str,
    api_base: str,
) -> list[tuple[str, str]] | None:
    """Fetch models from OpenAI-compatible /v1/models endpoint.

    Works for: anthropic, openai, deepseek, groq, minimax, custom.
    """
    import httpx

    url = f"{api_base.rstrip('/')}/v1/models"
    headers: dict[str, str] = {}
    if provider_key == "anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        resp = httpx.get(url, headers=headers, timeout=8)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    models_raw: list[dict] = []
    if isinstance(data, dict):
        models_raw = data.get("data") or data.get("models") or []
    elif isinstance(data, list):
        models_raw = data

    raw_ids = [m.get("id") or "" for m in models_raw if m.get("id")]
    raw_ids = _filter_chat_models(raw_ids)
    if not raw_ids:
        return None

    from src.providers.registry import find_by_name

    spec = find_by_name(provider_key)
    prefix = spec.model_prefix if spec and spec.model_prefix else provider_key

    results: list[tuple[str, str]] = []
    for raw_id in raw_ids:
        if provider_key == "custom":
            model_id = raw_id
        elif "/" in raw_id:
            model_id = raw_id
        else:
            model_id = f"{prefix}/{raw_id}"
        results.append((model_id, _display_model_label(model_id)))
    return results


def _fetch_gemini(api_key: str) -> list[tuple[str, str]] | None:
    """Fetch models from Google Gemini API.

    Gemini uses a different endpoint, auth, and response format:
    - URL: /v1beta/models?key=API_KEY (not /v1/models)
    - Auth: query parameter, not header
    - Response: {"models": [{"name": "models/gemini-...", ...}]}
    """
    import httpx

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"

    try:
        resp = httpx.get(url, timeout=8)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        console.print(f" [yellow]error: {type(exc).__name__}[/yellow]")
        return None

    models_raw: list[dict] = data.get("models") or []
    if not models_raw:
        return None

    results: list[tuple[str, str]] = []
    for m in models_raw:
        name = m.get("name") or ""
        if not name:
            continue
        # Strip "models/" prefix: "models/gemini-3.1-pro-preview" -> "gemini-3.1-pro-preview"
        bare = name.removeprefix("models/")
        if any(s in bare.lower() for s in _SKIP_PATTERNS):
            continue
        model_id = f"gemini/{bare}"
        label = m.get("displayName") or bare
        results.append((model_id, label))
    return results


def _fetch_openrouter(api_key: str | None = None) -> list[tuple[str, str]] | None:
    """Fetch models from OpenRouter.

    OpenRouter's /v1/models is public (no auth needed).
    Model IDs already have org prefix (e.g. "anthropic/claude-sonnet-4-6").
    We prepend "openrouter/" for model prefix routing.
    """
    import httpx

    url = "https://openrouter.ai/api/v1/models"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        resp = httpx.get(url, headers=headers, timeout=8)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        console.print(f" [yellow]error: {type(exc).__name__}[/yellow]")
        return None

    models_raw: list[dict] = data.get("data") or []
    if not models_raw:
        return None

    results: list[tuple[str, str]] = []
    for m in models_raw:
        raw_id = m.get("id") or ""
        if not raw_id:
            continue
        if any(s in raw_id.lower() for s in _SKIP_PATTERNS):
            continue
        model_id = f"openrouter/{raw_id}"
        label = _display_model_label(raw_id)
        results.append((model_id, label))
    return results


def _curate_live_model_choices(
    provider_key: str,
    fetched: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Trim noisy live catalogs back to the init shortlist for interactive flows."""
    if provider_key != "openrouter":
        return fetched

    curated = FALLBACK_MODELS.get(provider_key, [])
    if not curated:
        return fetched

    fetched_ids = {model_id for model_id, _label in fetched}
    curated_live = [(model_id, label) for model_id, label in curated if model_id in fetched_ids]
    return curated_live or fetched


def fetch_models_for_provider(
    provider_key: str,
    api_key: str,
    api_base: str | None = None,
) -> list[tuple[str, str]] | None:
    """Fetch available models for a provider. Returns list of (model_id, label) or None."""
    if provider_key == "gemini":
        return _fetch_gemini(api_key)
    if provider_key == "openrouter":
        return _fetch_openrouter(api_key)

    base = api_base or PROVIDER_API_BASES.get(provider_key)
    if not base:
        return None
    return _fetch_openai_compatible(provider_key, api_key, base)


def build_model_choices(
    configured_providers: list[str],
) -> list[tuple[str, str]]:
    """Return (model_id, display_label) for configured providers.

    For each provider: try live API fetch, fall back to FALLBACK_MODELS.
    """
    from src.config.loader import load_config

    config = load_config()
    api_keys = _api_keys_by_provider()
    choices: list[tuple[str, str]] = []

    for prov in configured_providers:
        if prov in PROVIDER_TOP_MODELS:
            choices.extend(PROVIDER_TOP_MODELS[prov])
            continue

        api_key, api_base = _resolve_model_fetch_credentials(prov, config, api_keys)

        fetched: list[tuple[str, str]] | None = None
        if api_key:
            console.print(f"  [dim]Fetching models for {prov}...[/dim]", end="")
            fetched = fetch_models_for_provider(prov, api_key, api_base)
            if fetched:
                console.print(f" [green]{len(fetched)} models[/green]")
            else:
                console.print(" [yellow]fallback[/yellow]")

        if fetched:
            PROVIDER_TOP_MODELS[prov] = _curate_live_model_choices(prov, fetched)
            PROVIDER_MODEL_SOURCE[prov] = "live"
        else:
            PROVIDER_TOP_MODELS[prov] = FALLBACK_MODELS.get(prov, [])
            PROVIDER_MODEL_SOURCE[prov] = "fallback"
        PROVIDER_DISCOVERED_MODELS[prov] = {model_id for model_id, _ in PROVIDER_TOP_MODELS[prov]}

        choices.extend(PROVIDER_TOP_MODELS[prov])

    return choices


def detect_configured_providers() -> list[str]:
    """Return provider keys that have auth configured."""
    from src.config.loader import load_config
    from src.security.secret_refs import resolve_secret_ref

    api_keys = _api_keys_by_provider()
    providers: list[str] = []
    for key, _label, _desc in INIT_PROVIDERS:
        if api_keys.get(key):
            providers.append(key)

    # Detect OpenAI Codex (OAuth — uses ~/.codex/auth.json, not API key)
    codex_creds = Path.home() / ".codex" / "auth.json"
    if codex_creds.exists() and check_codex_token(codex_creds) in ("ok", "expiring"):
        providers.append("openai-codex")

    config = load_config()
    custom_p = config.providers.custom
    if resolve_secret_ref(custom_p.api_key) and resolve_secret_ref(custom_p.api_base):
        providers.append("custom")
        if custom_p.models:
            custom_models = PROVIDER_TOP_MODELS.setdefault("custom", [])
            for m in custom_p.models:
                if not any(mid == m for mid, _ in custom_models):
                    custom_models.append((m, m))

    return providers


def _api_keys_by_provider() -> dict[str, str]:
    """Load auth store once and return the best API key per provider."""
    from src.auth.store import load_auth_store
    from src.auth.types import ApiKeyCredential, OAuthCredential
    from src.security.keychain import MasterKeyUnavailableError

    try:
        store = load_auth_store()
    except MasterKeyUnavailableError:
        return {}
    keys: dict[str, str] = {}

    for provider, profile_id in store.last_good.items():
        cred = store.profiles.get(profile_id)
        if isinstance(cred, ApiKeyCredential) and cred.key:
            keys[provider] = cred.key
        elif provider != "anthropic" and isinstance(cred, OAuthCredential) and cred.access:
            keys[provider] = cred.access

    for cred in store.profiles.values():
        if cred.provider not in keys:
            if isinstance(cred, ApiKeyCredential) and cred.key:
                keys[cred.provider] = cred.key
            elif cred.provider != "anthropic" and isinstance(cred, OAuthCredential) and cred.access:
                keys[cred.provider] = cred.access

    return keys


def _resolve_model_fetch_credentials(
    provider: str,
    config,
    api_keys: dict[str, str],
) -> tuple[str | None, str | None]:
    """Resolve credentials for provider model discovery.

    All providers follow the same flow:
    1. Try to get usable auth
    2. Attempt live model discovery
    3. Fall back to static defaults if discovery fails
    """
    from src.security.secret_refs import resolve_secret_ref

    if provider == "custom":
        cp = config.providers.custom
        return resolve_secret_ref(cp.api_key), resolve_secret_ref(cp.api_base)

    if provider == "openai-codex":
        token = _get_openai_codex_access_token()
        return token, PROVIDER_API_BASES.get(provider)

    return api_keys.get(provider), PROVIDER_API_BASES.get(provider)


def _get_openai_codex_access_token() -> str | None:
    """Return an OAuth access token for OpenAI Codex model discovery."""
    try:
        from oauth_cli_kit import get_token

        token = get_token()
        if token and token.access:
            return token.access
    except Exception:
        return None
    return None


def validate_model_choice(
    model: str,
    configured_providers: list[str],
) -> tuple[bool, str, bool]:
    """Validate a chosen default model.

    Returns ``(ok, message, verified_live)``.
    ``verified_live`` is True only when the model is confirmed by a live provider model list.
    """
    from src.providers.registry import find_by_model

    spec = find_by_model(model)
    if spec is None:
        return False, f"Unknown model/provider: {model}", False

    provider_key = spec.name.replace("_", "-")
    if provider_key not in configured_providers and spec.name not in configured_providers:
        return (
            False,
            f"Provider '{provider_key}' is not configured, so {model} cannot be used as default.",
            False,
        )

    known_models = PROVIDER_DISCOVERED_MODELS.get(provider_key) or PROVIDER_DISCOVERED_MODELS.get(
        spec.name
    )
    source = PROVIDER_MODEL_SOURCE.get(provider_key) or PROVIDER_MODEL_SOURCE.get(spec.name)

    if source == "live":
        if known_models and model in known_models:
            return True, f"Verified against live model list for {provider_key}.", True
        return (
            False,
            f"{model} was not found in the live model list for {provider_key}.",
            True,
        )

    if source == "fallback":
        return (
            True,
            f"Could not verify {model} live for {provider_key}; using fallback model metadata.",
            False,
        )

    return True, f"No live model metadata available for {provider_key}; saving as-is.", False


# ---------------------------------------------------------------------------
# Interactive provider + model wizard (called from init_cmd.py)
# ---------------------------------------------------------------------------


def configure_providers(config) -> list[str]:
    """Interactive provider selection, API key entry, and default model choice.

    Mutates *config* in place (providers, agents.defaults).
    Returns list of configured provider keys.
    """
    from src.auth.store import add_api_key_profile
    from src.config.loader import save_config

    console.print("\n[bold]Provider setup[/bold]\n")
    api_keys = _api_keys_by_provider()

    configured_providers: list[str] = []

    # Build provider list with status
    provider_status: list[tuple[str, str, bool]] = []  # (key, label, is_configured)
    for key, label, _desc in INIT_PROVIDERS:
        has_key = bool(api_keys.get(key))
        provider_status.append((key, label, has_key))

    # Detect OpenAI Codex OAuth status
    codex_creds = Path.home() / ".codex" / "auth.json"
    codex_available = False
    codex_status_label = ""
    if codex_creds.exists():
        codex_status = check_codex_token(codex_creds)
        if codex_status == "ok":
            codex_available = True
            codex_status_label = " [green]\u2713 token valid[/green]"
        elif codex_status == "expiring":
            codex_available = True
            codex_status_label = " [yellow]\u26a0 token expiring[/yellow]"
        else:
            codex_status_label = " [red]\u2717 token expired[/red]"

    # Check if custom provider is already configured
    custom_p = config.providers.custom
    custom_configured = bool(custom_p.api_key and custom_p.api_base)

    # Display numbered list
    default_indices: list[int] = []
    for i, (key, label, has_key) in enumerate(provider_status, 1):
        mark = " [green]\u2713 configured[/green]" if has_key else ""
        console.print(f"  [{i}] {label}{mark}")
        if has_key:
            default_indices.append(i)

    # OpenAI Codex (OAuth) option
    codex_idx = len(provider_status) + 1
    console.print(f"  [{codex_idx}] OpenAI Codex (OAuth){codex_status_label}")
    if codex_available:
        default_indices.append(codex_idx)

    # "Other" option for custom OpenAI-compatible providers
    other_idx = codex_idx + 1
    custom_mark = " [green]\u2713 configured[/green]" if custom_configured else ""
    console.print(f"  [{other_idx}] Other{custom_mark}")
    if custom_configured:
        default_indices.append(other_idx)

    default_str = ",".join(str(i) for i in default_indices) if default_indices else ""
    console.print()
    raw_selection = typer.prompt(
        "  Select providers (comma-separated, Enter to keep)",
        default=default_str,
        show_default=True,
        prompt_suffix=" ",
    ).strip()

    # Parse selection
    selected_indices: set[int] = set()
    selected_codex = False
    selected_custom = False
    if raw_selection:
        for part in raw_selection.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part)
                if idx == codex_idx:
                    selected_codex = True
                elif idx == other_idx:
                    selected_custom = True
                elif 1 <= idx <= len(provider_status):
                    selected_indices.add(idx)

    # Prompt API keys for newly selected but unconfigured providers
    for idx in sorted(selected_indices):
        key, label, was_configured = provider_status[idx - 1]
        if was_configured:
            configured_providers.append(key)
            continue

        if key == "anthropic":
            api_key = prompt_anthropic_key()
            if api_key:
                profile_id = add_api_key_profile(key, api_key, name="default")
                console.print(
                    f"  [green]\u2713[/green] Saved as auth profile [cyan]{profile_id}[/cyan]"
                )
                configured_providers.append(key)
        else:
            console.print(f"\n  [bold]{label}[/bold] \u2014 enter API Key (Enter to skip):")
            api_key = typer.prompt(
                f"  {label} API Key", default="", show_default=False, prompt_suffix=" "
            ).strip()

            if api_key:
                profile_id = add_api_key_profile(key, api_key, name="default")
                console.print(
                    f"  [green]\u2713[/green] Saved as auth profile [cyan]{profile_id}[/cyan]"
                )
                configured_providers.append(key)
            else:
                console.print("  [dim]Skipped.[/dim]")

    # OpenAI Codex (only use credentials if user selected it)
    if selected_codex:
        if codex_available:
            configured_providers.append("openai-codex")
            console.print("\n  [green]\u2713[/green] OpenAI Codex enabled")
        else:
            console.print(
                "\n  [yellow]\u26a0[/yellow] OpenAI Codex selected but no valid token found."
                " Run Codex CLI to authenticate first."
            )

    # Configure custom provider
    if selected_custom and not custom_configured:
        console.print("\n  [bold]Custom Provider[/bold]")
        api_base = typer.prompt(
            "  API Base URL",
            default=custom_p.api_base or "http://localhost:8000/v1",
            prompt_suffix=" ",
        ).strip()
        api_key = typer.prompt(
            "  API Key", default="", show_default=False, prompt_suffix=" "
        ).strip()
        models_raw = typer.prompt(
            "  Models (comma-separated)", default="", show_default=False, prompt_suffix=" "
        ).strip()
        api_base = api_base.strip("[]")  # strip accidental brackets from prompt
        if api_base:
            config.providers.custom.api_base = api_base
            config.providers.custom.api_key = api_key or "no-key"
            configured_providers.append("custom")
            if models_raw:
                model_names = [m.strip() for m in models_raw.split(",") if m.strip()]
                config.providers.custom.models = model_names
                custom_models = PROVIDER_TOP_MODELS.setdefault("custom", [])
                for m in model_names:
                    custom_models.append((m, m))
            console.print(f"  [green]\u2713[/green] Custom provider configured ({api_base})")
        else:
            console.print("  [dim]Skipped.[/dim]")
    elif selected_custom and custom_configured:
        configured_providers.append("custom")
        # Reload saved model names into the in-memory model list
        if custom_p.models:
            custom_models = PROVIDER_TOP_MODELS.setdefault("custom", [])
            for m in custom_p.models:
                custom_models.append((m, m))

    # -- Default model choice --------------------------------------------------
    model_choices = build_model_choices(configured_providers)
    model_chosen = None

    if not model_choices:
        console.print(
            "\n[yellow]  No provider configured.[/yellow]"
            " Run [cyan]theos init[/cyan] again or use [cyan]theos auth add[/cyan]."
        )
    elif len(model_choices) == 1:
        model_chosen = model_choices[0][0]
    else:
        while True:
            console.print("\n  Choose the default model:")
            for i, (mid, mlabel) in enumerate(model_choices, 1):
                console.print(f"    [{i}] {mlabel}  [dim]({mid})[/dim]")
            console.print("    [0] Custom (type model name)")
            raw = typer.prompt("  Default (number)", default="1", prompt_suffix=" ").strip()
            if raw == "0":
                custom = typer.prompt("  Model name", prompt_suffix=" ").strip()
                if not custom:
                    continue
                ok, validation_msg, verified_live = validate_model_choice(
                    custom, configured_providers
                )
                if not ok:
                    console.print(f"[red]\u2717[/red] {validation_msg}")
                    continue
                if verified_live:
                    console.print(f"[green]\u2713[/green] {validation_msg}")
                    model_chosen = custom
                    break
                console.print(f"[yellow]\u26a0[/yellow] {validation_msg}")
                if typer.confirm("  Keep this default model anyway?", default=False):
                    model_chosen = custom
                    break
                continue

            try:
                model_chosen = model_choices[int(raw) - 1][0]
            except (ValueError, IndexError):
                model_chosen = model_choices[0][0]
            break

    if model_chosen and model_chosen != config.agents.defaults.model:
        config.agents.defaults.model = model_chosen
        custom_model_ids = {m for m, _ in PROVIDER_TOP_MODELS.get("custom", [])}
        if model_chosen in custom_model_ids or configured_providers == ["custom"]:
            config.agents.defaults.provider = "custom"
        save_config(config)
        console.print(f"[green]\u2713[/green] Default model: [cyan]{model_chosen}[/cyan]")
        if config.agents.defaults.provider == "custom":
            console.print(
                f"[green]\u2713[/green] Provider: [cyan]custom[/cyan]"
                f" ({config.providers.custom.api_base})"
            )

    return configured_providers
