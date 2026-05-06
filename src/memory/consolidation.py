"""Memory consolidation orchestration service.

Owns: message selection, prompt building, provider chat (with retry),
save_memory tool-call interpretation, post-consolidation coordination
(session offset, SQLite row marking, FTS index sync).

Consolidation reads from ``Session.messages``, not from SQLite.
SQLite row marking (``mark_consolidated``) is a bookkeeping/audit step;
the short-term SQLite tier is a buffer, not the primary archive source.

Does NOT own: low-level markdown read/write (delegated to MemoryStore),
section parsing, session lifecycle, or index lifecycle setup.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from src.memory.index import MemoryIndex
    from src.memory.scope import MemoryScopeResolver
    from src.memory.store import MemoryStore
    from src.providers.base import LLMProvider
    from src.session.manager import Session


_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": "A paragraph (2-5 sentences) summarizing key events/decisions/topics. "
                        "Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search.",
                    },
                    "memory_update": {
                        "type": "string",
                        "description": "Full updated long-term memory as markdown. "
                        "Use ## sections with <!-- updated: YYYY-MM-DD --> timestamps. "
                        "Include all existing facts plus new ones. Return unchanged if nothing new.",
                    },
                },
                "required": ["history_entry", "memory_update"],
            },
        },
    }
]
_MAX_CONSOLIDATION_ATTEMPTS = 2
_MESSAGE_PROMPT_LIMIT = 1000
_MESSAGE_PROMPT_HEAD = 500
_MESSAGE_PROMPT_TAIL = 500


@dataclass(frozen=True)
class _ConsolidationPlan:
    old_messages: list[dict[str, Any]]
    keep_count: int
    archive_all: bool
    target_last_consolidated: int

    @property
    def archived_count(self) -> int:
        return len(self.old_messages)


def _validate_memory_update(old: str, new: str) -> tuple[bool, str]:
    """Validate a proposed memory update against the old memory.

    Returns (valid, reason). Reject if:
    - new drops >50% of existing sections (and old had >=3 sections)
    - new loses any pinned section from old
    - new is >3x or <0.3x the size of old (when old is non-trivial)
    """
    # Empty -> empty is trivially valid.
    if not old.strip() and not new.strip():
        return True, ""
    # Empty old -> any non-destructive new is valid (first real update).
    if not old.strip():
        return True, ""

    # Extract section titles (## Title lines).
    section_re = re.compile(r"^##\s+(.+?)$", re.MULTILINE)
    old_sections = section_re.findall(old)
    new_sections = section_re.findall(new)

    # Section count sanity: if old had >=3 sections, new must retain >=50%.
    if len(old_sections) >= 3:
        min_allowed = max(3, len(old_sections) // 2)
        if len(new_sections) < min_allowed:
            return False, (
                f"section count dropped from {len(old_sections)} to {len(new_sections)} "
                f"(below minimum {min_allowed})"
            )

    # Pinned section protection: find pinned titles in old, require all in new.
    pinned_titles: list[str] = []
    for match in re.finditer(r"^##\s+(.+?)$(.*?)(?=^##\s|\Z)", old, re.MULTILINE | re.DOTALL):
        title = match.group(1).strip()
        body = match.group(2)
        if "<!-- pinned" in body:
            pinned_titles.append(title)

    new_section_set = {s.strip() for s in new_sections}
    missing_pinned = [t for t in pinned_titles if t not in new_section_set]
    if missing_pinned:
        return False, f"lost pinned sections: {missing_pinned}"

    # Size sanity (characters). Upper bound always applies — even tiny memory
    # should not explode to huge size. Lower bound only for non-trivial old.
    old_len = len(old)
    new_len = len(new)
    # Upper bound: allow growth to 3x of old, or a 2KB floor for legitimate
    # expansion from near-empty memory. Anything beyond that is suspicious.
    upper_bound = max(old_len * 3, 2048)
    if new_len > upper_bound:
        return False, f"output too large ({new_len} chars vs old {old_len}, >{upper_bound})"
    if old_len >= 100 and new_len < old_len * 0.3:
        return False, f"output too small ({new_len} chars vs old {old_len}, <0.3x)"

    return True, ""


class MemoryConsolidationService:
    """Consolidation orchestration: LLM-based conversation archival into MEMORY.md + HISTORY.md.

    This service owns the full consolidation pipeline:
    - selecting messages to archive
    - building the consolidation prompt
    - calling the LLM provider (with retry)
    - interpreting the save_memory tool call
    - coordinating post-consolidation writes via MemoryStore helpers
    """

    def __init__(
        self,
        *,
        scope: "MemoryScopeResolver",
    ):
        self._scope = scope

    @staticmethod
    def _gather_recent_history(store: "MemoryStore", max_entries: int = 10) -> str:
        """Read recent entries from HISTORY.md for cross-session context."""
        try:
            if not store.history_file.exists():
                return ""
            raw = store.history_file.read_text(encoding="utf-8").strip()
            if not raw:
                return ""
            entries = [e.strip() for e in raw.split("\n\n") if e.strip()]
            recent = entries[-max_entries:]
            if not recent:
                return ""
            return "\n## Recent Project Activity (shared history log)\n" + "\n".join(
                f"- {e}" for e in recent
            )
        except Exception:
            return ""

    async def consolidate(
        self,
        *,
        session: "Session",
        provider: "LLMProvider",
        model: str,
        store: "MemoryStore",
        archive_all: bool = False,
        memory_window: int = 50,
        short_term_store: Any = None,
        session_key: str | None = None,
        memory_index: "MemoryIndex | None" = None,
    ) -> bool:
        """Consolidate old messages into MEMORY.md + HISTORY.md via LLM tool call.

        ``store`` is an explicit MemoryStore backend used for markdown I/O.
        The service never creates its own MemoryStore instance.

        Returns True on success (including no-op), False on failure.
        """
        plan = self._consolidation_plan(
            session,
            archive_all=archive_all,
            memory_window=memory_window,
        )
        if plan is None:
            return True
        self._log_consolidation_plan(plan, total_messages=len(session.messages))

        current_memory = store.read_long_term()
        history_context = self._gather_recent_history(store)
        prompt = self._build_consolidation_prompt(
            old_messages=plan.old_messages,
            current_memory=current_memory,
            history_context=history_context,
        )

        response = await self._call_provider(provider=provider, model=model, prompt=prompt)
        if response is None:
            return False

        if not response.has_tool_calls:
            logger.warning(
                "Memory consolidation: LLM did not call save_memory, using fallback archive"
            )
            return await self._persist_fallback(
                session,
                store=store,
                plan=plan,
                current_memory=current_memory,
                short_term_store=short_term_store,
                session_key=session_key,
                memory_index=memory_index,
            )

        args = self._coerce_save_memory_args(response.tool_calls[0].arguments)
        if args is None:
            logger.warning(
                "Memory consolidation: malformed save_memory arguments, using fallback archive"
            )
            return await self._persist_fallback(
                session,
                store=store,
                plan=plan,
                current_memory=current_memory,
                short_term_store=short_term_store,
                session_key=session_key,
                memory_index=memory_index,
            )

        parsed_args = self._parse_save_memory_args(
            args,
            current_memory=current_memory,
        )
        if parsed_args is None:
            logger.warning(
                "Memory consolidation: missing history_entry, using fallback archive"
            )
            return await self._persist_fallback(
                session,
                store=store,
                plan=plan,
                current_memory=current_memory,
                short_term_store=short_term_store,
                session_key=session_key,
                memory_index=memory_index,
            )
        history_entry, memory_update = parsed_args

        return await self._persist_consolidation_result(
            session,
            store=store,
            archive_all=plan.archive_all,
            keep_count=plan.keep_count,
            target_last_consolidated=plan.target_last_consolidated,
            archived_count=plan.archived_count,
            current_memory=current_memory,
            history_entry=history_entry,
            memory_update=memory_update,
            short_term_store=short_term_store,
            session_key=session_key,
            memory_index=memory_index,
        )

    @staticmethod
    def _consolidation_plan(
        session: "Session",
        *,
        archive_all: bool,
        memory_window: int,
    ) -> _ConsolidationPlan | None:
        if archive_all:
            return _ConsolidationPlan(
                old_messages=list(session.messages),
                keep_count=0,
                archive_all=True,
                target_last_consolidated=0,
            )

        keep_count = memory_window // 2
        if len(session.messages) <= keep_count:
            return None
        if len(session.messages) - session.last_consolidated <= 0:
            return None

        old_messages = session.messages[session.last_consolidated : -keep_count]
        if not old_messages:
            return None
        return _ConsolidationPlan(
            old_messages=old_messages,
            keep_count=keep_count,
            archive_all=False,
            target_last_consolidated=len(session.messages) - keep_count,
        )

    @staticmethod
    def _log_consolidation_plan(plan: _ConsolidationPlan, *, total_messages: int) -> None:
        if plan.archive_all:
            logger.info("Memory consolidation (archive_all): {} messages", total_messages)
            return
        logger.info(
            "Memory consolidation: {} to consolidate, {} keep",
            len(plan.old_messages),
            plan.keep_count,
        )

    @staticmethod
    def _format_prompt_message(message: dict[str, Any]) -> str | None:
        content = message.get("content")
        if not content:
            return None
        if isinstance(content, str) and len(content) > _MESSAGE_PROMPT_LIMIT:
            content = (
                content[:_MESSAGE_PROMPT_HEAD]
                + " ... [truncated] ... "
                + content[-_MESSAGE_PROMPT_TAIL:]
            )
        tools = (
            f" [tools: {', '.join(message['tools_used'])}]"
            if message.get("tools_used")
            else ""
        )
        timestamp = str(message.get("timestamp", "?"))[:16]
        role = str(message.get("role", "unknown")).upper()
        return f"[{timestamp}] {role}{tools}: {content}"

    @classmethod
    def _format_conversation(cls, messages: list[dict[str, Any]]) -> str:
        lines = [line for message in messages if (line := cls._format_prompt_message(message))]
        return "\n".join(lines)

    @classmethod
    def _build_consolidation_prompt(
        cls,
        *,
        old_messages: list[dict[str, Any]],
        current_memory: str,
        history_context: str,
    ) -> str:
        cross_session_rule = (
            "\n- **Project-wide awareness**: consider the recent project activity log when consolidating — it provides broader context beyond the current conversation."
            if history_context
            else ""
        )
        conversation = cls._format_conversation(old_messages)
        return f"""Process this conversation and call the save_memory tool with your consolidation.

## Rules
- **Merge, don't append**: update existing sections in place rather than duplicating information.
- **Resolve contradictions**: when new information contradicts existing memory, keep the newer or more authoritative version and remove the outdated entry.
- **Preserve timestamps**: every section must include a `<!-- updated: YYYY-MM-DD -->` comment reflecting when it was last changed.
- **Keep pinned sections**: retain sections marked as pinned unless they are explicitly contradicted by newer information.
- **Be concise**: remove redundant or superseded details; do not repeat the same fact in multiple sections.{cross_session_rule}

## Current Long-term Memory
{current_memory or "(empty)"}
{history_context}
## Conversation to Process
{conversation}"""

    @staticmethod
    async def _call_provider(
        *,
        provider: "LLMProvider",
        model: str,
        prompt: str,
    ) -> Any | None:
        for attempt in range(1, _MAX_CONSOLIDATION_ATTEMPTS + 1):
            try:
                return await provider.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a memory consolidation agent. "
                                "Call the save_memory tool with your consolidation "
                                "of the conversation."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    tools=_SAVE_MEMORY_TOOL,
                    model=model,
                )
            except Exception:
                if attempt < _MAX_CONSOLIDATION_ATTEMPTS:
                    logger.warning(
                        "Memory consolidation LLM call failed, retrying (attempt {}/{})",
                        attempt,
                        _MAX_CONSOLIDATION_ATTEMPTS,
                    )
                    continue
                logger.opt(exception=True).warning(
                    "Memory consolidation failed after {} attempts",
                    _MAX_CONSOLIDATION_ATTEMPTS,
                )
                return None
        return None

    @staticmethod
    def _coerce_save_memory_args(raw_args: Any) -> dict[str, Any] | None:
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                return None
        return raw_args if isinstance(raw_args, dict) else None

    @staticmethod
    def _parse_save_memory_args(
        args: dict[str, Any],
        *,
        current_memory: str,
    ) -> tuple[str, str] | None:
        entry = args.get("history_entry")
        if isinstance(entry, str):
            history_entry = entry.strip()
        elif entry:
            history_entry = json.dumps(entry, ensure_ascii=False)
        else:
            return None
        if not history_entry:
            return None

        update = args.get("memory_update")
        if isinstance(update, str):
            memory_update = update if update.strip() else current_memory
        elif update:
            memory_update = json.dumps(update, ensure_ascii=False)
        else:
            memory_update = current_memory
        return history_entry, memory_update

    async def _persist_fallback(
        self,
        session: "Session",
        *,
        store: "MemoryStore",
        plan: _ConsolidationPlan,
        current_memory: str,
        short_term_store: Any = None,
        session_key: str | None = None,
        memory_index: "MemoryIndex | None" = None,
    ) -> bool:
        return await self._persist_consolidation_result(
            session,
            store=store,
            archive_all=plan.archive_all,
            keep_count=plan.keep_count,
            target_last_consolidated=plan.target_last_consolidated,
            archived_count=plan.archived_count,
            current_memory=current_memory,
            history_entry=store._build_fallback_history_entry(plan.old_messages),
            memory_update=current_memory,
            short_term_store=short_term_store,
            session_key=session_key,
            memory_index=memory_index,
        )

    async def _persist_consolidation_result(
        self,
        session: "Session",
        *,
        store: "MemoryStore",
        archive_all: bool,
        keep_count: int,
        current_memory: str,
        history_entry: str | None,
        memory_update: str | None,
        short_term_store: Any = None,
        session_key: str | None = None,
        memory_index: "MemoryIndex | None" = None,
        target_last_consolidated: int | None = None,
        archived_count: int | None = None,
    ) -> bool:
        """Write consolidation artifacts and advance session offsets."""
        # Validate proposed memory update before persisting. A bad LLM output
        # should not wipe good memory — we still archive the history entry for
        # audit but skip the long-term write on rejection.
        if memory_update is not None and memory_update != current_memory:
            valid, reason = _validate_memory_update(current_memory, memory_update)
            if not valid:
                logger.warning(
                    "Consolidation rejected: {}. Keeping old memory, archiving history only.",
                    reason,
                )
                memory_update = None

        if history_entry:
            store.append_history(history_entry)
        if memory_update is not None and memory_update != current_memory:
            store.write_long_term(memory_update)

        archived_count = self._archived_message_count(
            session,
            archive_all=archive_all,
            keep_count=keep_count,
            planned_count=archived_count,
        )
        session.last_consolidated = (
            target_last_consolidated
            if target_last_consolidated is not None
            else (0 if archive_all else len(session.messages) - keep_count)
        )

        if short_term_store and session_key:
            try:
                rows = await short_term_store.get_unconsolidated(
                    session_key,
                    limit=max(archived_count, 0),
                )
                if rows:
                    await short_term_store.mark_consolidated(session_key, rows[-1]["id"])
            except Exception:
                logger.opt(exception=True).warning("Failed to mark consolidated in SQLite")

        if memory_index:
            try:
                await memory_index.sync_all(store.memory_dir)
            except Exception:
                logger.opt(exception=True).warning("FTS index sync failed after consolidation")

        logger.info(
            "Memory consolidation done: {} messages, last_consolidated={}",
            len(session.messages),
            session.last_consolidated,
        )
        return True

    @staticmethod
    def _archived_message_count(
        session: "Session",
        *,
        archive_all: bool,
        keep_count: int,
        planned_count: int | None = None,
    ) -> int:
        if planned_count is not None:
            return planned_count
        if archive_all:
            return len(session.messages)
        return max(len(session.messages) - keep_count - session.last_consolidated, 0)
