"""KG node garbage collection.

Removes superseded nodes older than max_age_days, plus orphan edges.
Runs VACUUM to reclaim space.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger


async def gc_superseded_nodes(workspace: Path, max_age_days: int = 30) -> int:
    """Delete superseded KG nodes older than ``max_age_days``.

    Returns count of nodes deleted. Also deletes dangling edges referencing
    the removed nodes and runs ``VACUUM`` if any deletion happened.
    """
    from src.memory.structured import StructuredMemoryStore

    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()

    store = StructuredMemoryStore(workspace)
    try:
        await store.ensure_kg()
        if store._kg is None:
            return 0

        db = store._kg._db

        # Find candidate ids (superseded + old)
        rows = await db.fetchall(
            "SELECT id FROM kg_nodes WHERE superseded_by IS NOT NULL AND updated_at < ?",
            (cutoff,),
        )
        ids = [r["id"] for r in rows]

        if not ids:
            return 0

        # Delete nodes and any edges referencing them
        for nid in ids:
            await db.execute(
                "DELETE FROM kg_edges WHERE from_id = ? OR to_id = ?",
                (nid, nid),
            )
            await db.execute("DELETE FROM kg_nodes WHERE id = ?", (nid,))

        # Reclaim space
        try:
            await db.execute("VACUUM")
        except Exception:
            logger.opt(exception=True).debug("VACUUM failed (non-fatal)")

        logger.info(
            "KG GC: deleted {} superseded node(s) older than {} days",
            len(ids),
            max_age_days,
        )
        return len(ids)
    finally:
        await store.close()
