# Sessions & Messaging

> Module documentation is not a requirements document or a changelog.

## Purpose

- **Owns**: Async message bus (inbound/outbound queues with backpressure), conversation session lifecycle (JSONL persistence, LRU cache, append-only writes, compaction), per-group concurrency dispatch, turn checkpoint storage, subagent checkpoint storage, and session runtime state recovery.
- **Does Not Own**: Message formatting, tool result truncation logic (`src/utils/truncation.py`), memory consolidation (`src/memory/consolidation.py`), LLM inference, channel adapter implementations, or dashboard telemetry.

## Source Scope

```
src/bus/
  events.py          # InboundMessage / OutboundMessage dataclasses
  queue.py           # MessageBus — async queue pair with backpressure

src/session/
  manager.py         # Session + SessionManager — JSONL persistence, LRU cache
  turn_store.py      # TurnStore + TurnCheckpoint — per-turn durable recovery
  subagent_store.py  # SubagentStore + SubagentCheckpoint — background task state
  runtime_state.py   # SessionRuntimeState — combined recovery view
  group_dispatcher.py # PerGroupDispatcher — per-group concurrent workers
```

Adjacent but not owned: `src/agent/` (consumes bus, drives sessions), `src/channels/` (publishes inbound, consumes outbound), `src/memory/` (consolidation reads `Session.messages`).

## Entry Points

| File | Start reading at | Role |
|------|-----------------|------|
| `src/bus/events.py:9` | `InboundMessage` | Defines the canonical inbound/outbound message contract |
| `src/bus/queue.py:12` | `MessageBus` | Async queue pair connecting channels to agent |
| `src/session/manager.py:121` | `SessionManager` | Session CRUD, JSONL I/O, cache management |
| `src/session/group_dispatcher.py:19` | `PerGroupDispatcher` | Per-group message serialization with concurrent groups |
| `src/session/turn_store.py:43` | `TurnStore` | Durable turn lifecycle checkpoints |
| `src/session/runtime_state.py:38` | `build_session_runtime_state()` | Recovery state builder |

## Architecture

```
Channels ──publish_inbound──> MessageBus.inbound ──consume_inbound──> Agent
Agent ──publish_outbound──> MessageBus.outbound ──consume_outbound──> Channels

                            PerGroupDispatcher
                           /        |         \
                     group-A     group-B     group-C   (concurrent workers)
                     [FIFO]      [FIFO]      [FIFO]    (serialized within group)

SessionManager ──────> Session (in-memory + JSONL on disk)
TurnStore ─────────────> turns/*.jsonl (append-only checkpoints)
SubagentStore ─────────> subagents/*.jsonl (append-only checkpoints)
```

**MessageBus** decouples channel adapters from the agent core. It holds two bounded `asyncio.Queue` instances with a drop-oldest backpressure strategy (`queue.py:22-29`).

**PerGroupDispatcher** sits between the bus consumer loop and the agent's `process_fn`. Each group (`channel:chat_id`) gets a dedicated asyncio queue and worker task. Workers self-terminate after 60 seconds of idle (`group_dispatcher.py:33`). This ensures cross-group concurrency while preserving intra-group message ordering.

**SessionManager** manages an LRU cache (max 500 entries) of `Session` objects backed by JSONL files. It uses append-only writes with periodic compaction (threshold: 500 appended lines) to balance I/O cost and file size (`manager.py:129`).

**TurnStore / SubagentStore** are structurally identical append-only JSONL stores, separated by concern: turns track the primary request lifecycle, subagents track background tasks. Both support `mark_interrupted_inflight()` for startup recovery.

## Data Flow

**Inbound message (channel to agent):**

1. Channel adapter creates `InboundMessage` with `channel`, `sender_id`, `chat_id`, `content` (`events.py:9-26`)
2. `MessageBus.publish_inbound()` enqueues it; drops oldest on overflow (`queue.py:19-29`)
3. Bus consumer loop calls `PerGroupDispatcher.dispatch(msg)` (`group_dispatcher.py:49`)
4. Dispatcher routes to per-group queue, spawns worker if needed (`group_dispatcher.py:54-57`)
5. Worker calls `process_fn(msg)`, which uses `SessionManager.get_or_create()` to load/create session

**Session key resolution:** `InboundMessage.session_key` returns `session_key_override` if set, else `"{channel}:{chat_id}"` (`events.py:23-25`). This allows thread-scoped sessions.

**Outbound message (agent to channel):**

1. Agent creates `OutboundMessage` with `channel`, `chat_id`, `content`, and metadata flags (`events.py:28-49`)
2. `MessageBus.publish_outbound()` enqueues it (`queue.py:35-47`)
3. Channel adapter consumes via `consume_outbound()` and delivers based on metadata: `_progress` -> typing/streaming, `_tool_hint` -> status, final -> main response (`events.py:33-41`)

**Session persistence (write path):**

1. `persist_user_message()` appends the accepted user turn with `turn_id`, idempotently (`manager.py:179-205`)
2. `save()` calls `_save_to_disk()` which appends new messages + trailing metadata snapshot (`manager.py:292-331`)
3. When append count reaches `_COMPACT_THRESHOLD` (500), triggers full rewrite via atomic temp file rename (`manager.py:333-342`)
4. Credential scrubbing runs on every persisted message when `scrub_session_history` is enabled (`manager.py:261-290`)

## State & Persistence

| Component | Storage | Lifetime |
|-----------|---------|----------|
| `MessageBus` queues | In-memory `asyncio.Queue` | Process lifetime; messages lost on crash |
| `Session` objects | LRU cache (memory) + `sessions/*.jsonl` (disk) | Survives restarts via JSONL reload |
| `Session.messages` | In-memory list, persisted to JSONL | Append-only; grows until consolidation advances `last_consolidated` |
| `TurnCheckpoint` | `turns/*.jsonl` | Append-only; never truncated |
| `SubagentCheckpoint` | `subagents/*.jsonl` | Append-only; never truncated |
| `PerGroupDispatcher` workers | `asyncio.Task` per group | Self-terminate after 60s idle |

**Recovery on restart:** `TurnStore.mark_interrupted_inflight()` and `SubagentStore.mark_interrupted_inflight()` scan all JSONL files and convert non-terminal latest checkpoints to `interrupted` status (`turn_store.py:103-119`, `subagent_store.py:79-98`). `build_session_runtime_state()` then aggregates this into a `SessionRuntimeState` with `recoverable` flag and `next_step` suggestion (`runtime_state.py:38-69`).

## Invariants

1. **Append-only session messages**: `Session.messages` is only appended to, never edited or reordered. `last_consolidated` is the only cursor that advances. This preserves LLM cache coherence (`manager.py:27-33`).
2. **JSONL atomic writes**: Full rewrites use temp file + rename to prevent corruption (`manager.py:333-340`).
3. **Session key uniqueness**: `channel:chat_id` is the canonical key. The LRU cache uses this key for deduplication (`manager.py:135`).
4. **Intra-group serialization**: `PerGroupDispatcher` guarantees FIFO ordering within a group. Cross-group messages run concurrently (`group_dispatcher.py:1-6`).
5. **Backpressure, not block**: `MessageBus` drops oldest message on overflow rather than blocking the publisher (`queue.py:22-29`).
6. **Credential scrubbing before persist**: When enabled, `tool_calls` arguments and tool result content are scrubbed via `leak_detector.scrub_credentials()` before writing to disk (`manager.py:261-290`).

## Extension Points

- **New channel**: Implement a channel adapter that publishes `InboundMessage` and consumes `OutboundMessage` via `MessageBus`. No changes to session or bus code required.
- **OutboundMessage metadata flags**: Add new flags to `metadata` dict (e.g. `_progress`, `_tool_hint`, `_genver_ask`). Channel adapters check these to decide delivery style (`events.py:32-41`).
- **Session metadata**: `Session.metadata` dict is free-form and persisted alongside messages. Can be used for per-session config without schema changes.
- **Turn checkpoint statuses**: Add new status strings to `TurnStore.record()`. Update `_RECOVERABLE_TURN_STATUSES` in `runtime_state.py:12` if the new status should trigger recovery logic.

## Failure Modes

- **Queue overflow**: `MessageBus` drops the oldest queued message and logs a warning. No exception propagated to publisher (`queue.py:22-29`).
- **JSONL load failure**: Returns `None`, creating a fresh session. Warning logged (`manager.py:258`).
- **Worker crash**: `PerGroupDispatcher` catches non-cancel exceptions per message, logs warning, continues draining the group queue (`group_dispatcher.py:100-106`).
- **Disk full / write failure**: `_save_to_disk` and `_write_full` do not catch I/O exceptions -- these propagate to the caller. No automatic retry.
- **Legacy migration failure**: `SessionManager._load()` attempts to migrate from `~/.theos/sessions/` to workspace-local; failure is logged but does not prevent session creation (`manager.py:210-217`).

## Verification

```bash
uv run pytest tests/test_session_manager.py tests/test_session_resume.py tests/test_sessions_tools.py -q
uv run pytest tests/test_turn_store.py tests/test_subagent_store.py -q
uv run pytest tests/test_compaction.py tests/test_system_message_routing.py -q
```

## Related Files

- `src/bus/events.py` -- message contracts
- `src/bus/queue.py` -- message bus
- `src/session/manager.py` -- session CRUD and JSONL persistence
- `src/session/turn_store.py` -- turn checkpoints
- `src/session/subagent_store.py` -- background task checkpoints
- `src/session/runtime_state.py` -- recovery state aggregation
- `src/session/group_dispatcher.py` -- per-group dispatch
- `src/utils/truncation.py` -- tool call argument truncation (used by `Session.get_history`)
- `src/safety/leak_detector.py` -- credential scrubbing (used by `SessionManager`)
- `docs/modules/memory/overview.md` -- memory module (consolidation reads `Session.messages`)
