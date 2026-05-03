# Memory Upgrade v1 Implementation Plan

> **For agentic workers:** Use subagent-driven-development or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让记忆系统从"被动可用"升级为"主动闭环"——prompt 约束、recall telemetry、KG recall ingestion、pre-compaction flush、统一事件流。

**Architecture:** 热路径只做 append-only journal 写入；聚合、KG ingestion、pre-flush 全部走离线 maintenance 或 best-effort background task。不新增真相源，不改 KG schema，不扩展 ToolContext。

**Tech Stack:** Python 3.14, pytest, existing `MemoryStore`/`KnowledgeGraph`/`extract.py`

**Spec:** `docs/dev/specs/2026-04-14-memory-upgrade-v1.md`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `src/agent/context.py:125-132` | Harden prompt recall policy |
| Create | `src/memory/recall_journal.py` | Journal writer: `append_recall_entries()` |
| Create | `src/memory/recall_maintenance.py` | Maintenance: `fold_recall_journal()` + `ingest_recall_to_kg()` |
| Modify | `src/agent/tools/memory_search.py:252-255` | Wire telemetry before return |
| Modify | `src/agent/tools/structured_memory.py:92-105,259-277` | Wire telemetry for StructuredMemorySearchTool + DomainRuleGetTool |
| Modify | `src/memory/rule_cleanup.py:113-117` | Add recall maintenance dispatch to system_event handler |
| Modify | `src/agent/loop_memory.py:686-700` | Pre-compaction flush scheduling |
| Modify | `src/agent/loop.py:1533-1540` | Pass persisted_history to pre-turn maybe_compact |
| Create | `tests/test_recall_journal.py` | Journal + telemetry tests |
| Create | `tests/test_recall_maintenance.py` | Fold + KG ingestion tests |
| Create | `tests/test_pre_compaction_flush.py` | Pre-flush tests |

---

### Task 1: recall_journal.py — Journal Writer

**Files:**
- Create: `src/memory/recall_journal.py`
- Create: `tests/test_recall_journal.py`

- [ ] **Step 1: Write tests for append_recall_entries**

Create `tests/test_recall_journal.py`:

```python
"""Tests for recall journal writer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestAppendRecallEntries:
    @pytest.mark.asyncio
    async def test_creates_journal_file(self, tmp_path):
        from src.memory.recall_journal import append_recall_entries

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="what did we decide",
            results=[
                {"target_kind": "markdown_section", "target_id": None, "path": "MEMORY:Decisions", "score": 0.8, "domains": []},
            ],
        )
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        assert journal.exists()
        entry = json.loads(journal.read_text().strip())
        assert entry["tool"] == "memory_search"
        assert entry["query_hash"]  # SHA1[:12], non-empty
        assert entry["day"]  # YYYY-MM-DD
        assert entry["target_kind"] == "markdown_section"
        assert entry["target_id"] is None

    @pytest.mark.asyncio
    async def test_multiple_results_produce_multiple_lines(self, tmp_path):
        from src.memory.recall_journal import append_recall_entries

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="architecture",
            results=[
                {"target_kind": "kg_rule", "target_id": "rule-abc", "path": "", "score": 0.9, "domains": ["coding"]},
                {"target_kind": "markdown_section", "target_id": None, "path": "MEMORY:Arch", "score": 0.7, "domains": []},
            ],
        )
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        lines = [l for l in journal.read_text().strip().split("\n") if l]
        assert len(lines) == 2
        assert json.loads(lines[0])["target_id"] == "rule-abc"

    @pytest.mark.asyncio
    async def test_domain_rule_get_telemetry(self, tmp_path):
        from src.memory.recall_journal import append_recall_entries

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="domain_rule_get",
            query="rule-xyz",
            results=[
                {"target_kind": "kg_rule", "target_id": "rule-xyz", "path": "", "score": None, "domains": []},
            ],
        )
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        entry = json.loads(journal.read_text().strip())
        assert entry["target_id"] == "rule-xyz"
        assert entry["score"] is None

    @pytest.mark.asyncio
    async def test_empty_results_no_write(self, tmp_path):
        from src.memory.recall_journal import append_recall_entries

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="nothing",
            results=[],
        )
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        assert not journal.exists()

    @pytest.mark.asyncio
    async def test_appends_to_existing(self, tmp_path):
        from src.memory.recall_journal import append_recall_entries

        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        journal.parent.mkdir(parents=True)
        journal.write_text('{"existing": true}\n')

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="test",
            results=[{"target_kind": "markdown_section", "target_id": None, "path": "", "score": 0.5, "domains": []}],
        )
        lines = [l for l in journal.read_text().strip().split("\n") if l]
        assert len(lines) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_recall_journal.py -v
```

- [ ] **Step 3: Implement recall_journal.py**

Create `src/memory/recall_journal.py`:

```python
"""Recall journal — append-only telemetry for memory search events.

Each memory_search / structured_memory_search / domain_rule_get call
appends one line per result to recall_journal.jsonl.  This is the sole
hot-path write; the derived recall_targets.json is built offline by
recall_maintenance.py.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

_JOURNAL_REL = Path("memory") / "instinct" / "recall_journal.jsonl"


def _query_hash(query: str) -> str:
    return hashlib.sha1(query.lower().strip().encode()).hexdigest()[:12]


async def append_recall_entries(
    *,
    workspace: Path,
    session_key: str | None,
    tool: str,
    query: str,
    results: list[dict[str, Any]],
) -> None:
    """Append one JSONL line per result to the recall journal.

    Best-effort: catches all exceptions internally, never raises.
    """
    if not results:
        return

    try:
        journal_path = workspace / _JOURNAL_REL
        journal_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        ts = now.isoformat()
        day = now.strftime("%Y-%m-%d")
        qhash = _query_hash(query)

        lines: list[str] = []
        for r in results:
            entry = {
                "timestamp": ts,
                "session_key": session_key or "",
                "tool": tool,
                "query": query,
                "query_hash": qhash,
                "day": day,
                "target_kind": r.get("target_kind", ""),
                "target_id": r.get("target_id"),
                "path": r.get("path", ""),
                "score": r.get("score"),
                "domains": r.get("domains", []),
            }
            lines.append(json.dumps(entry, ensure_ascii=False))

        with open(journal_path, "a") as f:
            f.write("\n".join(lines) + "\n")

    except Exception:
        logger.opt(exception=True).debug("Failed to write recall journal entry")
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_recall_journal.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/memory/recall_journal.py tests/test_recall_journal.py
git commit -m "feat(memory): add recall journal writer for search telemetry"
```

---

### Task 2: Wire Telemetry into Search Tools

**Files:**
- Modify: `src/agent/tools/memory_search.py:252-255`
- Modify: `src/agent/tools/structured_memory.py:92-105,259-277`

- [ ] **Step 1: Wire MemorySearchTool**

In `src/agent/tools/memory_search.py`, before the final `return "\n\n".join(results_parts)` (line 255), add telemetry dispatch. Need to build `results_for_telemetry` from both markdown and KG results:

```python
        # --- Recall telemetry (best-effort, non-blocking) ---
        if _context and results_parts:
            import asyncio
            from src.memory.recall_journal import append_recall_entries

            telemetry_results: list[dict] = []
            # Markdown results don't have stable IDs
            if source in ("markdown", "all") and md_results:
                for r in md_results:
                    telemetry_results.append({
                        "target_kind": "markdown_section",
                        "target_id": None,
                        "path": f"{r.get('source', '')}:{r.get('section', '')}",
                        "score": r.get("score"),
                        "domains": [],
                    })
            # KG results have stable IDs
            if source in ("knowledge_graph", "all") and enriched:
                for r in enriched:
                    telemetry_results.append({
                        "target_kind": r.get("node_type", ""),
                        "target_id": r.get("id"),
                        "path": "",
                        "score": r.get("final_score"),
                        "domains": r.get("domains", []),
                    })
            if telemetry_results:
                workspace_path = self._resolve_workspace(_context.session_key)
                if workspace_path:
                    asyncio.create_task(append_recall_entries(
                        workspace=workspace_path,
                        session_key=_context.session_key,
                        tool=self.name,
                        query=query,
                        results=telemetry_results,
                    ))
```

Note: `md_results` and `enriched` variables are already in scope from the search logic above. Need to hoist `enriched` and `md_results` to be accessible (they're currently inside conditional blocks — initialize them as `[]` at the top of `execute()`).

**No-op condition:** If `workspace_path` resolves to `None` (e.g., `_resolve_workspace` returns `None` because no workspace is bound for this session), skip telemetry silently — do not attempt to write or log an error. The `if workspace_path:` guard (line 283 above) handles this.

- [ ] **Step 2: Wire StructuredMemorySearchTool**

In `src/agent/tools/structured_memory.py`, after the return value is built (~line 105), before `return`:

```python
        # Recall telemetry
        if _context and results:
            import asyncio
            from src.memory.recall_journal import append_recall_entries

            asyncio.create_task(append_recall_entries(
                workspace=workspace,
                session_key=_context.session_key,
                tool=self.name,
                query=query,
                results=[
                    {
                        "target_kind": r.get("object_type", ""),
                        "target_id": r.get("id"),
                        "path": "",
                        "score": r.get("score"),
                        "domains": r.get("domains", []),
                    }
                    for r in results
                ],
            ))
```

- [ ] **Step 3: Wire DomainRuleGetTool**

In `src/agent/tools/structured_memory.py`, in `DomainRuleGetTool.execute()` (~line 277), after successful retrieval and before return:

```python
        # Recall telemetry
        if _context and rule is not None:
            import asyncio
            from src.memory.recall_journal import append_recall_entries

            asyncio.create_task(append_recall_entries(
                workspace=workspace,
                session_key=_context.session_key,
                tool=self.name,
                query=rule_id,
                results=[{
                    "target_kind": "kg_rule",
                    "target_id": rule_id,
                    "path": "",
                    "score": None,
                    "domains": [],
                }],
            ))
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_recall_journal.py tests/test_memory_store.py -v --tb=short
```

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools/memory_search.py src/agent/tools/structured_memory.py
git commit -m "feat(memory): wire recall telemetry into memory search tools"
```

---

### Task 3: Harden Prompt Recall Policy

**Files:**
- Modify: `src/agent/context.py:125-132`
- Modify: `tests/test_context_prompt_cache.py` (add prompt assertion)

- [ ] **Step 1: Write prompt assertion test**

Add to `tests/test_context_prompt_cache.py`:

```python
def test_memory_tools_prompt_contains_mandatory_policy(tmp_path):
    """Prompt must contain mandatory recall policy, not just a soft hint."""
    from src.agent.context import ContextBuilder

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "MEMORY.md").write_text("# Memory\n")

    ctx = ContextBuilder(workspace=workspace, group_workspace=workspace)
    # Build with memory tools available
    prompt = ctx.build_system_prompt(
        current_message="test",
        has_memory_tools=True,
        memory_config=MagicMock(),
    )
    assert "Mandatory recall policy" in prompt
    assert "MUST call" in prompt
    assert "Do NOT guess or fabricate" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_context_prompt_cache.py::test_memory_tools_prompt_contains_mandatory_policy -v
```

- [ ] **Step 3: Replace prompt text**

Replace lines 125-132 in `src/agent/context.py`:

```python
        if has_memory_tools:
            static.append(
                "# Memory Tools\n\n"
                "You have `memory_search`, `memory_get`, `structured_memory_search`, "
                "`research_note_get`, `task_memory_get`, and `domain_rule_get` tools available.\n\n"
                "**Mandatory recall policy:**\n"
                "- When the user asks about prior work, past decisions, stated preferences, "
                "commitments, or todos — and the injected Memory section above does not "
                "already cover the topic — you MUST call `memory_search` or "
                "`structured_memory_search` BEFORE answering.\n"
                "- Do NOT guess or fabricate historical facts. If memory tools return "
                "nothing, say you don't have that information.\n"
                "- The Memory section above is pre-loaded context; for specific historical "
                "questions beyond its scope, always search first."
            )
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_context_prompt_cache.py -v --tb=short
```

- [ ] **Step 5: Commit**

```bash
git add src/agent/context.py tests/test_context_prompt_cache.py
git commit -m "feat(memory): harden prompt recall policy — mandatory search before historical answers"
```

---

### Task 4: recall_maintenance.py — Fold + KG Ingestion

**Files:**
- Create: `src/memory/recall_maintenance.py`
- Create: `tests/test_recall_maintenance.py`
- Modify: `src/memory/rule_cleanup.py:113-117`

- [ ] **Step 1: Write tests for fold_recall_journal**

Create `tests/test_recall_maintenance.py`:

```python
"""Tests for recall maintenance — journal fold + KG ingestion."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_journal(tmp_path, entries):
    journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    journal.write_text("\n".join(lines) + "\n")
    return journal


class TestFoldRecallJournal:
    def test_fold_creates_targets(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(tmp_path, [
            {"target_kind": "kg_rule", "target_id": "rule-abc", "query_hash": "h1", "day": "2026-04-14", "score": 0.9},
            {"target_kind": "kg_rule", "target_id": "rule-abc", "query_hash": "h2", "day": "2026-04-14", "score": 0.8},
            {"target_kind": "markdown_section", "target_id": None, "query_hash": "h1", "day": "2026-04-14", "score": 0.7},
        ])
        result = fold_recall_journal(tmp_path)
        assert result == 1  # only 1 KG target folded

        targets_path = tmp_path / "memory" / "instinct" / "recall_targets.json"
        assert targets_path.exists()
        targets = json.loads(targets_path.read_text())
        assert "rule-abc" in targets
        assert targets["rule-abc"]["recall_count"] == 2
        assert len(targets["rule-abc"]["distinct_query_hashes"]) == 2
        assert targets["rule-abc"]["max_score"] == 0.9

    def test_fold_incremental_with_checkpoint(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(tmp_path, [
            {"target_kind": "kg_rule", "target_id": "rule-a", "query_hash": "h1", "day": "2026-04-14", "score": 0.9},
        ])
        fold_recall_journal(tmp_path)

        # Append more
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        with open(journal, "a") as f:
            f.write(json.dumps({"target_kind": "kg_rule", "target_id": "rule-a", "query_hash": "h2", "day": "2026-04-15", "score": 0.85}) + "\n")

        fold_recall_journal(tmp_path)
        targets = json.loads((tmp_path / "memory" / "instinct" / "recall_targets.json").read_text())
        assert targets["rule-a"]["recall_count"] == 2
        assert len(targets["rule-a"]["distinct_days"]) == 2

    def test_fold_rebuilds_from_scratch_if_no_checkpoint(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(tmp_path, [
            {"target_kind": "kg_rule", "target_id": "rule-x", "query_hash": "h1", "day": "2026-04-14", "score": 0.5},
        ])
        # Write targets but delete checkpoint — should rebuild
        targets_path = tmp_path / "memory" / "instinct" / "recall_targets.json"
        targets_path.parent.mkdir(parents=True, exist_ok=True)
        targets_path.write_text("{}")

        fold_recall_journal(tmp_path)
        targets = json.loads(targets_path.read_text())
        assert targets["rule-x"]["recall_count"] == 1

    def test_fold_recovers_from_corrupt_checkpoint(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(tmp_path, [
            {"target_kind": "kg_rule", "target_id": "rule-y", "query_hash": "h1", "day": "2026-04-14", "score": 0.6},
        ])
        # Write corrupt checkpoint
        cp = tmp_path / "memory" / "instinct" / "recall_targets.checkpoint.json"
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text("NOT VALID JSON{{{")

        fold_recall_journal(tmp_path)
        targets = json.loads((tmp_path / "memory" / "instinct" / "recall_targets.json").read_text())
        assert targets["rule-y"]["recall_count"] == 1

    def test_fold_recovers_from_missing_targets(self, tmp_path):
        """recall_targets.json deleted but checkpoint exists — rebuild from journal."""
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(tmp_path, [
            {"target_kind": "kg_rule", "target_id": "rule-z", "query_hash": "h1", "day": "2026-04-14", "score": 0.7},
        ])
        # First fold creates both files
        fold_recall_journal(tmp_path)
        # Delete targets but keep checkpoint
        (tmp_path / "memory" / "instinct" / "recall_targets.json").unlink()
        # Append more to journal
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        with open(journal, "a") as f:
            f.write(json.dumps({"target_kind": "kg_rule", "target_id": "rule-z", "query_hash": "h2", "day": "2026-04-15", "score": 0.8}) + "\n")
        # Fold should detect targets missing and rebuild
        fold_recall_journal(tmp_path)
        targets = json.loads((tmp_path / "memory" / "instinct" / "recall_targets.json").read_text())
        assert targets["rule-z"]["recall_count"] >= 1

    def test_fold_caps_hashes_and_days(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        entries = [
            {"target_kind": "kg_rule", "target_id": "rule-big", "query_hash": f"h{i}", "day": f"2026-04-{i:02d}", "score": 0.5}
            for i in range(1, 50)
        ]
        _write_journal(tmp_path, entries)
        fold_recall_journal(tmp_path)
        targets = json.loads((tmp_path / "memory" / "instinct" / "recall_targets.json").read_text())
        assert len(targets["rule-big"]["distinct_query_hashes"]) <= 32
        assert len(targets["rule-big"]["distinct_days"]) <= 16
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_recall_maintenance.py -v
```

- [ ] **Step 3: Implement recall_maintenance.py**

Create `src/memory/recall_maintenance.py`:

```python
"""Recall maintenance — offline fold + KG ingestion.

Runs during the 6h maintenance window. Two steps:
1. fold_recall_journal(): consume journal tail → update recall_targets.json
2. ingest_recall_to_kg(): read targets snapshot → update KG rule metadata

recall_targets.json is a derived cache. If missing, fold rebuilds from
the full journal (fallback full rebuild).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

_INSTINCT_DIR = Path("memory") / "instinct"
_JOURNAL_REL = _INSTINCT_DIR / "recall_journal.jsonl"
_TARGETS_REL = _INSTINCT_DIR / "recall_targets.json"
_CHECKPOINT_REL = _INSTINCT_DIR / "recall_targets.checkpoint.json"

_MAX_QUERY_HASHES = 32
_MAX_DAYS = 16


def fold_recall_journal(workspace: Path) -> int:
    """Fold recall_journal.jsonl tail into recall_targets.json.

    Uses a byte-offset checkpoint for incremental processing.
    Falls back to full rebuild if checkpoint is missing or invalid.

    Returns the number of distinct KG targets updated.
    """
    journal_path = workspace / _JOURNAL_REL
    targets_path = workspace / _TARGETS_REL
    checkpoint_path = workspace / _CHECKPOINT_REL

    if not journal_path.exists():
        return 0

    # Load existing targets (or start fresh)
    targets: dict[str, dict[str, Any]] = {}
    if targets_path.exists():
        try:
            targets = json.loads(targets_path.read_text())
        except (json.JSONDecodeError, OSError):
            targets = {}

    # Load checkpoint
    offset = 0
    if checkpoint_path.exists() and targets:
        try:
            cp = json.loads(checkpoint_path.read_text())
            offset = cp.get("byte_offset", 0)
        except (json.JSONDecodeError, OSError):
            offset = 0
            targets = {}  # checkpoint invalid → full rebuild

    # Read journal from offset
    updated_ids: set[str] = set()
    try:
        with open(journal_path, "r") as f:
            f.seek(offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                tid = entry.get("target_id")
                if not tid or entry.get("target_kind") != "kg_rule":
                    continue

                if tid not in targets:
                    targets[tid] = {
                        "recall_count": 0,
                        "distinct_query_hashes": [],
                        "distinct_days": [],
                        "last_recalled_at": "",
                        "max_score": 0.0,
                    }

                t = targets[tid]
                t["recall_count"] += 1
                t["last_recalled_at"] = entry.get("timestamp", t["last_recalled_at"])

                qh = entry.get("query_hash", "")
                if qh and qh not in t["distinct_query_hashes"]:
                    if len(t["distinct_query_hashes"]) < _MAX_QUERY_HASHES:
                        t["distinct_query_hashes"].append(qh)

                day = entry.get("day", "")
                if day and day not in t["distinct_days"]:
                    if len(t["distinct_days"]) < _MAX_DAYS:
                        t["distinct_days"].append(day)

                score = entry.get("score")
                if score is not None and score > t["max_score"]:
                    t["max_score"] = score

                updated_ids.add(tid)

            new_offset = f.tell()
    except OSError:
        logger.opt(exception=True).warning("Failed to read recall journal")
        return 0

    # Write targets + checkpoint
    try:
        targets_path.parent.mkdir(parents=True, exist_ok=True)
        targets_path.write_text(json.dumps(targets, indent=2, ensure_ascii=False) + "\n")
        checkpoint_path.write_text(json.dumps({"byte_offset": new_offset}) + "\n")
    except OSError:
        logger.opt(exception=True).warning("Failed to write recall targets")

    return len(updated_ids)


async def ingest_recall_to_kg(workspace: Path) -> int:
    """Batch-update KG rule nodes with recall metadata from targets snapshot."""
    targets_path = workspace / _TARGETS_REL
    if not targets_path.exists():
        return 0

    try:
        targets = json.loads(targets_path.read_text())
    except (json.JSONDecodeError, OSError):
        return 0

    from src.memory.structured import StructuredMemoryStore

    updated = 0
    store = StructuredMemoryStore(workspace)
    try:
        await store.ensure_kg()
        if store._kg is None:
            return 0

        for target_id, data in targets.items():
            if not target_id.startswith("rule-"):
                continue
            existing = await store._kg.get_node(target_id)
            if existing is None:
                continue

            meta = existing.get("metadata") or "{}"
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}

            meta["recall_count"] = data.get("recall_count", 0)
            meta["last_recalled_at"] = data.get("last_recalled_at", "")
            meta["distinct_recall_queries"] = len(data.get("distinct_query_hashes", []))
            meta["distinct_recall_days"] = len(data.get("distinct_days", []))

            await store._kg.update_node(target_id, metadata=meta)
            updated += 1
    finally:
        await store.close()

    if updated:
        logger.info("Recall ingestion: updated {} KG rule(s)", updated)
    return updated
```

- [ ] **Step 4: Wire into maintenance cron dispatch**

In `src/memory/rule_cleanup.py`, modify `run_structured_rule_cleanup_event()` (line 113-117):

```python
def run_structured_rule_cleanup_event(workspace: Path, event_name: str) -> str:
    """Execute a supported structured-rule system event."""
    if event_name != STRUCTURED_RULE_CLEANUP_EVENT:
        raise ValueError(f"unsupported system event '{event_name}'")
    cleanup_summary = cleanup_structured_rules(workspace).summary()

    # Recall maintenance: fold journal + ingest to KG
    import asyncio
    from src.memory.recall_maintenance import fold_recall_journal, ingest_recall_to_kg

    folded = fold_recall_journal(workspace)
    kg_updated = asyncio.get_event_loop().run_until_complete(ingest_recall_to_kg(workspace))
    recall_summary = f"Recall maintenance: folded {folded} target(s), ingested {kg_updated} KG rule(s)"

    return f"{cleanup_summary}\n{recall_summary}"
```

- [ ] **Step 5: Run tests**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_recall_maintenance.py tests/test_recall_journal.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/memory/recall_maintenance.py src/memory/rule_cleanup.py tests/test_recall_maintenance.py
git commit -m "feat(memory): add recall maintenance — journal fold + KG rule metadata ingestion"
```

---

### Task 5: Pre-compaction Background Flush (pre-turn path only, in-loop disabled)

**Files:**
- Modify: `src/agent/loop_memory.py:686-700`
- Modify: `src/agent/loop.py:1533-1540`
- Create: `tests/test_pre_compaction_flush.py`

- [ ] **Step 1: Write tests**

Create `tests/test_pre_compaction_flush.py`:

```python
"""Tests for pre-compaction background flush."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSchedulePreCompactionFlush:
    @pytest.mark.asyncio
    async def test_skips_when_flush_disabled(self, tmp_path):
        from src.agent.loop_memory import MemoryHandler

        handler = _make_handler(tmp_path, flush_enabled=False)
        # Should return without doing anything
        await handler._schedule_pre_compaction_flush(
            session_key="test",
            persisted_history=[{"role": "user", "content": f"msg {i}"} for i in range(10)],
            compact_prefix_count=8,
            provider=AsyncMock(),
            model="test",
            workspace=tmp_path,
        )
        # No flush event written
        events = list((tmp_path / "memory" / "instinct" / "events").glob("*-flush.json"))
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_skips_when_gap_too_large(self, tmp_path):
        from src.agent.loop_memory import MemoryHandler

        handler = _make_handler(tmp_path, flush_enabled=True)
        # 60 messages with cursor at 0 → gap > 50, should skip
        await handler._schedule_pre_compaction_flush(
            session_key="test",
            persisted_history=[{"role": "user", "content": f"msg {i}"} for i in range(60)],
            compact_prefix_count=55,
            provider=AsyncMock(),
            model="test",
            workspace=tmp_path,
        )
        events = list((tmp_path / "memory" / "instinct" / "events").glob("*-flush.json"))
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_skips_when_cursor_covers_window(self, tmp_path):
        from src.agent.loop_memory import MemoryHandler

        handler = _make_handler(tmp_path, flush_enabled=True)
        handler._extract_cursor["test"] = 10  # cursor already past compact_prefix
        await handler._schedule_pre_compaction_flush(
            session_key="test",
            persisted_history=[{"role": "user", "content": f"msg {i}"} for i in range(12)],
            compact_prefix_count=8,
            provider=AsyncMock(),
            model="test",
            workspace=tmp_path,
        )
        events = list((tmp_path / "memory" / "instinct" / "events").glob("*-flush.json"))
        assert len(events) == 0


def _make_handler(workspace, flush_enabled=True):
    """Create a minimal MemoryHandler for testing."""
    from src.agent.loop_memory import MemoryHandler
    from unittest.mock import MagicMock

    config = MagicMock()
    config.memory.flush.enabled = flush_enabled
    scope = MagicMock()
    scope.workspace = workspace

    handler = MemoryHandler.__new__(MemoryHandler)
    handler._config = config
    handler._scope = scope
    handler._extract_cursor = {}
    handler._memory_config = config.memory
    return handler
```

- [ ] **Step 2: Add _schedule_pre_compaction_flush to MemoryHandler**

In `src/agent/loop_memory.py`, add the method and call it from `maybe_compact()` before `compact_messages()` (around line 698):

```python
    async def _schedule_pre_compaction_flush(
        self,
        *,
        session_key: str,
        persisted_history: list[dict] | None,
        compact_prefix_count: int,
        provider: Any,
        model: str,
        workspace: Path,
    ) -> None:
        """Best-effort: extract durable facts from about-to-be-compacted messages."""
        if persisted_history is None:
            return
        cfg_flush = self._config.memory.flush
        if not cfg_flush.enabled:
            return

        cursor = max(self._extract_cursor.get(session_key, 0), 0)
        compact_end = min(len(persisted_history), compact_prefix_count)
        if compact_end <= cursor:
            return

        gap_msgs = persisted_history[cursor:compact_end]
        if len(gap_msgs) < 2:
            return
        if len(gap_msgs) > 50:
            logger.warning(
                "Pre-flush gap too large ({}), skipping to avoid stale extraction",
                len(gap_msgs),
            )
            return

        try:
            from src.memory.extract import extract_durable_facts, merge_extracted_facts

            facts = await asyncio.wait_for(
                extract_durable_facts(gap_msgs, provider, model),
                timeout=30.0,
            )
            if facts:
                merged = merge_extracted_facts(MemoryStore(workspace), facts)
                if merged > 0:
                    self._write_flush_event(workspace, session_key, merged)
                    logger.info("Pre-compaction flush: {} facts merged", merged)
        except Exception:
            logger.opt(exception=True).debug("Pre-compaction flush failed (best-effort)")

    @staticmethod
    def _write_flush_event(workspace: Path, session_key: str, facts_merged: int) -> None:
        events_dir = workspace / "memory" / "instinct" / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%dT%H%M%S")
        event = {
            "type": "pre_compaction_flush",
            "timestamp": datetime.now().isoformat(),
            "session_key": session_key,
            "facts_merged": facts_merged,
        }
        (events_dir / f"{ts}-flush.json").write_text(
            json.dumps(event, ensure_ascii=False, indent=2) + "\n"
        )
```

Then in `maybe_compact()`, before `compact_messages()` call (~line 698):

```python
        # Pre-compaction flush: extract durable facts from gap before compaction
        asyncio.create_task(self._schedule_pre_compaction_flush(
            session_key=session_key,
            persisted_history=persisted_history,
            compact_prefix_count=cut - history_start,
            provider=provider,
            model=model,
            workspace=workspace or self._scope.workspace,
        ))
```

- [ ] **Step 3: Pass persisted_history from loop.py pre-turn path**

In `src/agent/loop.py:1533-1540`, update the pre-turn `maybe_compact` lambda to pass `persisted_history=history`:

```python
            maybe_compact=lambda messages: self._memory.maybe_compact(
                messages,
                provider=self.provider,
                model=self.model,
                memory_window=self.memory_window,
                session_key=key,
                workspace=task_workspace,
                persisted_history=history,
            ),
```

And update `maybe_compact()` signature in `loop_memory.py` to accept `persisted_history: list[dict] | None = None`.

**Critical: the in-loop compaction callback (loop.py line 378-382) MUST continue to pass `persisted_history=None`, which disables pre-flush for in-loop compaction.** This is because `_extract_cursor` tracks absolute offsets into `session.messages`, not into the ephemeral `messages` list used during in-loop tool execution. Verify that the in-loop callback does NOT pass `persisted_history`.

- [ ] **Step 4: Run tests**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_pre_compaction_flush.py tests/test_compaction.py -v --tb=short
```

- [ ] **Step 5: Commit**

```bash
git add src/agent/loop_memory.py src/agent/loop.py tests/test_pre_compaction_flush.py
git commit -m "feat(memory): add pre-compaction background flush with soft threshold"
```

---

### Task 6: Lint + Integration Test

- [ ] **Step 1: Run make fmt + make lint**

```bash
cd /Users/tc/code/theos-agent && make fmt && make lint
```

- [ ] **Step 2: Run full affected test suite**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_recall_journal.py tests/test_recall_maintenance.py tests/test_pre_compaction_flush.py tests/test_context_prompt_cache.py tests/test_compaction.py tests/test_memory_store.py -v --tb=short
```

- [ ] **Step 3: Verify acceptance criteria checklist**

1. Context prompt contains "Mandatory recall policy" → check `test_context_prompt_cache.py`
2. recall_journal.jsonl writes on search → check `test_recall_journal.py`
3. recall_targets.json aggregation → check `test_recall_maintenance.py`
4. KG ingestion updates rule metadata → check `test_recall_maintenance.py`
5. Pre-flush event written → check `test_pre_compaction_flush.py`
6. Gap > 50 skipped → check `test_pre_compaction_flush.py`
7. All best-effort, no blocking → all tests confirm no exceptions propagate

- [ ] **Step 4: Commit any lint fixes + push**

```bash
git add -A && git commit -m "chore: lint fixes for memory upgrade v1" && git push
```

**Verification summary:**
- `make fmt`: ran/not ran — reason
- `make lint`: ran/not ran — reason
- `uv run pytest <affected>`: ran/not ran — reason
- `README.md: not needed` — no user-facing command or behavior change
- `BOT.md: not needed` — no development flow or doc structure change
