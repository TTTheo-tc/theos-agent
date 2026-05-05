from src.agent.slash_commands import resolve_model_alias
from src.auth.types import ApiKeyCredential, AuthProfileStore
from src.cli.init_genver import configure_genver_interactive
from src.cli.init_providers import (
    FALLBACK_MODELS,
    PROVIDER_DISCOVERED_MODELS,
    PROVIDER_MODEL_SOURCE,
    PROVIDER_TOP_MODELS,
    _api_keys_by_provider,
    _resolve_model_fetch_credentials,
    build_model_choices,
    prompt_anthropic_key,
    validate_model_choice,
)
from src.config.schema import DEFAULT_GENVER_VERIFIER_COMMANDS, Config


def test_api_keys_by_provider_uses_last_good_and_fallback(monkeypatch) -> None:
    store = AuthProfileStore(
        profiles={
            "anthropic:default": ApiKeyCredential(provider="anthropic", key="ak-1"),
            "openai:work": ApiKeyCredential(provider="openai", key="ok-1"),
            "openai:backup": ApiKeyCredential(provider="openai", key="ok-2"),
        },
        last_good={"openai": "openai:work"},
    )
    monkeypatch.setattr("src.auth.store.load_auth_store", lambda: store)

    keys = _api_keys_by_provider()

    assert keys["anthropic"] == "ak-1"
    assert keys["openai"] == "ok-1"


def test_api_keys_by_provider_recognizes_oauth_credential(monkeypatch) -> None:
    from src.auth.types import OAuthCredential

    store = AuthProfileStore(
        profiles={
            "anthropic:oauth": OAuthCredential(
                provider="anthropic",
                access="oat-token",
                refresh="rt-refresh",
                expires=9999999999999,
            ),
            "openai:default": ApiKeyCredential(provider="openai", key="ok-1"),
        },
        last_good={"anthropic": "anthropic:oauth"},
    )
    monkeypatch.setattr("src.auth.store.load_auth_store", lambda: store)

    keys = _api_keys_by_provider()

    assert "anthropic" not in keys
    assert keys["openai"] == "ok-1"


def test_prompt_anthropic_key_rejects_oauth_token(monkeypatch) -> None:
    """Anthropic OAuth tokens are rejected during init."""
    monkeypatch.setattr(
        "src.cli.init_providers.typer.prompt",
        lambda *args, **kwargs: "sk-ant-oat01-token",
    )

    token = prompt_anthropic_key()

    assert token == ""


def test_prompt_anthropic_key_falls_back_to_manual_entry(monkeypatch) -> None:
    """When no OAuth profile exists, prompt_anthropic_key prompts for manual key."""
    from src.security.keychain import MasterKeyUnavailableError

    monkeypatch.setattr(
        "src.auth.store.load_auth_store",
        lambda: (_ for _ in ()).throw(MasterKeyUnavailableError()),
    )
    monkeypatch.setattr(
        "src.cli.init_providers.typer.prompt",
        lambda *args, **kwargs: "sk-ant-manual-key",
    )

    token = prompt_anthropic_key()

    assert token == "sk-ant-manual-key"


def test_openai_codex_fallback_models_are_current() -> None:
    codex_models = [model_id for model_id, _ in FALLBACK_MODELS["openai-codex"]]

    assert "openai-codex/gpt-5.4" in codex_models
    assert "openai-codex/gpt-5.4-pro" in codex_models


def test_codex_model_alias_points_to_current_default() -> None:
    assert resolve_model_alias("codex") == "openai-codex/gpt-5.4"


def test_resolve_model_fetch_credentials_uses_oauth_for_openai_codex(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.cli.init_providers._get_openai_codex_access_token",
        lambda: "oauth-token",
    )

    api_key, api_base = _resolve_model_fetch_credentials("openai-codex", Config(), {})

    assert api_key == "oauth-token"
    assert api_base == "https://api.openai.com"


def test_build_model_choices_falls_back_when_fetch_fails(monkeypatch) -> None:
    PROVIDER_TOP_MODELS.clear()
    PROVIDER_MODEL_SOURCE.clear()
    PROVIDER_DISCOVERED_MODELS.clear()
    monkeypatch.setattr(
        "src.cli.init_providers.fetch_models_for_provider",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "src.cli.init_providers._api_keys_by_provider",
        lambda: {"anthropic": "sk-test"},
    )
    monkeypatch.setattr("src.config.loader.load_config", lambda: Config())

    choices = build_model_choices(["anthropic"])
    model_ids = [model_id for model_id, _ in choices]

    assert "anthropic/claude-opus-4-6" in model_ids
    assert PROVIDER_MODEL_SOURCE["anthropic"] == "fallback"


def test_build_model_choices_curates_openrouter_live_catalog(monkeypatch) -> None:
    PROVIDER_TOP_MODELS.clear()
    PROVIDER_MODEL_SOURCE.clear()
    PROVIDER_DISCOVERED_MODELS.clear()
    monkeypatch.setattr(
        "src.cli.init_providers.fetch_models_for_provider",
        lambda *args, **kwargs: [
            ("openrouter/meta-llama/llama-4-maverick", "llama-4-maverick"),
            ("openrouter/anthropic/claude-sonnet-4-6", "claude-sonnet-4-6"),
            ("openrouter/deepseek/deepseek-chat", "deepseek-chat"),
        ],
    )
    monkeypatch.setattr(
        "src.cli.init_providers._api_keys_by_provider",
        lambda: {"openrouter": "sk-or-test"},
    )
    monkeypatch.setattr("src.config.loader.load_config", lambda: Config())

    choices = build_model_choices(["openrouter"])

    assert choices == FALLBACK_MODELS["openrouter"]
    assert PROVIDER_MODEL_SOURCE["openrouter"] == "live"
    assert PROVIDER_DISCOVERED_MODELS["openrouter"] == {
        "openrouter/anthropic/claude-sonnet-4-6",
        "openrouter/deepseek/deepseek-chat",
    }


def test_validate_model_choice_rejects_missing_live_model() -> None:
    PROVIDER_MODEL_SOURCE.clear()
    PROVIDER_DISCOVERED_MODELS.clear()
    PROVIDER_MODEL_SOURCE["openai-codex"] = "live"
    PROVIDER_DISCOVERED_MODELS["openai-codex"] = {"openai-codex/gpt-5.4"}

    ok, message, verified_live = validate_model_choice(
        "openai-codex/gpt-5.4-pro",
        ["openai-codex"],
    )

    assert not ok
    assert "was not found in the live model list" in message
    assert verified_live


def test_validate_model_choice_allows_fallback_with_warning() -> None:
    PROVIDER_MODEL_SOURCE.clear()
    PROVIDER_DISCOVERED_MODELS.clear()
    PROVIDER_MODEL_SOURCE["openai-codex"] = "fallback"
    PROVIDER_DISCOVERED_MODELS["openai-codex"] = {"openai-codex/gpt-5.4"}

    ok, message, verified_live = validate_model_choice(
        "openai-codex/gpt-5.4-pro",
        ["openai-codex"],
    )

    assert ok
    assert "Could not verify" in message
    assert not verified_live


def test_genver_custom_model_reprompts_when_live_validation_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.cli.init_genver.build_model_choices",
        lambda _providers: [("anthropic/claude-opus-4-6", "Claude Opus 4.6")],
    )
    answers = iter(["0", "openai-codex/gpt-5.4", "1", "1", "1", "pytest -x"])
    monkeypatch.setattr("src.cli.init_genver.typer.prompt", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(
        "src.cli.init_genver.validate_model_choice",
        lambda model, providers: (
            (
                False,
                f"{model} was not found in the live model list for openai-codex.",
                True,
            )
            if model == "openai-codex/gpt-5.4"
            else (True, "ok", True)
        ),
    )

    cfg = configure_genver_interactive(["anthropic", "openai-codex"])

    assert cfg is not None
    assert cfg.generator_model == "anthropic/claude-opus-4-6"


def test_configure_genver_interactive_uses_expanded_default_verifier_commands(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "src.cli.init_genver.build_model_choices",
        lambda _providers: [("anthropic/claude-opus-4-6", "Claude Opus 4.6")],
    )
    answers = iter(["1", "1", "1", ""])
    monkeypatch.setattr("src.cli.init_genver.typer.prompt", lambda *args, **kwargs: next(answers))

    cfg = configure_genver_interactive(["anthropic"])

    assert cfg is not None
    assert cfg.verifier_commands == DEFAULT_GENVER_VERIFIER_COMMANDS


def test_fetch_models_openai_compatible(monkeypatch) -> None:
    """OpenAI-compatible providers: prefix bare model IDs with provider key."""

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "data": [
                    {"id": "deepseek-chat", "created": 1700000000},
                    {"id": "deepseek-reasoner", "created": 1700000001},
                    {"id": "deepseek-embedding", "created": 1700000002},
                ]
            }

    monkeypatch.setattr("httpx.get", lambda *a, **kw: _Resp())

    from src.cli.init_providers import fetch_models_for_provider

    result = fetch_models_for_provider("deepseek", "sk-test")

    assert result is not None
    model_ids = [mid for mid, _ in result]
    assert "deepseek/deepseek-chat" in model_ids
    assert "deepseek/deepseek-reasoner" in model_ids
    # embedding models should be filtered
    assert not any("embedding" in mid for mid in model_ids)


def test_fetch_models_gemini(monkeypatch) -> None:
    """Gemini: uses /v1beta/models?key=, response has 'models' with 'name' field."""

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "models": [
                    {"name": "models/gemini-3.1-pro-preview", "displayName": "Gemini 3.1 Pro"},
                    {"name": "models/gemini-3-flash", "displayName": "Gemini 3 Flash"},
                    {"name": "models/embedding-001", "displayName": "Embedding"},
                ]
            }

    def _check_url_and_params(*args, **kwargs):
        url = args[0] if args else kwargs.get("url", "")
        assert "v1beta/models" in url, f"Gemini should use /v1beta/models, got {url}"
        assert "key=" in url, f"Gemini should use ?key= auth, got {url}"
        return _Resp()

    monkeypatch.setattr("httpx.get", _check_url_and_params)

    from src.cli.init_providers import fetch_models_for_provider

    result = fetch_models_for_provider("gemini", "test-key")

    assert result is not None
    model_ids = [mid for mid, _ in result]
    assert "gemini/gemini-3.1-pro-preview" in model_ids
    assert "gemini/gemini-3-flash" in model_ids
    assert not any("embedding" in mid for mid in model_ids)


def test_fetch_models_openrouter(monkeypatch) -> None:
    """OpenRouter: public API, model IDs already have org prefix, needs openrouter/ added."""

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "data": [
                    {"id": "anthropic/claude-sonnet-4-6", "created": 1700000000},
                    {"id": "deepseek/deepseek-chat", "created": 1700000001},
                ]
            }

    monkeypatch.setattr("httpx.get", lambda *a, **kw: _Resp())

    from src.cli.init_providers import fetch_models_for_provider

    result = fetch_models_for_provider("openrouter", "sk-or-test")

    assert result is not None
    model_ids = [mid for mid, _ in result]
    assert "openrouter/anthropic/claude-sonnet-4-6" in model_ids
    assert "openrouter/deepseek/deepseek-chat" in model_ids


def test_fetch_models_anthropic(monkeypatch) -> None:
    """Anthropic: uses x-api-key header, returns bare model IDs."""

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "data": [
                    {"id": "claude-opus-4-6", "created": 1700000002},
                    {"id": "claude-sonnet-4-6", "created": 1700000001},
                ]
            }

    def _check_headers(*args, **kwargs):
        headers = kwargs.get("headers", {})
        assert "x-api-key" in headers, "Anthropic should use x-api-key header"
        assert "anthropic-version" in headers
        return _Resp()

    monkeypatch.setattr("httpx.get", _check_headers)

    from src.cli.init_providers import fetch_models_for_provider

    result = fetch_models_for_provider("anthropic", "sk-ant-test")

    assert result is not None
    model_ids = [mid for mid, _ in result]
    assert "anthropic/claude-opus-4-6" in model_ids
    assert "anthropic/claude-sonnet-4-6" in model_ids
