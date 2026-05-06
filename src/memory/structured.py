"""Structured memory store for task- and domain-level knowledge objects.

KG-backed implementation. Persists nodes (tasks, rules, research notes) in a
SQLite knowledge graph and uses FTS5 for search. Legacy JSON files are migrated
on first access via ``_migrate_json_to_kg()``.

This is a pure structured backend. It does NOT write markdown files
(MEMORY.md / HISTORY.md) -- that responsibility belongs to the caller
(MemoryHandler.persist_structured_memory).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from src.memory.embeddings import EmbeddingProvider

from src.memory.knowledge_graph import KnowledgeGraph, compute_importance
from src.memory.knowledge_search import KnowledgeSearch
from src.memory.mmr import mmr_rerank
from src.memory.structured_models import (
    derive_remembered_note,
    extract_rules,
    extract_source_refs,
    first_sentence,
    is_noise_response,
    is_research_hint,
    normalize_rule,
)
from src.utils.tokenize import tokenize_query

_NODE_TYPE_ALIASES = {"research_note": "research"}
_DISPLAY_TYPE_ALIASES = {"research": "research_note"}
_TASK_METADATA_FIELDS = (
    "session_key",
    "status",
    "user_message",
    "tools_used",
    "routed_skills",
    "selected_primary",
    "usage",
    "duration_ms",
    "source_refs",
    "artifacts",
    "tests",
    "is_latest_success",
    "superseded_by",
    "superseded_at",
    "response_summary",
)
_RULE_METADATA_FIELDS = (
    "rule_text",
    "selected_primary",
    "source_task_ids",
    "occurrence_count",
    "first_seen_at",
    "last_seen_at",
    "confidence",
)
_RESEARCH_METADATA_FIELDS = (
    "task_memory_id",
    "session_key",
    "summary",
    "source_refs",
)


@dataclass
class RecordTaskResult:
    """Describes what happened when a task was recorded.

    The caller uses this to decide whether markdown side effects
    (MEMORY.md remember, HISTORY.md append) are needed.
    """

    task_id: str
    rule_ids: list[str] = field(default_factory=list)
    research_id: str | None = None
    superseded_task_ids: list[str] = field(default_factory=list)
    remember_directive: str | None = None  # set if user said "remember/记住"
    history_entry: str | None = None  # set if task succeeded and needs HISTORY.md append


@dataclass(frozen=True)
class _TaskWrite:
    task_id: str
    title: str
    domains: list[str]
    skills: list[str]
    tags: list[str]
    artifacts: list[str]
    tests: list[str]
    source_refs: list[str]
    metadata: dict[str, Any]
    created_at: str


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _coerce_metadata(value: Any) -> dict[str, Any]:
    """Return parsed metadata dict from KG rows, tolerating legacy bad JSON."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean_deduped_strings(items: list[Any] | None) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        if item is None:
            continue
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def _legacy_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return _clean_deduped_strings(value)
    if isinstance(value, str):
        return _clean_deduped_strings([value])
    return []


def _legacy_metadata(data: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: data[key] for key in fields if key in data}


def _legacy_int(value: Any, default: int = 1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _legacy_id(data: dict[str, Any], path: Path) -> str:
    value = data.get("id", path.stem)
    return path.stem if value is None else str(value)


def _legacy_ref(value: Any) -> str | None:
    if value is None:
        return None
    ref = str(value).strip()
    return ref or None


def _legacy_json_files(directory: Path) -> list[tuple[Path, dict[str, Any]]]:
    if not directory.is_dir():
        return []

    payloads: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            payloads.append((path, data))
    return payloads


def _search_node_types(object_type: str) -> list[str | None]:
    if object_type == "all":
        return [None]
    return [_NODE_TYPE_ALIASES.get(object_type, object_type)]


def _split_domains(value: Any) -> list[str]:
    if isinstance(value, str):
        return [domain for domain in value.split(",") if domain]
    if isinstance(value, list):
        return [str(domain) for domain in value if domain]
    return []


def _domain_preference_boost(
    *,
    prefer_domain: str | None,
    domains: list[str],
    selected_primary: str | None,
) -> float:
    if not prefer_domain:
        return 0.0

    preferred = prefer_domain.lower()
    lowered = [domain.lower() for domain in domains]
    boost = 0.0
    if preferred in lowered:
        boost += 3.0
    elif any(domain.startswith(preferred.split("/", 1)[0] + "/") for domain in lowered):
        boost += 1.0
    if selected_primary and str(selected_primary).lower() == preferred:
        boost += 2.0
    return boost


def _search_result_from_row(
    row: dict[str, Any],
    *,
    prefer_domain: str | None,
) -> dict[str, Any]:
    node_type_raw = row.get("node_type", "")
    display_type = _DISPLAY_TYPE_ALIASES.get(node_type_raw, node_type_raw)
    meta = _coerce_metadata(row.get("metadata"))
    domains = _split_domains(row.get("domains", ""))
    selected_primary = meta.get("selected_primary")
    score = float(row.get("final_score", 0.0)) + _domain_preference_boost(
        prefer_domain=prefer_domain,
        domains=domains,
        selected_primary=selected_primary,
    )

    title = row.get("title", "")[:200]
    summary = ""
    if node_type_raw == "task":
        summary = str(meta.get("user_message", ""))[:500]
    elif node_type_raw == "rule":
        summary = f"domains={', '.join(domains)} occurrences={meta.get('occurrence_count', 0)}"
    elif node_type_raw == "research":
        summary = str(meta.get("summary", ""))[:500]

    return {
        "object_type": display_type,
        "id": row.get("id"),
        "title": title,
        "summary": summary,
        "score": round(score, 2),
        "created_at": row.get("created_at", ""),
        "domains": domains,
        "selected_primary": selected_primary,
    }


class StructuredMemoryStore:
    """Persist structured knowledge objects in a KnowledgeGraph (SQLite).

    The public API surface is preserved from the legacy JSON implementation,
    but all mutating / querying methods are now ``async``.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self._kg: KnowledgeGraph | None = None
        self._search: KnowledgeSearch | None = None
        self._embedding_provider: EmbeddingProvider | None = None

    # -- lifecycle -----------------------------------------------------------

    async def ensure_kg(self, embedding_config: Any = None) -> None:
        """Lazily initialise KG + FTS + optional embedding provider.

        Parameters
        ----------
        embedding_config:
            Optional ``EmbeddingConfig`` (or duck-typed object with
            ``provider``, ``model``, ``base_url``, ``api_key``,
            ``dimensions`` attrs).  When provided and ``provider != "none"``,
            an :class:`EmbeddingProvider` is created for hybrid search and
            background node embedding.  Safe to call multiple times.
        """
        if self._kg is not None:
            return

        # NOTE: uses separate kg.db (not shared theos.db) to avoid
        # row_factory side effects on existing Database callers. AD1 deviation
        # documented — can be merged into theos.db once Database supports
        # per-connection row_factory.
        db_path = self.workspace / "memory" / "kg.db"
        kg = KnowledgeGraph(db_path)
        await kg.connect()

        search = KnowledgeSearch(kg)
        await search.ensure_fts()

        # Migrate legacy JSON if present AND KG is empty (idempotent)
        legacy_dir = self.workspace / "memory" / "structured"
        tasks_dir = legacy_dir / "tasks"
        if tasks_dir.is_dir() and any(tasks_dir.glob("*.json")) and await kg.count() == 0:
            await self._migrate_json_to_kg(kg, search, legacy_dir)

        self._kg = kg
        self._search = search

        # Optional: init embedding provider for hybrid search
        if embedding_config:
            try:
                from src.memory.embeddings import create_embedding_provider

                self._embedding_provider = create_embedding_provider(embedding_config)
            except Exception:
                logger.opt(exception=True).debug("Failed to init embedding provider")
                self._embedding_provider = None

    async def close(self) -> None:
        """Close the KG database connection."""
        if self._kg is not None:
            await self._kg.close()
            self._kg = None
            self._search = None

    # -- background embedding ------------------------------------------------

    async def _embed_node(self, node_id: str) -> None:
        """Background-embed a single node. Safe to fire-and-forget."""
        if not self._embedding_provider or self._kg is None:
            return
        try:
            node = await self._kg.get_node(node_id)
            if not node:
                return
            text = f"{node['title']} {node['content']}"
            embedding = await self._embedding_provider.embed_one(text)
            await self._kg.set_embedding(node_id, embedding, self._embedding_provider.name())
        except Exception:
            logger.opt(exception=True).debug("Failed to embed node {}", node_id)

    # -- record_task ---------------------------------------------------------

    async def record_task(
        self,
        *,
        session_key: str,
        user_message: str,
        response: str,
        tools_used: list[str],
        routed_skills: list[str],
        routing_domains: list[str],
        selected_primary: str | None = None,
        usage: dict[str, int] | None = None,
        duration_ms: float | None = None,
        artifacts: list[str] | None = None,
        tests: list[str] | None = None,
        status: str = "success",
    ) -> RecordTaskResult:
        """Persist task memory and any derived structured knowledge.

        Returns a ``RecordTaskResult`` describing what was persisted.  The
        caller is responsible for acting on ``remember_directive`` and
        ``history_entry`` (e.g. writing to MEMORY.md / HISTORY.md).
        """
        await self.ensure_kg()
        assert self._kg is not None  # for type narrowing
        assert self._search is not None

        task = self._build_task_write(
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
        await self._add_task_node(task, user_message=user_message)

        # Supersede related older tasks
        superseded = await self._supersede_related_tasks(
            task_id=task.task_id,
            status=status,
            user_message=user_message,
            source_refs=task.source_refs,
            artifacts=task.artifacts,
            selected_primary=selected_primary,
            created_at=task.created_at,
        )

        history_entry = self._build_task_history_entry(
            task_id=task.task_id,
            title=task.title,
            domains=task.domains,
            status=status,
        )

        rule_ids = await self._record_rules(
            response=response,
            status=status,
            domains=task.domains,
            selected_primary=selected_primary,
            task_id=task.task_id,
            seen_at=task.created_at,
        )

        # Remember directive
        remember_directive = (
            derive_remembered_note(user_message, response) if status == "success" else None
        )

        research_id = await self._record_research_note(
            session_key=session_key,
            user_message=user_message,
            response=response,
            task_id=task.task_id,
            domains=task.domains,
            skills=task.skills,
            source_refs=task.source_refs,
        )

        self._schedule_embeddings(
            [task.task_id, *rule_ids, *([research_id] if research_id else [])]
        )

        return RecordTaskResult(
            task_id=task.task_id,
            rule_ids=rule_ids,
            research_id=research_id,
            superseded_task_ids=superseded,
            remember_directive=remember_directive,
            history_entry=history_entry,
        )

    @staticmethod
    def _build_task_write(
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
        artifacts: list[str] | None,
        tests: list[str] | None,
        status: str,
    ) -> _TaskWrite:
        deduped_tools = _clean_deduped_strings(tools_used)
        deduped_skills = _clean_deduped_strings(routed_skills)
        deduped_domains = _clean_deduped_strings(routing_domains)
        normalized_artifacts = _clean_deduped_strings(artifacts)
        normalized_tests = _clean_deduped_strings(tests)
        source_refs = extract_source_refs(user_message, response, *normalized_artifacts)
        created_at = _now_iso()

        metadata: dict[str, Any] = {
            "session_key": session_key,
            "status": status,
            "user_message": user_message.strip()[:300],
            "tools_used": deduped_tools,
            "routed_skills": deduped_skills,
            "selected_primary": selected_primary,
            "usage": usage or {},
            "duration_ms": duration_ms,
            "source_refs": source_refs,
            "artifacts": normalized_artifacts,
            "tests": normalized_tests,
            "is_latest_success": (status == "success"),
        }

        return _TaskWrite(
            task_id=f"task-{uuid.uuid4().hex[:12]}",
            title=first_sentence(response or user_message),
            domains=deduped_domains,
            skills=deduped_skills,
            tags=deduped_tools + deduped_skills,
            artifacts=normalized_artifacts,
            tests=normalized_tests,
            source_refs=source_refs,
            metadata=metadata,
            created_at=created_at,
        )

    async def _add_task_node(self, task: _TaskWrite, *, user_message: str) -> None:
        assert self._kg is not None
        await self._kg.add_node(
            node_type="task",
            title=task.title,
            content=user_message[:300],
            tags=task.tags,
            domains=task.domains,
            importance=compute_importance("task", user_message[:300]),
            metadata=task.metadata,
            node_id=task.task_id,
        )

    async def _record_rules(
        self,
        *,
        response: str,
        status: str,
        domains: list[str],
        selected_primary: str | None,
        task_id: str,
        seen_at: str,
    ) -> list[str]:
        if status != "success" or is_noise_response(response):
            return []

        assert self._kg is not None
        rule_ids: list[str] = []
        for rule in extract_rules(response):
            rule_id = await self._upsert_rule(
                rule_text=rule,
                routing_domains=domains,
                selected_primary=selected_primary,
                task_id=task_id,
                seen_at=seen_at,
            )
            rule_ids.append(rule_id)
            await self._kg.add_edge(task_id, rule_id, "derived")
        return rule_ids

    async def _record_research_note(
        self,
        *,
        session_key: str,
        user_message: str,
        response: str,
        task_id: str,
        domains: list[str],
        skills: list[str],
        source_refs: list[str],
    ) -> str | None:
        if not self._should_create_research_note(domains, user_message):
            return None

        assert self._kg is not None
        research_id = f"research-{uuid.uuid4().hex[:12]}"
        summary = (response or "").strip()[:3000]
        metadata: dict[str, Any] = {
            "task_memory_id": task_id,
            "session_key": session_key,
            "summary": summary,
            "source_refs": source_refs,
        }
        await self._kg.add_node(
            node_type="research",
            title=first_sentence(user_message, max_chars=160) or "Research task",
            content=summary[:500],
            tags=self._research_tags(domains, skills),
            domains=domains,
            importance=compute_importance("research", summary),
            metadata=metadata,
            node_id=research_id,
        )
        await self._kg.add_edge(task_id, research_id, "produced")
        return research_id

    def _schedule_embeddings(self, node_ids: list[str]) -> None:
        if not self._embedding_provider:
            return
        for node_id in node_ids:
            asyncio.create_task(self._embed_node(node_id))

    # -- search --------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        object_type: str = "all",
        max_results: int = 6,
        prefer_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search structured memory objects.

        Uses hybrid (FTS + vector) search when an embedding provider is
        available; falls back to FTS-only otherwise.
        """
        await self.ensure_kg()
        assert self._search is not None
        assert self._kg is not None

        # Try to embed the query for hybrid search
        query_embedding: list[float] | None = None
        if self._embedding_provider:
            try:
                query_embedding = await self._embedding_provider.embed_one(query)
            except Exception:
                logger.debug("Query embedding failed, falling back to FTS-only")

        all_results: list[dict[str, Any]] = []
        for node_type in _search_node_types(object_type):
            if query_embedding:
                results = await self._search.hybrid_search(
                    query,
                    query_embedding=query_embedding,
                    limit=max_results * 3,
                    node_type=node_type,
                )
            else:
                results = await self._search.fts_search(
                    query,
                    node_type=node_type,
                    limit=max_results * 3,
                )
            all_results.extend(
                _search_result_from_row(row, prefer_domain=prefer_domain) for row in results
            )

        all_results.sort(key=lambda item: (item["score"], item.get("created_at", "")), reverse=True)
        if len(all_results) > 1:
            all_results = mmr_rerank(all_results, k=max(1, int(max_results)), lambda_=0.7)
        return all_results[: max(1, int(max_results))]

    # -- get_* ---------------------------------------------------------------

    async def get_task_memory(self, task_id: str) -> dict[str, Any] | None:
        """Return a task memory by ID, or None if missing."""
        if not task_id:
            return None
        await self.ensure_kg()
        assert self._kg is not None
        return await self._kg.get_node(task_id)

    async def get_domain_rule(self, rule_id: str) -> dict[str, Any] | None:
        """Return a domain rule by ID, or None if missing."""
        if not rule_id:
            return None
        await self.ensure_kg()
        assert self._kg is not None
        return await self._kg.get_node(rule_id)

    async def get_research_note(self, note_id: str) -> dict[str, Any] | None:
        """Return a research note by ID, or None if missing."""
        if not note_id:
            return None
        await self.ensure_kg()
        assert self._kg is not None
        return await self._kg.get_node(note_id)

    # -- _upsert_rule --------------------------------------------------------

    async def _upsert_rule(
        self,
        *,
        rule_text: str,
        routing_domains: list[str],
        selected_primary: str | None,
        task_id: str,
        seen_at: str,
    ) -> str:
        assert self._kg is not None
        domains = list(dict.fromkeys(routing_domains))
        fingerprint = sha1(
            f"{normalize_rule(rule_text)}|{'|'.join(domains)}|{selected_primary or ''}".encode()
        ).hexdigest()[:16]
        rule_id = f"rule-{fingerprint}"

        existing = await self._kg.get_node(rule_id)
        if existing is not None:
            # Update existing rule
            meta = _coerce_metadata(existing.get("metadata"))
            source_task_ids: list[str] = meta.get("source_task_ids", [])
            if task_id not in source_task_ids:
                source_task_ids.append(task_id)
            occurrence_count = meta.get("occurrence_count", 0) + 1
            confidence = min(0.95, round(0.55 + 0.08 * occurrence_count, 2))
            await self._kg.update_node(
                rule_id,
                metadata={
                    "source_task_ids": source_task_ids,
                    "occurrence_count": occurrence_count,
                    "confidence": confidence,
                    "last_seen_at": seen_at,
                },
            )
        else:
            # Create new rule node
            occurrence_count = 1
            confidence = min(0.95, round(0.55 + 0.08 * occurrence_count, 2))
            meta_new: dict[str, Any] = {
                "rule_text": rule_text.strip(),
                "selected_primary": selected_primary,
                "source_task_ids": [task_id],
                "occurrence_count": occurrence_count,
                "first_seen_at": seen_at,
                "last_seen_at": seen_at,
                "confidence": confidence,
            }
            importance = compute_importance("rule", rule_text, occurrence_count)
            await self._kg.add_node(
                node_type="rule",
                title=rule_text.strip(),
                content=rule_text.strip(),
                tags=[],
                domains=domains,
                importance=importance,
                metadata=meta_new,
                node_id=rule_id,
            )

        return rule_id

    # -- _supersede_related_tasks -------------------------------------------

    async def _supersede_related_tasks(
        self,
        *,
        task_id: str,
        status: str,
        user_message: str,
        source_refs: list[str],
        artifacts: list[str],
        selected_primary: str | None,
        created_at: str,
    ) -> list[str]:
        if status != "success":
            return []

        assert self._kg is not None
        assert self._search is not None

        # Gather candidate tasks: FTS first, then direct scan as fallback
        # for Chinese / non-Latin content where FTS5 porter tokenizer is weak.
        fts_results = await self._search.fts_search(user_message[:200], node_type="task", limit=20)
        seen_ids: set[str] = set()
        candidates: list[dict[str, Any]] = []
        for r in fts_results:
            rid = r.get("id", "")
            if rid and rid not in seen_ids:
                seen_ids.add(rid)
                candidates.append(r)
        # Always supplement with direct scan to catch non-FTS-matchable nodes
        direct = await self._kg.list_nodes("task", limit=50, exclude_superseded=True)
        for r in direct:
            rid = r.get("id", "")
            if rid and rid not in seen_ids:
                seen_ids.add(rid)
                candidates.append(r)
        superseded: list[str] = []
        for candidate in candidates:
            cand_id = candidate.get("id", "")
            if not cand_id or cand_id == task_id:
                continue

            meta = _coerce_metadata(candidate.get("metadata"))

            if meta.get("status") != "success":
                continue
            if candidate.get("superseded_by"):
                continue
            if not meta.get("is_latest_success", True):
                continue

            # Check topic overlap
            if not self._same_task_topic_kg(
                user_message=user_message,
                source_refs=source_refs,
                artifacts=artifacts,
                selected_primary=selected_primary,
                existing_meta=meta,
            ):
                continue

            # Supersede
            await self._kg.supersede(cand_id, task_id)
            await self._kg.update_node(
                cand_id,
                metadata={
                    "is_latest_success": False,
                    "superseded_at": created_at,
                },
            )
            superseded.append(cand_id)

        return superseded

    @staticmethod
    def _same_task_topic_kg(
        *,
        user_message: str,
        source_refs: list[str],
        artifacts: list[str],
        selected_primary: str | None,
        existing_meta: dict[str, Any],
    ) -> bool:
        """Check whether two tasks cover the same topic (KG-compatible version)."""
        task_refs = set(source_refs + artifacts)
        existing_refs = set(
            existing_meta.get("source_refs", []) + existing_meta.get("artifacts", [])
        )
        if task_refs and existing_refs and task_refs & existing_refs:
            return True

        current_terms = set(tokenize_query(user_message))
        existing_msg = str(existing_meta.get("user_message", ""))
        existing_terms = set(tokenize_query(existing_msg))
        overlap = current_terms & existing_terms

        current_primary = str(selected_primary or "").strip().lower()
        existing_primary = str(existing_meta.get("selected_primary") or "").strip().lower()
        if current_primary and current_primary == existing_primary and overlap:
            return True

        current_domain = current_primary.split("/", 1)[0] if current_primary else ""
        existing_domain = existing_primary.split("/", 1)[0] if existing_primary else ""
        if current_domain and current_domain == existing_domain and len(overlap) >= 2:
            return True

        return len(overlap) >= 3

    # -- _build_task_history_entry ------------------------------------------

    @staticmethod
    def _build_task_history_entry(
        *,
        task_id: str,
        title: str,
        domains: list[str],
        status: str,
    ) -> str | None:
        """Build a one-line history index entry, or None if task failed.

        Format: ``[YYYY-MM-DD HH:MM] task-id | domains | summary``
        """
        if status != "success":
            return None

        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        domain_str = ", ".join(domains[:2]) or "general"
        title_short = (title or "")[:80]
        return f"[{timestamp}] {task_id} | {domain_str} | {title_short}"

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _should_create_research_note(routing_domains: list[str], user_message: str) -> bool:
        if any(domain.startswith("paper/") for domain in routing_domains):
            return True
        return is_research_hint(user_message)

    @staticmethod
    def _research_tags(routing_domains: list[str], routed_skills: list[str]) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()
        for domain in routing_domains:
            for part in domain.split("/"):
                part = part.strip()
                if part and part not in seen:
                    seen.add(part)
                    tags.append(part)
        for skill in routed_skills:
            if skill not in seen:
                seen.add(skill)
                tags.append(skill)
        return tags[:12]

    # -- migration -----------------------------------------------------------

    async def _migrate_json_to_kg(
        self,
        kg: KnowledgeGraph,
        search: KnowledgeSearch,
        legacy_dir: Path,
    ) -> None:
        """Migrate legacy JSON files to KG nodes and rename the directory."""
        logger.info("Migrating legacy JSON structured memory -> KG: {}", legacy_dir)
        await self._migrate_legacy_tasks(kg, legacy_dir / "tasks")
        await self._migrate_legacy_rules(kg, legacy_dir / "rules")
        await self._migrate_legacy_research_notes(kg, legacy_dir / "research_notes")
        await search.rebuild_fts()
        self._backup_legacy_dir(legacy_dir)
        logger.info("Legacy JSON migrated. Backup at {}", legacy_dir.parent / "structured_backup")

    async def _migrate_legacy_tasks(self, kg: KnowledgeGraph, tasks_dir: Path) -> None:
        for path, data in _legacy_json_files(tasks_dir):
            task_id = _legacy_id(data, path)
            user_message = str(data.get("user_message") or "")
            title = str(data.get("response_summary") or first_sentence(user_message) or task_id)
            content = user_message[:300]
            tools = _legacy_list(data.get("tools_used"))
            skills = _legacy_list(data.get("routed_skills"))

            await kg.add_node(
                node_type="task",
                title=title,
                content=content,
                tags=tools + skills,
                domains=_legacy_list(data.get("routing_domains")),
                importance=compute_importance("task", content),
                metadata=_legacy_metadata(data, _TASK_METADATA_FIELDS),
                node_id=task_id,
            )
            superseded_by = _legacy_ref(data.get("superseded_by"))
            if superseded_by:
                with suppress(Exception):
                    await kg.supersede(task_id, superseded_by)

    async def _migrate_legacy_rules(self, kg: KnowledgeGraph, rules_dir: Path) -> None:
        for path, data in _legacy_json_files(rules_dir):
            rule_id = _legacy_id(data, path)
            rule_text = str(data.get("rule_text") or "")
            occurrence_count = _legacy_int(data.get("occurrence_count"))

            await kg.add_node(
                node_type="rule",
                title=rule_text,
                content=rule_text,
                tags=[],
                domains=_legacy_list(data.get("domains")),
                importance=compute_importance("rule", rule_text, occurrence_count),
                metadata=_legacy_metadata(data, _RULE_METADATA_FIELDS),
                node_id=rule_id,
            )
            for task_id in _legacy_list(data.get("source_task_ids")):
                with suppress(Exception):
                    await kg.add_edge(task_id, rule_id, "derived")

    async def _migrate_legacy_research_notes(
        self,
        kg: KnowledgeGraph,
        research_dir: Path,
    ) -> None:
        for path, data in _legacy_json_files(research_dir):
            note_id = _legacy_id(data, path)
            title = str(data.get("title") or "Research")
            summary = str(data.get("summary") or "")

            await kg.add_node(
                node_type="research",
                title=title,
                content=summary[:500],
                tags=_legacy_list(data.get("tags")),
                domains=_legacy_list(data.get("domains")),
                importance=compute_importance("research", summary),
                metadata=_legacy_metadata(data, _RESEARCH_METADATA_FIELDS),
                node_id=note_id,
            )
            task_id = _legacy_ref(data.get("task_memory_id"))
            if task_id:
                with suppress(Exception):
                    await kg.add_edge(task_id, note_id, "produced")

    @staticmethod
    def _backup_legacy_dir(legacy_dir: Path) -> None:
        backup_dir = legacy_dir.parent / "structured_backup"
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        shutil.move(str(legacy_dir), str(backup_dir))
