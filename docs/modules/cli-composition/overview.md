# CLI & Composition

> Module documentation is not a requirements doc or a changelog.

## Purpose

- **Owns**: The Typer CLI application, all user-facing commands (`agent`, `gateway`, `init`, `status`, `channels`, `feishu-auth`, `report`, `ui`, `cron`, `auth`, `provider`), the interactive REPL, the Pydantic configuration schema, and config file I/O (load/save/migrate/encrypt).
- **Does Not Own**: Agent loop execution logic (`src/agent/`), channel adapters (`src/channels/`), daemon lifecycle management (`src/daemon/`), or the dashboard HTTP server (`src/ui/`). The CLI wires these together but does not implement their internals.

## Source Scope

```
src/cli/
  commands.py          # Typer app root, command registration, channel status/login
  agent_cmd.py         # `theos agent` interactive and single-message mode
  gateway_cmd.py       # `theos gateway` foreground server + stop/restart/logs/uninstall
  init_cmd.py          # `theos init` wizard (reset, providers, channels, orchestration, daemon)
  init_providers.py    # Provider detection sub-wizard
  init_roles.py        # Team-mode role configuration sub-wizard
  init_genver.py       # Generator-verifier configuration sub-wizard
  init_channels.py     # Channel setup sub-wizard
  init_soul.py         # Personality preset sub-wizard
  display.py           # Rich console instance, response/usage rendering
  repl.py              # prompt_toolkit session, terminal state, exit detection
  auth_cmd.py          # `theos auth` + `theos provider` sub-commands
  cron_cmd.py          # `theos cron` sub-commands
  report_cmd.py        # `theos report daily/weekly`
  ui_cmd.py            # `theos ui` standalone dashboard viewer

src/config/
  schema.py            # Root Config (BaseSettings), all nested Pydantic models
  schema_channels.py   # ChannelsConfig and per-channel sub-models (re-exported from schema.py)
  loader.py            # load_config(), save_config(), migration, proxy env propagation
```

## Entry Points

| Entry point | Trigger | File:line |
|---|---|---|
| `app` (Typer root) | `python -m src` or `theos` | `src/cli/commands.py:22` |
| `agent()` | `theos agent [-m MSG]` | `src/cli/agent_cmd.py:23` |
| `gateway()` | `theos gateway` | `src/cli/gateway_cmd.py:269` |
| `init()` | `theos init [--reset] [--no-daemon]` | `src/cli/init_cmd.py:199` |
| `status()` | `theos status` | `src/cli/commands.py:483` |
| `load_config()` | Any command that reads config | `src/config/loader.py:26` |

## Architecture

The CLI uses a hub-and-spoke layout. `commands.py` creates a single `typer.Typer` app and registers commands from sub-modules via `app.command()` and `app.add_typer()` (`commands.py:55-62`). Heavy logic lives in dedicated `*_cmd.py` files; the `init` wizard further delegates to `init_providers.py`, `init_roles.py`, `init_genver.py`, `init_channels.py`, and `init_soul.py`.

**Config model hierarchy** (all in `schema.py`):

```
Config (BaseSettings)
  +-- AgentsConfig
  |     +-- AgentDefaults (model, max_tokens, failover_models, ...)
  |     +-- dict[str, AgentRoleConfig]  (team roles)
  |     +-- GenVerConfig (generator/verifier models, phases, commands)
  |     +-- OrchestratorConfig, ReflectorConfig, SubagentPolicyConfig
  +-- ChannelsConfig (WhatsApp, Discord, Feishu, Telegram, Slack, DingTalk, QQ, Email, ...)
  +-- ProvidersConfig (16 provider slots, each a ProviderConfig with api_key/api_base/extra_headers)
  +-- GatewayConfig (host, port, HeartbeatConfig, PollersConfig, UIConfig)
  +-- ToolsConfig (web search/fetch, exec, stock, browser, MCP servers)
  +-- SecurityConfig + AutonomyConfig
  +-- MemoryConfig (injection, search, compaction, flush, GC)
  +-- KnowledgeGraphConfig, EmbeddingConfig, ResponseCacheConfig
```

All models extend a `Base(BaseModel)` with `alias_generator=to_camel` and `populate_by_name=True`, accepting both camelCase and snake_case in JSON (`schema.py:37-40`).

## Data Flow

1. **Config load**: `load_config()` reads `~/.theos/config.json`, runs `_migrate_config()` for legacy fields, decrypts secrets via `ConfigSecretsManager`, validates with `Config.model_validate()`, then applies proxy env vars (`loader.py:26-90`).
2. **Config save**: `save_config()` serializes with `model_dump(by_alias=True)`, encrypts sensitive fields if a master key is available, writes JSON (`loader.py:93-122`).
3. **Agent command**: loads config, creates `MessageBus`, `make_provider()`, `CronService`, optional `Reflector`, then constructs `AgentLoop`. Single-message mode calls `process_direct()`; interactive mode starts the bus consumer loop (`agent_cmd.py:90-352`).
4. **Gateway command**: loads config, runs startup checks, creates `AgentLoop`, `ChannelManager`, `CronService`, `HeartbeatService`, `PollerService`, optional OAuth callback server, optional UI server, then enters the async event loop (`gateway_cmd.py:269-750`).
5. **Init wizard**: sequential steps -- reset (optional), config create/refresh, symlinks, hooks, proxy, workspace+soul, providers, channels, web search, orchestration mode, daemon install (`init_cmd.py:199-426`).

## State & Persistence

| State | Location | Owner |
|---|---|---|
| Configuration | `~/.theos/config.json` | `loader.py` (read/write) |
| Auth profiles | `~/.theos/auth-profiles.enc` or `.json` | `src/auth/` (CLI reads via `init_cmd.py`) |
| Workspace | `~/.theos/workspace/` (default) | Created by `init`, synced by `sync_workspace_templates()` |
| REPL history | `~/.theos/cli_history` | `repl.py` via `prompt_toolkit.FileHistory` |
| Gateway logs | `<workspace>/logs/gateway.log` | `gateway_cmd.py:303-310` |
| Bridge | `~/.theos/bridge/` | `commands.py:133-188` |

## Invariants

1. Config always round-trips through Pydantic validation -- no raw dict patches survive a load/save cycle.
2. `load_config()` never raises on a missing file; it returns `Config()` defaults (`loader.py:88`).
3. All provider matching uses the `PROVIDERS` registry order; explicit prefix match wins over keyword match (`schema.py:493-515`).
4. The `gateway` command supports graceful restart via `SIGHUP` -- it drains pending outbound, waits for channels, then `os.execv()` restarts the process (`gateway_cmd.py:563-749`).
5. Encrypted config values fail loudly if the master key is wrong or missing, never silently falling back to encrypted ciphertext (`loader.py:55-66`).

## Extension Points

- **New CLI command**: Add a new `*_cmd.py` file, import and register it in `commands.py:47-62`.
- **New provider**: Add a `ProviderConfig` field to `ProvidersConfig` (`schema.py:270-289`) and a `ProviderSpec` to `src/providers/registry.py`.
- **New channel**: Add a config model to `schema_channels.py`, a field to `ChannelsConfig`, and display it in `channels_status()`.
- **New init wizard step**: Add a sub-wizard file (`init_*.py`) and call it from `init()` in `init_cmd.py`.
- **Config migration**: Add rules to `_migrate_config()` in `loader.py:125-139`.

## Failure Modes

| Failure | Behavior |
|---|---|
| Missing config file | Returns defaults, prints no warning (`loader.py:88`) |
| Invalid JSON in config | Prints warning, falls back to defaults (`loader.py:84-86`) |
| Encrypted config, wrong key | Raises `RuntimeError` with clear message (`loader.py:57-65`) |
| No provider API key configured | `make_provider()` raises `ValueError`, caught and displayed by CLI (`agent_cmd.py:49-50`) |
| Daemon install fails | Prints warning, suggests manual `theos gateway` (`init_cmd.py:412-418`) |
| Port bind failure (gateway UI) | Logs warning, continues without UI server (`gateway_cmd.py:683-690`) |

## Verification

```bash
# CLI commands and input
uv run pytest tests/test_commands.py tests/test_cli_input.py -q

# Config loading and encryption
uv run pytest tests/test_config_loader.py tests/test_config_loader_encryption.py -q

# Init wizard sub-modules
uv run pytest tests/test_init_cmd.py tests/test_init_channels.py tests/test_init_providers.py -q

# Gateway CLI
uv run pytest tests/test_gateway_cli.py -q

# Config schema drift
make schema-check
```

## Related Files

- `src/__main__.py` -- package entry point, calls `app()`
- `src/__init__.py` -- `__version__`, `__logo__`
- `src/agent/loop.py` -- `AgentLoop` (constructed by `agent_cmd.py` and `gateway_cmd.py`)
- `src/bus/queue.py` -- `MessageBus` (constructed by CLI, passed to agent)
- `src/daemon/` -- platform service abstraction used by `init_cmd.py` and `gateway_cmd.py`
- `src/providers/factory.py` -- `make_provider()` called from both `agent_cmd.py` and `gateway_cmd.py`
- `src/providers/registry.py` -- `PROVIDERS` list, used by `Config._match_provider()`
- `src/security/config_secrets.py` -- config encryption/decryption called by `loader.py`
- `src/templates/` -- bootstrap files synced to workspace by `sync_workspace_templates()`
