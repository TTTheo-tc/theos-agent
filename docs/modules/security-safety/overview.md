# Security & Safety

> Module doc -- not a requirements doc, not a changelog.

## Purpose

- **Owns**: Encryption at rest (AES-256-GCM), master key management, credential injection into HTTP requests, secret reference resolution, autonomy enforcement, prompt injection detection, credential leak detection, security policy evaluation.
- **Does Not Own**: Auth profile CRUD (owned by `src/auth/`), provider-level auth error handling (owned by `src/providers/`), tool registration or execution (owned by `src/agent/`).

## Source Scope

```
src/security/
  crypto.py              # AES-256-GCM with HKDF key derivation
  keychain.py            # Master key resolution: env -> OS keychain -> generate
  credential_injector.py # Host-based credential injection + secret:// resolution
  secret_refs.py         # Config-level secret:// reference resolution
  autonomy.py            # Three-level autonomy model (readonly/supervised/full)

src/safety/
  layer.py               # SafetyLayer: unified entry point for all safety checks
  sanitizer.py           # Prompt injection detection (Aho-Corasick + regex)
  leak_detector.py       # Credential leak detection + redaction
  policy.py              # Configurable security policy rules
```

## Entry Points

| Entry point | Purpose |
|---|---|
| `SafetyLayer` | Unified safety facade. Wired into agent loop, tool execution, HTTP paths. (`layer.py:54`) |
| `resolve_master_key()` | Resolve or generate the master encryption key. (`keychain.py:31`) |
| `encrypt()` / `decrypt()` | Per-secret AES-256-GCM operations. (`crypto.py:26`, `crypto.py:40`) |
| `resolve_secret_ref(value)` | Resolve `secret://name` from auth store then env. (`secret_refs.py:19`) |
| `CredentialInjector.prepare_request()` | Inject secrets into HTTP headers/params by host. (`credential_injector.py:140`) |
| `AutonomyPolicy` | Enforce tool/path/command/rate restrictions. (`autonomy.py:73`) |

## Architecture

Two subsystems, distinct responsibilities:

### Security (src/security/) -- secrets management

```
keychain.resolve_master_key()
    |-- env: SECRETS_MASTER_KEY (hex, >=32 bytes)
    |-- OS keychain: keyring.get_password("theos", "master_key")
    |-- generate + persist (only if keychain write succeeds)

crypto.encrypt(plaintext, master_key) -> salt(32) || nonce(12) || ciphertext+tag
crypto.decrypt(blob, master_key)      -> plaintext
    Key derivation: HKDF-SHA256(master_key, random_salt) -> per-secret 32-byte key

CredentialInjector
    |-- CredentialRegistry: host-pattern -> CredentialMapping
    |-- SecretResolver (interface)
    |   |-- EnvSecretResolver       env vars
    |   |-- EncryptedSecretResolver auth store
    |-- inject_headers() / resolve_secret_refs() / prepare_request()

secret_refs.resolve_secret_ref("secret://anthropic")
    -> auth store lookup -> env var fallback
```

### Safety (src/safety/) -- content filtering

```
SafetyLayer
    |-- Sanitizer          prompt injection detection
    |-- LeakDetector       credential leak detection + redaction
    |-- PolicyEngine       configurable rule evaluation

Sanitizer
    |-- Aho-Corasick automaton (exact patterns: instruction override, role manipulation, ...)
    |-- Regex patterns (role prefix injection, special tokens)
    |-- block=True: replace with "[BLOCKED: prompt injection detected]"
    |-- block=False: return warnings only

LeakDetector
    |-- Aho-Corasick automaton (prefix patterns: sk-ant-, ghp_, AKIA, ...)
    |-- Regex patterns (JWT, Bearer, DB connection strings)
    |-- High-entropy token detection (opt-in, Shannon entropy threshold)
    |-- Actions: BLOCK / REDACT / WARN

PolicyEngine
    |-- PolicyRule: id, pattern, severity, action
    |-- Default rules: SYS_FILE_ACCESS, PRIVATE_KEY_REF, ENV_FILE_ACCESS, DESTRUCTIVE_CMD
    |-- Actions: BLOCK / REVIEW / WARN / SANITIZE
```

## Data Flow

### SafetyLayer check points

The `SafetyLayer` is applied at four stages in the pipeline:

```
1. validate_input(text)        -- user message before LLM call
   -> Sanitizer.scan() [warn mode]

2. sanitize_tool_output(text)  -- tool result before entering LLM context
   -> Sanitizer.scan() [block mode]
   -> LeakDetector.scan()
   -> returns sanitized text

3. scan_outbound(text)         -- agent output before user delivery
   -> LeakDetector.scan()
   -> returns SafetyCheckResult with redacted text

4. scan_inbound(text)          -- full blocking check on user content
   -> Sanitizer + LeakDetector + PolicyEngine
   -> may set block_message to reject input
```

Inbound blocking logic (`layer.py:129-143`): instruction_override, system_injection, special_token, code_block_injection, and role_prefix_injection categories cause immediate blocking. Role_manipulation alone does not block (natural dialogue can trigger it), but role_manipulation combined with another category does.

### Credential injection flow

```
CredentialInjector.prepare_request(url, headers, query_params)
  -> extract hostname from url
  -> CredentialRegistry.find_for_host(hostname)  [fnmatch patterns]
  -> for each matching CredentialMapping:
      -> SecretResolver.resolve(secret_name)
      -> inject via method: BEARER / BASIC / HEADER / QUERY
  -> resolve remaining secret:// references in headers/params
  -> return (headers, query_params)
```

### Autonomy enforcement

`AutonomyPolicy` checks four dimensions: tool allowlist (READONLY blocks write tools), path restrictions (write-protected + forbidden + workspace-only), command whitelist, and sliding-window rate limit (1 hour window). SUPERVISED level adds approval gating based on risk level and `auto_approve`/`always_ask` config lists (`autonomy.py:94-139`).

## State & Persistence

| State | Location | Lifecycle |
|---|---|---|
| Master key | OS keychain or `SECRETS_MASTER_KEY` env var | Persistent across restarts |
| Encrypted auth store | `~/.theos/auth-profiles.enc` | On-disk, read by security module |
| Aho-Corasick automatons | In-memory (Sanitizer, LeakDetector) | Built once at init, immutable |
| Policy rules | In-memory (PolicyEngine) | Built at init, extensible via `add_rule()` |
| Rate limiter timestamps | In-memory deque (ActionTracker) | Process lifetime, not persisted |
| Write-protected paths | Hardcoded set in AutonomyPolicy | `~/.theos/config.json`, `~/.theos/auth-profiles.enc` |

## Invariants

1. **No ephemeral master keys.** `resolve_master_key()` refuses to return a key that cannot be persisted. This prevents silent data loss where encrypted data becomes unreadable after restart (`keychain.py:64-69`).
2. **Per-secret derived keys.** Each encryption uses a fresh random salt; HKDF derives a unique data-encryption key. Compromising one ciphertext does not reveal others (`crypto.py:33`).
3. **Ciphertext integrity.** AES-256-GCM provides authenticated encryption. Tampered ciphertext raises `InvalidTag` (`crypto.py:46`).
4. **Secret injection is per-request, not per-tool.** `CredentialInjector.prepare_request()` resolves `secret://` references into HTTP headers/params and returns them to the calling tool (`credential_injector.py:135`). The tool implementation (e.g., `web_http.py:179`) does receive the resolved values in order to make the HTTP call. The boundary is at the config layer (secrets stored as `secret://` refs), not at the tool execution layer.
5. **Injection detection does not modify by default.** `Sanitizer(block=False)` returns warnings only; the caller decides whether to block or proceed (`sanitizer.py:98`).
6. **Leak redaction preserves prefix.** `_redact_after_prefix()` keeps the prefix visible for debugging while replacing the secret portion (`leak_detector.py:244`).

## Extension Points

- **New injection pattern**: Add to `_EXACT_PATTERNS` (Aho-Corasick) or `_REGEX_PATTERNS` in `sanitizer.py`.
- **New leak pattern**: Add to `_PREFIX_PATTERNS` or `_REGEX_PATTERNS` in `leak_detector.py`. Choose action: BLOCK/REDACT/WARN.
- **New policy rule**: Call `PolicyEngine.add_rule()` or extend `_DEFAULT_RULES` in `policy.py`.
- **New credential mapping**: Call `CredentialRegistry.add_mapping()` or extend `build_default_registry()` in `credential_injector.py:227`.
- **Custom secret resolver**: Subclass `SecretResolver` and pass to `CredentialInjector`. Two built-in: `EnvSecretResolver`, `EncryptedSecretResolver` (`credential_injector.py:173-210`).

## Failure Modes

| Failure | Behavior |
|---|---|
| `keyring` not installed | Falls back to `SECRETS_MASTER_KEY` env var. If neither available, `MasterKeyUnavailableError` is raised -- never silently returns a non-persistent key. |
| Master key too short | `ValueError` raised immediately (`crypto.py:31`, `keychain.py:48`). |
| Ciphertext tampered | `cryptography.exceptions.InvalidTag` raised by `decrypt()`. |
| Aho-Corasick unavailable | Falls back to linear scan of patterns. Functionally equivalent, just slower (`sanitizer.py:126`, `leak_detector.py:134`). |
| High-entropy false positives | Entropy detection is opt-in (`entropy_sensitivity > 0`). Threshold scales with sensitivity: `3.5 + sensitivity * 1.25` (`leak_detector.py:231`). URLs are excluded. |
| Rate limit exceeded | `check_rate_limit()` returns a blocking message. Does not raise. |
| Path outside workspace | `check_path_allowed()` returns a blocking message when `workspace_only=True`. |

## Verification

```bash
# Security: crypto, keychain, secrets
uv run pytest tests/test_security_crypto.py tests/test_security_keychain.py tests/test_secret_refs.py tests/test_config_secrets.py tests/test_credential_injector_integration.py -q

# Safety: sanitizer, leak detector, layer
uv run pytest tests/test_safety_layer.py tests/test_safety_leak_detector.py tests/test_safety_sanitizer.py -q

# Autonomy
uv run pytest tests/test_autonomy.py tests/test_autonomous_mode.py -q
```

## Related Files

- `src/auth/store.py` -- uses `crypto.encrypt()`/`decrypt()` and `keychain.resolve_master_key()` for store persistence
- `src/providers/credentials.py` -- calls `secret_refs.resolve_secret_ref()` for config values
- `src/agent/loop_core.py` -- primary consumer of `SafetyLayer`
- `src/agent/tools/web_http.py` -- uses `CredentialInjector` for outbound requests
