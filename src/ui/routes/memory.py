"""Memory browser API routes — KG nodes, search, markdown, instinct."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse


async def _get_kg(request: Request):
    """Get or create a KnowledgeGraph connection for this request."""
    if not hasattr(request.app.state, "_kg"):
        from src.memory.knowledge_graph import KnowledgeGraph

        workspace = request.app.state.app_context.get("workspace")
        if not workspace:
            return None
        kg_path = Path(workspace) / "memory" / "kg.db"
        if not kg_path.parent.exists():
            return None
        kg = KnowledgeGraph(kg_path)
        await kg.connect()
        request.app.state._kg = kg
    return request.app.state._kg


async def memory_nodes_list(request: Request) -> JSONResponse:
    kg = await _get_kg(request)
    if not kg:
        return JSONResponse([])
    node_type = request.query_params.get("type", "rule")
    limit = int(request.query_params.get("limit", "50"))
    nodes = await kg.list_nodes(node_type, limit=limit)
    return JSONResponse(nodes)


async def memory_search(request: Request) -> JSONResponse:
    kg = await _get_kg(request)
    if not kg:
        return JSONResponse([])
    q = request.query_params.get("q", "")
    if not q:
        return JSONResponse([])

    from src.memory.knowledge_search import KnowledgeSearch

    ks = KnowledgeSearch(kg)
    await ks.ensure_fts()
    # Rebuild once per KG connection to catch nodes inserted before FTS triggers.
    if not getattr(request.app.state, "_fts_built", False):
        await ks.rebuild_fts()
        request.app.state._fts_built = True
    node_type = request.query_params.get("type")
    limit = int(request.query_params.get("limit", "20"))
    results = await ks.fts_search(q, node_type=node_type, limit=limit)
    return JSONResponse(results)


async def memory_node_detail(request: Request) -> JSONResponse:
    kg = await _get_kg(request)
    if not kg:
        return JSONResponse({"error": "not found"}, status_code=404)
    node_id = request.path_params["node_id"]
    node = await kg.get_node(node_id)
    if not node:
        return JSONResponse({"error": "not found"}, status_code=404)
    edges_out = await kg.find_related(node_id)
    edges_in = await kg.find_related_inbound(node_id)
    return JSONResponse(
        {
            "node": node,
            "edges": {"outbound": edges_out, "inbound": edges_in},
        }
    )


async def memory_markdown(request: Request) -> JSONResponse:
    workspace = (request.app.state.app_context or {}).get("workspace")
    if not workspace:
        return JSONResponse({"sections": []})

    from src.memory.store import MemoryStore

    store = MemoryStore(Path(workspace))
    text = store.read_long_term()
    sections = MemoryStore.split_sections(text)
    return JSONResponse(
        {
            "sections": [{"title": t, "body": b} for t, b in sections],
        }
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _read_text(path: Path, *, limit: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return text[:limit] if limit is not None else text


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _count_jsonl(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def _tail_jsonl(path: Path, *, limit: int = 8) -> list[dict[str, Any]]:
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def _parse_meta(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    meta: dict[str, str] = {}
    for pair in raw.split():
        if ":" not in pair:
            continue
        key, value = pair.split(":", 1)
        if key:
            meta[key] = value
    return meta


def _parse_rule_file(path: Path) -> dict[str, Any]:
    text = _read_text(path, limit=16_000)
    rules: list[dict[str, Any]] = []
    current_heading = ""
    for line in text.splitlines():
        heading = re.match(r"^#{1,3}\s+(.+)$", line)
        if heading:
            current_heading = heading.group(1).strip()
            continue
        if not line.startswith("- "):
            continue
        body = line[2:].strip()
        match = re.match(r"^\[(?P<id>[^\]]+)\]\s+(?P<text>.*?)(?:\s+<!--\s*(?P<meta>.*?)\s*-->)?$", body)
        if match:
            rules.append(
                {
                    "id": match.group("id"),
                    "text": match.group("text").strip(),
                    "meta": _parse_meta(match.group("meta")),
                    "section": current_heading,
                }
            )
        else:
            rules.append({"id": None, "text": body, "meta": {}, "section": current_heading})
    return {
        "path": str(path),
        "exists": path.is_file(),
        "count": len(rules),
        "rules": rules[:40],
        "raw": text[:4000],
    }


def _extract_markdown_section(path: Path, heading: str) -> str:
    content = _read_text(path)
    if not content:
        return ""
    match = re.search(
        rf"^## {re.escape(heading)}\s*(.*?)(?=^## |\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _core_rules(core_path: Path) -> dict[str, Any]:
    text = _read_text(core_path, limit=12_000)
    rules: list[dict[str, str]] = []
    for line in text.splitlines():
        match = re.match(r"^\d+\.\s+\*\*(.+?)\*\*\s*(.+)$", line.strip())
        if match:
            rules.append({"title": match.group(1).strip(), "text": match.group(2).strip()})
    return {"path": str(core_path), "exists": core_path.exists(), "rules": rules, "raw": text}


def _domain_catalog(workspace: Path | None) -> list[dict[str, Any]]:
    try:
        from src.agent.skills import SkillsLoader

        loader = SkillsLoader(workspace or _repo_root())
        catalog = loader.get_domain_catalog()
    except Exception:
        catalog = {}

    domains_dir = _repo_root() / "instinct" / "domains"
    domains: list[dict[str, Any]] = []
    for label, entry in sorted(catalog.items()):
        path = domains_dir / entry["category"] / f"{entry['domain']}.md"
        domains.append(
            {
                "id": label,
                "category": entry["category"],
                "domain": entry["domain"],
                "keywords": entry.get("keywords", []),
                "skills": entry.get("skills", []),
                "tools": entry.get("tools", []),
                "context": _extract_markdown_section(path, "Context"),
                "path": str(path),
            }
        )
    return domains


def _recent_reflection_events(events_dir: Path, *, limit: int = 8) -> list[dict[str, Any]]:
    if not events_dir.exists():
        return []
    files = sorted(events_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    events: list[dict[str, Any]] = []
    for path in files:
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        events.append(
            {
                "file": path.name,
                "timestamp": data.get("timestamp", ""),
                "session_key": data.get("session_key", ""),
                "status": (data.get("outcome") or {}).get("status", ""),
                "demand_class": (data.get("request") or {}).get("demand_class", ""),
                "summary": (data.get("request") or {}).get("intent_summary", ""),
                "domains": (data.get("routing") or {}).get("domains", []),
                "rule_count": len((data.get("generalization") or {}).get("transferable_rules", [])),
            }
        )
    return events


def _recent_lessons(lessons_dir: Path, *, limit: int = 8) -> list[dict[str, Any]]:
    if not lessons_dir.exists():
        return []
    files = sorted(lessons_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    lessons: list[dict[str, Any]] = []
    for path in files:
        text = _read_text(path, limit=1200)
        title = path.stem
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        lessons.append({"file": path.name, "title": title, "snippet": text[:400]})
    return lessons


def _rank_recall_targets(targets_path: Path) -> list[dict[str, Any]]:
    data = _read_json(targets_path)
    if not isinstance(data, dict):
        return []
    from src.memory.recall_ranking import score_recall_target

    ranked: list[dict[str, Any]] = []
    for target_id, target in data.items():
        if not isinstance(target, dict):
            continue
        scored = score_recall_target(target)
        ranked.append(
            {
                "target_id": target_id,
                "score": scored["score"],
                "components": scored["components"],
                "recall_count": int(target.get("recall_count", 0)),
                "distinct_queries": len(target.get("distinct_query_hashes", [])),
                "distinct_days": len(target.get("distinct_days", [])),
                "last_recalled_at": target.get("last_recalled_at", ""),
                "max_score": target.get("max_score", 0),
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:40]


async def memory_instinct(request: Request) -> JSONResponse:
    workspace_raw = (request.app.state.app_context or {}).get("workspace")
    workspace = Path(workspace_raw) if workspace_raw else None
    repo = _repo_root()
    instinct_root = (workspace / "memory" / "instinct") if workspace else None
    rules_dir = instinct_root / "rules" if instinct_root else None

    active = _parse_rule_file(rules_dir / "ACTIVE.md") if rules_dir else _parse_rule_file(Path(""))
    probation = (
        _parse_rule_file(rules_dir / "PROBATION.md") if rules_dir else _parse_rule_file(Path(""))
    )
    candidates = (
        _parse_rule_file(rules_dir / "CANDIDATES.md") if rules_dir else _parse_rule_file(Path(""))
    )

    live_rules_path = instinct_root / "live_rules.jsonl" if instinct_root else Path("")
    events_dir = instinct_root / "events" if instinct_root else Path("")
    lessons_dir = instinct_root / "lessons" if instinct_root else Path("")
    dreams_dir = instinct_root / "dreams" if instinct_root else Path("")
    targets_path = instinct_root / "recall_targets.json" if instinct_root else Path("")
    journal_path = instinct_root / "recall_journal.jsonl" if instinct_root else Path("")
    memory_events_path = instinct_root / "memory_events.jsonl" if instinct_root else Path("")
    index_path = rules_dir / "index.json" if rules_dir else Path("")
    index = _read_json(index_path)

    dream_count = 0
    if dreams_dir.exists():
        dream_count = sum(1 for item in dreams_dir.iterdir() if item.is_dir())

    framework = {
        "core": _core_rules(repo / "instinct" / "core.md"),
        "domains": _domain_catalog(workspace),
        "scripts": [
            {"name": name, "path": str(repo / "instinct" / "scripts" / name), "exists": (repo / "instinct" / "scripts" / name).exists()}
            for name in ("reflex.js", "reflect.js", "evolve.js")
        ],
    }
    runtime = {
        "path": str(instinct_root) if instinct_root else "",
        "exists": bool(instinct_root and instinct_root.exists()),
        "status": {
            "active_rules": active["count"],
            "probation_rules": probation["count"],
            "candidate_rules": candidates["count"],
            "live_rule_candidates": _count_jsonl(live_rules_path),
            "events": len(list(events_dir.glob("*.json"))) if events_dir.exists() else 0,
            "lessons": len(list(lessons_dir.glob("*.md"))) if lessons_dir.exists() else 0,
            "dream_sessions": dream_count,
            "recall_journal_entries": _count_jsonl(journal_path),
            "recall_targets": len(_read_json(targets_path) or {}),
            "memory_events": _count_jsonl(memory_events_path),
            "last_evolved": index.get("last_evolved", "never") if isinstance(index, dict) else "never",
        },
        "rules": {
            "active": active,
            "probation": probation,
            "candidates": candidates,
        },
        "live_rules": _tail_jsonl(live_rules_path, limit=12),
        "recall": {
            "targets": _rank_recall_targets(targets_path),
            "journal_tail": _tail_jsonl(journal_path, limit=12),
        },
        "events": {
            "recent": _recent_reflection_events(events_dir),
            "memory_tail": _tail_jsonl(memory_events_path, limit=12),
        },
        "lessons": _recent_lessons(lessons_dir),
    }
    return JSONResponse({"framework": framework, "runtime": runtime})
