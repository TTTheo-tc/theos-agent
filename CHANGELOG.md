# Changelog

## 2026-05-09
- **feat(agent)**: load gitignored `.theos/BOT.md` bootstrap overlays after repo bootstrap files, and document the public/project vs private/local instruction split

## 2026-04-14
- **feat(memory)**: extend recall journal entries with normalized `claim_hash` (SHA1[:12]) for grounding, and extend `recall_targets.json` with cumulative `total_score` plus per-day `daily_count` / `daily_counts` aggregates for downstream ranking
- **feat(memory)**: add unified memory event log at `memory/instinct/memory_events.jsonl` with `memory.recall.folded`, `memory.recall.ingested`, and `memory.flush.completed` emissions wired into fold / ingest / pre-compaction flush
- **feat(memory)**: add `src/memory/recall_ranking.py` with rank-only 6-component recall candidate scoring (frequency / relevance / diversity / recency / consolidation / conceptual) over `recall_targets.json`; no auto-promotion, no side effects
- **feat(memory)**: add `src/memory/kg_gc.py` with `gc_superseded_nodes(workspace, max_age_days=30)` that deletes long-superseded KG nodes plus dangling edges and runs `VACUUM`; wired into `run_structured_rule_cleanup_event` so the 6-hour cron reclaims KG space
- **docs**: add `docs/dev/specs/2026-04-14-memory-v2-roadmap.md`, defining the next memory roadmap around bugfixes, retrieval quality, recall intelligence, and safety/bridge work
- **docs**: update `docs/modules/memory/overview.md` and `docs/modules/agent-core/overview.md` to reflect memory recall telemetry, recall maintenance, and pre-compaction flush behavior
- **docs**: add a `docs/modules` sync check to `BOT.md` so durable source changes must explicitly evaluate whether module architecture docs need updating

## 2026-04-13
- **docs**: add workspace `src/templates/AGENTS.md` so `theos agent` can route to repo-local instructions, and align `README.md`/`BOT.md` with the current `docs/modules` + `docs/dev` structure
- **docs**: remove stale `BOT.md` references to `web/`, old module-doc paths, and conflicting `docs/dev/` lifecycle wording
- **docs**: tighten `docs/modules/TEMPLATE.md` with `Owns/Does Not Own`, a single source-reference format, and `overview.md` as the required split entrypoint
- **docs**: add `docs/dev/specs/TEMPLATE.md` and `docs/dev/plans/TEMPLATE.md`, and wire them into `BOT.md` as the standard format for design specs and implementation plans
- **docs**: add request-context and decision-history sections to spec/plan templates so user requirements and key design choices remain traceable

## 2026-04-12
- **docs**: clarify CI-aware verification rules in `BOT.md`, separating cross-repo expectations from TheOS-specific commit/push checks
- **docs**: align `BOT.md` with the current `docs/` and `ui/` layout, removing stale `web/` and old module-doc path references

## 2026-04-07
- **fix**: repair `feishu_sheet` tool schema so Codex no longer rejects requests with `invalid_function_parameters`
- **fix**: distinguish invalid tool-schema errors from genuinely corrupted session context in user-facing retry hints
- **fix**: retry one-shot Codex transport disconnects and return a short retry hint instead of surfacing raw stream-drop errors
- **fix**: fall back to Feishu plain-text replies when interactive card delivery fails
- **fix**: relax inbound prompt-injection blocking for personal use so natural model/role questions are allowed while structural injection and secret blocking remain
- **fix**: repair Linux systemd gateway restart by emitting an unquoted `WorkingDirectory` and surfacing restart failures in `theos gateway restart`
- **fix**: disable Anthropic/Claude OAuth usage in TheOS and require standard Anthropic API keys
- **docs**: update auth/provider docs for Anthropic OAuth shutdown

## 2026-03-30
- **fix**: delay restart notification to wait for channel initialization
- **feat**: add Pillow dependency for auto image compression
- **feat**: auto-compress oversized images before sending to Claude API


## 2026-03-29
- **feat**: add Pillow dependency for auto image compression
- **feat**: auto-compress oversized images before sending to Claude API
- **fix**: use auth_token instead of api_key for OAuth token refresh
- **docs**: align daemon docs with implementation
- **fix**: health endpoint PID match, env var coverage, stable tests
- **fix**: address review — health check, update path, env vars, logs
- **feat**: add --no-daemon flag to theos init
- **feat**: show gateway daemon status in theos status
- **feat**: add init Step 6 — auto-install gateway daemon
- **feat**: convert gateway to Typer group with stop/restart/uninstall/logs
- **feat**: add health check probe
- **feat**: implement SystemdService for Linux
- **feat**: implement LaunchdService for macOS
- **feat**: add GatewayService ABC and resolve_service()
- **docs**: add gateway daemon auto-start design spec
- **fix**: update config schema baseline for UIConfig, WebFetchConfig, and duckduckgo default
- **fix**: repair broken tests — wrong module paths, stale attributes, event loop pollution
- **fix**: create ui/dist dir in CI to fix hatchling build


## 1.0.0 - 2026-03-28

- Initial TheOS repository baseline.
- Reset version lineage for the new repository.
- Start a new changelog history from the TheOS codebase initialization.
