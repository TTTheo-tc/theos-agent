# Agent Core

> The engine that turns an inbound message into an LLM-driven tool-loop
> execution and routes the result back out. Everything between "bus receives
> a message" and "bus publishes a response."

## Purpose

- **Owns**: The message-to-response pipeline: session init, context assembly,
  LLM inference, tool dispatch, tool-call iteration, subagent delegation,
  safety scanning, response finalization.
- **Does Not Own**: Message transport (owned by `src/bus/`), session persistence
  format (owned by `src/session/`), memory retrieval and consolidation logic
  (owned by `src/memory/`), provider HTTP calls (owned by `src/providers/`),
  channel adapters (owned by `src/channels/`).

## Source Scope

| Directory / file | Role |
|---|---|
| `src/agent/loop.py` | `AgentLoop` -- top-level wiring, lifecycle, slash commands |
| `src/agent/loop_core.py` | `run_tool_loop()` -- stateless LLM-call + tool-dispatch engine |
| `src/agent/loop_detector.py` | `LoopDetector` -- infinite-loop and denial detection |
| `src/agent/loop_context.py` | `TurnContextAssembler` -- per-turn context routing |
| `src/agent/loop_memory.py` | `MemoryHandler` -- consolidation, compaction, recall, pre-compaction flush |
| `src/agent/loop_finalize.py` | `TurnFinalizer` -- post-inference hooks, safety, persistence |
| `src/agent/loop_genver.py` | `GenVerHandler` -- Generator-Verifier execution path |
| `src/agent/context.py` | `ContextBuilder` -- system prompt assembly |
| `src/agent/tools/` | Tool ABC, registry, concrete tool implementations |
| `src/agent/delegation/` | `SubagentExecutor`, task tree types, runtime role adapter |
| `src/agent/subagent.py` | `SubagentManager` -- facade over `SubagentExecutor` |
| `src/agent/tool_sets.py` | `register_standard_tools()` -- centralized registration |
| `src/agent/slash_commands.py` | `/agent`, `/model`, `/ui` command handlers |
| `src/orchestrator/` | `TurnLifecycle`, `ExecutionPolicy`, `TaskRecord`/`TaskState` |

Adjacent but out of scope: `src/bus/`, `src/session/`, `src/memory/`, `src/safety/`,
`src/providers/`, `src/genver/`.

## Entry Points

1. **`AgentLoop.run()`** (`src/agent/loop.py:564`) -- main event loop. Consumes
   `InboundMessage` from the bus, routes slash commands, dispatches everything
   else through `PerGroupDispatcher`.
2. **`AgentLoop._process_message()`** (`src/agent/loop.py:1213`) -- processes a
   single turn end-to-end: session init, context build, inference, finalization.
3. **`run_tool_loop()`** (`src/agent/loop_core.py:155`) -- the reusable
   LLM-iterate engine used by both the root agent and subagents.
4. **`register_standard_tools()`** (`src/agent/tool_sets.py:42`) -- single
   function that populates a `ToolRegistry` for any agent mode.

## Architecture

The module is decomposed into composable handlers. `AgentLoop.__init__`
constructs and wires them (`src/agent/loop.py:86`):

```
AgentLoop
  |-- ContextBuilder (context.py)          prompt assembly
  |-- TurnContextAssembler (loop_context)  per-turn routing, skills, ephemeral context
  |-- MemoryHandler (loop_memory)          recall, compaction, consolidation
  |-- GenVerHandler (loop_genver)          generator-verifier execution path
  |-- TurnFinalizer (loop_finalize)        post-chat hooks, safety, session save
  |-- ToolRegistry (tools/registry.py)     tool lookup, deferred activation, policy gates
  |-- SubagentManager (subagent.py)        delegation facade
  |   `-- SubagentExecutor (delegation/executor.py)   task tree engine
  |-- TurnLifecycle (orchestrator/turn_lifecycle.py)   retry + policy hooks
  |   `-- OrchestratorPolicy (orchestrator/policies.py)
  `-- PerGroupDispatcher (session/group_dispatcher.py)  per-session serialization
```

**Why this decomposition:** `AgentLoop` was originally monolithic. The handlers
(`MemoryHandler`, `GenVerHandler`, `TurnFinalizer`, `TurnContextAssembler`)
were extracted so each concern can be tested and evolved independently while
`AgentLoop` remains a thin wiring layer.

**run_tool_loop is stateless by design.** It receives all dependencies via
parameters and callbacks (`src/agent/loop_core.py:1-7`). This lets both
`AgentLoop._run_agent_loop` and `SubagentExecutor._run_subagent_loop` share
the same iteration engine with different tool registries and message helpers.

### Tool System

The `Tool` ABC (`src/agent/tools/base.py:7`) defines the contract:

- `name`, `description`, `parameters` (JSON Schema) -- identity
- `execute(**kwargs) -> str` -- async execution
- `risk_level`, `owner_only`, `requires_context` -- policy flags
- `parallel_safe`, `dedupe_within_turn` -- concurrency hints

`ToolRegistry` (`src/agent/tools/registry.py:16`) manages two pools:

- **Active pool** (`_tools`): definitions sent to the LLM on every call.
- **Deferred pool** (`_deferred`): registered but hidden until explicitly
  activated via `tool_search` or a direct call (`registry.py:173-175`).

The split is governed by `ALWAYS_ON_TOOLS` in `tool_profiles.py`.
The default visible tool surface is deliberately small: `read_file`,
`list_dir`, `glob`, `grep`, `memory_search`, and `tool_search`. Everything
else is registered as allowed by `tools.profile`, then kept deferred until
`tool_search` or automatic activation brings it into the active pool.

`register_standard_tools()` (`src/agent/tool_sets.py:42`) is the single
registration site. It combines feature gates, optional dependency availability,
agent mode, role restrictions, and `tools.profile`.

Advanced agent modes are opt-in. The default config keeps
`agents.teamEnabled=false`, `agents.genverEnabled=false`, and
`agents.mode="single"`; `/agent team` and `/agent genver` require the matching
flag unless the config already explicitly starts in that mode.

| Mode | Purpose | Key constraint |
|---|---|---|
| `single` | Root agent | Tool surface governed by `tools.profile`; default is `minimal` |
| `team` | Orchestrator root | Write tools restricted (`DocWriteFileTool`, `SafeExecTool`) |
| `subagent` | Delegated worker | Filtered by role's `allowed_tools` |
| `verifier` | GenVer verification | Read-only fs + bash only |

Named tool profiles:

| Profile | Runtime shape |
|---|---|
| `minimal` | Six always-on tools only |
| `coding` | FS write/edit, shell/process, web, memory, task tools, read-only Feishu, selected analysis tools |
| `messaging` | Memory, discovery, basic web, Feishu knowledge tools, message send |
| `readonly` | Read-only local, web, and browser exploration |
| `full` | No profile restriction; feature gates and policy still apply |

Tool groups (`tool_profiles.py:13`) provide symbolic aliases like `group:fs`
that expand to concrete tool names, used in role `allowed_tools` configs.

### Delegation

`SubagentManager` (`src/agent/subagent.py:65`) is the public facade.
Internally it delegates to `SubagentExecutor` (`src/agent/delegation/executor.py:38`),
which owns the task tree:

- **Records** (`_records`): `SubagentTaskRecord` dataclasses tracking task metadata
- **Tasks** (`_tasks`): `asyncio.Task` handles for running coroutines
- **Results** (`_results`): completed `SubagentResult` objects
- **Consumed** (`_consumed`): set of result IDs already read by the parent

Policy enforcement at spawn time (`executor.py:99-126`): `max_depth`,
`max_concurrent`, `max_children_per_agent`. Session-level freeze
(`_frozen_sessions`) prevents new spawns during session cancellation.

Each subagent gets its own `ToolRegistry` built via `register_standard_tools`
with `mode="subagent"` and `allowed_tools` from the role config
(`executor.py:474-484`). It runs its own `run_tool_loop` call
(`executor.py:517-526`).

### Plan Mode

`ToolRegistry` supports a read-only `plan_mode` (`registry.py:51-64`).
When active, `get_definitions()` returns only tools in `PLAN_MODE_TOOLS`
(a frozenset of read-only tools, `registry.py:27-33`). The agent toggles
this via `enter_plan_mode` / `exit_plan_mode` tools.

## Data Flow

A single turn (non-GenVer) flows through:

1. **Bus** delivers `InboundMessage` to `AgentLoop.run()`.
2. Slash commands (`/stop`, `/agent`, `/model`) are handled inline.
   Everything else goes to `PerGroupDispatcher.dispatch()` which serializes
   per session key.
3. `TurnLifecycle.handle_message()` creates a `TurnRecord`, runs policy
   `before_execute` hooks, then calls `_process_message`.
4. `_process_message` (`loop.py:1213`):
   - Inits session, runs safety inbound scan
   - Builds context via `ContextBuilder.build_messages()` (system prompt
     with identity + bootstrap + memory + skills, plus conversation history)
   - The `# Memory Tools` section now includes a mandatory recall policy:
     historical questions not covered by injected memory must search before answering (`context.py:125-138`)
   - Calls `_run_inference` -> `_run_agent_loop` -> `run_tool_loop`
5. `run_tool_loop` (`loop_core.py:155`) iterates:
   - Call `provider.chat()` (or `provider.chat_stream()` when streaming)
   - If response has tool calls: dispatch each via `ToolRegistry.execute()`,
     append results, check for loops/denials, run in-loop compaction, iterate
   - If response is final text: break and return
6. Response flows back up through `TurnFinalizer` (safety scan, hooks,
   session persist) and is published to the bus as `OutboundMessage`.

### Tool Execution Order

Within a single iteration, tool calls are either:

- **Parallel**: when all calls in the batch have `parallel_safe=True`
  (`loop_core.py:358-401`). Uses `asyncio.gather()`.
- **Sequential**: otherwise, tools run one at a time with a 10-second
  progress notification for long-running tools (`loop_core.py:405-462`).

Streaming preflight: when using `chat_stream()`, tools marked
`parallel_safe` are started as fire-and-forget tasks as soon as each
`tool_ready` event arrives (`loop_core.py:247-253`), overlapping tool
execution with remaining stream data.

Deduplication: tools with `dedupe_within_turn=True` skip re-execution
when called with identical arguments in the same assistant turn
(`loop_core.py:336-354`).

### Memory-Aware Context and Compaction

- `ContextBuilder` injects a stronger memory-use contract when memory tools are available: for prior work / past decisions / preferences / commitments / todos, the model must search first if injected memory does not already cover the topic (`context.py:125-138`).
- Pre-turn compaction passes persisted session history into `MemoryHandler.maybe_compact()` so memory can schedule a best-effort pre-compaction flush before summarizing older messages (`loop.py:1533-1541`, `loop_memory.py:698-719`).
- In-loop compaction does **not** run pre-flush. `_extract_cursor` is indexed against persisted `session.messages`, so it cannot be mixed with the ephemeral in-loop message list (`loop_memory.py:757-769`).
- Pre-compaction flush is memory-owned, but the agent core owns the wiring point: it is the caller that decides whether compaction is operating on persisted history or temporary loop state.

## State & Persistence

| State | Location | Lifetime |
|---|---|---|
| `AgentLoop` config fields | In-memory | Process lifetime |
| Session history | `SessionManager` (JSONL files) | Persisted across restarts |
| Subagent task records | `SubagentExecutor._records` (in-memory) | Process lifetime |
| Subagent checkpoints | `SubagentStore` (JSONL) | Persisted |
| Turn audit trail | `TurnStore` (JSONL) | Persisted |
| Tool registry state | `ToolRegistry._tools`, `_deferred`, `_plan_mode` | Process lifetime |
| Loop detection window | `LoopDetector._history` (10-element list) | Per tool-loop invocation |
| Orchestrator task records | `OrchestratorPolicy._active` | Per-turn |

On startup, `TurnStore` and `SubagentStore` mark any in-flight records as
interrupted (`loop.py:159`, `subagent.py:103-105`).

## Invariants

1. **Tool results always follow their tool_calls message.** `run_tool_loop`
   appends all tool results in the same iteration before calling
   `maybe_compact` (`loop_core.py:547-551`). Compaction never splits a
   tool_calls/results pair.
2. **ToolRegistry.execute() never raises to the caller.** All exceptions are
   caught and returned as `"Error: ..."` strings (`registry.py:274-276`).
3. **Safety scanning is mandatory.** Every tool result passes through
   `_sanitize_tool_result` (injection scan + credential scrub + size truncation)
   before being appended to messages (`loop_core.py:98-106`).
4. **Per-group serialization.** `PerGroupDispatcher` ensures at most one turn
   executes per session key. Different sessions run concurrently.
5. **Subagent depth is bounded.** `SubagentExecutor.spawn()` rejects when
   `depth >= policy.max_depth` (`executor.py:99-104`).
6. **Owner-only tools are enforced at the registry level**, not at individual
   tool implementations (`registry.py:190-198`).
7. **Plan mode filtering is enforced in both `get_definitions()` and
   `execute()`** (`registry.py:161-186`), so the LLM cannot call blocked
   tools even if it hallucinates their names.
8. **Pre-compaction flush only runs on persisted history.** Agent core must not enable it for the transient in-loop message list; the two coordinate systems are intentionally kept separate (`loop.py:1537-1541`, `loop_memory.py:757-769`).

## Extension Points

- **Add a new tool**: Subclass `Tool` in `src/agent/tools/`, register it in
  `register_standard_tools()` in `src/agent/tool_sets.py`. Declare it in
  `ALWAYS_ON_TOOLS` to make it always visible, or omit for deferred activation.
  Add to appropriate `TOOL_GROUPS` if it fits a category.
- **Add a new agent role**: Define in config `agents.roles.<name>` or create a
  `.md` file in `<workspace>/agents/`. Role is loaded at startup and on
  `reload_roles()`.
- **Add an execution policy**: Subclass `ExecutionPolicy` in
  `src/orchestrator/policies.py`, append to `TurnLifecycle.policies` in
  `AgentLoop.__init__`.
- **Add a tool profile**: Add to the `PROFILES` dict in `tool_profiles.py`.
- **MCP server integration**: Tools from MCP servers are registered via
  `MCPManager.connect()` (`loop.py:357-359`), which dynamically adds them
  to the existing `ToolRegistry`.

Do not: bypass `register_standard_tools()` for tool registration, or
register tools directly on `AgentLoop.tools` outside of `_register_default_tools`.

## Failure Modes

| Failure | Handling |
|---|---|
| LLM returns `invalid_request_error` | `_run_inference_inner` retries with clean context (no history) (`loop.py:1165-1205`) |
| Provider auth error | `ProviderAuthError` caught in `_run_inference`, error returned to user without saving to session (`loop.py:1121-1131`) |
| Tool execution exception | Caught in `ToolRegistry.execute()`, returned as error string with `"[Analyze the error...]"` hint (`registry.py:274-276`) |
| Infinite tool-call loop | `LoopDetector` injects a break message after 3 identical calls in a 10-call window (`loop_core.py:491-504`) |
| Repeated autonomy/approval denials | `LoopDetector.check_denials()` redirects after 3 denials of the same tool (`loop_core.py:507-520`) |
| Max iterations exceeded | `run_tool_loop` returns a user-facing message (`loop_core.py:567-573`) |
| Subagent timeout | `SubagentExecutor._execute()` wraps the loop in `asyncio.wait_for()`, records `TIMED_OUT` status (`executor.py:337-388`) |
| Oversized tool output | `_truncate_tool_result` preserves head (20KB) + tail (5KB), trims middle (`loop_core.py:83-95`) |
| Session cancellation during spawn | `_frozen_sessions` set prevents new spawns while `cancel_by_session` is draining (`executor.py:260-283`) |
| Pre-compaction flush failure | Scheduled as best-effort background work; failure is logged inside memory and does not block the main tool loop or the compaction summary path (`loop_memory.py:788-810`) |

## Verification

```bash
# Core loop, init, streaming
uv run pytest tests/test_loop_core.py tests/test_agent_loop_init.py tests/test_agent_streaming.py -q

# Tool dispatch, validation, profiles, deferred tools
uv run pytest tests/test_parallel_tool_exec.py tests/test_deferred_tools.py tests/test_tool_validation.py tests/test_tool_profiles.py -q

# Prompt assembly + memory-aware compaction wiring
uv run pytest tests/test_context_prompt_cache.py tests/test_pre_compaction_flush.py -q

# Plan mode, approval, owner-only, loop detection
uv run pytest tests/test_plan_mode.py tests/test_approval_gate.py tests/test_owner_only.py tests/test_loop_detector.py -q

# Agent tools
uv run pytest tests/agent/tools/ -q

# Slash commands and model switching
uv run pytest tests/agent/test_agent_mode_cmd.py tests/agent/test_model_cmd.py -q

# Multi-agent delegation
uv run pytest tests/test_multi_agent.py tests/test_multi_executor.py tests/test_multi_nested.py tests/test_multi_policy.py tests/test_multi_runtime.py tests/test_multi_types.py -q

# Turn lifecycle
uv run pytest tests/test_turn_lifecycle.py -q
```

## Related Files

- `src/bus/queue.py` -- `MessageBus` that feeds `AgentLoop.run()`
- `src/session/manager.py` -- `SessionManager` for history persistence
- `src/session/group_dispatcher.py` -- `PerGroupDispatcher` for concurrency
- `src/memory/store.py` -- `MemoryStore` for file-based memory retrieval
- `src/safety/layer.py` -- `SafetyLayer` for inbound/outbound scanning
- `src/providers/base.py` -- `LLMProvider` interface
- `src/config/schema.py` -- `Config`, `AgentRoleConfig`, `SubagentPolicyConfig`
- `docs/modules/sessions-messaging/` -- session and bus module docs
- `docs/modules/security-safety/` -- safety layer module docs
