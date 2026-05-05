"""Session persistence: append-only JSONL conversation storage.

Owns: JSONL read/write/compact, LRU cache, session lifecycle.
Does NOT own: message formatting, tool result truncation (see src/utils/truncation.py),
or memory consolidation (see src/memory/).
"""

from __future__ import annotations

import json
import shutil
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from src.utils.helpers import ensure_dir, safe_filename
from src.utils.truncation import truncate_tool_call_arguments  # re-export


@dataclass
class Session:
    """
    A conversation session.

    Stores messages in JSONL format for easy reading and persistence.

    Important: Messages are append-only for LLM cache efficiency.
    The consolidation process writes summaries to MEMORY.md/HISTORY.md
    but does NOT modify the messages list or get_history() output.
    """

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {"role": role, "content": content, "timestamp": datetime.now().isoformat(), **kwargs}
        self.messages.append(msg)
        self.updated_at = datetime.now()

    # Tool results are already fully processed by the LLM in prior turns;
    # keeping them verbose just adds noise and can push conversation out of
    # the context window.  We use a head+tail soft-trim (inspired by OpenClaw)
    # so the model still sees the beginning and end of long results.
    _TOOL_RESULT_HEAD_CHARS = 500  # keep first N chars
    _TOOL_RESULT_TAIL_CHARS = 500  # keep last N chars
    # Only trim when the result is meaningfully longer than what we'd keep.
    # head + tail + placeholder ≈ 1050, so fire at ~2× that to avoid
    # trimming results that would barely shrink.
    _TOOL_RESULT_TRIM_THRESHOLD = 2000
    _HISTORY_TOOL_CALL_ARGS_MAX_CHARS = 500

    def get_history(
        self,
        max_messages: int = 500,
        exclude_turn_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input, aligned to a user turn.

        Design principle (inspired by OpenClaw): **never discard user/assistant
        messages based on topic heuristics**.  Context windowing is handled by
        ``max_messages`` (turn-count limit) and compaction (LLM-generated
        summary).  Only tool results are trimmed, using a head+tail strategy
        so the model still sees the start and end of long outputs.
        """
        unconsolidated = self.messages[self.last_consolidated :]
        if exclude_turn_id is not None:
            unconsolidated = [
                msg for msg in unconsolidated if msg.get("turn_id") != exclude_turn_id
            ]
        sliced = unconsolidated[-max_messages:]

        # Drop leading non-user messages to avoid orphaned tool_result blocks
        for i, m in enumerate(sliced):
            if m.get("role") == "user":
                sliced = sliced[i:]
                break

        out: list[dict[str, Any]] = []
        for m in sliced:
            entry: dict[str, Any] = {"role": m["role"], "content": m.get("content", "")}
            for k in ("tool_calls", "tool_call_id", "name"):
                if k in m:
                    entry[k] = m[k]
            if "tool_calls" in entry:
                entry["tool_calls"] = truncate_tool_call_arguments(
                    entry["tool_calls"], self._HISTORY_TOOL_CALL_ARGS_MAX_CHARS
                )
            # Soft-trim tool results: keep head + tail so the model sees
            # structure without blowing up the context window.
            if (
                entry.get("role") == "tool"
                and isinstance(entry.get("content"), str)
                and len(entry["content"]) > self._TOOL_RESULT_TRIM_THRESHOLD
            ):
                name = entry.get("name", "tool")
                head = entry["content"][: self._TOOL_RESULT_HEAD_CHARS]
                tail = entry["content"][-self._TOOL_RESULT_TAIL_CHARS :]
                entry["content"] = f"{head}\n\n... [{name} result trimmed] ...\n\n{tail}"
            out.append(entry)
        return out

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    _MAX_CACHE_SIZE = 500
    _COMPACT_THRESHOLD = 500  # Compact after this many appended messages

    def __init__(self, workspace: Path, config: Any = None):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = Path.home() / ".theos" / "sessions"
        self._cache: OrderedDict[str, Session] = OrderedDict()
        self._persisted_msg_count: dict[str, int] = {}  # session_key -> messages already on disk
        self._append_lines_since_compact: dict[str, int] = {}  # session_key -> appended lines
        self._scrub_enabled = getattr(
            getattr(config, "security", None), "scrub_session_history", True
        )

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy global session path (~/.theos/sessions/)."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.legacy_sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._put_cache(session)
        return session

    def has_turn_user_message(self, session: Session, turn_id: str) -> bool:
        """Return True if the accepted user message for *turn_id* is already persisted."""
        return any(
            msg.get("role") == "user" and msg.get("turn_id") == turn_id for msg in session.messages
        )

    def persist_user_message(
        self,
        session: Session,
        content: str,
        *,
        turn_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Persist the user's accepted message immediately, idempotently.

        Returns True if a new message was appended, False if this turn's user
        message was already present.
        """
        if self.has_turn_user_message(session, turn_id):
            return False
        entry = {
            "role": "user",
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "turn_id": turn_id,
        }
        if metadata:
            entry["metadata"] = metadata
        session.messages.append(entry)
        session.updated_at = datetime.now()
        self.save(session)
        return True

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)
        if not path.exists():
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                try:
                    shutil.move(str(legacy_path), str(path))
                    logger.info("Migrated session {} from legacy path", key)
                except Exception:
                    logger.opt(exception=True).warning("Failed to migrate session {}", key)

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = (
                            datetime.fromisoformat(data["created_at"])
                            if data.get("created_at")
                            else None
                        )
                        last_consolidated = data.get("last_consolidated", 0)
                    else:
                        messages.append(data)

            session = Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated,
            )
            self._persisted_msg_count[key] = len(messages)
            self._append_lines_since_compact[key] = 0
            return session
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            return None

    def _scrub_message_for_persist(self, msg: dict) -> dict:
        """Scrub credentials from message before disk persistence.

        Creates new dicts for modified structures — does NOT mutate the
        original session.messages entries.
        """
        if not self._scrub_enabled:
            return msg
        from src.safety.leak_detector import scrub_credentials

        out = dict(msg)
        if "tool_calls" in out:
            scrubbed_tcs = []
            for tc in out["tool_calls"]:
                if "function" in tc:
                    scrubbed_tcs.append(
                        {
                            **tc,
                            "function": {
                                **tc["function"],
                                "arguments": scrub_credentials(tc["function"].get("arguments", "")),
                            },
                        }
                    )
                else:
                    scrubbed_tcs.append(tc)
            out["tool_calls"] = scrubbed_tcs
        if out.get("role") == "tool" and isinstance(out.get("content"), str):
            out["content"] = scrub_credentials(out["content"])
        return out

    def _save_to_disk(self, session: Session) -> None:
        """Append-only save: only write new messages since last persist.

        Falls back to full rewrite when:
        - File doesn't exist yet (new session)
        - Session was cleared (persisted > current)
        - Compact threshold reached
        """
        path = self._get_session_path(session.key)
        persisted = self._persisted_msg_count.get(session.key, 0)
        total = len(session.messages)

        append_lines = self._append_lines_since_compact.get(session.key, 0)

        # Full rewrite needed?
        needs_full = (
            not path.exists()
            or persisted > total
            or persisted == 0
            or append_lines >= self._COMPACT_THRESHOLD
        )
        if needs_full:
            self._write_full(session, path)
            return

        # If this save would push append growth past threshold, compact now.
        new_msgs = session.messages[persisted:]
        lines_to_append = len(new_msgs) + 1  # include trailing metadata snapshot
        if append_lines + lines_to_append >= self._COMPACT_THRESHOLD:
            self._write_full(session, path)
            return

        # Append only: new messages + trailing metadata snapshot.
        with open(path, "a", encoding="utf-8") as f:
            for msg in new_msgs:
                f.write(json.dumps(self._scrub_message_for_persist(msg), ensure_ascii=False) + "\n")
            f.write(json.dumps(self._metadata_dict(session), ensure_ascii=False) + "\n")

        self._persisted_msg_count[session.key] = total
        self._append_lines_since_compact[session.key] = append_lines + lines_to_append

    def _write_full(self, session: Session, path: Path) -> None:
        """Full rewrite: atomic write via temp file."""
        tmp = path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(self._metadata_dict(session), ensure_ascii=False) + "\n")
            for msg in session.messages:
                f.write(json.dumps(self._scrub_message_for_persist(msg), ensure_ascii=False) + "\n")
        tmp.replace(path)
        self._persisted_msg_count[session.key] = len(session.messages)
        self._append_lines_since_compact[session.key] = 0

    @staticmethod
    def _metadata_dict(session: Session) -> dict[str, Any]:
        return {
            "_type": "metadata",
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "last_consolidated": session.last_consolidated,
        }

    def _put_cache(self, session: "Session") -> None:
        """Insert into cache, evicting oldest if over capacity."""
        self._cache[session.key] = session
        self._cache.move_to_end(session.key)
        while len(self._cache) > self._MAX_CACHE_SIZE:
            evicted_key, evicted = self._cache.popitem(last=False)
            self._save_to_disk(evicted)
            logger.debug("Evicted session {} from cache", evicted_key)

    def save(self, session: Session) -> None:
        """Save a session to disk and update the cache."""
        self._save_to_disk(session)
        self._put_cache(session)

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)
        self._persisted_msg_count.pop(key, None)
        self._append_lines_since_compact.pop(key, None)

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Returns:
            List of session info dicts.
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                # Read latest metadata snapshot (supports append-only metadata tails).
                with open(path, encoding="utf-8") as f:
                    data = None
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        row = json.loads(line)
                        if row.get("_type") == "metadata":
                            data = row
                    if data:
                        if data.get("_type") == "metadata":
                            key = data.get("key") or path.stem.replace("_", ":", 1)
                            sessions.append(
                                {
                                    "key": key,
                                    "created_at": data.get("created_at"),
                                    "updated_at": data.get("updated_at"),
                                    "path": str(path),
                                }
                            )
            except Exception:
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
