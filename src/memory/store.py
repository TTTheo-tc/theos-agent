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
_DEFAULT_PREAMBLE = "# Long-term Memory"
_DEFAULT_DIRECTIVES_SECTION = "Remembered Directives"


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
        with self.history_file.open("a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def remember(self, note: str, *, section_title: str = _DEFAULT_DIRECTIVES_SECTION) -> bool:
        """Persist a user-issued remember directive into long-term memory."""
        clean = re.sub(r"\s+", " ", note or "").strip(" -\n\t")
        if not clean:
            return False

        bullet = f"- {clean}"
        sections = self._read_sections_with_preamble()
        self._merge_bullet(
            sections,
            section_title=section_title,
            bullet=bullet,
            duplicate_mode="promote",
        )

        self.write_long_term(self._render_sections(sections))
        return True

    def merge_bullets(self, items: list[tuple[str, str]]) -> int:
        """Merge bullet entries into MEMORY.md sections.

        Returns the number of new bullets added. Existing bullets are detected
        case-insensitively and skipped.
        """
        if not items:
            return 0

        sections = self._read_sections_with_preamble()
        merged = 0
        for section_title, content in items:
            if self._merge_bullet(
                sections,
                section_title=section_title,
                bullet=f"- {content}",
                duplicate_mode="skip",
            ):
                merged += 1

        if merged:
            self.write_long_term(self._render_sections(sections))
        return merged

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
        provider: LLMProvider,
        model: str,
    ) -> str:
        """Summarize a list of messages into a compact text summary.

        Used as emergency compaction when context window approaches limits.
        Returns summary text.
        """
        lines = [
            f"{m.get('role', 'unknown').upper()}: {content[:500]}"
            for m in messages
            if (content := m.get("content", ""))
        ]

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
            if self._is_pinned(body):
                kept.append((title, body))
                continue

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

        kept, overflow = self._trim_to_max_sections(kept, max_sections=max_sections)
        removed += overflow

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

    def _read_sections_with_preamble(self) -> list[tuple[str, str]]:
        long_term = self.read_long_term().strip()
        sections = self.split_sections(long_term) if long_term else []
        self._ensure_preamble(sections)
        return sections

    @staticmethod
    def _ensure_preamble(sections: list[tuple[str, str]]) -> None:
        if not sections or sections[0][0] != "_preamble":
            sections.insert(0, ("_preamble", _DEFAULT_PREAMBLE))

    @staticmethod
    def _updated_marker() -> str:
        return f"<!-- updated: {datetime.now().strftime('%Y-%m-%d')} -->"

    @staticmethod
    def _find_section_index(sections: list[tuple[str, str]], section_title: str) -> int | None:
        for idx, (title, _body) in enumerate(sections):
            if title == section_title:
                return idx
        return None

    @staticmethod
    def _split_section_body(body: str) -> tuple[list[str], list[str]]:
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
            entries.append(stripped)
        return metadata, entries

    def _merge_bullet(
        self,
        sections: list[tuple[str, str]],
        *,
        section_title: str,
        bullet: str,
        duplicate_mode: str,
    ) -> bool:
        idx = self._find_section_index(sections, section_title)
        if idx is None:
            sections.append((section_title, f"{self._updated_marker()}\n{bullet}"))
            return True

        title, body = sections[idx]
        metadata, entries = self._split_section_body(body)
        if duplicate_mode == "skip" and self._is_duplicate_entry(bullet, entries):
            return False

        if duplicate_mode == "promote":
            entries = [bullet, *(entry for entry in entries if entry != bullet)]
        else:
            entries.append(bullet)
        sections[idx] = (title, "\n".join([self._updated_marker(), *metadata, *entries]))
        return True

    @classmethod
    def _is_duplicate_entry(cls, bullet: str, entries: list[str]) -> bool:
        needle = cls._normalize_entry(bullet)
        return any(cls._normalize_entry(entry) == needle for entry in entries)

    @staticmethod
    def _normalize_entry(entry: str) -> str:
        return re.sub(r"\s+", " ", entry.strip()).lower()

    @staticmethod
    def _is_pinned(body: str) -> bool:
        return "<!-- pinned -->" in body

    @staticmethod
    def _trim_to_max_sections(
        sections: list[tuple[str, str]],
        *,
        max_sections: int,
    ) -> tuple[list[tuple[str, str]], int]:
        if len(sections) <= max_sections:
            return sections, 0

        preamble = sections[:1] if sections and sections[0][0] == "_preamble" else []
        candidates = sections[1:] if preamble else sections
        slots = max(max_sections - len(preamble), 0)
        if slots <= 0:
            return preamble, len(sections) - len(preamble)

        pinned = [
            (idx, section)
            for idx, section in enumerate(candidates)
            if MemoryStore._is_pinned(section[1])
        ]
        pinned_indexes = {idx for idx, _section in pinned}
        remaining_slots = max(slots - len(pinned), 0)
        unpinned = [
            (idx, section) for idx, section in enumerate(candidates) if idx not in pinned_indexes
        ]
        ranked = sorted(
            unpinned,
            key=lambda item: (MemoryStore._section_recency(item[1][1]), item[0]),
            reverse=True,
        )
        selected = pinned_indexes | {idx for idx, _section in ranked[:remaining_slots]}
        kept = [section for idx, section in enumerate(candidates) if idx in selected]
        return [*preamble, *kept], len(candidates) - len(kept)

    @staticmethod
    def _section_recency(body: str) -> datetime:
        return MemoryStore._extract_updated_at(body) or datetime.min

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
