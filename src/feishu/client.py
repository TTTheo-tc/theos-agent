"""Feishu orchestration client -- lean replacement for feishu-sync's FeishuSync.

Wraps the lark-oapi API layer (api.py, api_write.py) with simple JSON file caching,
user token lifecycle management, markdown conversion, and edit support via EditArena.
No diskcache, cachetools, tqdm, or fire dependencies.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from src.feishu import api, api_calendar, api_chat, api_contacts, api_sheets, api_tasks, api_write
from src.feishu.api import ctx_current_token, make_client, parse_url
from src.feishu.feishu2md import feishu2md
from src.feishu.token import get_access_token
from src.feishu.utils import write_json

_DEFAULT_CACHE_DIR = "~/.theos/feishu_cache"
_DEFAULT_TOKEN_DIR = "~/.theos/feishu_tokens"
_TTL_INFO = 86400  # 1 day
_TTL_PAGE = 86400  # 1 day
_TTL_SPACES = 864000  # 10 days
_TTL_USER = 864000  # 10 days


def _ck(prefix: str, token: str) -> str:
    """Filesystem-safe cache key with short hash prefix."""
    h = hashlib.md5(token.encode(), usedforsecurity=False).hexdigest()[:6]
    return f"{prefix}_{h}_{token}"


class FeishuClient:
    """Lean Feishu orchestration client with file-based caching."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        cache_dir: str = _DEFAULT_CACHE_DIR,
        token_dir: str = _DEFAULT_TOKEN_DIR,
        domain: str = "feishu.cn",
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._cache_dir = Path(cache_dir).expanduser()
        self._token_dir = token_dir
        self._domain = domain
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = make_client(app_id, app_secret)
        self._token_deadline: float = 0.0
        self._token_file_mtime: float = 0.0  # track file changes for hot-reload
        self._cached_token: str | None = None  # last loaded user token

    @property
    def base_url(self) -> str:
        """e.g. ``https://feishu.cn`` — no trailing slash."""
        return f"https://{self._domain}"

    def _token_file_changed(self) -> bool:
        """Check if the access_token file was modified since our last read."""
        token_path = Path(self._token_dir).expanduser() / "access_token.json"
        try:
            mtime = token_path.stat().st_mtime
            if mtime > self._token_file_mtime:
                return True
        except OSError:
            pass
        return False

    def ensure_token(self) -> None:
        """Set user token in ContextVar; falls back to app token if unavailable.

        Automatically picks up new tokens written by OAuth callback, CLI auth,
        or cron refresh — no gateway restart needed.

        IMPORTANT: Always sets ctx_current_token even when the cached token is
        still valid, because run_in_executor dispatches to a thread pool where
        each thread has its own ContextVar copy (default=None).  Without the
        unconditional set, a second thread would skip the set (deadline not
        expired) and fall back to the app/tenant token which lacks user-level
        permissions.
        """
        now = time.time()
        need_refresh = now >= self._token_deadline or self._token_file_changed()
        if need_refresh:
            try:
                token = get_access_token(self._app_id, self._app_secret, token_dir=self._token_dir)
                self._token_deadline = now + 25 * 60
                self._token_file_mtime = now
                self._cached_token = token
            except (ValueError, FileNotFoundError) as e:
                logger.warning("Feishu user token unavailable ({}), falling back to app token", e)
                self._token_deadline = now + 60  # retry in 1min
                self._cached_token = None

        # Always set ContextVar — each executor thread needs its own copy
        if self._cached_token:
            ctx_current_token.set(self._cached_token)

    def _cache_get(self, key: str, max_age: int | None = None) -> dict | list | None:
        path = self._cache_dir / f"{key}.json"
        if not path.exists():
            return None
        if max_age is not None and (time.time() - path.stat().st_mtime) > max_age:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _cache_set(self, key: str, data: dict[str, Any] | list[Any]) -> None:
        path = self._cache_dir / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _cache_del(self, key: str) -> None:
        path = self._cache_dir / f"{key}.json"
        if path.exists():
            path.unlink()

    def _call_api(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        self.ensure_token()
        return func(self._client, *args, **kwargs)

    def output_prefix(self, label: str, token: str) -> str:
        """File prefix for cache artefacts (EditArena compatibility)."""
        h = hashlib.md5(token.encode(), usedforsecurity=False).hexdigest()[:6]
        d = self._cache_dir / label
        d.mkdir(parents=True, exist_ok=True)
        return str(d / f"{h}_{token}")

    # --- info_page ---

    def info_page(
        self, url: str, *, max_age: int | None = None, force_refresh: bool = False
    ) -> tuple[str, dict]:
        """Get page metadata. Returns ``(cache_path, info_dict)``."""
        self.ensure_token()
        parsed = parse_url(url)
        wiki_id, doc_id = parsed.get("wiki_id"), parsed.get("doc_id")
        app_token = parsed.get("app_token")
        sheet_token = parsed.get("spreadsheet_token")
        min_token = parsed.get("minutes_token")

        if wiki_id:
            label, tok = "wiki", wiki_id
        elif doc_id:
            label, tok = "docs", doc_id
        elif app_token:
            label, tok = "bitable", app_token
        elif sheet_token:
            label, tok = "sheet", sheet_token
        elif min_token:
            label, tok = "minutes", min_token
        else:
            raise ValueError(f"Cannot parse URL: {url}")

        ck = _ck(f"{label}_info", tok)
        if force_refresh:
            self._cache_del(ck)

        cached = self._cache_get(ck, max_age=max_age or _TTL_INFO)
        if cached is not None:
            return str(self._cache_dir / f"{ck}.json"), cached

        if wiki_id:
            info = api.info_page(self._client, "wiki", wiki_id)
        elif doc_id:
            info = api.info_page(self._client, parsed.get("doc_type", "docx"), doc_id)
        elif app_token:
            info = self._info_bitable(url, app_token)
        elif sheet_token:
            info = {
                "obj_type": "sheet",
                "obj_token": sheet_token,
                "title": "",
                "node_token": None,
                "space_id": None,
                "parent_node_token": None,
                "has_child": False,
            }
        elif min_token:
            info = {
                "obj_type": "minutes",
                "obj_token": min_token,
                "title": "",
                "node_token": None,
                "space_id": None,
                "parent_node_token": None,
                "has_child": False,
            }
        else:
            raise ValueError(f"Cannot determine doc type for URL: {url}")

        self._cache_set(ck, info)
        return str(self._cache_dir / f"{ck}.json"), info

    def _info_bitable(self, url: str, app_token: str) -> dict:
        from src.feishu.extra.bitable import info_bitable, parse_bitable_url

        bi = info_bitable(app_token)
        p = parse_bitable_url(url)
        return {
            "obj_type": "bitable",
            "obj_token": app_token,
            "title": bi.get("name", ""),
            "node_token": None,
            "space_id": None,
            "parent_node_token": None,
            "has_child": False,
            "_bitable": bi,
            "_table_id": p.get("table_id"),
            "_view_id": p.get("view_id"),
        }

    # --- read_page_as_markdown ---

    def read_page_as_markdown(
        self,
        url: str,
        *,
        max_age: int | None = None,
        force_refresh: bool = False,
        with_annotations: bool = False,
    ) -> str:
        """Read a Feishu page and return its content as Markdown."""
        self.ensure_token()
        parsed = parse_url(url)

        # Direct bitable URL
        if parsed.get("app_token"):
            return self._bitable_md(url, parsed["app_token"], max_age, force_refresh)
        # Minutes / sheet placeholders
        if parsed.get("minutes_token"):
            return "Not yet implemented: minutes reading"
        if parsed.get("spreadsheet_token"):
            return self.read_sheet(url)

        # Wiki / docx / doc
        _, info = self.info_page(url, max_age=max_age, force_refresh=force_refresh)
        obj_type = info.get("obj_type")

        if obj_type == "bitable":
            return self._bitable_md(url, info.get("obj_token", ""), max_age, force_refresh)
        if obj_type == "sheet":
            return self.read_sheet(url)
        if obj_type == "minutes":
            return "Not yet implemented: minutes reading"
        if obj_type in {"mindnote", "slides", "file"}:
            raise NotImplementedError(
                f"Document type '{obj_type}' is not supported by Feishu API. "
                "Please export it manually from the web interface."
            )

        document_id = info.get("obj_token") or parsed.get("doc_id")
        if not document_id:
            raise ValueError(f"Could not resolve document_id from URL: {url}")

        ck = _ck("md", document_id)
        if force_refresh:
            self._cache_del(ck)
        cached = self._cache_get(ck, max_age=max_age or _TTL_PAGE)
        if cached is not None and not with_annotations:
            return cached.get("markdown", "")

        blocks = api.read_page(self._client, document_id)
        source_docs = self._collect_source_docs(blocks)
        output_annotations = {} if with_annotations else None

        md = feishu2md(
            blocks,
            source_docs=source_docs,
            bitable_renderer=self._make_bitable_renderer(max_age),
            sheet_renderer=self._make_sheet_renderer(max_age),
            sub_page_list_renderer=self._make_subpage_renderer(),
            output_annotations=output_annotations,
        )

        if with_annotations and output_annotations is not None:
            doc_prefix = self.output_prefix("docs", document_id)
            write_json(f"{doc_prefix}.anno.json", output_annotations)
            # Save raw blocks for cell-level table editing (block_map in EditArena)
            write_json(f"{doc_prefix}.content.json", {"page": blocks})

        self._cache_set(ck, {"markdown": md})
        return md

    # -- renderer factories (closures for feishu2md callbacks) --

    def _make_bitable_renderer(self, max_age: int | None) -> Callable[[str], str]:
        def render(token: str) -> str:
            try:
                if "_tbl" in token:
                    parts = token.split("_tbl", 1)
                    u = f"{self.base_url}/base/{parts[0]}?table=tbl{parts[1]}"
                else:
                    u = f"{self.base_url}/base/{token}"
                return self.read_page_as_markdown(u, max_age=max_age)
            except Exception as e:
                return f"<notice: [bitable]({token}) failed: {e}>"

        return render

    def _make_sheet_renderer(self, max_age: int | None) -> Callable[[str], str]:
        def render(token: str) -> str:
            try:
                parts = token.rsplit("_", 1)
                if len(parts) != 2:
                    return f"<notice: sheet={token} invalid format>"
                u = f"{self.base_url}/sheets/{parts[0]}?sheet={parts[1]}"
                return self.read_page_as_markdown(u, max_age=max_age)
            except Exception as e:
                return f"<notice: [sheet]({token}) failed: {e}>"

        return render

    def _make_subpage_renderer(self) -> Callable[[str], str]:
        def render(wiki_token: str) -> str:
            try:
                children = self.list_pages(f"{self.base_url}/wiki/{wiki_token}")
                if not children:
                    return ""
                lines = []
                for c in children:
                    t, nt = c.get("title", ""), c.get("node_token", "")
                    if t and nt:
                        lines.append(f"- [{t}]({self.base_url}/wiki/{nt})")
                    elif t:
                        lines.append(f"- {t}")
                return "\n".join(lines) + "\n" if lines else ""
            except Exception:
                return ""

        return render

    def _collect_source_docs(self, blocks: list[dict]) -> dict[str, list[dict]]:
        ids = {
            b.get("reference_synced", {}).get("source_document_id")
            for b in blocks
            if b.get("block_type") == 50
        }
        ids.discard(None)
        result: dict[str, list[dict]] = {}
        for did in ids:
            try:
                result[did] = api.read_page(self._client, did)
            except Exception as e:
                logger.warning(f"failed to fetch source doc {did}: {e}")
        return result

    def _bitable_md(
        self, url: str, app_token: str, max_age: int | None, force_refresh: bool
    ) -> str:
        from src.feishu.extra.bitable import (
            bitable2md,
            list_bitable_fields,
            list_bitable_records,
            list_bitable_tables,
            parse_bitable_url,
        )

        ck = _ck("bitable_md", app_token)
        if force_refresh:
            self._cache_del(ck)
        cached = self._cache_get(ck, max_age=max_age or _TTL_PAGE)
        if cached is not None:
            return cached.get("markdown", "")
        p = parse_bitable_url(url)
        tid, vid = p.get("table_id"), p.get("view_id")
        if not tid:
            tables = list_bitable_tables(app_token)
            if not tables:
                return "*No tables found in bitable*"
            tid = tables[0]["table_id"]
        fields = list_bitable_fields(app_token, tid)
        records = list_bitable_records(app_token, tid, vid)
        md = bitable2md(records, fields)
        self._cache_set(ck, {"markdown": md})
        return md

    # --- search / list ---

    def search_wiki(
        self, query: str, space: str | None = None, node: str | None = None
    ) -> list[dict]:
        """Search wiki pages by keyword."""
        self.ensure_token()
        if node and not space:
            parsed = parse_url(node)
            if not parsed.get("wiki_id"):
                raise ValueError("node must be a Feishu wiki page URL when used as search scope")
            _, info = self.info_page(node)
            space = info.get("space_id")
        space_id = self._resolve_space_id(space) if space else None
        return api.search_wiki(self._client, query, space_id=space_id)

    def search_docs(
        self,
        query: str,
        *,
        doc_types: list[str] | None = None,
        only_title: bool = False,
        sort_type: str | None = None,
        wiki_space_ids: list[str] | None = None,
        page_size: int = 20,
        max_items: int = 50,
    ) -> list[dict]:
        """Global cloud-document search (not limited to wiki).

        Searches across all accessible documents: DOCX, DOC, SHEET, BITABLE,
        FILE, WIKI, FOLDER, etc.

        Args:
            query: Search keyword.
            doc_types: Filter by doc type (e.g. ["DOCX", "SHEET"]).
            only_title: Search titles only.
            sort_type: Sort order (e.g. "EDIT_TIME").
            wiki_space_ids: Limit to specific wiki spaces.
            page_size: Results per page.
            max_items: Maximum total results.

        Returns:
            List of result dicts.
        """
        self.ensure_token()
        return api.search_docs(
            query,
            doc_types=doc_types,
            only_title=only_title,
            sort_type=sort_type,
            wiki_space_ids=wiki_space_ids,
            page_size=page_size,
            max_items=max_items,
        )

    def _resolve_space_id(self, space: str) -> str:
        for s in self.list_spaces():
            if s.get("name") == space:
                return s["space_id"]
        return space

    def list_pages(self, url: str) -> list[dict]:
        """List child pages of a wiki page or root pages of a space."""
        self.ensure_token()
        wiki_id = parse_url(url).get("wiki_id")
        if not wiki_id:
            return api.list_nodes(self._client, self._resolve_space_id(url))
        _, info = self.info_page(url)
        space_id = info.get("space_id")
        if not space_id:
            raise ValueError(f"Could not determine space_id from {url}")
        return api.list_nodes(self._client, space_id, parent_node_token=wiki_id)

    def list_spaces(self) -> list[dict]:
        """List all accessible wiki spaces."""
        self.ensure_token()
        cached = self._cache_get("spaces_list", max_age=_TTL_SPACES)
        if cached is not None:
            return cached
        spaces = api.list_spaces(self._client)
        self._cache_set("spaces_list", spaces)
        return spaces

    # --- edit_page ---

    def edit_page(
        self,
        url: str,
        old_string: str | None = None,
        new_string: str | None = None,
        *,
        edits: list[dict] | str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Edit a Feishu document (single, batch, or draft mode)."""
        has_single = old_string is not None or new_string is not None
        has_batch = edits is not None
        if has_single and has_batch:
            return {
                "error": "invalid_args",
                "detail": "Cannot mix old_string/new_string with edits",
            }
        if has_single and (old_string is None or new_string is None):
            return {"error": "invalid_args", "detail": "Both old_string and new_string required"}
        if not has_single and not has_batch:
            return {"error": "invalid_args", "detail": "Provide old_string/new_string or edits"}

        from src.feishu.edit_arena import EditArena

        arena = EditArena.from_url(self, url)

        if has_single:
            r = arena.stage(old_string, new_string)
            if isinstance(r, dict) and "error" in r:
                return r
        elif isinstance(edits, str):
            arena.stage_draft(edits)
        else:
            for e in edits:
                r = arena.stage(e["old"], e["new"])
                if isinstance(r, dict) and "error" in r:
                    return {"error": "stage_failed", "edit": e, "detail": r}

        if dry_run:
            return {"diff": arena.diff(), "preview_md": arena.preview_markdown()}

        result = arena.commit()
        if result.success:
            self._invalidate_cache(url)
        return result.to_dict()

    def _invalidate_cache(self, url: str) -> None:
        parsed = parse_url(url)
        wiki_id, doc_id = parsed.get("wiki_id"), parsed.get("doc_id")
        if wiki_id:
            ck = _ck("wiki_info", wiki_id)
            info = self._cache_get(ck)
            if info and info.get("obj_token"):
                self._cache_del(_ck("md", info["obj_token"]))
            self._cache_del(ck)
        if doc_id:
            self._cache_del(_ck("docs_info", doc_id))
            self._cache_del(_ck("md", doc_id))

    # --- create_page ---

    def create_page(
        self,
        ref_url: str,
        title: str,
        markdown: str | None = None,
        position: str = "child",
        grant_access_to: list[str] | None = None,
    ) -> dict:
        """Create a new wiki page under (or beside) ref_url.

        Args:
            ref_url: Reference wiki page URL.
            title: Title for the new page.
            markdown: Optional markdown content.
            position: ``"child"`` or ``"sibling"``.
            grant_access_to: List of user/chat IDs to grant full_access.
                Auto-detects member type from ID prefix (ou_ = openid, oc_ = chatid).
        """
        if position not in ("child", "sibling"):
            raise ValueError(f"position must be 'child' or 'sibling', got {position!r}")
        self.ensure_token()
        _, info = self.info_page(ref_url, force_refresh=True)
        space_id = info.get("space_id")

        if position == "sibling":
            parent = info.get("parent_node_token")
            if not space_id or not parent:
                raise ValueError(f"Cannot resolve parent for sibling from {ref_url}")
        else:
            parent = info.get("node_token")
            if not space_id or not parent:
                raise ValueError(f"Cannot resolve space/node from {ref_url}: {info}")

        node = api_write.create_wiki_node(
            self._client, space_id, parent_node_token=parent, title=title
        )
        doc_id = node["obj_token"]
        logger.info(f"created wiki node: {node['node_token']}, doc={doc_id}")

        # Small delay to let the wiki node become writable (prevents 131006 race condition).
        # See docs/pending/2026-03-27-feishu-write-reliability.md T1.
        time.sleep(0.3)  # let wiki node become writable

        wiki_url = f"{self.base_url}/wiki/{node['node_token']}"
        node["url"] = wiki_url

        content_written = False
        content_error = None
        if markdown and markdown.strip():
            # Parse once, reuse across fallback attempts
            block_result = api_write.markdown_to_blocks(markdown)
            children_ids = block_result.get("children_id", [])
            descendants = block_result.get("descendants", [])

            # Strategy: descendant API (batched) → children API → edit_page (draft mode)
            if children_ids:
                try:
                    api_write.create_descendant_blocks_batched(
                        self._client,
                        doc_id,
                        doc_id,
                        children_ids=children_ids,
                        descendants=descendants,
                    )
                    content_written = True
                    logger.info(f"wrote content to {doc_id} via descendant API (batched)")
                except Exception as e:
                    logger.debug(f"descendant API failed for {doc_id}: {e}, trying children API")

            # Fallback 1: Children API (top-level blocks, batched)
            if not content_written and descendants:
                try:
                    top_ids = set(children_ids)
                    top_level = [d for d in descendants if d.get("block_id") in top_ids]
                    if top_level:
                        batch_size = api_write.BLOCK_BATCH_SIZE
                        for i in range(0, len(top_level), batch_size):
                            batch = top_level[i : i + batch_size]
                            api_write.create_block_children(self._client, doc_id, doc_id, batch)
                        content_written = True
                        logger.info(f"wrote content to {doc_id} via children API")
                except Exception as e:
                    logger.debug(f"children API failed for {doc_id}: {e}, trying edit_page")

            # Fallback 2: edit_page (draft mode via EditArena)
            if not content_written:
                try:
                    edit_result = self.edit_page(wiki_url, edits=markdown)
                    if isinstance(edit_result, dict) and edit_result.get("success"):
                        content_written = True
                        logger.info(f"wrote content to {doc_id} via edit_page (draft mode)")
                    else:
                        content_error = edit_result.get("error") or edit_result.get("failed")
                        logger.warning(f"edit_page failed for new page {doc_id}: {content_error}")
                except Exception as e:
                    content_error = str(e)
                    logger.warning(f"edit_page exception for new page {doc_id}: {e}")

        # Grant permissions after creation
        permissions_granted: list[str] = []
        permissions_failed: list[str] = []
        if grant_access_to:
            for member_id in grant_access_to:
                try:
                    member_type = api_write.detect_member_type(member_id)
                    api_write.add_permission_member(
                        self._client,
                        file_token=node["node_token"],
                        file_type="wiki",
                        member_type=member_type,
                        member_id=member_id,
                        perm="full_access",
                    )
                    permissions_granted.append(member_id)
                    logger.info(f"granted full_access to {member_type}:{member_id} on {doc_id}")
                except Exception as e:
                    permissions_failed.append(member_id)
                    logger.warning(f"failed to grant permission to {member_id} on {doc_id}: {e}")

        node["content_written"] = content_written
        if permissions_granted:
            node["permissions_granted"] = permissions_granted
        if permissions_failed:
            node["permissions_failed"] = permissions_failed
        if content_error:
            node["content_error"] = (
                f"Page created but content write failed: {content_error}. "
                "Use feishu_edit with draft_markdown to retry writing content."
            )
        node["_verification_hint"] = (
            "⚠️ REQUIRED: Use feishu_list to verify page structure"
            + (", then feishu_read to verify content was written correctly" if markdown else "")
            + ". Do NOT skip verification."
        )
        return node

    # --- delete_page ---

    def delete_page(self, url: str) -> dict:
        """Delete (move to trash) a wiki page."""
        self.ensure_token()
        _, info = self.info_page(url, force_refresh=True)
        space_id = info.get("space_id")
        node_token = info.get("node_token")
        if not space_id or not node_token:
            raise ValueError(f"Cannot resolve space_id/node_token from {url}: {info}")
        result = api_write.delete_wiki_node(self._client, space_id, node_token)
        self._invalidate_cache(url)
        logger.info(f"deleted wiki node: {node_token} from space {space_id}")
        return {"success": True, "node_token": node_token, "space_id": space_id, **result}

    # --- URL resolution helpers ---

    def _resolve_file_token_and_type(self, url: str) -> tuple[str, str]:
        """Resolve a Feishu URL to (file_token, file_type).

        Returns:
            ``(file_token, file_type)`` — e.g. ``("doxcnXXX", "docx")``.

        Raises:
            ValueError: If the URL cannot be resolved to a file token.
        """
        parsed = parse_url(url)
        if parsed.get("wiki_id"):
            _, info = self.info_page(url)
            return info.get("obj_token", ""), info.get("obj_type", "docx")
        doc_id = parsed.get("doc_id")
        if doc_id:
            return doc_id, parsed.get("doc_type", "docx")
        raise ValueError(f"Cannot determine file token from URL: {url}")

    def _resolve_user_identifier(self, user: dict, source: str) -> tuple[str, str]:
        for field, receive_id_type in (
            ("open_id", "open_id"),
            ("user_id", "user_id"),
            ("union_id", "union_id"),
        ):
            value = user.get(field)
            if value:
                return value, receive_id_type
        raise ValueError(f"Could not resolve recipient ID from {source}")

    def _resolve_recipient(self, recipient: str) -> tuple[str, str]:
        self.ensure_token()
        if recipient.startswith("oc_"):
            return recipient, "chat_id"
        if recipient.startswith("ou_"):
            return recipient, "open_id"
        if recipient.startswith("on_"):
            return recipient, "union_id"
        if recipient.startswith("u_"):
            return recipient, "user_id"
        if "@" in recipient:
            user = self.contact_find_by_email(recipient)
            if not user:
                raise ValueError(f"No Feishu user found for email: {recipient}")
            return self._resolve_user_identifier(user, recipient)

        matches = self.search_users(recipient)
        exact_matches = [
            user
            for user in matches
            if recipient.lower()
            in {
                str(user.get("name", "")).lower(),
                str(user.get("en_name", "")).lower(),
                str(user.get("display_name", "")).lower(),
            }
        ]
        if len(exact_matches) == 1:
            return self._resolve_user_identifier(exact_matches[0], recipient)
        if len(exact_matches) > 1:
            raise ValueError(f"Recipient name is ambiguous: {recipient}")
        raise ValueError(f"No Feishu user found for recipient: {recipient}")

    # --- send_message ---

    def send_message(self, user: str, message: str) -> dict:
        """Send a text message to a user (open_id) or chat (chat_id)."""
        return self._send_json_message(user, "text", {"text": message})

    def _send_json_message(
        self,
        user_or_chat: str,
        msg_type: str,
        payload: dict[str, Any],
        *,
        ensure_ascii: bool = True,
    ) -> dict:
        receive_id, receive_id_type = self._resolve_recipient(user_or_chat)
        content = json.dumps(payload, ensure_ascii=ensure_ascii)
        return api.send_message(
            self._client, receive_id, msg_type, content, receive_id_type=receive_id_type
        )

    def send_card(
        self,
        user_or_chat: str,
        title: str,
        content: str,
        buttons: list[dict[str, Any]] | None = None,
    ) -> dict:
        """Send an interactive card message.

        Args:
            user_or_chat: Recipient open_id (ou_xxx) or chat_id (oc_xxx).
            title: Card header title.
            content: Markdown body for the card.
            buttons: Optional list of button dicts, each with ``text`` and ``url``.
        """
        receive_id, receive_id_type = self._resolve_recipient(user_or_chat)
        elements: list[dict] = [{"tag": "markdown", "content": content}]
        if buttons:
            actions = [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": btn.get("text", "Click")},
                    "url": btn.get("url", ""),
                    "type": btn.get("type", "primary"),
                }
                for btn in buttons
            ]
            elements.append({"tag": "action", "actions": actions})
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": elements,
        }
        card_json = json.dumps(card, ensure_ascii=False)
        return api.send_message(
            self._client, receive_id, "interactive", card_json, receive_id_type=receive_id_type
        )

    def send_image(self, user_or_chat: str, image_key: str) -> dict:
        """Send an image message.

        Args:
            user_or_chat: Recipient open_id (ou_xxx) or chat_id (oc_xxx).
            image_key: Image key obtained from image upload API.
        """
        return self._send_json_message(user_or_chat, "image", {"image_key": image_key})

    def send_file(self, user_or_chat: str, file_key: str) -> dict:
        """Send a file message.

        Args:
            user_or_chat: Recipient open_id (ou_xxx) or chat_id (oc_xxx).
            file_key: File key obtained from file upload API.
        """
        return self._send_json_message(user_or_chat, "file", {"file_key": file_key})

    def send_post(self, user_or_chat: str, title: str, content: list[list[dict[str, Any]]]) -> dict:
        """Send a rich text (post) message.

        Args:
            user_or_chat: Recipient open_id (ou_xxx) or chat_id (oc_xxx).
            title: Post title.
            content: List of lines, each line a list of element dicts.
                Example: ``[[{"tag": "text", "text": "hello "},
                             {"tag": "a", "href": "https://...", "text": "link"}]]``
        """
        return self._send_json_message(
            user_or_chat,
            "post",
            {"zh_cn": {"title": title, "content": content}},
            ensure_ascii=False,
        )

    # --- read_comments ---

    def read_comments(self, url: str) -> list[dict]:
        """Read all comments on a document."""
        self.ensure_token()
        file_token, file_type = self._resolve_file_token_and_type(url)
        return api.read_comments(self._client, file_token, file_type)

    # --- comment write operations ---

    def add_comment(self, url: str, content: str, reply_id: str | None = None) -> dict:
        """Add a comment to a document (or reply to an existing comment).

        Args:
            url: Feishu document URL.
            content: Comment text.
            reply_id: If set, reply to this comment thread.
        """
        self.ensure_token()
        file_token, file_type = self._resolve_file_token_and_type(url)
        return api_write.add_comment(
            self._client, file_token, file_type, content, reply_id=reply_id
        )

    def resolve_comment(self, url: str, comment_id: str, resolve: bool = True) -> dict:
        """Resolve or unresolve a comment.

        Args:
            url: Feishu document URL.
            comment_id: The comment ID.
            resolve: ``True`` to mark resolved, ``False`` to unresolve.
        """
        self.ensure_token()
        file_token, file_type = self._resolve_file_token_and_type(url)
        return api_write.resolve_comment(
            self._client, file_token, file_type, comment_id, is_solved=resolve
        )

    def delete_comment(self, url: str, comment_id: str) -> bool:
        """Delete a comment.

        Args:
            url: Feishu document URL.
            comment_id: The comment ID to delete.
        """
        self.ensure_token()
        file_token, file_type = self._resolve_file_token_and_type(url)
        return api_write.delete_comment(self._client, file_token, file_type, comment_id)

    # --- download_file ---

    def download_file(
        self,
        url: str,
        output_dir: str = ".",
        limit: int = 10,
        dry_run: bool = False,
        file_extension: str = "xlsx",
    ) -> list[str]:
        """Download file(s) from a Feishu Drive URL."""
        self.ensure_token()
        parsed = parse_url(url)
        out = Path(output_dir).expanduser()

        # Resolve wiki -> sheet
        st = parsed.get("spreadsheet_token")
        if parsed.get("wiki_id") and not st:
            _, info = self.info_page(url)
            if info.get("obj_type") == "sheet":
                st = info["obj_token"]
        if st:
            if dry_run:
                return [f"{st}.{file_extension}"]
            out.mkdir(parents=True, exist_ok=True)
            return self._dl_sheet(st, out, file_extension)

        ft = parsed.get("folder_token")
        if ft:
            if dry_run:
                return [f.get("name", "?") for f in api.list_folder_files(self._client, ft)]
            out.mkdir(parents=True, exist_ok=True)
            return self._dl_folder(ft, out, limit)

        fk = parsed.get("file_token")
        if fk:
            if dry_run:
                return [fk]
            out.mkdir(parents=True, exist_ok=True)
            return self._dl_file(fk, out)

        raise ValueError(f"URL is not a downloadable resource: {url}")

    def _dl_folder(self, folder_token: str, out: Path, limit: int) -> list[str]:
        files = api.list_folder_files(self._client, folder_token)
        dl = []
        for item in files:
            if item.get("type") != "file" or len(dl) >= limit:
                continue
            name = item.get("name", item["token"])
            dest = out / name
            if dest.exists():
                continue
            try:
                c = api.download_file_content(self._client, item["token"])
                dest.write_bytes(c)
                dl.append(str(dest))
            except Exception as e:
                logger.error(f"download {name}: {e}")
        return dl

    def _dl_file(self, file_token: str, out: Path) -> list[str]:
        dest = out / file_token
        if dest.exists():
            return []
        c = api.download_file_content(self._client, file_token)
        dest.write_bytes(c)
        return [str(dest)]

    def _dl_sheet(self, st: str, out: Path, ext: str = "xlsx") -> list[str]:
        ticket = api.create_export_task(
            self._client, file_token=st, file_extension=ext, type="sheet"
        )
        for _ in range(30):
            res = api.get_export_task_result(self._client, ticket, st)
            if res.get("job_status") == 0:
                break
            if res.get("job_status") in (1, 2):
                time.sleep(1)
                continue
            raise RuntimeError(f"export failed: {res.get('job_error_msg')}")
        else:
            raise RuntimeError("export timed out")
        fn = res.get("file_name", st)
        if not fn.endswith(f".{ext}"):
            fn = f"{fn}.{ext}"
        dest = out / fn
        if dest.exists():
            return []
        c = api.download_export_file(self._client, res["file_token"])
        dest.write_bytes(c)
        return [str(dest)]

    # --- spreadsheet read/write ---

    def _resolve_sheet_params(self, url: str) -> tuple[str, str | None]:
        """Parse spreadsheet_token and sheet_id from a URL or bare token.

        Returns:
            ``(spreadsheet_token, sheet_id | None)``
        """
        parsed = api_sheets.parse_sheet_url(url)
        token = parsed.get("spreadsheet_token")
        if not token:
            # Fallback: try parse_url for wiki->sheet resolution
            p = parse_url(url)
            token = p.get("spreadsheet_token")
        if not token:
            raise ValueError(f"Cannot extract spreadsheet token from: {url}")
        return token, parsed.get("sheet_id")

    def _resolve_sheet_id(self, token: str, sheet_id: str | None) -> tuple[str, list[dict]]:
        """Resolve sheet_id: use provided value or default to the first sheet.

        Returns:
            ``(sheet_id, sheets_list)`` so callers can reuse the sheets metadata.
        """
        sheets = api.list_spreadsheet_sheets(self._client, token)
        if sheet_id:
            return sheet_id, sheets
        if not sheets:
            raise ValueError(f"Spreadsheet {token} has no sheets")
        return sheets[0].get("sheet_id", ""), sheets

    def read_sheet(self, url: str, range: str = "") -> str:
        """Read spreadsheet cells and return as markdown table.

        Args:
            url: Feishu spreadsheet URL or bare token.
            range: Cell range e.g. ``"A1:C10"``. Empty reads all data.

        Returns:
            Markdown-formatted table string.
        """
        self.ensure_token()
        token, sheet_id = self._resolve_sheet_params(url)
        sheet_id, sheets = self._resolve_sheet_id(token, sheet_id)

        result = api_sheets.read_sheet_values(self._client, token, sheet_id, range=range)
        values = result.get("values", [])

        # Determine sheet name for display (reuse sheets list from _resolve_sheet_id)
        sheet_name = sheet_id
        for s in sheets:
            if s.get("sheet_id") == sheet_id:
                sheet_name = s.get("title", sheet_id)
                break

        md = api_sheets.values_to_markdown(values, sheet_name=sheet_name, range_str=range)
        rows, cols = result.get("rows", 0), result.get("cols", 0)
        md += f"\n\n*{rows} rows x {cols} cols*"
        return md

    def write_sheet(self, url: str, range: str, values: list[list[Any]]) -> dict:
        """Write values to spreadsheet cells.

        Args:
            url: Feishu spreadsheet URL or bare token.
            range: Target range e.g. ``"A1:C3"``.
            values: 2D list of cell values.

        Returns:
            API response data dict.
        """
        self.ensure_token()
        token, sheet_id = self._resolve_sheet_params(url)
        sheet_id, _ = self._resolve_sheet_id(token, sheet_id)
        return api_sheets.write_sheet_values(self._client, token, sheet_id, range, values)

    def append_sheet(self, url: str, values: list[list[Any]]) -> dict:
        """Append rows to the end of a spreadsheet.

        Args:
            url: Feishu spreadsheet URL or bare token.
            values: 2D list of row values to append.

        Returns:
            API response data dict.
        """
        self.ensure_token()
        token, sheet_id = self._resolve_sheet_params(url)
        sheet_id, _ = self._resolve_sheet_id(token, sheet_id)
        return api_sheets.append_sheet_values(self._client, token, sheet_id, values)

    def sheet_info(self, url: str) -> dict:
        """Get spreadsheet metadata.

        Args:
            url: Feishu spreadsheet URL or bare token.

        Returns:
            Dict with title, spreadsheet_token, and sheets list.
        """
        self.ensure_token()
        token, _ = self._resolve_sheet_params(url)
        return api_sheets.get_sheet_metadata(self._client, token)

    # --- calendar ---

    @staticmethod
    def _default_day_range() -> tuple[str, str]:
        """Return (start, end) RFC3339 timestamps for today in local timezone."""
        from datetime import datetime

        now = datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        return start.isoformat(), end.isoformat()

    def calendar_list(self) -> list[dict]:
        """List the current user's calendars."""
        return self._call_api(api_calendar.list_calendars)

    def calendar_events(
        self,
        start: str | None = None,
        end: str | None = None,
        calendar_id: str = "primary",
    ) -> list[dict]:
        """List calendar events in a time range.

        If start/end not provided, defaults to today 00:00-23:59 local time.
        """
        if not start or not end:
            default_start, default_end = self._default_day_range()
            start = start or default_start
            end = end or default_end
        return self._call_api(
            api_calendar.list_events,
            calendar_id=calendar_id,
            start_time=start,
            end_time=end,
        )

    def calendar_get_event(self, event_id: str, calendar_id: str = "primary") -> dict:
        """Get a single calendar event's details."""
        return self._call_api(api_calendar.get_event, calendar_id, event_id)

    def calendar_create_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        calendar_id: str = "primary",
        **kwargs: Any,
    ) -> dict:
        """Create a calendar event.

        Keyword args are forwarded to ``api_calendar.create_event``
        (description, attendees, location, is_all_day).
        """
        return self._call_api(
            api_calendar.create_event,
            calendar_id=calendar_id,
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            **kwargs,
        )

    def calendar_delete_event(self, event_id: str, calendar_id: str = "primary") -> bool:
        """Delete a calendar event."""
        return self._call_api(api_calendar.delete_event, calendar_id, event_id)

    def calendar_freebusy(self, user_ids: list[str], start_time: str, end_time: str) -> dict:
        """Query free/busy status for one or more users."""
        return self._call_api(api_calendar.freebusy_query, user_ids, start_time, end_time)

    # --- tasks ---

    def task_list(self, completed: bool | None = None) -> list[dict]:
        """List tasks visible to the current user."""
        return self._call_api(api_tasks.list_tasks, completed=completed)

    def task_get(self, task_guid: str) -> dict:
        """Get a single task's detail."""
        return self._call_api(api_tasks.get_task, task_guid)

    def task_create(
        self,
        summary: str,
        description: str = "",
        due: str | None = None,
        assignee: str | None = None,
    ) -> dict:
        """Create a task.

        Args:
            summary: Task title/summary.
            description: Task description.
            due: Due timestamp (epoch seconds string) or ``None``.
            assignee: User open_id to assign, or ``None``.
        """
        members = [{"id": assignee, "role": "assignee"}] if assignee else None
        return self._call_api(
            api_tasks.create_task,
            summary=summary,
            description=description,
            due=due,
            members=members,
            origin={"platform_i18n_name": "theos"},
        )

    def task_complete(self, task_guid: str) -> bool:
        """Mark a task as completed."""
        return self._call_api(api_tasks.complete_task, task_guid)

    def task_delete(self, task_guid: str) -> bool:
        """Delete a task."""
        return self._call_api(api_tasks.delete_task, task_guid)

    def task_add_subtask(self, task_guid: str, summary: str) -> dict:
        """Add a subtask to an existing task."""
        return self._call_api(api_tasks.create_subtask, task_guid, summary)

    # --- user operations ---

    def info_user(self, user_id: str) -> dict:
        """Get user info by user_id / open_id."""
        self.ensure_token()
        ck = _ck("user", user_id)
        cached = self._cache_get(ck, max_age=_TTL_USER)
        if cached is not None:
            return cached
        info = api.info_user(self._client, user_id)
        self._cache_set(ck, info)
        return info

    def search_users(self, query: str) -> list[dict]:
        """Search users by keyword."""
        return self._call_api(api.search_users, query)

    # --- permissions ---

    def _resolve_file_params(self, url: str) -> tuple[str, str]:
        """Extract (file_token, file_type) from a Feishu URL.

        Returns:
            ``(file_token, file_type)`` for use with drive permission APIs.
        """
        parsed = parse_url(url)
        wiki_id = parsed.get("wiki_id")
        if wiki_id:
            # Wiki pages: resolve to the underlying obj_token
            _, info = self.info_page(url)
            return info.get("obj_token", wiki_id), info.get("obj_type", "wiki")
        doc_id = parsed.get("doc_id")
        if doc_id:
            return doc_id, parsed.get("doc_type", "docx")
        app_token = parsed.get("app_token")
        if app_token:
            return app_token, "bitable"
        sheet_token = parsed.get("spreadsheet_token")
        if sheet_token:
            return sheet_token, "sheet"
        file_token = parsed.get("file_token")
        if file_token:
            return file_token, "file"
        raise ValueError(f"Cannot extract file_token from URL: {url}")

    def perm_list(self, url: str) -> list[dict]:
        """List all collaborators on a document."""
        file_token, file_type = self._resolve_permission_target(url)
        return api_write.list_permission_members(self._client, file_token, file_type)

    def perm_add(self, url: str, member_id: str, perm: str = "full_access") -> dict:
        """Grant permission to a member on a document."""
        file_token, file_type, member_type = self._resolve_member_permission_target(url, member_id)
        return api_write.add_permission_member(
            self._client, file_token, file_type, member_type, member_id, perm=perm
        )

    def perm_update(self, url: str, member_id: str, perm: str) -> dict:
        """Update a collaborator's permission level."""
        file_token, file_type, member_type = self._resolve_member_permission_target(url, member_id)
        return api_write.update_permission_member(
            self._client, file_token, file_type, member_type, member_id, perm
        )

    def perm_remove(self, url: str, member_id: str) -> bool:
        """Remove a collaborator from a document."""
        file_token, file_type, member_type = self._resolve_member_permission_target(url, member_id)
        return api_write.remove_permission_member(
            self._client, file_token, file_type, member_type, member_id
        )

    def perm_transfer(self, url: str, new_owner: str) -> dict:
        """Transfer document ownership."""
        file_token, file_type = self._resolve_permission_target(url)
        new_owner_type = api_write.detect_member_type(new_owner)
        return api_write.transfer_owner(
            self._client, file_token, file_type, new_owner, new_owner_type=new_owner_type
        )

    def _resolve_permission_target(self, url: str) -> tuple[str, str]:
        self.ensure_token()
        return self._resolve_file_params(url)

    def _resolve_member_permission_target(self, url: str, member_id: str) -> tuple[str, str, str]:
        file_token, file_type = self._resolve_permission_target(url)
        return file_token, file_type, api_write.detect_member_type(member_id)

    # --- chat/group management ---

    def chat_create(
        self, name: str, description: str = "", user_ids: list[str] | None = None
    ) -> dict:
        """Create a group chat."""
        return self._call_api(
            api_chat.create_chat, name, description=description, user_ids=user_ids
        )

    def chat_info(self, chat_id: str) -> dict:
        """Get chat details."""
        return self._call_api(api_chat.get_chat, chat_id)

    def chat_update(
        self, chat_id: str, name: str | None = None, description: str | None = None
    ) -> dict:
        """Update chat properties."""
        return self._call_api(api_chat.update_chat, chat_id, name=name, description=description)

    def chat_members(self, chat_id: str) -> list[dict]:
        """List members of a chat."""
        return self._call_api(api_chat.list_chat_members, chat_id)

    def chat_add_members(self, chat_id: str, user_ids: list[str]) -> dict:
        """Add members to a chat."""
        return self._call_api(api_chat.add_chat_members, chat_id, user_ids)

    def chat_remove_members(self, chat_id: str, user_ids: list[str]) -> dict:
        """Remove members from a chat."""
        return self._call_api(api_chat.remove_chat_members, chat_id, user_ids)

    def chat_messages(self, chat_id: str, page_size: int = 50) -> list[dict]:
        """Get message history for a chat."""
        return self._call_api(api_chat.list_chat_messages, chat_id, page_size=page_size)

    # --- drive file management ---

    def file_create_folder(self, folder_token: str, name: str) -> dict:
        """Create a subfolder inside *folder_token*."""
        return self._call_api(api_write.create_folder, folder_token, name)

    def file_move(self, file_token: str, dest_folder: str, file_type: str = "") -> dict:
        """Move a file/folder to *dest_folder*."""
        return self._call_api(api_write.move_file, file_token, dest_folder, file_type=file_type)

    def file_copy(
        self, file_token: str, dest_folder: str, new_name: str = "", file_type: str = ""
    ) -> dict:
        """Copy a file to *dest_folder*."""
        return self._call_api(
            api_write.copy_file, file_token, dest_folder, new_name=new_name, file_type=file_type
        )

    def file_delete(self, file_token: str, file_type: str) -> bool:
        """Delete (trash) a file/folder."""
        return self._call_api(api_write.delete_file, file_token, file_type)

    def file_upload(
        self,
        file_name: str,
        file_path: str,
        parent_token: str,
        parent_type: str = "explorer",
    ) -> dict:
        """Upload a local file (< 20 MB) to Drive."""
        return self._call_api(
            api_write.upload_file, file_name, file_path, parent_token, parent_type=parent_type
        )

    def file_list(self, folder_token: str) -> list[dict]:
        """List files in a Drive folder."""
        return self._call_api(api.list_folder_files, folder_token)

    def import_file(
        self,
        file_path: str,
        *,
        file_name: str | None = None,
        target_type: str = "docx",
        mount_key: str = "",
        wiki_parent_url: str | None = None,
        poll_interval: float = 1.0,
        max_polls: int = 30,
    ) -> dict:
        """Import a local file as a Feishu cloud document.

        Supports: .docx, .doc, .md, .xlsx, .xls, .csv → Feishu DOCX or Sheet.

        Args:
            file_path: Path to the local file.
            file_name: Display name (defaults to filename without extension).
            target_type: "docx" or "sheet".
            mount_key: Mount point key (empty for personal space).
            wiki_parent_url: If provided, move the imported doc under this wiki page.
            poll_interval: Seconds between import status polls.
            max_polls: Maximum number of polls before timeout.

        Returns:
            Dict with token, url, type of the imported document.
            If moved to wiki, also includes wiki_token.
        """
        import os

        self.ensure_token()

        basename = os.path.basename(file_path)
        name_no_ext, ext = os.path.splitext(basename)
        ext_clean = ext.lstrip(".")
        display_name = file_name or name_no_ext

        # Step 1: Upload media
        file_token = api_write.upload_media_for_import(file_path, basename, target_type, ext_clean)

        # Step 2: Create import task
        ticket = api_write.create_import_task(
            ext_clean, file_token, target_type, display_name, mount_key
        )

        result = self._poll_import_result(ticket, poll_interval, max_polls)

        # Step 4: Optionally move to wiki
        if wiki_parent_url:
            self._move_import_to_wiki(result, target_type, wiki_parent_url, poll_interval, max_polls)

        return result

    def _poll_import_result(self, ticket: str, poll_interval: float, max_polls: int) -> dict:
        for _ in range(max_polls):
            result = api_write.get_import_task_result(ticket)
            status = result.get("job_status", -1)
            if status == 0:
                return result
            if status >= 3:
                msg = f"Import failed with status {status}: {result}"
                raise RuntimeError(msg)
            time.sleep(poll_interval)
        msg = f"Import timed out after {max_polls * poll_interval}s"
        raise TimeoutError(msg)

    def _move_import_to_wiki(
        self,
        result: dict,
        target_type: str,
        wiki_parent_url: str,
        poll_interval: float,
        max_polls: int,
    ) -> None:
        parsed = parse_url(wiki_parent_url)
        wiki_id = parsed.get("wiki_id")
        if not wiki_id:
            msg = f"Cannot parse wiki URL: {wiki_parent_url}"
            raise ValueError(msg)
        _, info = self.info_page(wiki_parent_url)
        space_id = info.get("space_id")
        if not space_id:
            msg = f"Cannot determine space_id from {wiki_parent_url}"
            raise ValueError(msg)

        move_result = api_write.move_docs_to_wiki(
            self._client,
            space_id,
            wiki_id,
            result.get("type", target_type),
            result["token"],
        )
        result["wiki_token"] = move_result.get("wiki_token")

        task_id = move_result.get("task_id")
        if task_id:
            self._poll_wiki_task(task_id, poll_interval, max_polls)

    def _poll_wiki_task(self, task_id: str, poll_interval: float, max_polls: int) -> None:
        for _ in range(max_polls):
            task_result = api_write.get_wiki_task_result(task_id)
            if task_result.get("status") == "done":
                return
            time.sleep(poll_interval)

    # --- contacts: departments ---

    def contact_departments(self, parent_id: str = "0") -> list[dict]:
        """List child departments of *parent_id* (``"0"`` for root)."""
        return self._call_api(api_contacts.list_departments, parent_id)

    def contact_department(self, department_id: str) -> dict:
        """Get a department's info."""
        return self._call_api(api_contacts.get_department, department_id)

    def contact_department_users(self, department_id: str) -> list[dict]:
        """List users in a department."""
        return self._call_api(api_contacts.list_department_users, department_id)

    def contact_find_by_email(self, email: str) -> dict | None:
        """Find a user by email address."""
        return self._call_api(api_contacts.get_user_by_email, email)

    def contact_find_by_phone(self, phone: str) -> dict | None:
        """Find a user by phone number."""
        return self._call_api(api_contacts.get_user_by_phone, phone)
