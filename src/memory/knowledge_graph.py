"""KnowledgeGraph — async SQLite-backed node/edge store for structured memory.

Provides typed nodes (task, rule, research) linked by directed edges.
Each node carries tags, domains, importance, timestamps, and optional embeddings.

Uses :class:`src.store.database.Database` for all I/O; the KG gets its own
``kg.db`` file (not the shared ``theos.db``).
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from src.store.database import Database

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS kg_nodes (
    id            TEXT PRIMARY KEY,
    node_type     TEXT NOT NULL,
    title         TEXT NOT NULL,
    content       TEXT NOT NULL DEFAULT '',
    tags          TEXT NOT NULL DEFAULT '',
    domains       TEXT NOT NULL DEFAULT '',
    importance    REAL NOT NULL DEFAULT 0.5,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    superseded_by TEXT,
    metadata      TEXT NOT NULL DEFAULT '{}',
    embedding     BLOB,
    embedding_model TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS kg_edges (
    from_id    TEXT NOT NULL,
    to_id      TEXT NOT NULL,
    relation   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id, relation),
    FOREIGN KEY (from_id) REFERENCES kg_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (to_id)   REFERENCES kg_nodes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash TEXT NOT NULL,
    provider     TEXT NOT NULL,
    model        TEXT NOT NULL,
    embedding    BLOB NOT NULL,
    created_at   TEXT NOT NULL,
    accessed_at  TEXT NOT NULL,
    PRIMARY KEY (content_hash, provider, model)
);

CREATE INDEX IF NOT EXISTS idx_kg_nodes_type       ON kg_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_superseded ON kg_nodes(superseded_by);
CREATE INDEX IF NOT EXISTS idx_kg_edges_from       ON kg_edges(from_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_to         ON kg_edges(to_id);
"""

# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------

_IMPORTANCE_KEYWORDS = {"must", "always", "never", "critical"}
_BASE_IMPORTANCE = {
    "rule": 0.45,
    "task": 0.35,
    "research": 0.30,
}
_NODE_UPDATE_COLUMNS = {
    "node_type",
    "title",
    "content",
    "tags",
    "domains",
    "importance",
    "created_at",
    "updated_at",
    "superseded_by",
    "metadata",
    "embedding",
    "embedding_model",
}


def compute_importance(node_type: str, text: str, occurrence_count: int = 1) -> float:
    """Heuristic importance score in ``[0.0, 0.95]``.

    * Keyword boosts for "must", "always", "never", "critical".
    * Higher for rules with high occurrence count.
    """
    base = _BASE_IMPORTANCE.get(node_type, 0.25)

    # Keyword boost
    lowered = text.lower()
    keyword_hits = sum(1 for kw in _IMPORTANCE_KEYWORDS if kw in lowered)
    base += keyword_hits * 0.08

    # Occurrence boost (mainly useful for rules)
    if occurrence_count > 1:
        base += min(0.20, 0.05 * (occurrence_count - 1))

    return round(min(0.95, max(0.0, base)), 3)


def temporal_decay(created_at: str, half_life_days: float) -> float:
    """Exponential decay factor based on age.  Returns 1.0 if *half_life_days* <= 0."""
    if half_life_days <= 0:
        return 1.0
    try:
        ts = datetime.fromisoformat(created_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        age_days = (datetime.now(UTC) - ts).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return 1.0
    if age_days <= 0:
        return 1.0
    return math.exp(-math.log(2) * age_days / half_life_days)


# ---------------------------------------------------------------------------
# KnowledgeGraph
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _join_list(items: list[str] | None) -> str:
    """Join a list into a comma-separated string, filtering blanks."""
    if not items:
        return ""
    return ",".join(s.strip() for s in items if s and s.strip())


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _metadata_json(existing: dict[str, Any] | None, patch: Any) -> str:
    if existing is None:
        return patch if isinstance(patch, str) else json.dumps(patch, ensure_ascii=False)

    metadata = _json_dict(existing.get("metadata") or "{}")
    metadata.update(_json_dict(patch))
    return json.dumps(metadata, ensure_ascii=False)


def _validate_update_columns(fields: dict[str, Any]) -> None:
    bad_cols = set(fields) - _NODE_UPDATE_COLUMNS
    if bad_cols:
        raise ValueError(f"Invalid column names: {bad_cols}")


class KnowledgeGraph:
    """Async node/edge store backed by a dedicated SQLite database."""

    def __init__(self, db_path: Path) -> None:
        self._db = Database(db_path)

    # -- lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        """Open the database, enable dict-style row access, and create schema."""
        await self._db.connect()
        await self._db.set_row_factory()
        await self._db.executescript(_SCHEMA_SQL)
        logger.debug("KnowledgeGraph ready: {}", self._db.db_path)

    async def close(self) -> None:
        """Close the underlying database connection."""
        await self._db.close()

    # -- nodes ---------------------------------------------------------------

    async def add_node(
        self,
        *,
        node_type: str,
        title: str,
        content: str = "",
        tags: list[str] | None = None,
        domains: list[str] | None = None,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
        node_id: str | None = None,
    ) -> str:
        """Insert a new node and return its id."""
        if node_id is None:
            node_id = f"{node_type}-{uuid.uuid4().hex[:12]}"

        now = _now_iso()
        meta_str = json.dumps(metadata or {}, ensure_ascii=False)
        tags_str = _join_list(tags)
        domains_str = _join_list(domains)

        await self._db.execute(
            """INSERT INTO kg_nodes
               (id, node_type, title, content, tags, domains,
                importance, created_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node_id,
                node_type,
                title,
                content,
                tags_str,
                domains_str,
                importance,
                now,
                now,
                meta_str,
            ),
        )
        return node_id

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Return a single node as a dict, or ``None`` if not found."""
        row = await self._db.fetchone("SELECT * FROM kg_nodes WHERE id = ?", (node_id,))
        if row is None:
            return None
        return dict(row)

    async def update_node(self, node_id: str, **fields: Any) -> None:
        """Update arbitrary columns on an existing node.

        If *metadata* is among the fields it is **merged** with the existing
        metadata (not replaced), so callers can pass partial dicts.
        """
        if not fields:
            return

        fields = dict(fields)
        if "metadata" in fields:
            existing = await self.get_node(node_id)
            fields["metadata"] = _metadata_json(existing, fields["metadata"])

        fields["updated_at"] = _now_iso()
        _validate_update_columns(fields)

        set_clause = ", ".join(f"{col} = ?" for col in fields)
        values = [*fields.values(), node_id]
        await self._db.execute(
            f"UPDATE kg_nodes SET {set_clause} WHERE id = ?",
            tuple(values),
        )

    async def supersede(self, old_id: str, new_id: str) -> None:
        """Mark *old_id* as superseded by *new_id*."""
        await self._db.execute(
            "UPDATE kg_nodes SET superseded_by = ?, updated_at = ? WHERE id = ?",
            (new_id, _now_iso(), old_id),
        )

    async def list_nodes(
        self,
        node_type: str,
        limit: int = 50,
        exclude_superseded: bool = True,
    ) -> list[dict[str, Any]]:
        """List nodes of a given type, newest first."""
        clauses = ["node_type = ?"]
        params: list[Any] = [node_type]
        if exclude_superseded:
            clauses.append("superseded_by IS NULL")
        params.append(limit)
        where = " AND ".join(clauses)
        rows = await self._db.fetchall(
            f"""SELECT * FROM kg_nodes
                WHERE {where}
                ORDER BY created_at DESC LIMIT ?""",
            tuple(params),
        )
        return [dict(r) for r in rows]

    async def count(self) -> int:
        """Return total number of nodes."""
        row = await self._db.fetchone("SELECT COUNT(*) AS cnt FROM kg_nodes")
        return int(row["cnt"]) if row else 0

    # -- edges ---------------------------------------------------------------

    async def add_edge(self, from_id: str, to_id: str, relation: str) -> None:
        """Insert a directed edge. Silently ignores duplicates."""
        await self._db.execute(
            """INSERT OR IGNORE INTO kg_edges (from_id, to_id, relation, created_at)
               VALUES (?, ?, ?, ?)""",
            (from_id, to_id, relation, _now_iso()),
        )

    async def find_related(self, node_id: str) -> list[dict[str, Any]]:
        """Return edges originating from *node_id*, joined with target nodes."""
        rows = await self._db.fetchall(
            """SELECT e.from_id, e.to_id, e.relation, e.created_at AS edge_created,
                      n.*
               FROM kg_edges e
               JOIN kg_nodes n ON n.id = e.to_id
               WHERE e.from_id = ?""",
            (node_id,),
        )
        return [dict(r) for r in rows]

    async def find_related_inbound(self, node_id: str) -> list[dict[str, Any]]:
        """Return edges pointing TO this node."""
        rows = await self._db.fetchall(
            "SELECT from_id, to_id, relation, created_at FROM kg_edges WHERE to_id = ?",
            (node_id,),
        )
        return [dict(row) for row in rows]

    # -- embeddings (Phase 2) ------------------------------------------------

    async def set_embedding(self, node_id: str, embedding: list[float], model: str) -> None:
        """Store a pre-computed embedding vector on a node.

        The float list is packed into a binary blob via :mod:`struct` so that
        sqlite-vec's ``vec_distance_cosine()`` can operate on it directly.
        """
        import struct

        blob = struct.pack(f"{len(embedding)}f", *embedding)
        await self._db.execute(
            "UPDATE kg_nodes SET embedding = ?, embedding_model = ?, updated_at = ? WHERE id = ?",
            (blob, model, _now_iso(), node_id),
        )
