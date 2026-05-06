"""MemoryHandler: composition object coordinating memory subsystems.

Owns: memory index lifecycle (ensure_db, FTS sync), tier pipeline coordination
(buffer -> flush -> consolidate), structured memory persistence, consolidation state.
Consolidation orchestration is delegated to MemoryConsolidationService.
Does NOT own: MemoryStore (creates per-workspace, delegates), SessionManager.

Note on the SQLite short-term tier (Tier 2):
    The short-term SQLite store (``MemoryTierManager.short_term_store``) is a
    buffer/audit layer.  Consolidation reads from ``Session.messages``, not
    from SQLite.  Marking SQLite rows as consolidated (via the short-term
    store passed to ``MemoryConsolidationService``) is bookkeeping, not an
    indication that SQLite is the archive source.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from src.memory.consolidation import MemoryConsolidationService
from src.memory.extract import extract_durable_facts, merge_extracted_facts
from src.memory.recall import MemoryRecallService
from src.memory.scope import MemoryScopeResolver

if TYPE_CHECKING:
    from src.memory.index import MemoryIndex
    from src.memory.tiers import MemoryTierManager
    from src.store.database import Database


_MICROCOMPACT_MARKER = "... [tool result microcompacted] ..."
_MICROCOMPACT_TRIM_THRESHOLD = 2_000
_MICROCOMPACT_HEAD_CHARS = 500
_MICROCOMPACT_TAIL_CHARS = 500
_MICROCOMPACT_TRIGGER_RATIO = 0.6
_MICROCOMPACT_KEEP_RECENT_USER_TURNS = 2


@dataclass(frozen=True)
class _CompactionBudget:
    estimated_tokens: int
    context_limit: int
    threshold: int
    micro_threshold: int

    @property
    def should_microcompact(self) -> bool:
        return self.estimated_tokens > self.micro_threshold

    @property
    def should_compact(self) -> bool:
        return self.estimated_tokens > self.threshold


def _find_safe_cut(
    messages: list[dict],
    history_start: int,
    history_end: int,
) -> int | None:
    """Find a cut index that preserves tool_use/tool_result pairs.

    Returns an index such that messages[history_start:cut] can be safely
    summarised without orphaning any tool_result from its tool_use.
    Returns None if no safe cut exists.
    """
    target_mid = history_start + (history_end - history_start) // 2

    # Collect all candidate cut indices (user message boundaries).
    candidates = [
        i for i in range(history_start + 1, history_end) if messages[i].get("role") == "user"
    ]

    if not candidates:
        return None

    # Sort by distance from target_mid (prefer cutting near 50% of history).
    candidates.sort(key=lambda i: abs(i - target_mid))

    for cut in candidates:
        if _cut_preserves_tool_pairs(messages, cut, history_end):
            return cut

    return None


def _cut_preserves_tool_pairs(
    messages: list[dict],
    cut: int,
    history_end: int,
) -> bool:
    """Check that messages[cut:] has no orphaned tool_result messages.

    A tool_result is orphaned if its corresponding tool_use (identified by
    tool_call_id matching a tool_calls[].id) is in messages[:cut] — i.e.
    it was summarised away.
    """
    # Collect tool_call IDs present in the kept range [cut, end_of_messages].
    kept_tool_call_ids: set[str] = set()
    for m in messages[cut:]:
        for tc in m.get("tool_calls") or []:
            kept_tool_call_ids.add(tc.get("id", ""))

    # Check that every tool_result in the kept range has its tool_use.
    for m in messages[cut:]:
        if m.get("role") == "tool":
            tcid = m.get("tool_call_id", "")
            if tcid and tcid not in kept_tool_call_ids:
                return False

    return True


def _build_restoration_context(
    *,
    session_key: str | None,
    max_files: int = 5,
    max_chars_per_file: int = 20_000,
    workspace: Path | None = None,
) -> str | None:
    """Build a context block from recently-read files for post-compact injection.

    Reads from ReadFileTool._read_state for *session_key* only — sessions are
    isolated to prevent cross-session leakage.  Workspace prefix filtering is
    applied as a defense-in-depth check.  Honors the original offset/limit so
    the restored content matches what the model actually saw.  Skips files
    whose mtime has changed since the original read (file was modified
    externally).
    """
    scoped_entries = _scoped_recent_reads(session_key=session_key, workspace=workspace)
    if not scoped_entries:
        return None

    parts: list[str] = []
    # Sort by mtime descending (most recently read first).
    scoped_entries.sort(key=lambda kv: kv[1][0], reverse=True)

    for path_str, state in scoped_entries[:max_files]:
        block = _restored_file_block(path_str, state, max_chars_per_file=max_chars_per_file)
        if block:
            parts.append(block)

    if not parts:
        return None

    from src.agent.context import ContextBuilder

    return (
        ContextBuilder._RUNTIME_CONTEXT_TAG
        + "\n[Recently read files — restored after compaction]\n\n"
        + "\n\n".join(parts)
    )


def _scoped_recent_reads(
    *,
    session_key: str | None,
    workspace: Path | None,
) -> list[tuple[str, tuple[float, int | None, int | None]]]:
    from src.agent.tools.fs_read import ReadFileTool

    session_reads = ReadFileTool.get_recent_reads(session_key)
    if not session_reads:
        return []

    ws_prefix = str(workspace) + "/" if workspace else None
    return [
        (path_str, state)
        for path_str, state in session_reads.items()
        if ws_prefix is None or path_str.startswith(ws_prefix)
    ]


def _restored_file_block(
    path_str: str,
    state: tuple[float, int | None, int | None],
    *,
    max_chars_per_file: int,
) -> str | None:
    try:
        p = Path(path_str)
        if not p.is_file():
            return None
        read_mtime, offset, limit = state
        if _read_is_stale(p, read_mtime):
            return None
        content = _restore_read_slice(p, offset=offset, limit=limit)
        content = _truncate_restored_content(content, max_chars_per_file)
        return f"### {p.name}\n```\n{content}\n```"
    except Exception:
        return None


def _read_is_stale(path: Path, read_mtime: float) -> bool:
    return bool(read_mtime and abs(path.stat().st_mtime - read_mtime) > 0.01)


def _restore_read_slice(path: Path, *, offset: int | None, limit: int | None) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines(keepends=True)
    start = (offset or 1) - 1  # read_file offsets are 1-based.
    end = start + limit if limit else len(lines)
    return "".join(lines[start:end])


def _truncate_restored_content(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    half = max_chars // 2
    trimmed = len(content) - max_chars
    return f"{content[:half]}\n\n... [truncated {trimmed} chars] ...\n\n{content[-half:]}"


def _find_microcompact_preserve_start(
    messages: list[dict],
    *,
    keep_recent_user_turns: int = _MICROCOMPACT_KEEP_RECENT_USER_TURNS,
) -> int | None:
    """Return the index where recent turns begin and should be preserved."""
    seen_users = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") != "user":
            continue
        seen_users += 1
        if seen_users == keep_recent_user_turns:
            return i
    return None


def _microcompact_tool_content(content: str, tool_name: str) -> str:
    """Shrink an old tool result with a stable head+tail summary marker."""
    if len(content) <= _MICROCOMPACT_TRIM_THRESHOLD or _MICROCOMPACT_MARKER in content:
        return content
    head = content[:_MICROCOMPACT_HEAD_CHARS]
    tail = content[-_MICROCOMPACT_TAIL_CHARS:]
    return f"{head}\n\n{_MICROCOMPACT_MARKER} [{tool_name}]\n\n{tail}"


def _apply_microcompaction(messages: list[dict]) -> tuple[list[dict], int]:
    """Trim old tool results in-place-on-copy before full LLM compaction."""
    preserve_start = _find_microcompact_preserve_start(messages)
    if preserve_start is None or preserve_start <= 1:
        return messages, 0

    compacted = list(messages)
    changed = 0
    for i in range(1, preserve_start):
        msg = compacted[i]
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        trimmed = _microcompact_tool_content(content, str(msg.get("name", "tool")))
        if trimmed == content:
            continue
        compacted[i] = {**msg, "content": trimmed}
        changed += 1
    if changed == 0:
        return messages, 0
    return compacted, changed


def _pre_compaction_gap(
    persisted_history: list[dict] | None,
    *,
    cursor: int,
    compact_prefix_count: int,
) -> list[dict] | None:
    """Return the not-yet-extracted history prefix that compaction will summarize."""
    if persisted_history is None:
        return None
    compact_end = min(len(persisted_history), compact_prefix_count)
    if compact_end <= cursor:
        return None
    gap_msgs = persisted_history[cursor:compact_end]
    if len(gap_msgs) < 2:
        return None
    return gap_msgs


def _compaction_range(messages: list[dict]) -> tuple[int, int] | None:
    """Return the compactable history range between system and final user turn."""
    history_start = 1
    history_end = len(messages) - 1
    if history_end - history_start < 4:
        return None
    return history_start, history_end


def _compacted_messages(messages: list[dict], *, cut: int, summary: str) -> list[dict]:
    return [
        messages[0],
        {"role": "user", "content": f"[Prior conversation summary]\n{summary}"},
        {
            "role": "assistant",
            "content": "Understood, I have the context from our previous conversation.",
        },
        *messages[cut:],
    ]


class MemoryHandler:
    """Encapsulates all memory-related state and operations for AgentLoop."""

    def __init__(
        self,
        workspace: Path,
        memory_config: Any,
        orchestrator_config: Any,
        group_memory_enabled: bool,
        groups_base_dir: Path,
        structured_memory_enabled: bool = True,
    ):
        self._scope = MemoryScopeResolver(workspace, groups_base_dir, group_memory_enabled)
        self._memory_config = memory_config
        self._orchestrator_config = orchestrator_config
        self._structured_memory_enabled = structured_memory_enabled
        self._recall = MemoryRecallService(scope=self._scope, memory_config=memory_config)
        self._consolidation = MemoryConsolidationService(
            scope=self._scope, memory_config=memory_config
        )

        # Three-tier memory (lazy init when memory_tiers.enabled)
        self._memory_tiers: MemoryTierManager | None = None
        self._memory_tiers_enabled = bool(
            orchestrator_config and orchestrator_config.memory_tiers.enabled
        )
        self._memory_index: MemoryIndex | None = None  # global index
        self._memory_indexes: dict[str, MemoryIndex] = {}  # per-session indexes
        self._memory_dbs: dict[str, Database] = {}  # scope -> db handle
        self._consolidating: set[str] = set()
        self._consolidation_tasks: set[asyncio.Task] = set()
        self._consolidation_locks: dict[str, asyncio.Lock] = {}
        self._kg_pending_imported: bool = False
        # Circuit breaker: per-session consecutive compaction failure count.
        self._compact_consecutive_failures: dict[str, int] = {}
        # Extraction cursor: per-session index of last message sent to extract.
        # Prevents re-scanning the same unconsolidated tail on every turn.
        self._extract_cursor: dict[str, int] = {}

    # -- narrow public APIs ---------------------------------------------------

    @property
    def tiers(self) -> "MemoryTierManager":
        """Access the underlying MemoryTierManager."""
        if self._memory_tiers is None:
            from src.memory.tiers import MemoryTierManager

            self._memory_tiers = MemoryTierManager(
                self._scope.workspace, self._orchestrator_config
            )
        return self._memory_tiers

    def tiers_enabled(self) -> bool:
        """Return whether the short-term tier pipeline is enabled."""
        return self._memory_tiers_enabled

    def tiers_or_none(self) -> "MemoryTierManager | None":
        """Return the tier manager only when full memory tiers are enabled."""
        if not self._memory_tiers_enabled:
            return None
        return self.tiers

    @property
    def scope(self) -> MemoryScopeResolver:
        """Access the underlying scope resolver."""
        return self._scope

    @property
    def recall(self) -> MemoryRecallService:
        """Access the underlying recall service."""
        return self._recall

    @property
    def group_memory_enabled(self) -> bool:
        return self._scope.group_memory_enabled

    @property
    def workspace(self) -> Path:
        return self._scope.workspace

    def is_consolidating(self, key: str) -> bool:
        """Check if a consolidation task is in-flight for *key*."""
        return key in self._consolidating

    def add_consolidating(self, key: str) -> None:
        self._consolidating.add(key)

    def discard_consolidating(self, key: str) -> None:
        self._consolidating.discard(key)

    def get_consolidation_lock(self, key: str) -> asyncio.Lock:
        """Return (or create) a per-session consolidation lock."""
        return self._consolidation_locks.setdefault(key, asyncio.Lock())

    def pop_consolidation_lock(self, key: str) -> None:
        """Remove the consolidation lock for *key* if it is not held."""
        lock = self._consolidation_locks.get(key)
        if lock is not None and not lock.locked():
            self._consolidation_locks.pop(key, None)

    def add_consolidation_task(self, task: asyncio.Task) -> None:
        self._consolidation_tasks.add(task)

    def discard_consolidation_task(self, task: asyncio.Task) -> None:
        self._consolidation_tasks.discard(task)

    @property
    def consolidation_tasks(self) -> set[asyncio.Task]:
        return self._consolidation_tasks

    async def close_dbs(self) -> None:
        """Close all open memory database connections."""
        dbs = list(self._memory_dbs.values())
        self._memory_dbs.clear()
        if not dbs:
            return
        results = await asyncio.gather(*(db.close() for db in dbs), return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.opt(exception=result).warning("Failed to close memory database cleanly")

    # -- group workspace ------------------------------------------------------

    def get_group_workspace(self, session_key: str) -> Path:
        """Return per-group workspace path for the session, creating it if needed."""
        return self._scope.get_group_workspace(session_key)

    # -- structured memory persistence ----------------------------------------

    async def persist_structured_memory(
        self,
        *,
        session_key: str,
        user_message: str,
        response: str,
        tools_used: list[str],
        routed_skills: list[str],
        routing_domains: list[str],
        selected_primary: str | None,
        usage: dict[str, int] | None,
        duration_ms: float | None,
        artifacts: list[str] | None = None,
        tests: list[str] | None = None,
        status: str = "success",
        workspace_override: Path | None = None,
    ) -> None:
        """Persist high-value task knowledge as structured JSON objects."""
        if not self.memory_enabled() or not self._structured_memory_enabled:
            return

        from src.memory.structured import StructuredMemoryStore

        workspace = workspace_override or self._scope.resolve_structured_workspace(session_key)
        structured_store: StructuredMemoryStore | None = None
        try:
            structured_store = StructuredMemoryStore(workspace)
            await structured_store.ensure_kg()
            result = await structured_store.record_task(
                session_key=session_key,
                user_message=user_message,
                response=response,
                tools_used=tools_used,
                routed_skills=routed_skills,
                routing_domains=routing_domains,
                selected_primary=selected_primary,
                usage=usage,
                duration_ms=duration_ms,
                artifacts=artifacts,
                tests=tests,
                status=status,
            )

            # Markdown sync — MemoryHandler owns this decision
            from src.memory.store import MemoryStore

            md_store: MemoryStore | None = None
            if result.remember_directive:
                md_store = MemoryStore(workspace)
                md_store.remember(result.remember_directive)

            if result.history_entry:
                if md_store is None:
                    md_store = MemoryStore(workspace)
                md_store.append_history(result.history_entry)

            index = self.resolve_index_for_tools(session_key)
            default_workspace = self._scope.resolve_structured_workspace(session_key)
            if index is not None and workspace == default_workspace:
                await index.sync_all(workspace / "memory")

            # One-shot import of Instinct stable rules queued for KG
            if not self._kg_pending_imported:
                self._kg_pending_imported = True
                await self._import_kg_pending(workspace)
        except Exception:
            logger.opt(exception=True).warning(
                "Structured memory persistence failed for session {}", session_key
            )
        finally:
            if structured_store is not None:
                await structured_store.close()

    # -- Instinct-to-KG pending import ----------------------------------------

    async def _import_kg_pending(self, workspace: Path) -> None:
        """Import Instinct stable rules from kg_pending.jsonl into KG as lesson nodes."""
        pending_path = workspace / "memory" / "instinct" / "kg_pending.jsonl"
        if not pending_path.exists():
            return

        try:
            lines = pending_path.read_text(encoding="utf-8").strip().splitlines()
            if not lines:
                return

            from src.memory.structured import StructuredMemoryStore

            store = StructuredMemoryStore(workspace)
            try:
                await store.ensure_kg()
                assert store._kg is not None

                imported = 0
                for line in lines:
                    try:
                        record = json.loads(line)
                        rule_text = record.get("rule_text", "").strip()
                        if not rule_text:
                            continue

                        domains = record.get("domains", [])
                        confidence = record.get("confidence", 0.9)

                        # Create lesson node
                        await store._kg.add_node(
                            node_type="lesson",
                            title=rule_text[:120],
                            content=rule_text,
                            domains=domains,
                            metadata={
                                "source": "instinct",
                                "confidence": confidence,
                                "promoted_at": record.get("promoted_at", ""),
                            },
                        )

                        imported += 1
                    except Exception:
                        logger.opt(exception=True).debug("Failed to import KG pending record")

                # Rebuild FTS once after all imports, then link lessons to rules
                if imported:
                    from src.memory.knowledge_search import KnowledgeSearch

                    search = KnowledgeSearch(store._kg)
                    await search.ensure_fts()
                    await search.rebuild_fts()
                    logger.info("Imported {} Instinct stable rules as KG lessons", imported)
            finally:
                await store.close()

            # Truncate the file after successful import
            pending_path.write_text("", encoding="utf-8")
        except Exception:
            logger.opt(exception=True).warning("Failed to import kg_pending.jsonl")

    # -- structured recall ----------------------------------------------------

    async def build_structured_recall(
        self,
        *,
        session_key: str,
        query: str,
        selected_primary: str | None,
        workspace_override: Path | None = None,
    ) -> str | None:
        """Build a concise structured-memory recall block for the current turn."""
        if not self.memory_enabled() or not self._structured_memory_enabled:
            return None

        return await self._recall.build_structured_recall(
            session_key=session_key,
            query=query,
            selected_primary=selected_primary,
            workspace_override=workspace_override,
        )

    # -- search config accessors ----------------------------------------------

    def memory_enabled(self) -> bool:
        if self._memory_config is None:
            return True
        return bool(getattr(self._memory_config, "enabled", True))

    def search_enabled(self) -> bool:
        if self._memory_config is None:
            return True
        return self.memory_enabled() and bool(self._memory_config.search.enabled)

    def recall_telemetry_enabled(self) -> bool:
        if not self.memory_enabled() or self._memory_config is None:
            return False
        telemetry = getattr(self._memory_config, "telemetry", None)
        return bool(telemetry and telemetry.recall_enabled)

    def search_max_results(self) -> int:
        if self._memory_config is None:
            return 6
        return int(self._memory_config.search.max_results)

    def search_min_score(self) -> float:
        if self._memory_config is None:
            return 0.0
        return float(self._memory_config.search.min_score)

    # -- index / scope resolution ---------------------------------------------

    def resolve_index_for_tools(self, session_key: str | None) -> "MemoryIndex | None":
        """Resolve memory index for tool execution context."""
        if self._scope.group_memory_enabled:
            if not session_key:
                return None
            return self._memory_indexes.get(session_key)
        return self._memory_index

    def resolve_structured_workspace_for_tools(
        self,
        session_key: str | None,
        *,
        genver_workspace_resolver: Callable[[str], Path | None] | None = None,
    ) -> Path:
        """Resolve workspace used by structured memory tools."""
        return self._scope.resolve_structured_workspace(
            session_key, genver_workspace_resolver=genver_workspace_resolver
        )

    def resolve_scope(self, session_key: str | None = None) -> tuple[str, Path]:
        """Resolve index scope key and workspace path for the current session."""
        return self._scope.resolve_scope(session_key)

    # -- DB lifecycle ---------------------------------------------------------

    async def ensure_db(self, session_key: str | None = None) -> None:
        """Lazy-init memory index DB and FTS table, independent from memory tiers."""
        from src.memory.index import MemoryIndex
        from src.store.database import Database

        if not self.memory_enabled():
            return
        if self._memory_tiers_enabled:
            await self.tiers.ensure_db()
        if not self.search_enabled():
            return
        scope_key, scope_workspace = self.resolve_scope(session_key)

        if self._scope.group_memory_enabled:
            if scope_key in self._memory_indexes:
                return
        elif self._memory_index is not None:
            return

        # Reuse short-term DB in global mode when tiers DB is available.
        db: Database
        if (
            not self._scope.group_memory_enabled
            and self._memory_tiers is not None
            and self._memory_tiers._db is not None
        ):
            db = self._memory_tiers._db
        else:
            existing = self._memory_dbs.get(scope_key)
            if existing is None:
                db_path = scope_workspace / "memory_index.db"
                existing = Database(db_path)
                await existing.connect()
                self._memory_dbs[scope_key] = existing
            db = existing

        index = MemoryIndex(db)
        try:
            await index.ensure_table()
            await index.sync_all(scope_workspace / "memory")
            if self._scope.group_memory_enabled:
                self._memory_indexes[scope_key] = index
                logger.info("Memory FTS index initialized for session {}", scope_key)
            else:
                self._memory_index = index
                logger.info("Memory FTS index initialized (global)")
        except Exception:
            logger.opt(exception=True).warning(
                "Memory FTS index init failed for scope {}", scope_key
            )

    # -- consolidation --------------------------------------------------------

    async def consolidate(
        self,
        session: Any,
        *,
        provider: Any,
        model: str,
        memory_window: int,
        archive_all: bool = False,
    ) -> bool:
        """Delegate to MemoryConsolidationService. Returns True on success."""
        from src.memory.store import MemoryStore

        if not self.memory_enabled():
            return False
        workspace = self._scope.resolve_structured_workspace(session.key)
        await self.ensure_db(session.key)
        index = self.resolve_index_for_tools(session.key)
        store = MemoryStore(workspace)
        result = await self._consolidation.consolidate(
            session=session,
            provider=provider,
            model=model,
            store=store,
            archive_all=archive_all,
            memory_window=memory_window,
            short_term_store=(
                self._memory_tiers.short_term_store if self._memory_tiers is not None else None
            ),
            session_key=session.key,
            memory_index=index,
        )
        # Run GC after successful consolidation
        if result and self._memory_config and self._memory_config.gc.enabled:
            gc_cfg = self._memory_config.gc
            removed = store.gc(max_age_days=gc_cfg.max_age_days, max_sections=gc_cfg.max_sections)
            if removed > 0:
                logger.info("Memory GC removed {} sections", removed)
        return result

    # -- compaction -----------------------------------------------------------

    async def maybe_compact(
        self,
        messages: list[dict],
        *,
        provider: Any,
        model: str,
        memory_window: int,
        session_key: str = "",
        workspace: Path | None = None,
        persisted_history: list[dict] | None = None,
    ) -> list[dict]:
        """Compact history messages if estimated tokens exceed context threshold.

        Returns the (possibly modified) messages list.
        Preserves assistant(tool_calls)/tool message pairs to avoid orphan tool messages.
        """
        from src.memory.store import MemoryStore
        cfg = self._memory_config
        if not cfg or not cfg.compaction.enabled:
            return messages

        max_failures = cfg.compaction.max_consecutive_failures
        failures = self._compact_consecutive_failures.get(session_key, 0)
        if failures >= max_failures:
            logger.debug(
                "Compaction circuit breaker open ({}/{} consecutive failures, session={}), skipping",
                failures,
                max_failures,
                session_key[:8],
            )
            return messages

        messages, budget = self._apply_microcompaction_if_needed(messages, model=model)

        if not budget.should_compact:
            return messages

        logger.warning(
            "Context approaching limit (~{} tokens, threshold {}), running compaction",
            budget.estimated_tokens,
            budget.threshold,
        )

        compact_range = _compaction_range(messages)
        if compact_range is None:
            return messages
        history_start, history_end = compact_range

        cut = _find_safe_cut(messages, history_start, history_end)
        if cut is None:
            return messages  # no safe cut point found

        old_msgs = messages[history_start:cut]

        self._schedule_pre_compaction_flush_if_needed(
            session_key=session_key,
            persisted_history=persisted_history,
            compact_prefix_count=cut - history_start,
            provider=provider,
            model=model,
            workspace=workspace or self._scope.workspace,
        )

        try:
            summary = await MemoryStore(self._scope.workspace).compact_messages(
                old_msgs, provider, model
            )
            compacted = _compacted_messages(messages, cut=cut, summary=summary)
            # Post-compact context restoration: re-inject recently read files
            # so the LLM retains awareness of code it was working with.
            restore_max_files = cfg.compaction.restore_max_files
            restore_max_chars = cfg.compaction.restore_max_chars_per_file
            restoration = _build_restoration_context(
                session_key=session_key or None,
                max_files=restore_max_files,
                max_chars_per_file=restore_max_chars,
                workspace=workspace or self._scope.workspace,
            )
            if restoration:
                # Insert runtime-only restoration context right after the summary
                # pair so the next model call can still see it, while save_turn()
                # skips persisting it into session transcript history.
                compacted.insert(3, {"role": "user", "content": restoration})

            logger.info("Compaction replaced {} messages with summary", len(old_msgs))
            self._compact_consecutive_failures.pop(session_key, None)
            return compacted
        except Exception:
            new_count = self._compact_consecutive_failures.get(session_key, 0) + 1
            self._compact_consecutive_failures[session_key] = new_count
            logger.opt(exception=True).warning(
                "Compaction failed ({}/{} consecutive failures, session={}), using original messages",
                new_count,
                max_failures,
                session_key[:8],
            )
            return messages

    def _compaction_budget(self, messages: list[dict], *, model: str) -> _CompactionBudget:
        from src.memory.token_budget import estimate_messages_tokens, resolve_context_limit

        estimated_tokens = estimate_messages_tokens(
            messages,
            safety_margin=self._memory_config.compaction.safety_margin,
        )
        context_limit = resolve_context_limit(model)
        threshold = int(context_limit * self._memory_config.compaction.threshold_ratio)
        budget = _CompactionBudget(
            estimated_tokens=estimated_tokens,
            context_limit=context_limit,
            threshold=threshold,
            micro_threshold=int(threshold * _MICROCOMPACT_TRIGGER_RATIO),
        )
        logger.debug(
            "Token budget: estimated={} limit={} threshold={} compacting={}",
            budget.estimated_tokens,
            budget.context_limit,
            budget.threshold,
            budget.should_compact,
        )
        return budget

    def _apply_microcompaction_if_needed(
        self,
        messages: list[dict],
        *,
        model: str,
    ) -> tuple[list[dict], _CompactionBudget]:
        budget = self._compaction_budget(messages, model=model)
        if not budget.should_microcompact:
            return messages, budget

        microcompacted, trimmed_results = _apply_microcompaction(messages)
        if not trimmed_results:
            return messages, budget

        budget = self._compaction_budget(microcompacted, model=model)
        logger.info(
            "Microcompaction trimmed {} old tool result(s); estimated tokens now {}",
            trimmed_results,
            budget.estimated_tokens,
        )
        return microcompacted, budget

    def _schedule_pre_compaction_flush_if_needed(
        self,
        *,
        session_key: str,
        persisted_history: list[dict] | None,
        compact_prefix_count: int,
        provider: Any,
        model: str,
        workspace: Path,
    ) -> None:
        if persisted_history is None:
            return
        asyncio.create_task(
            self._schedule_pre_compaction_flush(
                session_key=session_key,
                persisted_history=persisted_history,
                compact_prefix_count=compact_prefix_count,
                provider=provider,
                model=model,
                workspace=workspace,
            )
        )

    async def _schedule_pre_compaction_flush(
        self,
        *,
        session_key: str,
        persisted_history: list[dict] | None,
        compact_prefix_count: int,
        provider: Any,
        model: str,
        workspace: Path,
    ) -> None:
        """Best-effort: extract durable facts from about-to-be-compacted messages.

        Only runs on the pre-turn compaction path (persisted_history != None).
        The in-loop compaction path passes persisted_history=None, which disables
        this, because _extract_cursor tracks absolute offsets into session.messages
        and cannot be mixed with the ephemeral in-loop messages list.
        """
        if persisted_history is None:
            return
        cfg_flush = self._memory_config.flush
        if not cfg_flush.enabled:
            return

        cursor = max(self._extract_cursor.get(session_key, 0), 0)
        gap_msgs = _pre_compaction_gap(
            persisted_history,
            cursor=cursor,
            compact_prefix_count=compact_prefix_count,
        )
        if gap_msgs is None:
            return
        if len(gap_msgs) > 50:
            logger.warning(
                "Pre-flush gap too large ({}), skipping to avoid stale extraction",
                len(gap_msgs),
            )
            return

        try:
            facts = await asyncio.wait_for(
                extract_durable_facts(gap_msgs, provider, model),
                timeout=30.0,
            )
            if facts:
                from src.memory.store import MemoryStore

                merged = merge_extracted_facts(MemoryStore(workspace), facts)
                if merged > 0:
                    self._write_flush_event(workspace, session_key, merged)
                    logger.info("Pre-compaction flush: {} facts merged", merged)
                    # Best-effort FTS sync so flushed facts are searchable immediately
                    index = self.resolve_index_for_tools(session_key)
                    if index is not None:
                        try:
                            await index.sync_all(workspace / "memory")
                        except Exception:
                            logger.opt(exception=True).debug(
                                "FTS sync after pre-compaction flush failed (best-effort)"
                            )
        except Exception:
            logger.opt(exception=True).debug("Pre-compaction flush failed (best-effort)")

    @staticmethod
    def _write_flush_event(workspace: Path, session_key: str, facts_merged: int) -> None:
        import hashlib
        from datetime import datetime

        from src.memory.memory_events import append_memory_event

        events_dir = workspace / "memory" / "instinct" / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        ts = now.strftime("%Y-%m-%dT%H%M%S-%f")
        # Include session hash plus microseconds to avoid same-second collisions,
        # including repeated flushes from the same session.
        suffix = hashlib.sha1(session_key.encode()).hexdigest()[:6]
        fname = f"{ts}-{suffix}-flush.json"
        event = {
            "type": "pre_compaction_flush",
            "timestamp": now.isoformat(),
            "session_key": session_key,
            "facts_merged": facts_merged,
        }
        (events_dir / fname).write_text(json.dumps(event, ensure_ascii=False, indent=2) + "\n")

        # Also emit to the unified memory event log.
        append_memory_event(
            workspace=workspace,
            event_type="memory.flush.completed",
            payload={"session_key": session_key, "facts_merged": facts_merged},
        )
