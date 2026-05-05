"""Memory facade: MEMORY.md (long-term) + HISTORY.md (searchable log).

Owns: markdown file I/O, section parsing, remember directives, GC, compaction.
Does NOT own: consolidation orchestration (MemoryConsolidationService),
FTS index (MemoryIndex), tier pipeline (MemoryTierManager),
structured memory (StructuredMemoryStore), or session persistence (SessionManager).

This store is the markdown backend only.  It has no knowledge of the
SQLite short-term tier (Tier 2).  The consolidation service writes to
this store's files but reads conversation history from ``Session.messages``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from src.providers.base import LLMProvider

_UPDATED_MARKER_RE = re.compile(r"<!-- updated: ([\d-]+) -->")


class MemoryStore:
    """Two-layer memory: MEMORY.md (long-term facts) + HISTORY.md (grep-searchable log)."""

    def __init__(self, workspace: Path):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        tmp = self.memory_file.with_suffix(".md.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(self.memory_file)

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def remember(self, note: str, *, section_title: str = "Remembered Directives") -> bool:
        """Persist a user-issued remember directive into long-term memory."""
        clean = re.sub(r"\s+", " ", note or "").strip(" -\n\t")
        if not clean:
            return False

        long_term = self.read_long_term().strip()
        sections = self.split_sections(long_term) if long_term else []
        if not sections or sections[0][0] != "_preamble":
            sections.insert(0, ("_preamble", "# Long-term Memory"))

        updated_marker = f"<!-- updated: {datetime.now().strftime('%Y-%m-%d')} -->"
        bullet = f"- {clean}"
        rebuilt: list[tuple[str, str]] = []
        found = False

        for title, body in sections:
            if title != section_title:
                rebuilt.append((title, body))
                continue

            found = True
            metadata: list[str] = []
            entries: list[str] = []
            for line in body.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("<!--"):
                    if not stripped.startswith("<!-- updated:"):
                        metadata.append(stripped)
                    continue
                if stripped != bullet:
                    entries.append(stripped)

            rebuilt.append(
                (section_title, "\n".join([updated_marker, *metadata, bullet, *entries]))
            )

        if not found:
            rebuilt.append((section_title, f"{updated_marker}\n{bullet}"))

        self.write_long_term(self._render_sections(rebuilt))
        return True

    def _build_fallback_history_entry(self, messages: list[dict[str, Any]]) -> str:
        """Build a deterministic archive entry when LLM consolidation is unavailable."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        snippets: list[str] = []
        seen: set[str] = set()

        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            text = re.sub(r"\s+", " ", content).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            snippets.append(text[:120])
            if len(snippets) >= 3:
                break

        summary = " | ".join(snippets) if snippets else "No textual content captured."
        return f"[{timestamp}] Archived {len(messages)} messages. Summary: {summary}"

    # ------------------------------------------------------------------
    # Phase D: Compaction (emergency context overflow protection)
    # ------------------------------------------------------------------

    async def compact_messages(
        self,
        messages: list[dict[str, Any]],
        provider: "LLMProvider",
        model: str,
    ) -> str:
        """Summarize a list of messages into a compact text summary.

        Used as emergency compaction when context window approaches limits.
        Returns summary text.
        """
        lines = []
        for m in messages:
            content = m.get("content", "")
            if not content:
                continue
            role = m.get("role", "unknown").upper()
            lines.append(f"{role}: {content[:500]}")

        if not lines:
            return "(no content to summarize)"

        prompt = (
            "Summarize the following conversation excerpt concisely. "
            "Preserve key decisions, facts, and action items. "
            "Output only the summary, no preamble.\n\n" + "\n".join(lines)
        )

        try:
            response = await provider.chat(
                messages=[
                    {"role": "system", "content": "You are a concise summarizer."},
                    {"role": "user", "content": prompt},
                ],
                model=model,
                max_tokens=1024,
            )
            return response.content or "(summary failed)"
        except Exception as exc:
            logger.warning("Compaction LLM call failed: {}", exc)
            return "(compaction summary unavailable)"

    # ------------------------------------------------------------------
    # Phase E: GC / time decay
    # ------------------------------------------------------------------

    def gc(self, max_age_days: int = 90, max_sections: int = 20) -> int:
        """Remove old unpinned memory sections. Returns number of sections removed."""
        long_term = self.read_long_term()
        if not long_term:
            return 0

        sections = self.split_sections(long_term)
        if not sections:
            return 0

        cutoff = datetime.now() - timedelta(days=max_age_days)
        kept: list[tuple[str, str]] = []
        removed = 0

        for title, body in sections:
            # Check for pin
            if "<!-- pinned -->" in body:
                kept.append((title, body))
                continue

            # Check timestamp
            updated = self._extract_updated_at(body)
            if updated is not None and updated < cutoff:
                removed += 1
                logger.info(
                    "Memory GC: removing section '{}' (updated {})",
                    title,
                    updated.strftime("%Y-%m-%d"),
                )
                continue

            kept.append((title, body))

        # Enforce max_sections (keep most recent)
        if len(kept) > max_sections:
            overflow = len(kept) - max_sections
            removed += overflow
            kept = kept[:1] + kept[1 + overflow :]  # keep preamble + most recent

        if removed > 0:
            self.write_long_term(self._render_sections(kept))
            logger.info("Memory GC: removed {} sections, {} remaining", removed, len(kept))

        return removed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def extract_section_age_days(body: str) -> int | None:
        """Extract age in days from ``<!-- updated: YYYY-MM-DD -->``."""
        updated = MemoryStore._extract_updated_at(body)
        if updated is None:
            return None
        return (datetime.now() - updated).days

    @staticmethod
    def _extract_updated_at(body: str) -> datetime | None:
        """Parse the first ``updated`` marker in a memory section."""
        ts_match = _UPDATED_MARKER_RE.search(body)
        if not ts_match:
            return None
        try:
            return datetime.strptime(ts_match.group(1), "%Y-%m-%d")
        except ValueError:
            return None

    @staticmethod
    def split_sections(text: str) -> list[tuple[str, str]]:
        """Split markdown by ## headings. Returns [(title, body)].

        Public API used by ``MemoryRecallService`` for section-based retrieval.
        """
        sections: list[tuple[str, str]] = []
        parts = re.split(r"^(## .+)$", text, flags=re.MULTILINE)

        i = 1
        while i < len(parts) - 1:
            title = parts[i].lstrip("# ").strip()
            body = parts[i + 1].strip()
            sections.append((title, body))
            i += 2

        if parts and parts[0].strip():
            sections.insert(0, ("_preamble", parts[0].strip()))

        return sections

    @staticmethod
    def _render_sections(sections: list[tuple[str, str]]) -> str:
        rendered: list[str] = []
        for title, body in sections:
            clean_body = (body or "").strip()
            if title == "_preamble":
                if clean_body:
                    rendered.append(clean_body)
                continue
            rendered.append(f"## {title}\n{clean_body}".rstrip())
        return "\n\n".join(part for part in rendered if part.strip())
