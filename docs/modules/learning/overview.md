# Learning

> Module documentation is not a requirements doc or a changelog.

## Purpose

- **Owns**: Three learning subsystems: (1) Dream -- sandboxed LLM exploration sessions, (2) Hooks -- pre/post chat lifecycle scripts, (3) Instinct -- the reflex/reflect/evolve pipeline that extracts, stores, promotes, and injects learned rules from task history.
- **Does Not Own**: Agent core loop (`src/agent/`), session management (`src/session/`), LLM provider abstraction (`src/providers/`), memory storage (`src/memory/`).

## Source Scope

```
src/dream/                  # Sandboxed exploration runner
  runner.py                 # DreamRunner: orchestrates a dream session
  tool_registry.py          # DreamToolRegistry: policy-gated tool proxy
  sandbox/tool_policy.py    # DreamToolPolicy: 4 runtime guards
  reflux/dream_domain.py    # L1/L2 dream content retrieval
  reflux/reflex_hook.py     # Dream injection stub (disabled by default)
  output/                   # Artifact tracking, eval, narrative, review, diary

src/hooks/                  # Lifecycle hook runner
  runner.py                 # HookRunner: pre-chat / post-chat script executor
  reflector.py              # DEPRECATED: lesson writing moved to reflect.js

instinct/scripts/           # Node.js learning pipeline
  reflex.js                 # Pre-chat: domain routing + context injection
  reflect.js                # Post-chat: event/lesson/rule extraction
  evolve.js                 # Batch: rule clustering, promotion, decay
```

## Entry Points

| Entry Point | Role |
|---|---|
| `HookRunner.run_pre_chat` (`hooks/runner.py:47`) | Invokes `pre-chat` hook (typically calls `reflex.js`) |
| `HookRunner.run_post_chat` (`hooks/runner.py:62`) | Invokes `post-chat` hook (typically calls `reflect.js`) |
| `DreamRunner.run` (`dream/runner.py:90`) | Executes a sandboxed dream exploration session |
| `reflex.js` main (`reflex.js:588-606`) | Domain matching + tiered context assembly |
| `reflect.js` main (`reflect.js:378-389`) | Event/lesson writing from post-task JSON |
| `evolve.js` main | Batch rule promotion from events to ACTIVE/PROBATION |

Learning is opt-in at runtime. The default config sets `learning.enabled=false`, so
TheOS does not inject `instinct/core.md`, instantiate `HookRunner`, or execute
`/instinct` commands unless the feature is explicitly enabled.

## Architecture

### Three Subsystems

```
                    ┌─────────────┐
  user message ───> │  pre-chat   │──> reflex.js ──> context injection
                    │  (HookRunner)│
                    └─────────────┘
                          │
                    [agent processes task]
                          │
                    ┌─────────────┐
  task result ────> │  post-chat  │──> reflect.js ──> events/ + lessons/ + rules/
                    │  (HookRunner)│
                    └─────────────┘
                          │
                    ┌─────────────┐
  batch/cron ─────> │   evolve    │──> evolve.js ──> PROBATION.md -> ACTIVE.md
                    │  (standalone)│
                    └─────────────┘
                          │
                    ┌─────────────┐
  manual/cron ────> │   dream     │──> DreamRunner ──> DREAM_INDEX + artifacts
                    └─────────────┘
```

### Hooks (src/hooks/)

`HookRunner` (`hooks/runner.py:29`) executes arbitrary scripts from a hooks directory:

- **pre-chat** (`runner.py:47`): Receives user message on stdin, returns context to inject (stdout). Timeout: 10s. The standard hook calls `reflex.js` with the user message.
- **post-chat** (`runner.py:62`): Receives JSON payload on stdin (session_key, response, error, status, tools_used, usage, duration_ms, routing_domains, artifacts, tests). Fire-and-forget, timeout 15s. The standard hook calls `reflect.js --mode post-task`.

Scripts run with `cwd` set to the hook's parent directory and `ARIESCLAW_WORKSPACE` in the environment.

### Reflex (instinct/scripts/reflex.js)

The reflex engine is the pre-chat domain router. Given a user message, it loads domain definitions from `instinct/domains/<category>/<domain>.md` (`reflex.js:51-86`), scores keywords with length-weighted matching (`reflex.js:92-145`), selects top-K domains (default 4, `INSTINCT_TOP_K`), and applies active rule boosts from `ACTIVE.md` (`reflex.js:592-605`).

Output is assembled in tiers (`reflex.js:413-586`) under a soft character budget (default 8000): Tier A (stable rules + top domain + gotchas), Tier B (remaining domains + adaptive rules, up to 70%), Tier C (secondary gotchas, fill budget). Risk warnings and a structured sidecar (`<!-- instinct-routing:{...} -->`) are always emitted.

### Reflect (instinct/scripts/reflect.js)

Post-chat learning writer with two modes. **Post-task mode** (`reflect.js:336-353`) reads JSON from stdin, builds a structured event (`reflect.js:156-231`), and writes: `events/<ts>.json`, `lessons/<ts>.md`, `rules/CANDIDATES.md`, and `live_rules.jsonl`. **Legacy mode** (`reflect.js:357-374`) writes simple gotcha files via `--domain X --gotcha Y`.

Rule extraction uses regex patterns (`reflect.js:90-114`) for conditional/advisory language. Quality filter (`reflect.js:118-145`) excludes task-status phrases, single-bug fixes, TODO items, and entries containing dates/versions/commit hashes.

### Evolve (instinct/scripts/evolve.js)

Batch rule promotion engine. Loads events from `events/` + `live_rules.jsonl`, clusters rules by normalized text with (rule, task_id) dedup (`evolve.js:124-185`). Promotion thresholds (`evolve.js:9-12`): frequency >= 3, avg confidence >= 0.72, last seen within 14 days. Rules promote through PROBATION then ACTIVE, with conflict detection against existing rules (`evolve.js:333-349`). Temporal decay (`evolve.js:96-120`): stable=never, adaptive=60d half-life, volatile=21d half-life; below 0.3 confidence = archived.

### Dream (src/dream/)

`DreamRunner` (`dream/runner.py:43`) gathers seed material from `events/` (`runner.py:273-287`), builds a constrained system prompt, creates a `DreamToolRegistry` (`tool_registry.py:15`) wrapping the base registry with policy enforcement, then runs `run_tool_loop` (`runner.py:118-126`). Outputs: eval JSON, narrative, review, artifact manifest, diary entry, DREAM_INDEX.jsonl.

`DreamToolPolicy` (`sandbox/tool_policy.py:26`) enforces 4 guards: path confinement to `sandbox_root` (`tool_policy.py:100-123`), USD budget cap (default $30), loop detection (threshold 5), and web query cap (default 50). Allowed tools: read_file, list_dir, glob, grep, memory_search/get, web_search/fetch, browser, bash, python. All write/deploy/side-effect tools are blocked (`tool_policy.py:54-69`).

**Dream Reflux**: L0 (default) = no injection; L1 = `reflex.js` queries DREAM_INDEX.jsonl (`reflex.js:373-410`); L2 = reserved.

## Data Flow

```
User message
    |
    v
pre-chat hook -> reflex.js
    |  keyword scoring -> domain matching -> tiered context assembly
    v
[Injected context string returned to agent]
    |
    v
[Agent processes task with injected domain knowledge]
    |
    v
post-chat hook -> reflect.js --mode post-task
    |  event + lesson + candidates + live_rules written
    v
memory/instinct/events/     (structured events)
memory/instinct/lessons/    (human-readable lessons)
memory/instinct/rules/      (CANDIDATES.md, ACTIVE.md, PROBATION.md)
memory/instinct/live_rules.jsonl
    |
    v (batch)
evolve.js
    |  cluster rules -> check thresholds -> promote -> decay
    v
rules/ACTIVE.md (injected by next reflex.js run)
```

## State & Persistence

All learning state lives under `~/.theos/workspace/memory/instinct/`:

| Path | Format | Writer | Reader |
|---|---|---|---|
| `events/*.json` | Structured JSON | reflect.js | evolve.js, reflex.js |
| `lessons/*.md` | Markdown | reflect.js | reflex.js |
| `rules/CANDIDATES.md` | Markdown list | reflect.js | Human review |
| `rules/PROBATION.md` | Markdown + HTML comments | evolve.js | evolve.js, reflex.js |
| `rules/ACTIVE.md` | Markdown + HTML comments | evolve.js | reflex.js |
| `live_rules.jsonl` | JSONL | reflect.js | evolve.js |
| `DREAM_INDEX.jsonl` | JSONL | DreamRunner | reflex.js, dream_domain.py |
| `dreams/<session>/` | Mixed (JSON, MD, files) | DreamRunner | Human review |

Dream sandbox files are written under `dreams/<session>/sandbox/`. `DreamToolRegistry` injects `working_dir=sandbox_root` for bash/python calls (`tool_registry.py:67`), and `DreamToolPolicy` blocks absolute path escapes and `..` traversal. However, this is not full filesystem isolation — the underlying `ExecTool` maintains persistent shell state (`shell.py:367`) and relative-path writes from within a `cd`'d shell could escape the sandbox. The current implementation is a best-effort cwd restriction, not a hard sandbox.

## Invariants

1. Dream bash/python calls run with `working_dir` set to `sandbox_root` (`tool_registry.py:67`). `DreamToolPolicy` rejects absolute paths outside sandbox and `..` traversal (`tool_policy.py:164-207`). Note: this does not prevent all relative-path escapes — it is a best-effort guard, not container-level isolation.
2. Dream sessions never use side-effect tools (message, deploy, write_file, etc.) -- `BLOCKED_TOOLS` set (`tool_policy.py:54-69`).
3. `reflect.js` is the single lesson/event writer (I6) -- `Reflector._do_reflect` is a no-op (`reflector.py:73-79`).
4. Rule promotion requires: frequency >= 3, avg confidence >= 0.72, last seen within 14 days (`evolve.js:9-12`).
5. Pre-chat hook timeout is 10s; post-chat hook timeout is 15s (`hooks/runner.py:53`, `runner.py:101`).
6. Hooks are fire-and-forget on error -- failures are logged but never crash the agent (`hooks/runner.py:57-60`, `runner.py:102-105`).
7. Kill switches: `INSTINCT_REFLECT_ENABLED=false`, `INSTINCT_EVOLVE_ENABLED=false` disable respective scripts at startup.

## Extension Points

- **New domain**: Add `instinct/domains/<category>/<name>.md` with `## Keywords`, `## Skills`, `## Tools`, `## Context` sections.
- **Custom hooks**: Place executable scripts named `pre-chat` / `post-chat` in the hooks directory.
- **Dream seed sources**: Extend `DreamRunner._gather_seeds` to pull from additional sources.
- **Reflux levels**: L2 integration stubs exist in `dream_domain.py:124-137` and `reflex_hook.py`.

## Failure Modes

| Failure | Impact | Mitigation |
|---|---|---|
| pre-chat hook timeout (>10s) | No context injection; agent proceeds without domain routing | Logged as warning (`hooks/runner.py:55-56`) |
| post-chat hook failure | Event/lesson not written for this task | Logged; next tasks still write independently |
| reflex.js finds no matching domains | Outputs generic "no domain matched" message with skills directory hint | Agent proceeds without domain-specific context |
| evolve.js conflict detection | Candidate blocked from promotion | Logged; stays in CANDIDATES for human review |
| Dream budget exceeded | Session stops with `budget_exceeded` status | Eval records actual spend; no financial loss beyond cap |
| Dream path escape attempt | Tool call rejected with policy violation message | `DreamToolPolicy` blocks and sets `stop_reason` |

## Verification

```bash
# Dream subsystem
uv run pytest tests/test_dream_tool_policy.py tests/test_dream_tool_registry.py tests/test_dream_runner.py tests/test_dream_output.py -q

# Instinct subsystem
uv run pytest tests/test_instinct_commands.py tests/test_instinct_learning.py tests/test_instinct_bridge.py tests/test_instinct_tools.py -q

# Hooks
uv run pytest tests/test_hooks.py -q

# Instinct scripts: node instinct/scripts/reflect.js --help
# Evolve dry run: node instinct/scripts/evolve.js --dry-run
```

## Related Files

- `src/agent/loop_core.py` -- `run_tool_loop` used by DreamRunner
- `hooks/pre-chat` -- Shell wrapper that invokes `reflex.js`
- `hooks/post-chat` -- Shell wrapper that invokes `reflect.js`
- `instinct/domains/` -- Domain definition files read by reflex.js
- `instinct/core.md` -- Core instinct rules injected into system prompt
- `src/dream/output/` -- Artifact tracker, eval writer, narrative writer, review writer, diary publisher
