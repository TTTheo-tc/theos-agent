"""FTS5-based memory index — derived from MEMORY.md + HISTORY.md.

The index is a secondary layer that can be rebuilt from the markdown files at any time.
Provides full-text search for the memory_search / memory_get agent tools.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from loguru import logger

from src.memory.store import MemoryStore
from src.store.database import Database

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    source,
    section,
    content,
    timestamp,
    tokenize='porter unicode61'
);
"""


class MemoryIndex:
    """Full-text search index over MEMORY.md and HISTORY.md."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def ensure_table(self) -> None:
        """Create the FTS5 table if it doesn't exist."""
        await self._db.executescript(_FTS_SCHEMA)

    # ------------------------------------------------------------------
    # Sync: rebuild index from markdown truth files
    # ------------------------------------------------------------------

    async def sync_memory(self, memory_file: Path) -> int:
        """Re-index MEMORY.md sections. Returns number of sections indexed."""
        if not memory_file.exists():
            return 0
        text = memory_file.read_text(encoding="utf-8")
        sections = self._split_sections(text)
        if not sections:
            await self._replace_source_entries("memory", [])
            return 0

        rows = [
            (title, content, ts)
            for title, body, ts in sections
            if (content := body.strip())
        ]

        await self._replace_source_entries("memory", rows)
        logger.debug("memory_fts: synced {} memory sections", len(rows))
        return len(rows)

    async def sync_history(self, history_file: Path) -> int:
        """Re-index HISTORY.md entries. Returns number of entries indexed."""
        if not history_file.exists():
            return 0
        text = history_file.read_text(encoding="utf-8")
        entries = self._split_history(text)
        if not entries:
            await self._replace_source_entries("history", [])
            return 0

        rows = [
            ("", clean_content, ts)
            for ts, content in entries
            if (clean_content := content.strip())
        ]

        await self._replace_source_entries("history", rows)
        logger.debug("memory_fts: synced {} history entries", len(rows))
        return len(rows)

    async def sync_all(self, memory_dir: Path) -> None:
        """Full re-index of both MEMORY.md and HISTORY.md."""
        await self.sync_memory(memory_dir / "MEMORY.md")
        await self.sync_history(memory_dir / "HISTORY.md")

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        max_results: int = 6,
        source: str = "all",
        min_score: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Search memory via FTS5 BM25 ranking.

        Returns list of {source, section, content, timestamp, score}.
        """
        # Sanitize query for FTS5 (remove special chars)
        safe_query = re.sub(r"[^\w\s]", " ", query).strip()
        if not safe_query:
            return []

        source_filter = ""
        params: list[Any] = [safe_query]
        if source != "all":
            source_filter = " AND source = ?"
            params.append(source)
        params.append(max_results)

        rows = await self._db.fetchall(
            "SELECT source, section, content, timestamp, rank"
            " FROM memory_fts"
            f" WHERE memory_fts MATCH ?{source_filter}"
            " ORDER BY rank"
            " LIMIT ?",
            tuple(params),
        )

        results = []
        for row in rows:
            # FTS5 rank is negative (lower = better), convert to positive score
            score = -row[4] if row[4] else 0.0
            if score < min_score:
                continue
            results.append(
                {
                    "source": row[0],
                    "section": row[1],
                    "content": row[2],
                    "timestamp": row[3],
                    "score": round(score, 4),
                }
            )
        return results

    async def get_section(self, section_title: str) -> str | None:
        """Retrieve a specific memory section by title."""
        row = await self._db.fetchone(
            "SELECT content FROM memory_fts WHERE source = 'memory' AND section = ? LIMIT 1",
            (section_title,),
        )
        return row[0] if row else None

    async def _replace_source_entries(
        self,
        source: str,
        rows: list[tuple[str, str, str]],
    ) -> None:
        await self._db.execute("DELETE FROM memory_fts WHERE source = ?", (source,))
        if not rows:
            return
        await self._db.execute_many(
            "INSERT INTO memory_fts (source, section, content, timestamp) VALUES (?, ?, ?, ?)",
            [(source, section, content, timestamp) for section, content, timestamp in rows],
        )

    # ------------------------------------------------------------------
    # Internal parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _split_sections(text: str) -> list[tuple[str, str, str]]:
        """Split MEMORY.md by ## headings. Returns [(title, body, timestamp)]."""
        sections: list[tuple[str, str, str]] = []
        for title, body in MemoryStore.split_sections(text):
            ts_match = re.search(r"<!-- updated: ([\d-]+) -->", body)
            ts = ts_match.group(1) if ts_match else ""
            sections.append((title, body.strip(), ts))
        return sections

    @staticmethod
    def _split_history(text: str) -> list[tuple[str, str]]:
        """Split HISTORY.md into entries. Returns [(timestamp, content)]."""
        entries: list[tuple[str, str]] = []
        # Each entry starts with [YYYY-MM-DD HH:MM]
        pattern = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]", re.MULTILINE)
        matches = list(pattern.finditer(text))

        for idx, match in enumerate(matches):
            ts = match.group(1)
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            content = text[start:end].strip()
            entries.append((ts, content))

        return entries
