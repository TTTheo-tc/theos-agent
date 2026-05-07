# Auth

> Module doc -- not a requirements doc, not a changelog.

## Purpose

- **Owns**: OAuth credential lifecycle (resolve, refresh, persist), auth profile storage, plugin-based provider authentication. Runtime-registered plugins: OpenAI Codex, GitHub Copilot. Anthropic plugin exists in source but is intentionally not registered at startup (`src/auth/plugins/__init__.py:8`).
- **Does Not Own**: Credential injection into HTTP requests (owned by `src/security/credential_injector.py`), API key validation or provider-side auth errors (owned by providers), master key management (owned by `src/security/keychain.py`).

## Source Scope

```
src/auth/
  types.py            # Pydantic models: ApiKeyCredential, TokenCredential, OAuthCredential, AuthProfileStore
  store.py            # Load/save encrypted auth store, profile CRUD
  oauth_manager.py    # Resolve, refresh, background-maintain OAuth tokens
  oauth_plugin.py     # OAuthPlugin protocol definition
  plugins/
    __init__.py        # register_builtin_plugins()
    anthropic.py       # Anthropic PKCE OAuth (token refresh, login flow)
    openai_codex.py    # Codex oauth_cli_kit integration
    github_copilot.py  # GitHub device-flow OAuth + API key exchange
```

Adjacent: `src/security/crypto.py` and `src/security/keychain.py` provide the encryption layer used by the store.

API-key profile storage is part of the core runtime. OAuth login/refresh flows
for OpenAI Codex and GitHub Copilot require the `auth-oauth` optional
dependency extra because they use `oauth-cli-kit`, `keyring`, and `filelock`.

## Entry Points

| Entry point | Purpose |
|---|---|
| `store.load_auth_store()` | Load `~/.theos/auth-profiles.enc`, auto-migrating from legacy JSON if needed. (`store.py:33`) |
| `store.get_credential_for_provider(provider)` | Return `(api_key, profile_id)` for a provider, preferring `last_good`. (`store.py:168`) |
| `store.add_api_key_profile(provider, key, name)` | Save an API key and mark as default. (`store.py:197`) |
| `store.add_oauth_profile(...)` | Save an OAuth credential and mark as default. (`store.py:223`) |
| `OAuthManager.resolve(provider, profile_id)` | Return `(api_key, headers)`, refreshing if expired. (`oauth_manager.py:34`) |
| `OAuthManager.try_cached(provider, profile_id)` | Return cached credentials without refresh (hot-path safe). (`oauth_manager.py:62`) |

## Architecture

```
AuthProfileStore (Pydantic model, on-disk as AES-256-GCM encrypted JSON)
    |
    |-- profiles: dict[str, ApiKeyCredential | TokenCredential | OAuthCredential]
    |-- last_good: dict[str, str]     # provider -> preferred profile_id
    |-- usage_stats: dict[str, ProfileUsageStats]

OAuthManager
    |-- plugins: dict[str, OAuthPlugin]   # provider_id -> plugin
    |-- resolve() / try_cached()          # read store, check expiry, refresh via plugin
    |-- _refresh_with_lock()              # file-lock protected refresh
    |-- background thread                 # proactive refresh of expiring tokens
```

Profile IDs follow the format `"provider:name"` (e.g., `"anthropic:default"`). The discriminated union type uses `"type"` field: `api_key`, `token`, or `oauth` (`types.py:47`).

### Plugin architecture

`OAuthPlugin` is a `Protocol` with five methods (`oauth_plugin.py:10`):
- `format_api_key(cred)` -- extract the usable key from an OAuth credential
- `auth_headers(token)` -- provider-specific headers
- `refresh(cred)` -- exchange refresh_token for new tokens
- `login(redirect_uri)` -- full OAuth authorization flow
- `read_external_credentials()` -- import from external sources

All methods are synchronous; async callers wrap with `run_in_executor`.

Built-in plugins are registered lazily in `plugins/__init__.py:8`. The `AnthropicPlugin` is intentionally not registered -- Anthropic OAuth is disabled in TheOS (`anthropic.py:78`).

## Data Flow

### Credential resolution (called by `src/providers/credentials.py`)

```
resolve_credentials(provider_name, config, model)
  -> store.get_credential_for_provider(provider_name)
      -> load_auth_store()
          -> _decrypt_file(~/.theos/auth-profiles.enc)
              -> keychain.resolve_master_key()
              -> crypto.decrypt(blob, master_key)
      -> prefer last_good[provider], fallback to any matching profile
  -> if no key: config file (secret_refs)
  -> if no key: env vars
  -> return ProviderCredentials(api_key, api_base, oauth_manager, profile_id)
```

### OAuth token refresh

```
OAuthManager.resolve(provider, profile_id)
  -> load_auth_store()
  -> check _is_expired(cred)  [cred.expires is ms since epoch]
  -> if expired: _refresh_with_lock()
      -> acquire FileLock(~/.theos/auth-profiles.oauth.lock)
      -> double-check: reload store, re-check expiry
      -> plugin.refresh(cred)  [HTTP call to provider token endpoint]
      -> save_credential(profile_id, refreshed_cred)
      -> release lock
  -> plugin.format_api_key(cred) + plugin.auth_headers(key)
```

### Legacy migration

On first load, if `auth-profiles.enc` does not exist but `auth-profiles.json` does, the store auto-migrates: reads JSON, encrypts with `save_auth_store()`, deletes the plaintext file (`store.py:64-85`).

## State & Persistence

| State | Location | Lifecycle |
|---|---|---|
| Auth profiles | `~/.theos/auth-profiles.enc` | Persistent, AES-256-GCM encrypted |
| Legacy profiles | `~/.theos/auth-profiles.json` | Auto-migrated on first load, then deleted |
| OAuth file lock | `~/.theos/auth-profiles.oauth.lock` | Transient, cross-process coordination |
| OAuthManager singleton | `factory._oauth_manager` | Process lifetime, lazily created |
| Background refresh thread | Daemon thread in `OAuthManager` | Runs every `interval_s` (default 1800s) |

## Invariants

1. **Encrypted at rest.** The auth store is always written encrypted. If the master key is unavailable during migration, the legacy file is kept as plaintext with a warning -- never silently dropped (`store.py:68-75`).
2. **No silent data loss.** `load_auth_store()` raises on decryption failure (wrong key, corruption) rather than returning an empty store (`store.py:56-61`).
3. **File-locked refresh.** OAuth refresh is protected by `FileLock` to prevent concurrent refresh from multiple processes (`oauth_manager.py:128`).
4. **Double-check after lock.** After acquiring the lock, the store is reloaded to check if another process already refreshed (`oauth_manager.py:139-145`).
5. **Expired tokens are returned, not silently dropped.** If refresh fails, `resolve()` returns the expired credential so the provider can propagate the auth error (`oauth_manager.py:56-59`).

## Extension Points

- **New OAuth provider**: Implement `OAuthPlugin` protocol, register in `plugins/__init__.py:register_builtin_plugins()`. The plugin handles token format and refresh; the manager handles persistence and locking.
- **New credential type**: Add a new Pydantic model to `types.py`, add parsing in `store._coerce_store()`, and update `store._extract_key()`.
- **External credential import**: Implement `read_external_credentials()` in the plugin. See `github_copilot.py:109` (reads LiteLLM token store and GitHub Copilot `hosts.json`).

## Failure Modes

| Failure | Behavior |
|---|---|
| Master key unavailable | `MasterKeyUnavailableError` raised, not swallowed (`store.py:54-55`) |
| Decryption failure (wrong key) | `RuntimeError` raised with guidance to check `SECRETS_MASTER_KEY` (`store.py:57-61`) |
| OAuth refresh HTTP error | Plugin returns `None`, manager logs warning, returns expired credential (`oauth_manager.py:53-59`) |
| OAuth refresh timeout | `FileLock` has 30s timeout. On timeout, refresh is skipped for that cycle. |
| Missing `keyring` package | Master key cannot be persisted; falls back to `SECRETS_MASTER_KEY` env var. If neither available, raises `MasterKeyUnavailableError`. |
| Background refresh failure | Logged as warning, does not crash the process (`oauth_manager.py:90`) |

## Verification

```bash
uv run pytest tests/test_oauth_manager.py tests/test_oauth_plugin.py tests/test_oauth_plugins.py tests/test_oauth_types.py tests/test_oauth_integration.py -q
uv run pytest tests/test_anthropic_oauth_login.py tests/test_anthropic_oauth_refresh.py -q
```

## Related Files

- `src/security/crypto.py` -- AES-256-GCM encrypt/decrypt (see `docs/modules/security-safety/overview.md`)
- `src/security/keychain.py` -- master key resolution from env/keychain
- `src/providers/credentials.py` -- three-tier credential cascade (primary consumer)
- `src/providers/factory.py` -- `_get_oauth_manager()` singleton
- `src/providers/anthropic_provider.py` -- OAuth token refresh on auth failure
