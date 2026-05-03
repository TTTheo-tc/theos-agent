# UI & Observability

> Module documentation is not a requirements doc or a changelog.

## Purpose

- **Owns**: The dashboard HTTP API (Starlette), read-only SQLite access to dashboard data, real-time SSE event streaming (dashboard events and log events), the React SPA frontend, and the reporting subsystem (metrics collection + markdown report generation).
- **Does Not Own**: Dashboard SQLite schema creation or writes (`src/store/dashboard_writer.py` owns the schema), agent loop logic, session management internals, or the CLI layer that launches the server.

## Source Scope

```
src/ui/
  server.py           # Starlette app factory, SPA static serving, uvicorn lifecycle
  db.py               # DashboardReader — async read-only SQLite access
  events.py           # UIEventBus — fan-out pub/sub for dashboard SSE
  log_events.py       # LogEventBus — fan-out pub/sub for log line SSE (credential-scrubbed)
  tailscale.py        # Tailscale/LAN IP detection for dashboard URL generation
  routes/
    __init__.py       # collect_routes() — central route registration
    health.py         # /api/health
    sessions.py       # /api/sessions, /api/agents, /api/metrics, /api/events, /api/search
    memory.py         # /api/memory/nodes, /api/memory/search, /api/memory/markdown
    cron.py           # /api/cron/jobs (CRUD + run)
    logs.py           # /api/logs, /api/logs/stream
    config.py         # /api/config (GET/PUT)
    settings.py       # /api/settings (GET/PUT)
    tools.py          # /api/tools, /api/tools/profiles

src/reporting/
  generator.py        # ReportGenerator.render() — metrics dict to markdown
  metrics.py          # MetricsCollector — queries task_events table for aggregate stats

ui/                   # React SPA (separate build artifact)
  package.json        # React 19 + Vite 6 + TypeScript 5 + Tailwind CSS 4 + shadcn
```

**Frontend stack** (from `ui/package.json`): React 19, React Router 7, Recharts 3 (charts), Radix UI + shadcn (components), XY Flow (graph visualization), Lucide (icons), cmdk (command palette). Built with Vite, tested with Vitest.

## Entry Points

| Entry point | Trigger | File:line |
|---|---|---|
| `create_ui_app()` | Gateway startup or standalone `theos ui` | `src/ui/server.py:34` |
| `start_ui_server()` | Gateway async boot | `src/ui/server.py:109` |
| `theos ui` CLI | Standalone viewer mode | `src/cli/ui_cmd.py:14` |
| `theos report daily\|weekly` | CLI report generation | `src/cli/report_cmd.py:48-58` |
| `collect_routes()` | Called by `create_ui_app()` | `src/ui/routes/__init__.py:37` |

## Architecture

The UI backend is a Starlette application created by `create_ui_app()` (`server.py:34-106`). It receives:
- `db_path`: path to the dashboard SQLite DB
- `event_bus`: optional `UIEventBus` for SSE streaming
- `app_context`: dict with workspace path, cron service, tool registry, config, etc.

These are stored on `app.state` and accessed by route handlers.

**Route structure** (`routes/__init__.py:37-66`):

| API group | Endpoints | Methods |
|---|---|---|
| Health | `/api/health` | GET |
| Sessions | `/api/sessions`, `/api/sessions/{id}`, `/api/agents`, `/api/search` | GET |
| Metrics | `/api/metrics`, `/api/metrics/cost` | GET |
| Events | `/api/events` | GET (SSE) |
| Memory | `/api/memory/nodes`, `/api/memory/search`, `/api/memory/nodes/{id}`, `/api/memory/markdown` | GET |
| Cron | `/api/cron/jobs`, `/api/cron/jobs/{id}`, `/api/cron/jobs/{id}/run` | GET/POST/PUT/DELETE |
| Logs | `/api/logs`, `/api/logs/stream` | GET (SSE) |
| Config | `/api/config` | GET/PUT |
| Settings | `/api/settings` | GET/PUT |
| Tools | `/api/tools`, `/api/tools/profiles` | GET |

**SPA serving**: When a built `ui/dist/` exists (or packaged `ui_static/`), the app mounts a catch-all handler that serves static assets for real files and `index.html` for all other paths (React Router handles client-side routing). API paths (`/api/*`) are excluded from SPA fallback (`server.py:57-59`).

**Event buses**: Two independent fan-out buses exist:
- `UIEventBus` (`events.py`): Dashboard data change events (session updates, agent activity). Fed by `DashboardWriter.set_event_callback()` in the gateway.
- `LogEventBus` (`log_events.py`): Real-time log lines. Fed by a loguru sink added in `gateway_cmd.py:669-683`. Sanitizes credentials via `scrub_credentials()` before delivery.

Both use the same pattern: subscribers get an `asyncio.Queue` (max 256 items), full queues silently drop events.

**Reporting** is a standalone subsystem:
- `MetricsCollector` (`metrics.py:14`) queries the `task_events` table in the orchestrator's event store DB, computing counts for completed/failed/retried tasks, events by type, and active sessions.
- `ReportGenerator.render()` (`generator.py:13`) converts the metrics dict into a markdown table report.
- Accessed via `theos report daily|weekly` (`report_cmd.py`).

## Data Flow

1. **Gateway boot**: `gateway_cmd.py` creates `DashboardWriter` (write-side), then `create_ui_app()` with `DashboardReader` (read-side). Both point to `<workspace>/data/dashboard.db`.
2. **Agent activity**: `AgentLoop` calls `DashboardWriter` methods to insert/update sessions, agents, events. Writer publishes change events via `UIEventBus.publish()`.
3. **Dashboard poll/SSE**: Frontend fetches `/api/sessions`, `/api/metrics`, etc. The `DashboardReader` executes read-only SQL. For real-time updates, the frontend subscribes to `/api/events` (SSE), which iterates over `UIEventBus.subscribe()`.
4. **Log streaming**: loguru sink in `gateway_cmd.py` pushes log entries to `LogEventBus`. Frontend subscribes to `/api/logs/stream` (SSE). Each entry is scrubbed by `scrub_credentials()` before delivery (`log_events.py:52-56`).
5. **Session enrichment**: `DashboardReader._enrich_session_row()` augments raw DB rows with runtime state from `TurnStore` and `SubagentStore` -- recoverable status, latest turn, background tasks (`db.py:168-237`).
6. **Reports**: `MetricsCollector` reads from a separate DB (`<workspace>/theos.db`, the orchestrator event store), not the dashboard DB.

## State & Persistence

| State | Location | Owner |
|---|---|---|
| Dashboard DB | `<workspace>/data/dashboard.db` | `src/store/dashboard_writer.py` (schema+writes), `src/ui/db.py` (reads) |
| Event store DB | `<workspace>/theos.db` | `src/store/database.py` (used by `MetricsCollector`) |
| Turn checkpoints | `<workspace>/turns/` | `src/session/turn_store.py` (read by `DashboardReader`) |
| Subagent checkpoints | `<workspace>/subagents/` | `src/session/subagent_store.py` (read by `DashboardReader`) |
| Frontend build | `ui/dist/` or `src/ui/ui_static/` | Vite build (`cd ui && npm run build`) |

## Invariants

1. `DashboardReader` never creates tables or writes data -- it opens the DB in `mode=ro` (`db.py:35`).
2. The SPA catch-all never intercepts `/api/*` paths -- they return a JSON 404 instead of `index.html` (`server.py:59`).
3. Path traversal is blocked in static file serving -- resolved paths must stay within the static root (`server.py:65-66`).
4. `LogEventBus` always scrubs credentials before publishing, regardless of log level (`log_events.py:52-56`).
5. CORS middleware is only added when no static dir is found (dev mode with Vite proxy) (`server.py:78-85`).
6. Event bus queues are bounded (256 items); overflows are silently dropped, never block the publisher (`events.py:57-58`).

## Extension Points

- **New API route**: Add a handler module in `src/ui/routes/`, import it in `routes/__init__.py`, append to `collect_routes()`.
- **New SSE stream**: Create a subscriber on `UIEventBus` or `LogEventBus`, or add a third event bus for a different data source.
- **New report type**: Add a method to `MetricsCollector` for the time range, call it from a new `report_app.command()`.
- **Frontend pages**: Add React components under `ui/src/`, register routes in React Router.

## Failure Modes

| Failure | Behavior |
|---|---|
| Dashboard DB missing (standalone `theos ui`) | Prints error, exits with code 1 (`ui_cmd.py:29-33`) |
| Port already in use | `start_ui_server()` propagates `OSError`; gateway logs warning and continues without UI (`gateway_cmd.py:683-690`) |
| DB opened while writer is active | SQLite WAL mode allows concurrent read+write; reader inherits journal mode from writer (`db.py:37`) |
| Event bus subscriber disconnects | `_SubscriptionIterator._cleanup()` removes queue from subscriber list (`events.py:32-36`); also triggered by `__del__` |
| Event store DB missing (reports) | Returns descriptive error string, does not crash (`report_cmd.py:31`) |

## Verification

```bash
# Backend route tests
uv run pytest tests/ui/ -q

# Reporting
uv run pytest tests/test_reporting.py -q

# Frontend build and lint
cd ui && npm run build
cd ui && npm run lint
cd ui && npm run test
```

## Related Files

- `src/store/dashboard_writer.py` -- Schema owner for `dashboard.db`, write-side counterpart to `DashboardReader`
- `src/store/database.py` -- Generic async SQLite wrapper used by `MetricsCollector`
- `src/session/runtime_state.py` -- `build_session_runtime_state()` used by `DashboardReader._enrich_session_row()`
- `src/session/turn_store.py`, `src/session/subagent_store.py` -- Checkpoint stores read during session enrichment
- `src/safety/leak_detector.py` -- `scrub_credentials()` used by `LogEventBus`
- `src/cli/gateway_cmd.py:539-690` -- Gateway-side UI/log event bus wiring
- `src/cli/ui_cmd.py` -- Standalone dashboard launcher
- `src/cli/report_cmd.py` -- CLI report commands
