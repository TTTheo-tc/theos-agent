# Automation

> Module documentation, not a requirements doc or changelog.

## Purpose

- **Owns**: Scheduled job execution (cron), high-frequency content polling (poller), periodic LLM-based task checking (heartbeat), and OS-level gateway daemon lifecycle (daemon).
- **Does Not Own**: The agent loop itself (`src/agent/`), message bus (`src/bus/`), LLM providers (`src/providers/`), or CLI command wiring (`src/cli/`).

## Source Scope

```
src/cron/
    __init__.py        — re-exports CronService, CronJob, CronSchedule
    service.py         — CronService: schedule engine with timer-based dispatch
    types.py           — Pydantic models: CronJob, CronSchedule, CronPayload, CronStore
    heartbeat.py       — HeartbeatService: periodic LLM-based task checker

src/poller/
    __init__.py        — package docstring (no re-exports)
    base.py            — BasePoller ABC + PollerEvent dataclass
    service.py         — PollerService: lifecycle manager for BasePoller instances
    x_poller.py        — XPoller: X/Twitter account monitor (concrete implementation)

src/daemon/
    __init__.py        — resolve_service(): platform dispatch factory
    base.py            — GatewayService ABC: install/uninstall/stop/restart/status
    launchd.py         — LaunchdService: macOS LaunchAgent backend
    systemd.py         — SystemdService: Linux systemd user service backend
    health.py          — wait_for_gateway(): post-start HTTP health probe
```

## Entry Points

| Entry Point | Where | Purpose |
|---|---|---|
| `CronService.start()` | `cron/service.py:101` | Start cron timer loop |
| `CronService.add_job()` | `cron/service.py:246` | Register a scheduled job |
| `HeartbeatService.start()` | `cron/heartbeat.py:114` | Start heartbeat polling loop |
| `PollerService.start()` | `poller/service.py:41` | Start all registered pollers |
| `PollerService.register()` | `poller/service.py:34` | Register a BasePoller before start |
| `resolve_service()` | `daemon/__init__.py:9` | Get platform-appropriate GatewayService |
| `wait_for_gateway()` | `daemon/health.py:48` | Block until gateway HTTP endpoint is up |

Callers: `src/cli/commands.py` (gateway command) creates CronService and HeartbeatService. PollerService is wired by the gateway startup. Daemon commands (`theos gateway install/uninstall`) use `resolve_service()`.

Runtime defaults are slim: cron service wiring is available, but heartbeat is
disabled by `gateway.heartbeat.enabled=false` and pollers are disabled until
their individual config flags are enabled. X/Twitter polling additionally
requires the `pollers` optional dependency extra.

## Architecture

Three independent subsystems, no coupling between them:

```
+------------------+    +-------------------+    +------------------+
|   CronService    |    |  PollerService    |    |  GatewayService  |
|  (timer-based)   |    |  (loop-based)     |    |  (OS daemon)     |
+--------+---------+    +--------+----------+    +--------+---------+
         |                       |                        |
   on_job callback         on_event callback        install/restart
         |                       |                        |
         v                       v                        v
   Agent loop              MessageBus            launchd / systemd
                           (InboundMessage)
```

**HeartbeatService** is a companion to CronService, not a separate subsystem. It runs its own `asyncio.sleep` loop and uses an LLM call to decide whether to trigger the agent.

### Cron

`CronService` is a single-process, in-memory scheduler backed by a JSON file. It does NOT use OS-level cron. Timer dispatch uses `asyncio.create_task(tick())` with computed sleep delays.

Three schedule kinds: `at` (one-shot epoch-ms), `every` (interval-ms), `cron` (cron expression via `croniter`, with optional timezone).

### Poller

`PollerService` manages multiple `BasePoller` instances, each in its own `asyncio.Task`. Pollers are pure-Python loops with zero token cost -- they only trigger the agent when content is detected.

Events flow through `PollerEvent` -> `PollerService._default_on_event()` -> `MessageBus.publish_inbound()` as `InboundMessage` with `channel="poller"`.

### Daemon

`GatewayService` is an ABC for OS-level daemon lifecycle. `resolve_service()` picks `LaunchdService` (macOS) or `SystemdService` (Linux) based on `sys.platform`. Both prefer `SIGHUP` for graceful restart, falling back to service manager restart.

## Data Flow

### Cron job execution

1. `_arm_timer()` computes delay to the earliest `next_run_at_ms` across all enabled jobs.
2. `asyncio.sleep(delay)` -> `_on_timer()` fires.
3. Due jobs filtered by `now >= next_run_at_ms`.
4. `_execute_job()` calls `on_job(job)` callback with a 5-minute timeout (`_JOB_TIMEOUT_S`).
5. Job state updated: `last_run_at_ms`, `last_status`, `last_error`.
6. One-shot (`at`) jobs: either deleted (`delete_after_run`) or disabled.
7. Recurring jobs: `next_run_at_ms` recomputed from scheduled time (not wall clock) to avoid drift.
8. Store saved to disk, timer re-armed.

### Poller event flow

1. `BasePoller.run_loop()` calls `setup()` (30s timeout), then enters `poll_once()` loop.
2. Each `poll_once()` returns `list[PollerEvent]` (empty = nothing new).
3. Events passed to `on_event` callback.
4. Default handler (`PollerService._default_on_event`) wraps event as `InboundMessage` and publishes to `MessageBus`.
5. Agent loop processes the message like any user message.

### Heartbeat decision

1. `_tick()` reads `HEARTBEAT.md` from workspace.
2. `_decide()` sends file content to LLM with a virtual tool call (`heartbeat` tool).
3. LLM returns `action: "skip"` or `action: "run"` with task summary.
4. If `run`: `on_execute(tasks)` triggers agent loop, result delivered via `on_notify`.

## State & Persistence

| Component | Storage | Location | Lifecycle |
|---|---|---|---|
| CronService jobs | JSON file | `store_path` (configured by caller) | Persists across restarts |
| CronJob state | In-memory + JSON | Inside `CronStore.jobs` | Updated per execution |
| BasePoller state | Impl-specific | e.g. XPoller uses JSON for last-seen IDs | Persists across restarts |
| HeartbeatService | `HEARTBEAT.md` | `workspace/HEARTBEAT.md` | User-managed file |
| GatewayService config | OS service files | `~/.config/systemd/user/` or `~/Library/LaunchAgents/` | Persists until uninstall |
| Daemon logs (macOS) | Log files | `~/.theos/logs/gateway-{stdout,stderr}.log` | Append-only |

## Invariants

1. **Cron schedule immutability**: `_compute_next_run` uses the scheduled time (not wall clock) as base for recurring jobs to prevent drift (`service.py:234-236`).
2. **Expired one-shot cleanup**: Past-due `at` jobs that never ran are silently removed on startup (`service.py:134-139`).
3. **Recurring job auto-expiry**: Non-`at` jobs get a 30-day `expires_at_ms` on creation (`service.py:263-264`).
4. **Timezone validation**: `tz` field is only valid for `cron` kind; validated on `add_job()` (`service.py:50-61`).
5. **Poller setup timeout**: `BasePoller.setup()` has a hard 30s timeout; failure prevents the poller from entering its loop (`base.py:56-61`).
6. **Poller events are owner-trusted**: `sender_is_owner=True` on all poller-injected messages (`poller/service.py:74`).
7. **Daemon restart prefers SIGHUP**: Both backends try `os.kill(pid, SIGHUP)` before falling back to service manager restart (`launchd.py:83-97`, `systemd.py:86-96`).
8. **Platform exclusivity**: `resolve_service()` raises `NotImplementedError` on unsupported platforms (`daemon/__init__.py:20-23`).

## Extension Points

- **New poller type**: Subclass `BasePoller` (`poller/base.py:26`), implement `setup()`, `poll_once()`, `teardown()`. Register via `PollerService.register()`.
- **New schedule kind**: Add to `CronSchedule.kind` literal (`types.py:14`), handle in `_compute_next_run()` (`service.py:21`).
- **New daemon backend**: Subclass `GatewayService` (`daemon/base.py:7`), add platform branch in `resolve_service()`.
- **Custom cron job handler**: Pass `on_job` callback to `CronService.__init__()`.
- **Custom poller event handler**: Pass `on_event` callback to `PollerService.__init__()`.

## Failure Modes

| Failure | Behavior | Location |
|---|---|---|
| Cron job timeout | `asyncio.TimeoutError` caught, `last_status="error"` | `service.py:213-216` |
| Cron job exception | Caught, `last_error` set, job stays enabled | `service.py:218-221` |
| Cron store corrupt | Warning logged, empty `CronStore` created | `service.py:86-88` |
| Poller setup failure | Logged, poller does not enter poll loop | `base.py:58-61` |
| Poller poll_once error | Warning logged, retries after `interval_s` | `base.py:78-83` |
| Poller event handler error | Warning logged, continues to next event | `base.py:72-75` |
| Heartbeat LLM error | Warning logged, skips tick | `heartbeat.py:167-168` |
| Daemon SIGHUP fails | Falls back to service manager restart | `launchd.py:90-97` |
| Gateway health probe timeout | Returns False after `timeout_s` | `health.py:82-88` |

## Verification

```bash
# Cron
uv run pytest tests/test_cron_service.py tests/test_cron_types.py tests/test_cron_commands.py -q

# Heartbeat
uv run pytest tests/test_heartbeat_service.py -q

# Daemon
uv run pytest tests/daemon/test_health.py tests/daemon/test_launchd.py tests/daemon/test_resolve.py tests/daemon/test_systemd.py -q
```

## Related Files

- `src/bus/queue.py` -- `MessageBus` that pollers inject events into
- `src/bus/events.py` -- `InboundMessage` dataclass used by poller event injection
- `src/cli/commands.py` -- gateway command wires CronService, HeartbeatService
- `src/cli/gateway_cmd.py` -- daemon install/uninstall CLI commands
- `src/agent/tools/gateway_restart.py` -- tool that calls `GatewayService.restart()`
- `src/config/schema.py` -- cron and poller configuration sections
- `docs/modules/agent-core/` -- agent loop that processes cron/poller-triggered messages
- `docs/modules/sessions-messaging/` -- MessageBus architecture
