"""Personal LLM Wiki API routes."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

WIKI_DIRS = (
    "raw",
    "wiki",
    "wiki/concepts",
    "wiki/entities",
    "wiki/sources",
    "wiki/outputs",
)
TEXT_EXTENSIONS = {".md", ".markdown", ".txt"}
RECORD_CATEGORIES = {"sources", "concepts", "entities", "outputs"}
INDEX_HEADINGS = {
    "sources": "Sources",
    "concepts": "Concepts",
    "entities": "Entities",
    "outputs": "Outputs",
}
DEFAULT_OPERATIONS = {
    "sources": "ingest",
    "concepts": "note",
    "entities": "note",
    "outputs": "query",
}
SINGULAR_TAGS = {
    "sources": "source",
    "concepts": "concept",
    "entities": "entity",
    "outputs": "output",
}


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _wiki_root(request: Request) -> Path | None:
    context = request.app.state.app_context or {}
    configured = context.get("wiki_root")
    if configured:
        return Path(configured).expanduser()
    workspace = context.get("workspace")
    if not workspace:
        return None
    return Path(workspace).expanduser() / "llm-wiki"


def _safe_relative(root: Path, raw_path: str) -> Path | None:
    if not raw_path:
        return None
    root_resolved = root.resolve(strict=False)
    target = (root / raw_path).resolve(strict=False)
    if target == root_resolved:
        return target
    try:
        target.relative_to(root_resolved)
    except ValueError:
        return None
    return target


def _read_text(path: Path, *, limit: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:limit] if limit is not None else text


def _write_if_missing(path: Path, content: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _append_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)


def _index_template(date: str) -> str:
    return f"""---
tags: [wiki/index]
date: {date}
sources: []
---

# Index

## Sources

## Concepts

## Entities

## Outputs
"""


def _log_template(date: str) -> str:
    return f"""---
tags: [wiki/log]
date: {date}
sources: []
---

# Log

## [{date}] init | Created LLM Wiki workspace
"""


def _schema_template(date: str) -> str:
    return f"""# Personal LLM Wiki

This workspace is a local Markdown knowledge base for the user's learning notes.

## Architecture

- `raw/` stores original materials. Treat it as read-only.
- `wiki/` stores generated and maintained Markdown pages.
- `CLAUDE.md` defines this workflow and schema.

## Wiki Structure

- `wiki/index.md` lists every page with a link and one-sentence summary.
- `wiki/log.md` records each operation as `## [YYYY-MM-DD] type | title`.
- `wiki/concepts/` stores one page per important concept.
- `wiki/entities/` stores people, projects, companies, and tools.
- `wiki/sources/` stores one summary page per raw source.
- `wiki/outputs/` stores synthesis, comparisons, and saved answers.

## Workflows

### Ingest

When new material is placed in `raw/`:

1. Read the source without modifying it.
2. Discuss the useful points with the user when the source is ambiguous.
3. Create or update a source summary in `wiki/sources/`.
4. Update related concept and entity pages.
5. Update `wiki/index.md`.
6. Append a log entry to `wiki/log.md`.

### Query

When the user asks a knowledge question:

1. Read `wiki/index.md` first.
2. Read relevant pages in depth.
3. Answer with references to concrete wiki pages.
4. If the answer is reusable, save it in `wiki/outputs/` and update the index.

### Lint

Periodically check for contradictions, orphan pages, missing concept pages, and pages
that should be refreshed from newer sources.

## Markdown Rules

- Use Markdown for every wiki page.
- Link pages with `[[wiki-links]]`.
- Start pages with YAML frontmatter containing `tags`, `date`, and `sources`.
- Keep `raw/` immutable unless the user explicitly asks to change source files.

Initialized: {date}
"""


def _init_wiki(root: Path) -> None:
    for relative in WIKI_DIRS:
        (root / relative).mkdir(parents=True, exist_ok=True)
    date = _today()
    _write_if_missing(root / "wiki" / "index.md", _index_template(date))
    _write_if_missing(root / "wiki" / "log.md", _log_template(date))
    _write_if_missing(root / "CLAUDE.md", _schema_template(date))


def _slugify(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).strip().lower()
    chars: list[str] = []
    last_dash = False
    for char in normalized:
        if char.isalnum():
            chars.append(char)
            last_dash = False
        elif not last_dash:
            chars.append("-")
            last_dash = True
    slug = "".join(chars).strip("-")[:80].strip("-")
    return slug or datetime.now(UTC).strftime("note-%Y%m%d-%H%M%S")


def _unique_page_path(root: Path, category: str, title: str) -> Path:
    directory = root / "wiki" / category
    slug = _slugify(title)
    path = directory / f"{slug}.md"
    suffix = 2
    while path.exists():
        path = directory / f"{slug}-{suffix}.md"
        suffix += 1
    return path


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            cleaned.append(item.strip())
    return cleaned[:24]


def _split_field(value: Any) -> list[str]:
    if isinstance(value, list):
        return _clean_list(value)
    if not isinstance(value, str):
        return []
    pieces = re.split(r"[,，\n]", value)
    return [piece.strip() for piece in pieces if piece.strip()][:24]


def _frontmatter_list(values: list[str]) -> str:
    if not values:
        return "[]"
    escaped = [value.replace("\\", "\\\\").replace('"', '\\"') for value in values]
    return "[" + ", ".join(f'"{value}"' for value in escaped) + "]"


def _page_template(
    *,
    title: str,
    category: str,
    summary: str,
    body: str,
    tags: list[str],
    sources: list[str],
) -> str:
    date = _today()
    tag_values = [f"wiki/{SINGULAR_TAGS[category]}", *tags]
    sections: list[str] = [
        "---",
        f"tags: {_frontmatter_list(tag_values)}",
        f"date: {date}",
        f"sources: {_frontmatter_list(sources)}",
        "---",
        "",
        f"# {title}",
    ]
    if summary:
        sections.extend(["", summary])
    if body:
        sections.extend(["", "## Notes", "", body.rstrip()])
    return "\n".join(sections).rstrip() + "\n"


def _wiki_link(path: Path) -> str:
    relative = path.as_posix()
    if relative.startswith("wiki/"):
        relative = relative[len("wiki/") :]
    if relative.endswith(".md"):
        relative = relative[:-3]
    return relative


def _append_index_entry(root: Path, category: str, path: Path, title: str, summary: str) -> None:
    index_path = root / "wiki" / "index.md"
    heading = INDEX_HEADINGS[category]
    relative = path.relative_to(root).as_posix()
    link = _wiki_link(Path(relative))
    entry = f"- [[{link}|{title}]]"
    if summary:
        entry += f" - {summary}"

    text = _read_text(index_path)
    if relative in text or entry in text:
        return
    if not text:
        text = _index_template(_today())

    lines = text.rstrip().splitlines()
    target = f"## {heading}"
    insert_at: int | None = None
    for index, line in enumerate(lines):
        if line.strip() != target:
            continue
        insert_at = index + 1
        while insert_at < len(lines) and lines[insert_at].strip() and not lines[insert_at].startswith("## "):
            insert_at += 1
        break

    if insert_at is None:
        lines.extend(["", target, entry])
    else:
        lines.insert(insert_at, entry)
    index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _append_log_entry(root: Path, operation: str, title: str, path: Path) -> None:
    relative = path.relative_to(root).as_posix()
    link = _wiki_link(Path(relative))
    entry = f"\n## [{_today()}] {operation} | {title}\n\n- [[{link}|{title}]]\n"
    _append_text(root / "wiki" / "log.md", entry)


def _is_initialized(root: Path) -> bool:
    return (
        (root / "raw").is_dir()
        and (root / "wiki" / "index.md").is_file()
        and (root / "wiki" / "log.md").is_file()
        and (root / "CLAUDE.md").is_file()
    )


def _first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def _summary(text: str) -> str:
    in_frontmatter = False
    for index, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if index == 0 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            continue
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:180]
    return ""


def _file_info(root: Path, path: Path) -> dict[str, Any]:
    rel = path.relative_to(root).as_posix()
    text = _read_text(path, limit=12_000)
    stat = path.stat()
    parts = rel.split("/")
    if rel == "CLAUDE.md":
        category = "schema"
    elif len(parts) == 2 and parts[0] == "wiki":
        category = "base"
    elif len(parts) > 2 and parts[0] == "wiki":
        category = parts[1]
    else:
        category = parts[0]
    return {
        "path": rel,
        "name": path.name,
        "title": _first_heading(text, path.stem),
        "summary": _summary(text),
        "category": category,
        "size": stat.st_size,
        "updatedAt": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    }


def _iter_text_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() in TEXT_EXTENSIONS:
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _log_entries(log_path: Path, *, limit: int = 12) -> list[dict[str, str]]:
    text = _read_text(log_path)
    entries: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.startswith("## ["):
            continue
        raw = line[3:].strip()
        date = ""
        kind = ""
        title = raw
        if "]" in raw:
            date = raw[1 : raw.index("]")]
            rest = raw[raw.index("]") + 1 :].strip()
            if "|" in rest:
                kind, title = [part.strip() for part in rest.split("|", 1)]
            else:
                title = rest
        entries.append({"date": date, "kind": kind, "title": title})
    return entries[-limit:][::-1]


def _counts(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {"raw": 0, "concepts": 0, "entities": 0, "sources": 0, "outputs": 0}
    if (root / "raw").exists():
        counts["raw"] = sum(1 for path in (root / "raw").rglob("*") if path.is_file())
    for key in ("concepts", "entities", "sources", "outputs"):
        directory = root / "wiki" / key
        if directory.exists():
            counts[key] = sum(1 for path in directory.rglob("*.md") if path.is_file())
    return counts


def _status_payload(root: Path | None) -> dict[str, Any]:
    if root is None:
        return {
            "root": "",
            "initialized": False,
            "counts": {},
            "files": [],
            "log": [],
            "indexPreview": "",
            "error": "workspace not configured",
        }

    files = [_file_info(root, path) for path in _iter_text_files(root)] if root.exists() else []
    return {
        "root": str(root),
        "initialized": _is_initialized(root),
        "counts": _counts(root),
        "files": files,
        "log": _log_entries(root / "wiki" / "log.md"),
        "indexPreview": _read_text(root / "wiki" / "index.md", limit=2000),
        "schemaPath": "CLAUDE.md",
    }


async def wiki_status(request: Request) -> JSONResponse:
    return JSONResponse(_status_payload(_wiki_root(request)))


async def wiki_init(request: Request) -> JSONResponse:
    root = _wiki_root(request)
    if root is None:
        return JSONResponse({"error": "workspace not configured"}, status_code=503)
    _init_wiki(root)
    return JSONResponse(_status_payload(root), status_code=201)


async def wiki_page(request: Request) -> JSONResponse:
    root = _wiki_root(request)
    if root is None:
        return JSONResponse({"error": "workspace not configured"}, status_code=503)

    path = _safe_relative(root, request.query_params.get("path", ""))
    if path is None:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if not path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)

    content = _read_text(path, limit=120_000)
    return JSONResponse(
        {
            "path": path.relative_to(root).as_posix(),
            "content": content,
            "truncated": path.stat().st_size > len(content.encode("utf-8")),
        }
    )


async def wiki_record(request: Request) -> JSONResponse:
    root = _wiki_root(request)
    if root is None:
        return JSONResponse({"error": "workspace not configured"}, status_code=503)

    _init_wiki(root)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    category = body.get("category", "sources")
    if category not in RECORD_CATEGORIES:
        return JSONResponse({"error": "invalid category"}, status_code=400)

    title = body.get("title", "")
    if not isinstance(title, str) or not title.strip():
        return JSONResponse({"error": "title required"}, status_code=400)
    title = title.strip()

    summary = body.get("summary", "")
    summary = summary.strip() if isinstance(summary, str) else ""
    content = body.get("body", "")
    content = content.strip() if isinstance(content, str) else ""
    tags = _split_field(body.get("tags", []))
    sources = _split_field(body.get("sources", []))
    operation = body.get("operation") or DEFAULT_OPERATIONS[category]
    operation = operation if isinstance(operation, str) and operation.strip() else DEFAULT_OPERATIONS[category]
    operation = operation.strip().split()[0][:24]

    path = _unique_page_path(root, category, title)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _page_template(
            title=title,
            category=category,
            summary=summary,
            body=content,
            tags=tags,
            sources=sources,
        ),
        encoding="utf-8",
    )
    _append_index_entry(root, category, path, title, summary)
    _append_log_entry(root, operation, title, path)

    return JSONResponse(
        {
            "file": _file_info(root, path),
            "status": _status_payload(root),
        },
        status_code=201,
    )


def _search_snippet(text: str, query: str) -> str:
    lower = text.lower()
    index = lower.find(query.lower())
    if index < 0:
        return _summary(text)
    start = max(0, index - 80)
    end = min(len(text), index + len(query) + 140)
    return " ".join(text[start:end].split())


async def wiki_search(request: Request) -> JSONResponse:
    root = _wiki_root(request)
    if root is None:
        return JSONResponse([])

    query = request.query_params.get("q", "").strip()
    if not query or not root.exists():
        return JSONResponse([])

    results: list[dict[str, Any]] = []
    for path in _iter_text_files(root):
        text = _read_text(path, limit=80_000)
        haystack = f"{path.name}\n{text}".lower()
        if query.lower() not in haystack:
            continue
        info = _file_info(root, path)
        info["snippet"] = _search_snippet(text, query)
        results.append(info)
        if len(results) >= 30:
            break
    return JSONResponse(results)
