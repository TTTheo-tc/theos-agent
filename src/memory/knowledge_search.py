"""FTS5 and hybrid search over the KnowledgeGraph.

Provides full-text search via an external-content FTS5 table backed by
``kg_nodes``, plus an optional hybrid mode that merges FTS and vector
similarity results when embeddings are available.

Design reference: AD5 (rowid join pattern for external-content FTS5).
"""

from __future__ import annotations

import math
import re
from collections import deque
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.memory.knowledge_graph import temporal_decay

# Shared half-life configuration for all scoring paths.
# Used by both _compute_final_score (FTS) and _merge_results (hybrid).
_HALF_LIFE_MAP: dict[str, float] = {
    "task": 30.0,
    "rule": 60.0,
    "research": 90.0,
    "lesson": 120.0,
}
_DEFAULT_HALF_LIFE = 30.0


def _row_decay(row: dict[str, Any]) -> float:
    """Compute temporal decay factor for a KG result row.

    Uses updated_at (falling back to created_at) and per-node-type half-life.
    Shared by FTS scoring and hybrid post-merge scoring.
    """
    decay_ts = row.get("updated_at") or row.get("created_at", "")
    node_type = row.get("node_type", "task")
    half_life = _HALF_LIFE_MAP.get(node_type, _DEFAULT_HALF_LIFE)
    return temporal_decay(decay_ts, half_life) if decay_ts else 0.5


def _temporal_component(row: dict[str, Any]) -> float:
    """Return the temporal score component used in final ranking."""
    return _row_decay(row) * _W_TEMPORAL


def _text_relevance_component(row: dict[str, Any]) -> float:
    """Return the text-side score before temporal weighting.

    This is shared by pure FTS scoring and hybrid merge, so hybrid search
    can apply temporal decay exactly once after combining text and vector
    signals.
    """
    raw_rank = float(row.get("rank", 0.0))
    fts_norm = 1.0 / (1.0 + math.exp(raw_rank)) if raw_rank != 0.0 else 0.0
    importance = float(row.get("importance", 0.5))
    return fts_norm * _W_FTS_RANK + importance * _W_IMPORTANCE


if TYPE_CHECKING:
    from src.memory.knowledge_graph import KnowledgeGraph

# ---------------------------------------------------------------------------
# FTS5 DDL — external-content table + sync triggers
# ---------------------------------------------------------------------------

_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS kg_fts USING fts5(
    title, content, tags, domains,
    content='kg_nodes', content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS kg_fts_ai AFTER INSERT ON kg_nodes BEGIN
    INSERT INTO kg_fts(rowid, title, content, tags, domains)
    VALUES (new.rowid, new.title, new.content, new.tags, new.domains);
END;

CREATE TRIGGER IF NOT EXISTS kg_fts_ad AFTER DELETE ON kg_nodes BEGIN
    INSERT INTO kg_fts(kg_fts, rowid, title, content, tags, domains)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags, old.domains);
END;

CREATE TRIGGER IF NOT EXISTS kg_fts_au AFTER UPDATE ON kg_nodes BEGIN
    INSERT INTO kg_fts(kg_fts, rowid, title, content, tags, domains)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags, old.domains);
    INSERT INTO kg_fts(rowid, title, content, tags, domains)
    VALUES (new.rowid, new.title, new.content, new.tags, new.domains);
END;
"""

# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------

# fts_search final score composition
_W_FTS_RANK = 0.7
_W_IMPORTANCE = 0.2
_W_TEMPORAL = 0.1

# hybrid_search merge weights
_W_VECTOR = 0.7
_W_TEXT = 0.3


class KnowledgeSearch:
    """FTS5 + optional vector hybrid search over ``kg_nodes``."""

    def __init__(self, kg: KnowledgeGraph) -> None:
        self._kg = kg

    # ------------------------------------------------------------------
    # FTS lifecycle
    # ------------------------------------------------------------------

    _vec_available: bool = False

    async def ensure_fts(self) -> None:
        """Create the FTS5 virtual table and sync triggers if not present."""
        db = self._kg._db
        await db.executescript(_FTS_DDL)
        logger.debug("kg_fts: ensured FTS5 table + triggers")

        # Try to load sqlite-vec for vector search support
        try:
            import sqlite_vec

            self._vec_available = await db.load_extension(sqlite_vec.loadable_path())
            if self._vec_available:
                logger.debug("sqlite-vec loaded — vector search enabled")
        except ImportError:
            logger.debug("sqlite-vec not installed — vector search disabled")
            self._vec_available = False

    async def rebuild_fts(self) -> None:
        """Drop and repopulate FTS content from ``kg_nodes``.

        Useful after bulk inserts (e.g. migration) where triggers were
        not yet in place or the FTS index may be stale.
        """
        db = self._kg._db
        # The special 'rebuild' command re-reads from the content table.
        await db.execute("INSERT INTO kg_fts(kg_fts) VALUES ('rebuild')", ())
        logger.debug("kg_fts: rebuilt FTS index from kg_nodes")

    # ------------------------------------------------------------------
    # FTS search (AD5 rowid join)
    # ------------------------------------------------------------------

    async def fts_search(
        self,
        query: str,
        *,
        node_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Full-text search with rowid join, filtered by ``node_type``.

        Returns dicts with all ``kg_nodes`` columns plus ``final_score``.
        Superseded nodes are excluded.
        """
        if not query or not query.strip():
            return []

        fts_query = _sanitize_fts_query(query)
        if not fts_query:
            return []

        # Build parameterised SQL
        clauses = ["n.superseded_by IS NULL"]
        params: list[Any] = [fts_query]

        if node_type is not None:
            clauses.append("n.node_type = ?")
            params.append(node_type)

        params.append(limit)

        where = " AND ".join(clauses)
        sql = (
            "SELECT n.*, f.rank "
            "FROM kg_fts f "
            "JOIN kg_nodes n ON f.rowid = n.rowid "
            f"WHERE kg_fts MATCH ? AND {where} "
            "ORDER BY f.rank "
            "LIMIT ?"
        )

        db = self._kg._db
        rows = await db.fetchall(sql, tuple(params))
        if not rows:
            return []

        # row_factory=aiosqlite.Row is set in KG.connect(), so dict(row) works
        results: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            d["final_score"] = _compute_final_score(d)
            results.append(d)

        # Re-sort by final_score descending (FTS rank is negative — lower is better —
        # but our composite score is positive — higher is better).
        results.sort(key=lambda r: r["final_score"], reverse=True)
        return results

    # ------------------------------------------------------------------
    # Hybrid search (FTS + vector)
    # ------------------------------------------------------------------

    async def hybrid_search(
        self,
        query: str,
        *,
        query_embedding: list[float],
        limit: int = 10,
        node_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Merge FTS and vector search results.

        If the KG has no vector support (``_vec_available`` is False) or
        ``query_embedding`` is empty, falls back to FTS-only.
        """
        # Always run FTS
        fts_results = await self.fts_search(query, node_type=node_type, limit=limit * 3)

        # Attempt vector search
        vec_results: list[dict[str, Any]] = []
        if query_embedding and self._vec_available:
            try:
                vec_results = await self._vector_search(
                    query_embedding,
                    node_type=node_type,
                    limit=limit * 3,
                )
            except Exception:
                logger.opt(exception=True).debug("Vector search failed, using FTS only")

        if not vec_results:
            return fts_results[:limit]

        # --- Merge ---
        merged = _merge_results(fts_results, vec_results)
        return merged[:limit]

    # ------------------------------------------------------------------
    # Internal: vector search
    # ------------------------------------------------------------------

    async def _vector_search(
        self,
        query_embedding: list[float],
        *,
        node_type: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Cosine-similarity search via sqlite-vec (if loaded).

        Queries ``kg_nodes.embedding`` directly using ``vec_distance_cosine``.
        Returns dicts with ``kg_nodes`` columns + ``vec_score``.
        """
        if not self._vec_available:
            return []

        import struct

        db = self._kg._db
        blob = struct.pack(f"{len(query_embedding)}f", *query_embedding)

        type_filter = ""
        params: list[Any] = [blob]
        if node_type is not None:
            type_filter = " AND n.node_type = ?"
            params.append(node_type)
        params.append(limit)

        sql = (
            "SELECT n.*, vec_distance_cosine(n.embedding, ?) AS distance "
            "FROM kg_nodes n "
            "WHERE n.embedding IS NOT NULL AND n.superseded_by IS NULL"
            f"{type_filter} "
            "ORDER BY distance ASC "
            "LIMIT ?"
        )

        rows = await db.fetchall(sql, tuple(params))
        if not rows:
            return []

        results: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            distance = d.pop("distance", 1.0)
            d["vec_score"] = round(max(0.0, 1.0 - float(distance)), 4)
            d["final_score"] = _compute_final_score(d, vec_score=d["vec_score"])
            results.append(d)

        return results

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    async def detect_conflicts(
        self, node_id: str, *, similarity_threshold: float = 0.85
    ) -> list[str]:
        """Find and supersede conflicting nodes. Returns IDs of superseded nodes."""
        node = await self._kg.get_node(node_id)
        if not node:
            return []
        node_type = node["node_type"]

        if node.get("embedding") and self._vec_available:
            superseded = await self._detect_vector_conflicts(
                node_id=node_id,
                node_type=node_type,
                embedding=_unpack_embedding(node["embedding"]),
                similarity_threshold=similarity_threshold,
            )
            if superseded:
                return superseded

        if node.get("embedding"):
            return []

        return await self._detect_jaccard_conflicts(
            node_id=node_id,
            node_type=node_type,
            node_text=_node_text(node),
        )

    async def _detect_vector_conflicts(
        self,
        *,
        node_id: str,
        node_type: str,
        embedding: list[float],
        similarity_threshold: float,
    ) -> list[str]:
        superseded: list[str] = []
        candidates = await self._vector_search(embedding, limit=10, node_type=node_type)
        for candidate in candidates:
            candidate_id = candidate.get("id")
            if not candidate_id or candidate_id == node_id or candidate.get("superseded_by"):
                continue
            if _vector_similarity(candidate) >= similarity_threshold:
                await self._kg.supersede(candidate_id, node_id)
                superseded.append(candidate_id)
        return superseded

    async def _detect_jaccard_conflicts(
        self,
        *,
        node_id: str,
        node_type: str,
        node_text: str,
    ) -> list[str]:
        node_tokens = set(_tokenize(node_text))
        if not node_tokens:
            return []

        superseded: list[str] = []
        same_type = await self._kg.list_nodes(node_type=node_type, limit=50)
        for candidate in same_type:
            candidate_id = candidate.get("id")
            if not candidate_id or candidate_id == node_id or candidate.get("superseded_by"):
                continue

            candidate_text = _node_text(candidate)
            jaccard = _jaccard(node_tokens, set(_tokenize(candidate_text)))
            if jaccard <= 0.6:
                continue

            if node_type == "rule" and _is_antonym_conflict(node_text, candidate_text):
                await self._kg.add_edge(node_id, candidate_id, "conflicts_with")
            else:
                await self._kg.supersede(candidate_id, node_id)
                superseded.append(candidate_id)
        return superseded

    # ------------------------------------------------------------------
    # Graph traversal
    # ------------------------------------------------------------------

    async def get_subgraph(self, root_id: str, *, max_depth: int = 3) -> dict[str, Any]:
        """BFS traversal returning {nodes: [...], edges: [...]}."""
        visited: set[str] = set()
        nodes: list[dict] = []
        edges: list[dict] = []
        queue: deque[tuple[str, int]] = deque([(root_id, 0)])
        while queue:
            cid, depth = queue.popleft()
            if cid in visited:
                continue
            visited.add(cid)
            node = await self._kg.get_node(cid)
            if node:
                nodes.append(node)
            if depth >= max_depth:
                continue
            for edge in await self._kg.find_related(cid):
                edges.append(edge)
                if edge["to_id"] not in visited:
                    queue.append((edge["to_id"], depth + 1))
            inbound = await self._kg.find_related_inbound(cid)
            for edge in inbound:
                edges.append(edge)
                if edge["from_id"] not in visited:
                    queue.append((edge["from_id"], depth + 1))
        return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Helpers (module-private)
# ---------------------------------------------------------------------------


def _sanitize_fts_query(query: str) -> str:
    """Turn a user query into a safe FTS5 MATCH expression.

    Strips operators that might cause parse errors and wraps each token
    in double quotes so punctuation / CJK chars are handled gracefully.
    """
    # Remove FTS5 operators and special chars
    cleaned = query.replace('"', " ").replace("'", " ")
    for ch in ("(", ")", "*", ":", "^", "{", "}", "[", "]"):
        cleaned = cleaned.replace(ch, " ")

    tokens = cleaned.split()
    if not tokens:
        return ""

    # Wrap each token in quotes for safety; join with implicit AND
    return " ".join(f'"{t}"' for t in tokens if t)


def _compute_final_score(
    row: dict[str, Any],
    *,
    vec_score: float | None = None,
) -> float:
    """Compute the composite score for a search result.

    ``final_score = fts_rank_norm * 0.7 + importance * 0.2 + temporal_decay * 0.1``

    When a ``vec_score`` is provided (hybrid mode), the vector component
    is blended in via the merge step, not here.
    """
    score = _text_relevance_component(row) + _temporal_component(row)
    return round(score, 4)


def _merge_results(
    fts_results: list[dict[str, Any]],
    vec_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge FTS and vector results using weighted reciprocal-rank fusion.

    ``merged_score = vector_weight * vec_final + text_weight * fts_final``

    Deduplicates by node ``id``.
    """
    by_id: dict[str, dict[str, Any]] = {}

    # Index FTS results
    for r in fts_results:
        nid = r.get("id", "")
        if not nid:
            continue
        by_id[nid] = {
            **r,
            "_text_score": _text_relevance_component(r),
            "_vec_score": 0.0,
        }

    # Merge vector results
    for r in vec_results:
        nid = r.get("id", "")
        if not nid:
            continue
        if nid in by_id:
            by_id[nid]["_vec_score"] = r.get("vec_score", 0.0)
        else:
            by_id[nid] = {
                **r,
                "_text_score": 0.0,
                "_vec_score": r.get("vec_score", 0.0),
            }

    # Compute blended score with a single temporal component applied post-merge.
    for entry in by_id.values():
        text_s = entry.pop("_text_score", 0.0)
        vec_s = entry.pop("_vec_score", 0.0)
        raw_score = _W_VECTOR * vec_s + _W_TEXT * text_s
        entry["final_score"] = round(raw_score + _temporal_component(entry), 4)

    merged = sorted(by_id.values(), key=lambda r: r["final_score"], reverse=True)
    return merged


def _node_text(node: dict[str, Any]) -> str:
    return f"{node.get('title', '')} {node.get('content', '')}"


def _unpack_embedding(blob: bytes) -> list[float]:
    import struct

    n_floats = len(blob) // 4
    return list(struct.unpack(f"{n_floats}f", blob))


def _vector_similarity(candidate: dict[str, Any]) -> float:
    value = candidate.get("vec_score", candidate.get("score", 0.0))
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase word tokens."""
    return re.findall(r"\w+", text.lower())


def _is_antonym_conflict(text_a: str, text_b: str) -> bool:
    """Detect whether two texts contain antonym pairs suggesting a conflict."""
    antonym_pairs = [
        ("always", "never"),
        ("\u4f18\u5148", "\u907f\u514d"),
        ("must", "must not"),
        ("should", "should not"),
        ("\u5fc5\u987b", "\u4e0d\u8981"),
        ("\u9700\u8981", "\u4e0d\u9700\u8981"),
        ("enable", "disable"),
        ("add", "remove"),
    ]
    a, b = text_a.lower(), text_b.lower()
    for pos, neg in antonym_pairs:
        if (pos in a and neg in b) or (neg in a and pos in b):
            return True
    return False
