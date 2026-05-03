"""Unified memory recall service -- single read-side facade.

Owns: prompt memory injection, retrieval policy (mode selection, section
scoring, budget handling, fallback), structured recall, tool-facing index
resolution.

Does NOT own: memory persistence, consolidation, structured writes,
markdown I/O (delegated to MemoryStore), section parsing (MemoryStore.split_sections).

Retrieval sources
-----------------
Retrieval currently draws from two sources:
  1. Markdown long-term memory (MEMORY.md) — via ``MemoryStore``
  2. Structured memory (JSON) — via ``StructuredMemoryStore``

The SQLite short-term tier (``memory_short_term``) is a buffer/audit
layer and is **not** a retrieval source.  If SQLite data is ever surfaced
for recall or indexing, it must go through a normalization seam so that
raw database rows are never leaked directly into prompts.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

# Stop words excluded from section scoring — prevents inflation by "the", "is", etc.
_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "by",
        "from",
        "as",
        "and",
        "or",
        "but",
        "not",
        "this",
        "that",
        "these",
        "those",
        "there",
        "it",
        "its",
        "i",
        "you",
        "we",
        "they",
        "he",
        "she",
        "him",
        "her",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "will",
        "would",
        "shall",
        "can",
        "could",
        "should",
        "may",
        "might",
        "must",
        "if",
        "then",
    }
)


def _tokenize_for_score(text: str) -> list[str]:
    """Lowercase word tokens with stop-word filtering."""
    tokens = re.findall(r"\w+", text.lower())
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]


def _score_section(section: tuple[str, str], query: str) -> float:
    """Score section relevance with title boost + length normalization.

    Prevents long sections from dominating on raw word-count overlap.
    """
    title, body = section
    query_tokens = set(_tokenize_for_score(query))
    if not query_tokens:
        return 0.0

    title_tokens = set(_tokenize_for_score(title))
    body_tokens = _tokenize_for_score(body)

    if not body_tokens and not title_tokens:
        return 0.0

    # Title match is 2x weighted
    title_hits = len(query_tokens & title_tokens) * 2.0

    # Body match with length normalization
    body_set = set(body_tokens)
    body_hits = len(query_tokens & body_set)
    body_len = max(len(body_tokens), 10)  # avoid zero-division and tiny-body inflation
    normalized_body = body_hits / (body_len**0.5) * 10.0  # BM25-like normalization

    return title_hits + normalized_body


def _graded_fallback(sections: list[tuple[str, str]], budget_chars: int) -> str:
    """Fallback when no sections match: pinned first, then recent.

    Returns empty string if no pinned/recent sections — caller decides
    whether to fall back to full MEMORY.md dump.
    """
    pinned: list[tuple[str, str]] = []
    for title, body in sections:
        if title == "_preamble":
            continue
        if "<!-- pinned" in body:
            pinned.append((title, body))

    if pinned:
        selected: list[str] = []
        used = 0
        for title, body in pinned:
            txt = f"## {title}\n{body}"
            if used + len(txt) > budget_chars and selected:
                break
            selected.append(txt)
            used += len(txt)
        return "## Long-term Memory (pinned fallback)\n" + "\n\n".join(selected)

    return ""


if TYPE_CHECKING:
    from src.config.schema import MemoryConfig
    from src.memory.scope import MemoryScopeResolver

# Rough token estimation: ~4 chars per token (matches store.py constant)
_CHARS_PER_TOKEN = 4


class MemoryRecallService:
    """Unified read-side memory facade.

    This is the primary owner of retrieval policy.  It decides between
    full injection and retrieval mode, scores sections by keyword overlap,
    and applies budget constraints.

    Raw markdown I/O is delegated to ``MemoryStore``; section parsing uses
    ``MemoryStore.split_sections()``.
    """

    def __init__(
        self,
        scope: "MemoryScopeResolver",
        *,
        memory_config: Any = None,
    ):
        self._scope = scope
        self._memory_config = memory_config

    # ------------------------------------------------------------------
    # Prompt memory injection (primary retrieval path)
    # ------------------------------------------------------------------

    def get_memory_context(
        self,
        query: str | None = None,
        *,
        workspace: Path | None = None,
        memory_config: "MemoryConfig | Any | None" = None,
    ) -> str:
        """Return memory context for system prompt injection.

        Retrieval policy:
        - **full** mode (or no query): return entire MEMORY.md content.
        - **retrieval** mode with a query: score sections by word overlap,
          select by token budget, optionally fall back to full.

        Parameters
        ----------
        query:
            Current user message used for relevance scoring.
        workspace:
            Override workspace path (defaults to scope workspace).
        memory_config:
            Per-call config override (defaults to instance config).
        """
        from src.memory.store import MemoryStore

        target_workspace = workspace or self._scope.workspace
        store = MemoryStore(target_workspace)
        effective_config = memory_config if memory_config is not None else self._memory_config
        if effective_config is not None and not getattr(effective_config, "enabled", True):
            return ""

        long_term = store.read_long_term()
        if not long_term:
            return ""

        # Default: full injection (backward compatible)
        if not effective_config or effective_config.injection.mode == "full" or not query:
            return f"## Long-term Memory\n{self._annotate_freshness(long_term)}"

        return self._select_markdown_sections(
            long_term=long_term,
            query=query,
            config=effective_config,
            store=store,
        )

    # ------------------------------------------------------------------
    # Freshness annotation
    # ------------------------------------------------------------------

    @staticmethod
    def _annotate_freshness(text: str) -> str:
        """Append freshness warnings to stale or undated memory sections."""
        from src.memory.store import MemoryStore

        sections = MemoryStore.split_sections(text)
        if not sections:
            return text

        parts: list[str] = []
        for title, body in sections:
            if title == "_preamble":
                parts.append(body)
                continue

            section_text = f"## {title}\n{body}"
            age = MemoryStore.extract_section_age_days(body)
            if age is not None and age > 7:
                section_text += (
                    f"\n> \u26a0 Last updated {age} days ago"
                    " \u2014 verify before acting on this information."
                )
            elif age is None:
                section_text += (
                    "\n> \u26a0 No timestamp" " \u2014 verify before acting on this information."
                )
            parts.append(section_text)

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Section-based retrieval (internal)
    # ------------------------------------------------------------------

    @staticmethod
    def _select_markdown_sections(
        long_term: str,
        query: str,
        config: "MemoryConfig",
        store: Any,
    ) -> str:
        """Score and select sections by keyword overlap against *query*.

        Uses ``MemoryStore.split_sections()`` for parsing, then applies
        budget constraints from *config*.
        """
        sections = store.split_sections(long_term)
        if not sections:
            return f"## Long-term Memory\n{long_term}"

        # Score sections with title boost + length normalization (BM25-lite)
        scored: list[tuple[float, str, str]] = []
        for title, body in sections:
            score = _score_section((title, body), query)
            if score > 0:
                scored.append((score, title, body))

        budget_chars = config.injection.max_context_tokens * _CHARS_PER_TOKEN
        if not scored:
            # Graded fallback: pinned sections first, then full dump (if enabled)
            pinned_fallback = _graded_fallback(sections, budget_chars)
            if pinned_fallback:
                return pinned_fallback
            if config.injection.fallback_to_full:
                return f"## Long-term Memory\n{long_term}"
            return ""

        # Sort by relevance (descending), then fill up to budget
        budget = config.injection.max_context_tokens
        scored.sort(key=lambda x: x[0], reverse=True)
        selected: list[str] = []
        used_chars = 0
        budget_chars = budget * _CHARS_PER_TOKEN

        for _score, title, body in scored:
            section_text = f"## {title}\n{body}" if title != "_preamble" else body

            # Annotate freshness per section
            if title != "_preamble":
                age = store.extract_section_age_days(body)
                if age is not None and age > 7:
                    section_text += (
                        f"\n> \u26a0 Last updated {age} days ago"
                        " \u2014 verify before acting on this information."
                    )
                elif age is None:
                    section_text += (
                        "\n> \u26a0 No timestamp"
                        " \u2014 verify before acting on this information."
                    )

            if used_chars + len(section_text) > budget_chars and selected:
                break
            selected.append(section_text)
            used_chars += len(section_text)

        return "## Long-term Memory (filtered)\n" + "\n\n".join(selected)

    # ------------------------------------------------------------------
    # Structured memory recall
    # ------------------------------------------------------------------

    async def build_structured_recall(
        self,
        *,
        session_key: str,
        query: str,
        selected_primary: str | None,
        workspace_override: Path | None = None,
    ) -> str | None:
        """Build structured-memory recall block for context injection."""
        from src.memory.structured import StructuredMemoryStore

        workspace = workspace_override or self._scope.resolve_structured_workspace(session_key)

        store: StructuredMemoryStore | None = None
        try:
            store = StructuredMemoryStore(workspace)
            await store.ensure_kg()
            results = await store.search(
                query,
                max_results=3,
                prefer_domain=selected_primary,
            )
        except Exception:
            logger.opt(exception=True).warning(
                "Structured memory recall failed for session {}", session_key
            )
            return None
        finally:
            if store is not None:
                await store.close()

        if not results:
            return None

        lines = ["[Structured Recall]"]
        for result in results:
            lines.append(
                f"- [{result['object_type']}] {result['title']} "
                f"(id={result['id']}, score={result['score']})"
            )
            if result.get("summary"):
                lines.append(f"  {result['summary']}")
        return "\n".join(lines)
