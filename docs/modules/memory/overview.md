# Memory

> Module documentation is not a requirements document or a changelog.

## Purpose

- **Owns**: Long-term memory (MEMORY.md), searchable history log (HISTORY.md), structured knowledge graph (KG via SQLite), FTS indexes (both markdown-based and KG-based), three-tier memory pipeline (immediate queue -> SQLite short-term -> file-based long-term), LLM-driven consolidation, memory recall/retrieval for prompt injection, recall telemetry (`recall_journal.jsonl`), recall maintenance (`recall_targets.json` fold + KG metadata ingestion), unified memory event log (`memory_events.jsonl`), pre-compaction durable-fact flush, scope resolution (global vs per-group), remember directives, GC/time decay, and dashboard telemetry writes.
- **Does Not Own**: Session lifecycle or conversation transcript persistence (`src/session/`), LLM provider implementation (`src/providers/`), agent tool registration (`src/agent/tools/`), or channel-level message formatting.

## Source Scope

```
src/memory/
  store.py             # MemoryStore — MEMORY.md / HISTORY.md markdown I/O
  structured.py        # StructuredMemoryStore — KG-backed task/rule/research
  knowledge_graph.py   # KnowledgeGraph — SQLite node/edge store
  knowledge_search.py  # KnowledgeSearch — FTS5 + hybrid vector search over KG
  index.py             # MemoryIndex — FTS5 over MEMORY.md / HISTORY.md
  tiers.py             # MemoryTierManager — immediate queue -> SQLite flush
  sql.py               # ShortTermMemoryStore — SQLite buffer/audit layer
  consolidation.py     # MemoryConsolidationService — LLM-driven archival
  recall.py            # MemoryRecallService — unified read-side facade
  recall_journal.py    # append-only recall telemetry writer
  recall_maintenance.py # journal fold + KG rule recall ingestion
  recall_ranking.py    # 6-component recall ranking (rank-only, no side effects)
  kg_gc.py             # KG node GC — delete long-superseded nodes + VACUUM
  memory_events.py     # unified memory event log (fold / ingest / flush)
  scope.py             # MemoryScopeResolver — session -> workspace mapping
  embeddings.py        # EmbeddingProvider — optional vector support
  token_budget.py      # Token budget helpers

src/store/
  database.py          # Database — async SQLite wrapper (shared infra)
  dashboard_writer.py  # DashboardWriter — telemetry SQLite for web UI
  event_store.py       # EventStore — task lifecycle event log
```

Adjacent but not owned: `src/session/manager.py` (consolidation reads `Session.messages`), `src/agent/tools/` (tool implementations call into recall/structured stores).

## Entry Points

| File | Start reading at | Role |
|------|-----------------|------|
| `src/memory/recall.py:38` | `MemoryRecallService` | Primary read-side facade for prompt injection |
| `src/memory/consolidation.py:59` | `MemoryConsolidationService` | Orchestrates LLM-driven archival |
| `src/memory/store.py:33` | `MemoryStore` | Markdown backend for MEMORY.md / HISTORY.md |
| `src/memory/structured.py:63` | `StructuredMemoryStore` | KG-backed structured knowledge |
| `src/memory/tiers.py:36` | `MemoryTierManager` | Three-tier pipeline management |
| `src/store/database.py:43` | `Database` | Shared async SQLite wrapper |
| `src/store/dashboard_writer.py:89` | `DashboardWriter` | Dashboard telemetry writes |

## Architecture

The memory system has four conceptual layers:

```
                     MemoryRecallService (read-side facade)
                    /                    \
          MemoryStore                StructuredMemoryStore
      (MEMORY.md/HISTORY.md)            (KnowledgeGraph)
              |                         /           \
         MemoryIndex              KnowledgeSearch   EmbeddingProvider
        (FTS5 over md)          (FTS5+vec over KG)    (optional)
              |                         |
           Database                  Database
        (theos.db)              (kg.db)

  MemoryConsolidationService (write-side orchestration)
         reads: Session.messages
         writes: MemoryStore (MEMORY.md, HISTORY.md)
         marks: ShortTermMemoryStore (bookkeeping)
         syncs: MemoryIndex (FTS)

  MemoryTierManager (pipeline)
    immediate queue -> ShortTermMemoryStore (SQLite) -> consolidation -> MemoryStore
```

**Two separate SQLite databases**: `theos.db` hosts `memory_short_term`, `task_events`, and `memory_fts`. `kg.db` hosts `kg_nodes`, `kg_edges`, and `kg_fts`. This separation avoids `row_factory` side effects (`structured.py:96-97`).

**MemoryScopeResolver** maps `session_key` to a workspace path, supporting global memory (single workspace) or per-group memory (per-`channel:chat_id` directory under `groups/`) (`scope.py:14-57`).

### Runtime Defaults

The core runtime keeps markdown memory on and structured systems off:

| Config path | Default | Effect |
|---|---|---|
| `memory.enabled` | `true` | MEMORY.md/HISTORY.md recall can be injected |
| `memory.search.enabled` | `true` | `memory_search` is available in the minimal profile |
| `memory.compaction.enabled` | `true` | long sessions can be summarized |
| `memory.flush.enabled` | `false` | pre-compaction durable-fact flush is opt-in |
| `memory.gc.enabled` | `false` | markdown GC/time decay is opt-in |
| `memory.telemetry.recallEnabled` | `false` | recall journal/maintenance data is opt-in |
| `knowledgeGraph.enabled` | `false` | structured KG memory and KG tools are opt-in |
| `memory.search.hybrid.enabled` | `false` | vector/hybrid search is opt-in |

This preserves TheOS's long-term markdown memory as a core feature while
keeping KG/vector/maintenance pipelines out of the default runtime.

## Data Flow

**Consolidation (old messages -> long-term memory):**

1. Trigger: message count exceeds `memory_window` threshold
2. `MemoryConsolidationService.consolidate()` selects unconsolidated messages from `Session.messages` (`consolidation.py:118-133`)
3. Builds prompt with current MEMORY.md content + recent HISTORY.md + conversation excerpt (`consolidation.py:134-164`)
4. Calls LLM with `save_memory` tool, expecting `history_entry` + `memory_update` (`consolidation.py:31-56`)
5. On success: writes to MEMORY.md/HISTORY.md via `MemoryStore`, advances `session.last_consolidated`, marks SQLite rows, syncs FTS index (`consolidation.py:259-302`)
6. On LLM failure (no tool call or bad args): falls back to deterministic archive entry (`consolidation.py:196-209`, `store.py:100-119`)

**Recall (memory -> prompt context):**

1. `MemoryRecallService.get_memory_context()` reads MEMORY.md via `MemoryStore` (`recall.py:62-104`)
2. In `full` mode: returns entire content with freshness annotations (`recall.py:96-97`)
3. In `retrieval` mode: scores sections by keyword overlap with query, selects by token budget, optionally falls back to full (`recall.py:145-204`)
4. Structured recall: when `knowledgeGraph.enabled=true`,
   `build_structured_recall()` searches KG via `StructuredMemoryStore.search()`
   and formats top results (`recall.py:210-252`)

**Recall telemetry and maintenance:**

1. When recall telemetry is enabled, `memory_search`, `structured_memory_search`, and `domain_rule_get` append best-effort recall telemetry to `memory/instinct/recall_journal.jsonl` (`recall_journal.py:1-77`)
2. The journal is append-only and records one line per recalled result, including `target_kind`, `target_id`, `query_hash`, `day`, `score`, and (when content is supplied) a normalized `claim_hash` for grounding
3. During the 6-hour maintenance window, `fold_recall_journal()` folds new journal tail entries into `recall_targets.json` plus a checkpoint file and emits `memory.recall.folded` into the unified event log (`recall_maintenance.py:50-189`)
4. `recall_targets.json` entries carry both v1 signals (`recall_count`, `distinct_query_hashes`, `distinct_days`, `last_recalled_at`, `max_score`) and v2.1 aggregates (`total_score`, `daily_count`, `daily_counts`) for downstream ranking
5. `ingest_recall_to_kg()` then reads that derived snapshot, writes `recall_count`, `last_recalled_at`, `distinct_recall_queries`, `distinct_recall_days` into KG rule metadata, and emits `memory.recall.ingested` (`recall_maintenance.py:191-241`)
6. Only rule-like targets are ingested into KG metadata; markdown section recalls remain journal-only
7. After recall ingestion, `gc_superseded_nodes()` deletes KG nodes whose `superseded_by` was set more than 30 days ago, removes any edges referencing them, and runs `VACUUM` on `kg.db` to reclaim space (`kg_gc.py:1-55`)

**Pre-compaction flush (about-to-be-compacted history -> MEMORY.md):**

1. `MemoryHandler.maybe_compact()` may schedule `_schedule_pre_compaction_flush()` before summarizing old messages (`loop_memory.py:698-719`)
2. This path is enabled only for pre-turn compaction, where persisted session history is available with the same coordinate system as `_extract_cursor`
3. The flush reuses `extract_durable_facts()` + `merge_extracted_facts()` to best-effort persist durable facts into MEMORY.md before compaction (`loop_memory.py:757-810`, `extract.py:81-185`)
4. Flush writes a small event record to `memory/instinct/events/*-flush.json` and also emits `memory.flush.completed` into the unified event log; failure is silent and never blocks compaction (`loop_memory.py:820-847`)

**Structured knowledge recording:**

1. `StructuredMemoryStore.record_task()` creates a `task` node in KG with metadata (`structured.py:148-299`)
2. Extracts rules from response text, upserts as `rule` nodes with fingerprint-based dedup (`structured.py:432-498`)
3. Optionally creates `research` node for research-domain tasks (`structured.py:259-282`)
4. Links nodes via directed edges: `task --derived--> rule`, `task --produced--> research` (`structured.py:251, 282`)
5. Supersedes related older tasks by topic overlap (source refs, artifacts, keyword Jaccard) (`structured.py:502-580`)
6. Returns `RecordTaskResult` with `remember_directive` and `history_entry` for caller to persist to markdown (`structured.py:44-56`)

**Three-tier pipeline:**

1. `MemoryTierManager.buffer_entry()` appends to in-memory queue per session (`tiers.py:119-126`)
2. When queue reaches threshold, schedules async flush to `ShortTermMemoryStore` (SQLite) (`tiers.py:112-117`)
3. Consolidation reads from `Session.messages` (not SQLite); SQLite marking is bookkeeping only (`tiers.py:1-19`)

## State & Persistence

| Component | Storage | Lifetime |
|-----------|---------|----------|
| MEMORY.md | File (workspace/memory/) | Persistent; atomic writes via tmp+rename |
| HISTORY.md | File (workspace/memory/) | Persistent; append-only |
| MemoryIndex (FTS) | `theos.db` table `memory_fts` | Derived; rebuildable from markdown |
| KnowledgeGraph | `kg.db` tables `kg_nodes`, `kg_edges` | Persistent; primary structured store |
| KG FTS index | `kg.db` table `kg_fts` | Derived; auto-synced via triggers |
| Embeddings | `kg_nodes.embedding` column (BLOB) | Optional; background-computed |
| Short-term buffer | `theos.db` table `memory_short_term` | Buffer/audit; not a retrieval source |
| Immediate queue | In-memory `dict[str, list]` | Process lifetime; lost on crash |
| Recall journal | `memory/instinct/recall_journal.jsonl` | Append-only telemetry log; source of truth for recall signals |
| Recall targets | `memory/instinct/recall_targets.json` | Derived maintenance snapshot; rebuildable from journal |
| Recall checkpoint | `memory/instinct/recall_targets.checkpoint.json` | Derived maintenance cursor; rebuildable |
| Pre-flush events | `memory/instinct/events/*-flush.json` | Best-effort observability records |
| Unified memory events | `memory/instinct/memory_events.jsonl` | Append-only JSONL; fold/ingest/flush events for downstream observability |
| DashboardWriter | Separate SQLite file | Telemetry; errors never block agent |
| EventStore | `theos.db` table `task_events` | Append-only event sourcing log |

**KG schema** (`knowledge_graph.py:23-64`): `kg_nodes` has `id`, `node_type`, `title`, `content`, `tags`, `domains`, `importance`, timestamps, `superseded_by`, `metadata` (JSON), `embedding` (BLOB). `kg_edges` has `from_id`, `to_id`, `relation`. Both use WAL mode.

## Invariants

1. **Markdown is the memory truth source**: MEMORY.md and HISTORY.md are the authoritative long-term memory. FTS indexes and SQLite tiers are derived/buffer layers that can be rebuilt (`store.py:1-11`, `tiers.py:1-19`).
2. **Consolidation reads Session.messages, not SQLite**: The short-term SQLite tier is a buffer/audit layer. `mark_consolidated()` is bookkeeping, not the consolidation input (`consolidation.py:5-8`, `sql.py:1-13`).
3. **SQLite is not a retrieval source**: `MemoryRecallService` reads from markdown and structured memory. If SQLite data is ever surfaced for recall, it must go through a normalization boundary (`recall.py:14-19`, `sql.py:9-12`).
4. **KG uses separate database**: `kg.db` is distinct from `theos.db` to avoid `row_factory` conflicts (`structured.py:96-97`).
5. **Dashboard schema owned by Python**: `DashboardWriter` in `src/store/dashboard_writer.py` owns the dashboard SQLite schema. UI reads are read-only (`dashboard_writer.py:1-4`).
6. **Atomic markdown writes**: `MemoryStore.write_long_term()` uses tmp file + rename (`store.py:47-49`).
7. **Rule dedup by fingerprint**: Rules are keyed by `sha1(normalized_text|domains|primary)[:16]`, so identical rules across tasks merge rather than duplicate (`structured.py:443-447`).
8. **Superseded nodes excluded from search**: Both FTS and vector search filter `superseded_by IS NULL` (`knowledge_search.py:131`, `knowledge_search.py:239`).
9. **Recall journal is the recall signal truth source**: `recall_targets.json` and its checkpoint are derived caches owned by maintenance. If they drift or are deleted, they must be rebuilt from `recall_journal.jsonl` (`recall_maintenance.py:24-117`).
10. **Pre-flush is pre-turn only**: `_extract_cursor` tracks absolute offsets in persisted `session.messages`, so pre-compaction flush must not run on the in-loop temporary message list (`loop_memory.py:759-769`).

## Extension Points

- **New KG node types**: Add a new `node_type` string to `KnowledgeGraph.add_node()`. FTS triggers auto-index new nodes. Update `compute_importance()` half-life map in `knowledge_search.py:388-390` for decay tuning.
- **New edge relations**: Call `KnowledgeGraph.add_edge()` with any relation string. `find_related()` / `find_related_inbound()` return all relation types (`knowledge_graph.py:293-319`).
- **Custom retrieval mode**: Extend `MemoryRecallService.get_memory_context()` with new `injection.mode` values. Current modes: `full`, `retrieval` (`recall.py:96-104`).
- **Embedding providers**: Implement `EmbeddingProvider` interface for new vector backends. Hybrid search auto-activates when a provider is configured (`structured.py:113-121`).
- **Memory GC policy**: `MemoryStore.gc()` accepts `max_age_days` and `max_sections`. Pinned sections (`<!-- pinned -->`) survive GC (`store.py:208-256`).
- **Dashboard event callback**: `DashboardWriter.set_event_callback()` registers an async callback invoked after each event INSERT, enabling real-time push to WebSocket clients (`dashboard_writer.py:111-113`).

## Failure Modes

- **LLM consolidation failure**: Retries once. On persistent failure, falls back to deterministic archive (head of each message) (`consolidation.py:167-209`, `store.py:100-119`).
- **KG database connection failure**: `ensure_kg()` is lazy; failure logged, operations that require KG raise (`structured.py:78-121`).
- **FTS index corruption**: Both `MemoryIndex.sync_all()` and `KnowledgeSearch.rebuild_fts()` can rebuild from source tables (`index.py:90-93`, `knowledge_search.py:95-104`).
- **Embedding failure**: Fire-and-forget; logged at debug level, search degrades to FTS-only (`structured.py:284-291`, `knowledge_search.py:196-198`).
- **Dashboard write failure**: All `DashboardWriter` writes are wrapped in try/except; errors are logged but never propagate to the agent (`dashboard_writer.py:117-124`).
- **Short-term flush failure**: `MemoryTierManager.flush_immediate()` catches exceptions and logs warning. Unflushed entries remain in the in-memory queue for the next attempt (`tiers.py:103`).
- **Legacy JSON migration**: Runs once when KG is empty and legacy JSON exists. Backup created at `structured_backup/`. Migration errors per-file are caught and skipped (`structured.py:692-843`).
- **Recall maintenance drift or corruption**: Missing/corrupt `recall_targets.json` or checkpoint triggers a rebuild from `recall_journal.jsonl`; journal is the only required persistent input (`recall_maintenance.py:37-60`).
- **Pre-compaction flush failure**: Extraction timeout, provider error, or merge failure are caught and logged at debug level. Compaction proceeds unchanged (`loop_memory.py:788-810`).

## Verification

```bash
# Store backends
uv run pytest tests/test_memory_store.py tests/test_memory_store_sql.py -q

# Tiers, context, recall
uv run pytest tests/test_memory_tiers.py tests/test_memory_context.py tests/test_memory_recall_service.py -q

# Consolidation
uv run pytest tests/test_consolidation_service.py tests/test_consolidate_offset.py tests/test_cross_session_consolidation.py tests/test_extract_memories.py -q

# Knowledge graph and search
uv run pytest tests/test_knowledge_graph.py tests/test_knowledge_search.py -q

# Structured memory
uv run pytest tests/test_structured_memory.py tests/test_structured_memory_tools.py -q

# Recall telemetry + maintenance (incl. unified event log)
uv run pytest tests/test_recall_journal.py tests/test_recall_maintenance.py tests/test_memory_sprint2.py -q

# Pre-compaction flush
uv run pytest tests/test_pre_compaction_flush.py -q

# Embeddings, token budget, rule cleanup, response cache, event store
uv run pytest tests/test_embeddings.py tests/test_token_budget.py tests/test_rule_cleanup.py tests/test_response_cache.py tests/test_event_store.py -q

# Full integration
uv run pytest tests/test_memory_full_integration.py -q
```

## Related Files

- `src/memory/store.py` -- markdown backend (MEMORY.md / HISTORY.md)
- `src/memory/structured.py` -- KG-backed structured knowledge store
- `src/memory/knowledge_graph.py` -- SQLite node/edge store
- `src/memory/knowledge_search.py` -- FTS5 + hybrid vector search
- `src/memory/index.py` -- FTS5 index over markdown files
- `src/memory/tiers.py` -- three-tier pipeline manager
- `src/memory/sql.py` -- short-term SQLite buffer
- `src/memory/consolidation.py` -- LLM-driven consolidation service
- `src/memory/recall.py` -- unified retrieval facade
- `src/memory/recall_journal.py` -- append-only recall telemetry log
- `src/memory/recall_maintenance.py` -- journal fold + KG recall ingestion
- `src/memory/recall_ranking.py` -- rank-only 6-component recall candidate scoring
- `src/memory/memory_events.py` -- unified memory event log writer
- `src/memory/scope.py` -- scope resolution (global vs per-group)
- `src/store/database.py` -- shared async SQLite wrapper
- `src/store/dashboard_writer.py` -- dashboard telemetry writer
- `src/store/event_store.py` -- task event sourcing log
- `docs/modules/sessions-messaging/overview.md` -- sessions module (consolidation reads `Session.messages`)
