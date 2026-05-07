# Providers

> Module doc -- not a requirements doc, not a changelog.

## Purpose

- **Owns**: LLM API integration, message format conversion, streaming, tool-call parsing, credential resolution, failure classification, retry/failover orchestration.
- **Does Not Own**: Auth profile storage (owned by `src/auth/`), prompt construction (owned by `src/agent/context.py`), tool execution (owned by `src/agent/tools/`).

## Source Scope

```
src/providers/
  base.py                  # Abstract interface + data types
  anthropic_provider.py    # Native Anthropic SDK provider
  custom_provider.py       # OpenAI-compatible provider (vLLM, Ollama, OpenRouter, etc.)
  openai_codex_provider.py # OpenAI Codex Responses API (OAuth via oauth_cli_kit)
  factory.py               # Single entry point: make_provider(), make_provider_for_model()
  credentials.py           # Three-tier credential resolution cascade
  registry.py              # ProviderSpec definitions + model-name matching
  recovery.py              # Recovery policy (retry vs failover vs stop)
  recovery_provider.py     # RecoveryProvider wrapper with backoff
  errors.py                # Failure classification from responses/exceptions
  tool_call_parser.py      # Text-based tool-call fallback for weak providers
```

Adjacent but not owned: `src/auth/store.py` (credential storage), `src/security/secret_refs.py` (secret reference resolution).

The core Python dependency set includes Anthropic and OpenAI-compatible SDK
support. OAuth-backed providers such as OpenAI Codex and GitHub Copilot depend
on the `auth-oauth` extra for `oauth-cli-kit`, `keyring`, and `filelock`.

## Entry Points

| Entry point | Purpose |
|---|---|
| `factory.make_provider(config)` | Primary provider for the agent loop. Reads `config.agents.defaults.model` and `.provider`. Wraps in `RecoveryProvider` when `failover_models` is configured. (`factory.py:172`) |
| `factory.make_provider_for_model(config, model)` | Provider for a specific model (for example failover targets). Always auto-detects provider from model name. (`factory.py:213`) |
| `LLMProvider.chat()` | Non-streaming request. Returns `LLMResponse`. (`base.py:150`) |
| `LLMProvider.chat_stream()` | Streaming request. Yields `StreamDelta` chunks. Default impl wraps `chat()`. (`base.py:119`) |

## Architecture

Three backends, one interface:

```
LLMProvider (ABC)
  |-- AnthropicProvider     backend="anthropic"     native anthropic SDK
  |-- OpenAICompatProvider  backend="openai_compat"  native openai SDK
  |-- OpenAICodexProvider   backend="codex"          httpx SSE + oauth_cli_kit
  |-- RecoveryProvider      wrapper: retry + failover over any LLMProvider
```

`factory._build_provider()` routes by `ProviderSpec.backend` (`factory.py:79`). The backend string is the branching key -- all providers must declare one of `anthropic`, `openai_compat`, or `codex`.

Provider modules are imported on demand by backend. Keeping Codex/Copilot OAuth
dependencies optional means the default `theos agent` path can run with only
Anthropic/OpenAI-compatible dependencies installed.

`credentials.resolve_credentials()` implements a three-tier cascade (`credentials.py:30`):
1. Auth profile store (`~/.theos/auth-profiles.enc`)
2. Config file (`providers.<name>.apiKey`, resolved via `secret_refs`)
3. Environment variables (spec-defined `env_key` / `env_extras`)

## Data Flow

### Non-streaming request

```
caller -> make_provider(config)
       -> _resolve_spec_for_model()  [registry lookup]
       -> _build_provider()
           -> resolve_credentials()  [auth store -> config -> env]
           -> AnthropicProvider(api_key, ...)
       -> RecoveryProvider(primary, fallbacks)  [if failover_models set]

caller -> provider.chat(messages, tools, model, ...)
       -> _build_kwargs()
           -> _sanitize_empty_content()
           -> _convert_messages()      [OpenAI chat -> Anthropic Messages API]
           -> _convert_tools()
           -> _apply_cache_control()   [if spec.supports_prompt_caching]
       -> client.messages.create(**kwargs)
       -> _parse_response() -> LLMResponse
```

### Streaming request

Anthropic yields `StreamDelta` per event, with `tool_ready` for completed tool-use blocks (`anthropic_provider.py:715`) -- enables parallel tool execution while the stream continues.

OpenAI-compat accumulates tool-call deltas across chunks, emitting a final `StreamDelta(is_final=True)` with all completed calls (`custom_provider.py:377-418`).

### Recovery flow

`RecoveryProvider` wraps `chat()`/`chat_stream()`. On error:
1. `classify_failure()` categorizes the error (`errors.py:68`).
2. `decide_recovery()` returns RETRY / FAILOVER / STOP (`recovery.py:35`).
3. Retryable errors retry up to `MAX_RETRIES=2` with exponential backoff (`recovery_provider.py:23`).
4. Non-retryable auth/model/context errors skip straight to failover.
5. Fallbacks are tried in order, each using its own default model.

## State & Persistence

- **In-memory**: Provider instances hold SDK clients, default model, extra headers. `RecoveryProvider` holds the primary + fallback list.
- **Singleton**: `factory._oauth_manager` is lazily created once and starts a background refresh thread (`factory.py:16`).
- **On-disk**: Credentials live in `~/.theos/auth-profiles.enc` (owned by `src/auth/store.py`). Providers read but do not write credentials.

## Invariants

1. **Provider never stores plaintext secrets in logs.** Error messages are truncated to 500 chars before logging (`anthropic_provider.py:642`, `custom_provider.py:265`).
2. **Anthropic OAuth tokens are disabled.** `AnthropicProvider.__init__` raises `ValueError` if an `sk-ant-oat` key is supplied (`anthropic_provider.py:64-68`).
3. **Empty content is sanitized.** `_sanitize_empty_content()` in `LLMProvider` replaces empty strings/blocks that cause 400 errors (`base.py:70`).
4. **All providers must use a known backend.** `_build_provider()` raises `ValueError` for unknown backends (`factory.py:161`).
5. **RecoveryProvider never retries non-retryable failures.** `_IMMEDIATE_STOP` and `_NO_RETRY` sets enforce this (`recovery.py:13-21`).

## Extension Points

- **New LLM provider**: Subclass `LLMProvider`, add a `ProviderSpec` in `registry.py` with a matching `backend`, add routing in `factory._build_provider()`.
- **New OAuth plugin**: Add to `src/auth/plugins/`, register in `register_builtin_plugins()`. The plugin handles token refresh; the provider receives the resolved key.
- **Text tool-call fallback**: Add provider name to `FALLBACK_PROVIDER_ALLOWLIST` in `tool_call_parser.py`. Only for providers whose models may embed tool calls in prose.
- **Model-specific overrides**: Define `model_overrides` in the `ProviderSpec` (e.g., temperature caps for specific models).

## Failure Modes

| Failure | Classification | Recovery |
|---|---|---|
| Auth error (401) | `FailureClass.AUTH` | Skip retry, failover if available |
| Rate limit (429) | `FailureClass.RATE_LIMIT` | Retry with backoff, then failover |
| Context too long | `FailureClass.CONTEXT_EXCEEDED` | No retry, failover if available |
| Model not found | `FailureClass.MODEL_NOT_FOUND` | No retry, failover if available |
| Network / transient | `FailureClass.RETRYABLE` | Retry up to 2x, then failover |
| Programming error | `FailureClass.NON_RETRYABLE` | Stop immediately |

Anthropic-specific: on `AuthenticationError`, the provider attempts one OAuth token reload before failing (`anthropic_provider.py:438`). Stream auth recovery only triggers if no content/tool_ready events have been yielded yet (`anthropic_provider.py:762`).

Codex-specific: transport errors (connection drops) retry once with 0.5s delay (`openai_codex_provider.py:121`).

## Verification

```bash
# Provider implementations
uv run pytest tests/test_anthropic_provider.py tests/test_openai_codex_provider.py tests/test_openai_compat_provider.py -q

# Provider infrastructure
uv run pytest tests/test_provider_errors.py tests/test_provider_integration.py tests/test_provider_recovery.py tests/test_provider_registry.py tests/test_provider_secret_refs.py -q

# Recovery, streaming, tool-call parsing
uv run pytest tests/test_recovery_stream.py tests/test_stream_interface.py tests/test_stream_tool_loop.py tests/test_tool_call_parser.py -q

# Credential resolution
uv run pytest tests/test_credentials.py -q
```

## Related Files

- `src/providers/registry.py` -- ProviderSpec definitions, model-name routing
- `src/auth/store.py` -- credential persistence (see `docs/modules/auth/overview.md`)
- `src/security/secret_refs.py` -- `secret://` reference resolution
- `src/agent/context.py` -- `PROMPT_CACHE_BOUNDARY` used by Anthropic cache control
- `src/agent/loop_core.py` -- primary consumer of `chat_stream()`
