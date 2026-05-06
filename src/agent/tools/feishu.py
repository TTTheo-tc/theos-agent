"""Feishu/Lark knowledge tools — direct Python API integration.

Uses ``src.feishu.FeishuClient`` for all operations. No external CLI dependency.
All tools receive a shared client instance from ``tool_sets.register_standard_tools``.
Since FeishuClient methods are synchronous (lark-oapi uses requests internally),
tools wrap calls with ``loop.run_in_executor`` for async compatibility.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from functools import partial
from typing import Any

from src.agent.tools.base import Tool

# Maximum output characters returned to the LLM to avoid context blowout.
_MAX_OUTPUT = 60_000


def _truncate(text: str) -> str:
    if len(text) > _MAX_OUTPUT:
        return text[:_MAX_OUTPUT] + f"\n\n... (truncated, {len(text)} chars total)"
    return text


def _format_result(result: Any) -> str:
    """Format a result for tool output — strings pass through, dicts/lists become JSON."""
    if isinstance(result, str):
        return _truncate(result)
    return _truncate(json.dumps(result, ensure_ascii=False, indent=2))


_AUTH_HINT = (
    "\n\nHint: Feishu user token has expired. "
    "Run `theos feishu-auth` (local) or `theos feishu-auth --remote` (phone). "
    "Alternatively, use the feishu_auth tool to initiate re-authorization in this chat."
)

# Map retry.ErrorCategory to human-readable labels for tool output.
_CATEGORY_LABELS: dict[str, str] = {
    "permanent": "invalid_request",
    "rate_limited": "rate_limited",
    "retryable": "transient",
}


def _classify_for_output(exc: Exception) -> str:
    """Return a human-readable error label using the canonical retry classifier."""
    from src.feishu.retry import classify_error

    cat = classify_error(exc)
    # Refine "permanent" into more specific labels for tool users
    msg = str(exc).lower()
    if cat.value == "permanent":
        if any(kw in msg for kw in ("token", "unauthorized", "99991668", "99991663")):
            return "auth_expired"
        if any(kw in msg for kw in ("permission", "forbidden", "99991672")):
            return "permission_denied"
        if any(kw in msg for kw in ("not found", "404")):
            return "not_found"
    return _CATEGORY_LABELS.get(cat.value, "unknown")


async def _run(func: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    """Run a sync function in executor and format the result."""
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, partial(func, *args, **kwargs))
        return _format_result(result)
    except Exception as e:
        error_type = _classify_for_output(e)
        hint = _AUTH_HINT if error_type == "auth_expired" else ""
        return f"Error ({error_type}): {e}{hint}"


class _FeishuClientTool(Tool):
    """Base for Feishu tools that only need the shared client."""

    def __init__(self, client: Any) -> None:
        self._client = client


def _missing(field: str, context: str, *, plural: bool = False) -> str:
    quoted = field if "'" in field else f"'{field}'"
    verb = "are" if plural else "is"
    return f"Error: {quoted} {verb} required for {context}"


def _unknown(kind: str, value: str, choices: str) -> str:
    return f"Error: unknown {kind} '{value}'. Use {choices}."


# ---------------------------------------------------------------------------
# P1 — Knowledge tools (read / search / list)
# ---------------------------------------------------------------------------


class FeishuReadTool(_FeishuClientTool):
    """Read a Feishu page as Markdown.

    Maps to ``FeishuClient.read_page_as_markdown()``.
    Supports: wiki/docx, bitable, sheet, minutes.
    """

    name = "feishu_read"
    description = (
        "Read a Feishu/Lark document page as Markdown. Supports wiki docs, "
        "bitable (multi-dimensional tables) and sheets. "
        "Provide the full Feishu URL. Results are cached; use force_refresh "
        "or max_age to control freshness."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Feishu page URL (e.g. https://<tenant>.feishu.cn/wiki/...)",
            },
            "max_age": {
                "type": "integer",
                "description": "Maximum cache age in seconds (omit for system default)",
                "minimum": 0,
            },
            "force_refresh": {
                "type": "boolean",
                "description": "Bypass cache and fetch fresh content (default: false)",
            },
        },
        "required": ["url"],
    }

    async def execute(
        self, url: str, max_age: int | None = None, force_refresh: bool = False, **kw: Any
    ) -> str:
        del kw
        return await _run(
            self._client.read_page_as_markdown, url, max_age=max_age, force_refresh=force_refresh
        )


class FeishuSearchTool(_FeishuClientTool):
    """Search Feishu wiki pages by keyword."""

    name = "feishu_search"
    description = (
        "Search for Feishu/Lark wiki pages by keyword. "
        "Optionally scope to a specific space, or to the space containing a wiki page. "
        "Set scope='all' to search all cloud documents (not just wiki)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query keywords"},
            "space": {"type": "string", "description": "Space name or ID (optional)"},
            "node": {
                "type": "string",
                "description": "Wiki page URL used only to infer its parent space (optional)",
            },
            "scope": {
                "type": "string",
                "enum": ["wiki", "all"],
                "description": "Search scope: 'wiki' (default) for wiki pages only, "
                "'all' for all cloud documents (DOCX, SHEET, BITABLE, etc.)",
            },
            "doc_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filter by doc type when scope='all'. "
                "Values: DOC, DOCX, SHEET, BITABLE, FILE, WIKI, FOLDER, SLIDES, etc.",
            },
        },
        "required": ["query"],
    }

    async def execute(
        self,
        query: str,
        space: str | None = None,
        node: str | None = None,
        scope: str | None = None,
        doc_types: list[str] | None = None,
        **kw: Any,
    ) -> str:
        del kw
        if scope == "all":
            return await _run(
                self._client.search_docs,
                query,
                doc_types=doc_types,
            )
        return await _run(self._client.search_wiki, query, space=space, node=node)


class FeishuListTool(_FeishuClientTool):
    """List child pages of a Feishu wiki page or space."""

    name = "feishu_list"
    description = (
        "List child pages under a Feishu wiki page, or the root pages of a Feishu space. "
        "For spaces, pass the space name or space ID."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Feishu page URL, space name, or space ID"},
        },
        "required": ["url"],
    }

    async def execute(self, url: str, **kw: Any) -> str:
        del kw
        return await _run(self._client.list_pages, url)


class FeishuSpacesTool(_FeishuClientTool):
    """List all accessible Feishu knowledge spaces."""

    name = "feishu_spaces"
    description = "List all accessible Feishu/Lark knowledge spaces (wikis)."
    parameters = {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kw: Any) -> str:
        del kw
        return await _run(self._client.list_spaces)


# ---------------------------------------------------------------------------
# P2 — Calendar tools
# ---------------------------------------------------------------------------


class FeishuCalendarTool(_FeishuClientTool):
    """Manage Feishu calendar events: list, get, create, delete, freebusy."""

    name = "feishu_calendar"
    description = (
        "Manage Feishu/Lark calendar events. Actions: "
        "list (list calendars), events (list events in time range), "
        "create (create event), delete (delete event), "
        "freebusy (check availability). "
        "Supports natural date strings: 'today', 'tomorrow', 'this week'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "events", "create", "delete", "freebusy"],
                "description": "Operation to perform",
            },
            "calendar_id": {
                "type": "string",
                "description": 'Calendar ID (default: "primary")',
            },
            "start_time": {
                "type": "string",
                "description": (
                    "Start time: RFC3339 (e.g. '2026-03-25T00:00:00+08:00') "
                    "or natural ('today', 'tomorrow', 'this week')"
                ),
            },
            "end_time": {
                "type": "string",
                "description": "End time: RFC3339 or natural date string",
            },
            "summary": {
                "type": "string",
                "description": "Event title (for create)",
            },
            "description": {
                "type": "string",
                "description": "Event description (for create)",
            },
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "User IDs for attendees (for create)",
            },
            "location": {
                "type": "string",
                "description": "Event location (for create)",
            },
            "event_id": {
                "type": "string",
                "description": "Event ID (for delete)",
            },
            "user_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "User IDs to check availability (for freebusy)",
            },
        },
        "required": ["action"],
    }

    @property
    def risk_level(self) -> str:
        return "medium"

    async def execute(
        self,
        action: str,
        calendar_id: str = "primary",
        start_time: str | None = None,
        end_time: str | None = None,
        summary: str | None = None,
        description: str | None = None,
        attendees: list[str] | None = None,
        location: str | None = None,
        event_id: str | None = None,
        user_ids: list[str] | None = None,
        **kw: Any,
    ) -> str:
        del kw
        # Resolve natural dates
        start_resolved = _resolve_natural_date(start_time, is_end=False) if start_time else None
        end_resolved = _resolve_natural_date(end_time, is_end=True) if end_time else None

        if action == "list":
            return await _run(self._client.calendar_list)

        if action == "events":
            return await _run(
                self._client.calendar_events,
                start=start_resolved,
                end=end_resolved,
                calendar_id=calendar_id,
            )

        if action == "create":
            if not summary:
                return _missing("summary", "create action")
            if not start_resolved or not end_resolved:
                return _missing("'start_time' and 'end_time'", "create action", plural=True)
            att_dicts = (
                [{"type": "user", "user_id": uid} for uid in attendees] if attendees else None
            )
            return await _run(
                self._client.calendar_create_event,
                summary=summary,
                start_time=start_resolved,
                end_time=end_resolved,
                calendar_id=calendar_id,
                description=description or "",
                attendees=att_dicts,
                location=location or "",
            )

        if action == "delete":
            if not event_id:
                return _missing("event_id", "delete action")
            return await _run(
                self._client.calendar_delete_event,
                event_id=event_id,
                calendar_id=calendar_id,
            )

        if action == "freebusy":
            if not user_ids:
                return _missing("user_ids", "freebusy action")
            if not start_resolved or not end_resolved:
                return _missing("'start_time' and 'end_time'", "freebusy action", plural=True)
            return await _run(
                self._client.calendar_freebusy,
                user_ids=user_ids,
                start_time=start_resolved,
                end_time=end_resolved,
            )

        return _unknown("action", action, "list, events, create, delete, or freebusy")


def _resolve_natural_date(value: str, *, is_end: bool = False) -> str:
    """Convert natural date strings to RFC3339. Pass through if already RFC3339.

    Supports: "today", "tomorrow", "this week".
    """
    from datetime import datetime, timedelta  # noqa: PLC0415

    lowered = value.strip().lower()
    now = datetime.now().astimezone()

    if lowered == "today":
        if is_end:
            dt = now.replace(hour=23, minute=59, second=59, microsecond=0)
        else:
            dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return dt.isoformat()

    if lowered == "tomorrow":
        day = now + timedelta(days=1)
        if is_end:
            dt = day.replace(hour=23, minute=59, second=59, microsecond=0)
        else:
            dt = day.replace(hour=0, minute=0, second=0, microsecond=0)
        return dt.isoformat()

    if lowered == "this week":
        # Monday = 0, Sunday = 6
        monday = now - timedelta(days=now.weekday())
        sunday = monday + timedelta(days=6)
        if is_end:
            dt = sunday.replace(hour=23, minute=59, second=59, microsecond=0)
        else:
            dt = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        return dt.isoformat()

    # Already RFC3339 or some other format -- pass through
    return value


# ---------------------------------------------------------------------------
# P2 — Write tools (edit / create / send)
# ---------------------------------------------------------------------------


class FeishuEditTool(_FeishuClientTool):
    """Edit a Feishu document."""

    name = "feishu_edit"
    description = (
        "Edit a Feishu document. Three modes: "
        "(1) single find-replace with old_string/new_string (supports fuzzy matching); "
        "(2) batch edits with a list of old/new pairs; "
        "(3) draft mode with complete edited markdown. "
        "Use dry_run=true to preview diff without applying. "
        "After this tool succeeds, you MUST call feishu_read to verify "
        "the rendered content. Do not claim completion without verification."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Feishu docx page URL"},
            "old_string": {"type": "string", "description": "Text to find (single edit mode)"},
            "new_string": {"type": "string", "description": "Replacement text (single edit mode)"},
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
                    "required": ["old", "new"],
                },
                "description": 'Batch edits: list of {"old": "...", "new": "..."} dicts',
            },
            "draft_markdown": {
                "type": "string",
                "description": "Draft mode: complete edited markdown (diffed against current).",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Preview without applying (default: false)",
            },
        },
        "required": ["url"],
    }

    @property
    def risk_level(self) -> str:
        return "medium"

    async def execute(
        self,
        url: str,
        old_string: str | None = None,
        new_string: str | None = None,
        edits: list[dict[str, str]] | None = None,
        draft_markdown: str | None = None,
        dry_run: bool = False,
        **kw: Any,
    ) -> str:
        del kw
        if draft_markdown is not None:
            result = await _run(self._client.edit_page, url, edits=draft_markdown, dry_run=dry_run)
        elif edits:
            result = await _run(self._client.edit_page, url, edits=edits, dry_run=dry_run)
        elif old_string is not None and new_string is not None:
            result = await _run(
                self._client.edit_page,
                url,
                old_string=old_string,
                new_string=new_string,
                dry_run=dry_run,
            )
        else:
            return (
                "Error: Provide one of: (old_string + new_string), edits list, or draft_markdown."
            )
        # Append verification hint for successful non-dry-run edits
        if not dry_run and not result.startswith("Error"):
            result += "\n\n⚠️ Verification required: call feishu_read to confirm the edit rendered correctly."
        return result


class FeishuCreateTool(_FeishuClientTool):
    """Create a new Feishu wiki page."""

    name = "feishu_create"
    description = (
        "Create a new Feishu wiki page under (or next to) a reference page. "
        "Returns the new page URL. "
        "After this tool succeeds, you MUST call feishu_list to verify "
        "the page structure, and feishu_read to verify content if markdown "
        "was provided. Do not claim completion without verification."
    )
    parameters = {
        "type": "object",
        "properties": {
            "ref_url": {"type": "string", "description": "Reference page wiki URL"},
            "title": {"type": "string", "description": "Title for the new page"},
            "markdown": {"type": "string", "description": "Markdown content (optional)"},
            "position": {
                "type": "string",
                "enum": ["child", "sibling"],
                "description": "Create as child (default) or sibling",
            },
            "grant_access_to": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List of user/chat IDs to grant full_access after creation. "
                    "Auto-detects type from ID prefix (ou_ = openid, oc_ = chatid)."
                ),
            },
        },
        "required": ["ref_url", "title"],
    }

    @property
    def risk_level(self) -> str:
        return "medium"

    def __init__(self, client: Any, allow_from: list[str] | None = None) -> None:
        super().__init__(client)
        self._allow_from = allow_from or []

    async def execute(
        self,
        ref_url: str,
        title: str,
        markdown: str | None = None,
        position: str = "child",
        grant_access_to: list[str] | None = None,
        **kw: Any,
    ) -> str:
        del kw
        # Auto-grant to allowFrom contacts when not explicitly specified
        if grant_access_to is None and self._allow_from:
            grant_access_to = list(self._allow_from)

        result = await _run(
            self._client.create_page,
            ref_url,
            title,
            markdown=markdown,
            position=position,
            grant_access_to=grant_access_to,
        )
        if not result.startswith("Error"):
            hint = "\n\n⚠️ Verification required: call feishu_list to verify page structure"
            if markdown:
                hint += ", then feishu_read to verify content rendered correctly"
            hint += "."
            result += hint
        return result


class FeishuSendTool(_FeishuClientTool):
    """Send a message to a Feishu user or chat."""

    name = "feishu_send"
    description = (
        "Send a message to a Feishu user or chat. "
        "Recipient: open_id (ou_xxx), chat_id (oc_xxx), user_id, email, or a unique user name. "
        "Supports: text (default), card (interactive card with markdown), "
        "image (by image_key), file (by file_key), post (rich text)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "user": {"type": "string", "description": "Recipient identifier"},
            "message": {"type": "string", "description": "Text content (for text/card/post)"},
            "msg_type": {
                "type": "string",
                "enum": ["text", "card", "image", "file", "post"],
                "description": "Message type (default: text)",
            },
            "title": {
                "type": "string",
                "description": "Title (for card/post)",
            },
            "image_key": {
                "type": "string",
                "description": "Image key from upload API (for image)",
            },
            "file_key": {
                "type": "string",
                "description": "File key from upload API (for file)",
            },
            "buttons": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "url": {"type": "string"},
                    },
                },
                "description": "Optional buttons (for card)",
            },
        },
        "required": ["user"],
    }

    @property
    def risk_level(self) -> str:
        return "medium"

    async def execute(
        self,
        user: str,
        message: str | None = None,
        msg_type: str = "text",
        title: str | None = None,
        image_key: str | None = None,
        file_key: str | None = None,
        buttons: list[dict[str, Any]] | None = None,
        **kw: Any,
    ) -> str:
        del kw
        if msg_type == "text":
            if not message:
                return _missing("message", "text messages")
            return await _run(self._client.send_message, user, message)

        if msg_type == "card":
            if not message:
                return _missing("message", "card messages (markdown body)")
            return await _run(self._client.send_card, user, title or "", message, buttons=buttons)

        if msg_type == "image":
            if not image_key:
                return _missing("image_key", "image messages")
            return await _run(self._client.send_image, user, image_key)

        if msg_type == "file":
            if not file_key:
                return _missing("file_key", "file messages")
            return await _run(self._client.send_file, user, file_key)

        if msg_type == "post":
            if not message:
                return _missing("message", "post messages")
            # Convert simple text to post format: one line with one text element
            post_content = [[{"tag": "text", "text": message}]]
            return await _run(self._client.send_post, user, title or "", post_content)

        return _unknown("msg_type", msg_type, "text, card, image, file, or post")


# ---------------------------------------------------------------------------
# P3 — Supplementary tools
# ---------------------------------------------------------------------------


class FeishuCommentsTool(_FeishuClientTool):
    """Read, add, reply, resolve, or delete comments on a Feishu page."""

    name = "feishu_comments"
    description = (
        "Manage comments on a Feishu document. Actions: "
        "read (list all comments, default), "
        "add (add a new comment), "
        "reply (reply to an existing comment thread), "
        "resolve (resolve/unresolve a comment), "
        "delete (delete a comment)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Feishu page URL"},
            "action": {
                "type": "string",
                "enum": ["read", "add", "reply", "resolve", "delete"],
                "description": "Operation to perform (default: read)",
            },
            "content": {
                "type": "string",
                "description": "Comment text (for add/reply)",
            },
            "comment_id": {
                "type": "string",
                "description": "Comment ID (for reply/resolve/delete)",
            },
        },
        "required": ["url"],
    }

    @property
    def risk_level(self) -> str:
        return "medium"

    async def execute(
        self,
        url: str,
        action: str = "read",
        content: str | None = None,
        comment_id: str | None = None,
        **kw: Any,
    ) -> str:
        del kw
        if action == "read":
            return await _run(self._client.read_comments, url)

        if action == "add":
            if not content:
                return _missing("content", "add action")
            return await _run(self._client.add_comment, url, content)

        if action == "reply":
            if not comment_id:
                return _missing("comment_id", "reply action")
            if not content:
                return _missing("content", "reply action")
            return await _run(self._client.add_comment, url, content, reply_id=comment_id)

        if action == "resolve":
            if not comment_id:
                return _missing("comment_id", "resolve action")
            return await _run(self._client.resolve_comment, url, comment_id)

        if action == "delete":
            if not comment_id:
                return _missing("comment_id", "delete action")
            return await _run(self._client.delete_comment, url, comment_id)

        return _unknown("action", action, "read, add, reply, resolve, or delete")


class FeishuDownloadTool(_FeishuClientTool):
    """Download files from Feishu Drive."""

    name = "feishu_download"
    description = "Download file(s) from Feishu Drive. Supports folders, files, and sheets."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Feishu Drive URL"},
            "output_dir": {
                "type": "string",
                "description": "Save directory (default: /tmp/feishu_downloads)",
            },
            "limit": {
                "type": "integer",
                "description": "Max files from folder (default: 10)",
                "minimum": 1,
            },
            "file_extension": {
                "type": "string",
                "description": "Export format (xlsx/csv for sheets; txt/srt/mp4 for minutes)",
            },
            "dry_run": {
                "type": "boolean",
                "description": "List files without downloading (default: false)",
            },
        },
        "required": ["url"],
    }

    async def execute(
        self,
        url: str,
        output_dir: str = "/tmp/feishu_downloads",
        limit: int = 10,
        file_extension: str = "xlsx",
        dry_run: bool = False,
        **kw: Any,
    ) -> str:
        del kw
        return await _run(
            self._client.download_file,
            url,
            output_dir=output_dir,
            limit=limit,
            dry_run=dry_run,
            file_extension=file_extension,
        )


class FeishuInfoTool(_FeishuClientTool):
    """Get metadata about a Feishu page."""

    name = "feishu_info"
    description = "Get metadata (title, creator, last editor, timestamps) for a Feishu page."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Feishu page URL"},
            "max_age": {"type": "integer", "description": "Max cache age in seconds", "minimum": 0},
            "force_refresh": {"type": "boolean", "description": "Bypass cache (default: false)"},
        },
        "required": ["url"],
    }

    async def execute(
        self, url: str, max_age: int | None = None, force_refresh: bool = False, **kw: Any
    ) -> str:
        del kw
        return await _run(self._client.info_page, url, max_age=max_age, force_refresh=force_refresh)


# ---------------------------------------------------------------------------
# Spreadsheet tools
# ---------------------------------------------------------------------------


class FeishuPermTool(_FeishuClientTool):
    """Manage document permissions: list, add, update, remove, transfer ownership."""

    name = "feishu_perm"
    description = (
        "Manage Feishu document permissions. Actions: "
        "list (list collaborators), add (grant access), "
        "update (change permission level), remove (revoke access), "
        "transfer (transfer ownership). "
        "Provide the full Feishu document URL."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "add", "update", "remove", "transfer"],
                "description": "Operation to perform",
            },
            "url": {
                "type": "string",
                "description": "Feishu document URL",
            },
            "member_id": {
                "type": "string",
                "description": (
                    "Member identifier (open_id ou_xxx, chat_id oc_xxx, or user_id). "
                    "Required for add/update/remove."
                ),
            },
            "perm": {
                "type": "string",
                "enum": ["full_access", "edit", "view"],
                "description": "Permission level (for add/update)",
            },
            "new_owner": {
                "type": "string",
                "description": "New owner identifier (for transfer)",
            },
        },
        "required": ["action", "url"],
    }

    @property
    def risk_level(self) -> str:
        return "medium"

    async def execute(
        self,
        action: str,
        url: str,
        member_id: str | None = None,
        perm: str | None = None,
        new_owner: str | None = None,
        **kw: Any,
    ) -> str:
        del kw
        if action == "list":
            return await _run(self._client.perm_list, url)

        if action == "add":
            if not member_id:
                return _missing("member_id", "add action")
            return await _run(self._client.perm_add, url, member_id, perm=perm or "full_access")

        if action == "update":
            if not member_id:
                return _missing("member_id", "update action")
            if not perm:
                return _missing("perm", "update action")
            return await _run(self._client.perm_update, url, member_id, perm)

        if action == "remove":
            if not member_id:
                return _missing("member_id", "remove action")
            return await _run(self._client.perm_remove, url, member_id)

        if action == "transfer":
            if not new_owner:
                return _missing("new_owner", "transfer action")
            return await _run(self._client.perm_transfer, url, new_owner)

        return _unknown("action", action, "list, add, update, remove, or transfer")


class FeishuChatTool(_FeishuClientTool):
    """Manage Feishu chats/groups: create, info, update, members, messages, pin, react."""

    name = "feishu_chat"
    description = (
        "Manage Feishu/Lark group chats. Actions: "
        "create (create a group chat), info (get chat details), "
        "update (rename/describe a chat), members (list members), "
        "add_members (add users to chat), remove_members (remove users), "
        "messages (get message history), pin (pin a message), "
        "react (add emoji reaction to a message)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "create",
                    "info",
                    "update",
                    "members",
                    "add_members",
                    "remove_members",
                    "messages",
                    "pin",
                    "react",
                ],
                "description": "Operation to perform",
            },
            "chat_id": {
                "type": "string",
                "description": "Chat ID (for info/update/members/add_members/remove_members/messages)",
            },
            "name": {
                "type": "string",
                "description": "Chat name (for create/update)",
            },
            "description": {
                "type": "string",
                "description": "Chat description (for create/update)",
            },
            "user_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "User IDs (for create/add_members/remove_members)",
            },
            "message_id": {
                "type": "string",
                "description": "Message ID (for pin/react)",
            },
            "emoji": {
                "type": "string",
                "description": "Emoji type string e.g. 'THUMBSUP', 'SMILE' (for react)",
            },
        },
        "required": ["action"],
    }

    @property
    def risk_level(self) -> str:
        return "medium"

    async def execute(
        self,
        action: str,
        chat_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        user_ids: list[str] | None = None,
        message_id: str | None = None,
        emoji: str | None = None,
        **kw: Any,
    ) -> str:
        del kw
        if action == "create":
            if not name:
                return _missing("name", "create action")
            return await _run(
                self._client.chat_create,
                name,
                description=description or "",
                user_ids=user_ids,
            )

        if action == "info":
            if not chat_id:
                return _missing("chat_id", "info action")
            return await _run(self._client.chat_info, chat_id)

        if action == "update":
            if not chat_id:
                return _missing("chat_id", "update action")
            return await _run(self._client.chat_update, chat_id, name=name, description=description)

        if action == "members":
            if not chat_id:
                return _missing("chat_id", "members action")
            return await _run(self._client.chat_members, chat_id)

        if action == "add_members":
            if not chat_id:
                return _missing("chat_id", "add_members action")
            if not user_ids:
                return _missing("user_ids", "add_members action")
            return await _run(self._client.chat_add_members, chat_id, user_ids)

        if action == "remove_members":
            if not chat_id:
                return _missing("chat_id", "remove_members action")
            if not user_ids:
                return _missing("user_ids", "remove_members action")
            return await _run(self._client.chat_remove_members, chat_id, user_ids)

        if action == "messages":
            if not chat_id:
                return _missing("chat_id", "messages action")
            return await _run(self._client.chat_messages, chat_id)

        if action == "pin":
            if not message_id:
                return _missing("message_id", "pin action")
            from src.feishu import api_chat  # noqa: PLC0415

            return await _run(api_chat.pin_message, self._client._client, message_id)

        if action == "react":
            if not message_id:
                return _missing("message_id", "react action")
            if not emoji:
                return _missing("emoji", "react action")
            from src.feishu import api_chat  # noqa: PLC0415

            return await _run(api_chat.add_reaction, self._client._client, message_id, emoji)

        return _unknown(
            "action",
            action,
            "create, info, update, members, add_members, remove_members, messages, pin, or react",
        )


class FeishuSheetTool(_FeishuClientTool):
    """Read and write Feishu spreadsheet data."""

    name = "feishu_sheet"
    description = (
        "Read, write, or append data in a Feishu spreadsheet. "
        "Actions: read (get cells as markdown table), write (set cell values), "
        "append (add rows to end), info (get sheet metadata). "
        "Provide the full Feishu sheet URL."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write", "append", "info"],
                "description": "Operation to perform",
            },
            "url": {
                "type": "string",
                "description": "Feishu spreadsheet URL (e.g. https://<tenant>.feishu.cn/sheets/...)",
            },
            "range": {
                "type": "string",
                "description": 'Cell range e.g. "A1:C10" (optional for read, required for write)',
            },
            "values": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "number"},
                            {"type": "boolean"},
                            {"type": "null"},
                        ]
                    },
                },
                "description": "2D array of cell values (for write/append)",
            },
        },
        "required": ["action", "url"],
    }

    @property
    def risk_level(self) -> str:
        # Dynamically determined in execute, but schema needs a static default
        return "medium"

    async def execute(
        self,
        action: str,
        url: str,
        range: str | None = None,
        values: list[list[Any]] | None = None,
        **kw: Any,
    ) -> str:
        del kw
        if action == "read":
            return await _run(self._client.read_sheet, url, range=range or "")
        if action == "info":
            return await _run(self._client.sheet_info, url)
        if action == "write":
            if not range:
                return _missing("range", "write action (e.g. 'A1:C3')")
            if not values:
                return _missing("values", "write action")
            return await _run(self._client.write_sheet, url, range, values)
        if action == "append":
            if not values:
                return _missing("values", "append action")
            return await _run(self._client.append_sheet, url, values)
        return _unknown("action", action, "read, write, append, or info")


# ---------------------------------------------------------------------------
# P2 -- Task tools
# ---------------------------------------------------------------------------


class FeishuTaskTool(_FeishuClientTool):
    """Manage Feishu tasks: list, get, create, complete, delete, subtask."""

    name = "feishu_task"
    description = (
        "Manage Feishu/Lark tasks (task.v2). Actions: "
        "list (list tasks, optionally filter by completed), "
        "get (get task detail by ID), "
        "create (create a task with summary, description, due, assignee), "
        "complete (mark task done), "
        "delete (delete task), "
        "subtask (add a subtask to an existing task). "
        "Supports natural due dates: 'today', 'tomorrow', 'next monday'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "get", "create", "complete", "delete", "subtask"],
                "description": "Operation to perform",
            },
            "task_id": {
                "type": "string",
                "description": "Task GUID (for get/complete/delete/subtask)",
            },
            "summary": {
                "type": "string",
                "description": "Task title (for create/subtask)",
            },
            "description": {
                "type": "string",
                "description": "Task description (for create)",
            },
            "due": {
                "type": "string",
                "description": (
                    "Due date: RFC3339 (e.g. '2026-03-25T18:00:00+08:00'), "
                    "natural ('today', 'tomorrow', 'next monday'), "
                    "or epoch seconds string"
                ),
            },
            "assignee": {
                "type": "string",
                "description": "User open_id to assign (for create)",
            },
            "completed": {
                "type": "boolean",
                "description": "Filter by completion status (for list; omit for all)",
            },
        },
        "required": ["action"],
    }

    @property
    def risk_level(self) -> str:
        return "medium"

    async def execute(
        self,
        action: str,
        task_id: str | None = None,
        summary: str | None = None,
        description: str | None = None,
        due: str | None = None,
        assignee: str | None = None,
        completed: bool | None = None,
        **kw: Any,
    ) -> str:
        del kw
        if action == "list":
            return await _run(self._client.task_list, completed=completed)

        if action == "get":
            if not task_id:
                return _missing("task_id", "get action")
            return await _run(self._client.task_get, task_id)

        if action == "create":
            if not summary:
                return _missing("summary", "create action")
            due_resolved = _resolve_due_date(due) if due else None
            return await _run(
                self._client.task_create,
                summary=summary,
                description=description or "",
                due=due_resolved,
                assignee=assignee,
            )

        if action == "complete":
            if not task_id:
                return _missing("task_id", "complete action")
            return await _run(self._client.task_complete, task_id)

        if action == "delete":
            if not task_id:
                return _missing("task_id", "delete action")
            return await _run(self._client.task_delete, task_id)

        if action == "subtask":
            if not task_id:
                return _missing("task_id", "subtask action")
            if not summary:
                return _missing("summary", "subtask action")
            return await _run(self._client.task_add_subtask, task_id, summary)

        return _unknown("action", action, "list, get, create, complete, delete, or subtask")


# ---------------------------------------------------------------------------
# P2 -- File management tool
# ---------------------------------------------------------------------------


class FeishuFileTool(_FeishuClientTool):
    """Manage files on Feishu Drive: list, upload, create folder, move, copy, delete."""

    name = "feishu_file"
    description = (
        "Manage files on Feishu Drive. Actions: "
        "list (list files in a folder), "
        "upload (upload a local file), "
        "create_folder (create a subfolder), "
        "move (move a file/folder), "
        "copy (copy a file), "
        "delete (trash a file/folder), "
        "import (import a local .docx/.md/.xlsx/.csv file as a Feishu cloud document). "
        "Use folder tokens from feishu_download or feishu_list results."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "upload", "create_folder", "move", "copy", "delete", "import"],
                "description": "Operation to perform",
            },
            "folder_token": {
                "type": "string",
                "description": "Folder token (for list, create_folder, upload destination)",
            },
            "file_token": {
                "type": "string",
                "description": "File/folder token (for move, copy, delete)",
            },
            "file_type": {
                "type": "string",
                "description": 'File type hint: "file", "docx", "sheet", "folder" etc. (for delete)',
            },
            "path": {
                "type": "string",
                "description": "Local file path (for upload source)",
            },
            "name": {
                "type": "string",
                "description": "Name (for create_folder, upload display name, copy rename)",
            },
            "dest_folder": {
                "type": "string",
                "description": "Destination folder token (for move, copy)",
            },
            "target_type": {
                "type": "string",
                "enum": ["docx", "sheet"],
                "description": "Target type for import: 'docx' (default) or 'sheet'",
            },
            "wiki_parent_url": {
                "type": "string",
                "description": "Wiki page URL to place the imported doc under (for import)",
            },
        },
        "required": ["action"],
    }

    @property
    def risk_level(self) -> str:
        return "medium"

    async def execute(
        self,
        action: str,
        folder_token: str | None = None,
        file_token: str | None = None,
        file_type: str | None = None,
        path: str | None = None,
        name: str | None = None,
        dest_folder: str | None = None,
        target_type: str | None = None,
        wiki_parent_url: str | None = None,
        **kw: Any,
    ) -> str:
        del kw
        if action == "list":
            if not folder_token:
                return _missing("folder_token", "list action")
            return await _run(self._client.file_list, folder_token)

        if action == "create_folder":
            if not folder_token:
                return _missing("folder_token", "create_folder action")
            if not name:
                return _missing("name", "create_folder action")
            return await _run(self._client.file_create_folder, folder_token, name)

        if action == "upload":
            if not folder_token:
                return _missing("folder_token", "upload action")
            if not path:
                return _missing("path", "upload action")
            display_name = name or path.rsplit("/", 1)[-1]
            return await _run(self._client.file_upload, display_name, path, folder_token)

        if action == "move":
            if not file_token:
                return _missing("file_token", "move action")
            if not dest_folder:
                return _missing("dest_folder", "move action")
            return await _run(
                self._client.file_move, file_token, dest_folder, file_type=file_type or ""
            )

        if action == "copy":
            if not file_token:
                return _missing("file_token", "copy action")
            if not dest_folder:
                return _missing("dest_folder", "copy action")
            return await _run(
                self._client.file_copy,
                file_token,
                dest_folder,
                new_name=name or "",
                file_type=file_type or "",
            )

        if action == "delete":
            if not file_token:
                return _missing("file_token", "delete action")
            if not file_type:
                return _missing("file_type", "delete action")
            return await _run(self._client.file_delete, file_token, file_type)

        if action == "import":
            if not path:
                return _missing("path", "import action")
            return await _run(
                self._client.import_file,
                path,
                file_name=name,
                target_type=target_type or "docx",
                wiki_parent_url=wiki_parent_url,
            )

        return _unknown(
            "action",
            action,
            "list, upload, create_folder, move, copy, delete, or import",
        )


# ---------------------------------------------------------------------------
# P2 -- Contact / department tool
# ---------------------------------------------------------------------------


class FeishuContactTool(_FeishuClientTool):
    """Query Feishu users and departments."""

    name = "feishu_contact"
    description = (
        "Query Feishu/Lark users and departments. Actions: "
        "user (get user info by ID), "
        "search (search users by name/keyword), "
        "departments (list child departments), "
        "department_users (list users in a department), "
        "find_by_email (find user by email), "
        "find_by_phone (find user by phone number)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "user",
                    "search",
                    "departments",
                    "department_users",
                    "find_by_email",
                    "find_by_phone",
                ],
                "description": "Operation to perform",
            },
            "user_id": {
                "type": "string",
                "description": "User open_id / union_id (for user action)",
            },
            "query": {
                "type": "string",
                "description": "Search keyword (for search action)",
            },
            "department_id": {
                "type": "string",
                "description": 'Department ID (for departments/department_users; "0" for root)',
            },
            "email": {
                "type": "string",
                "description": "Email address (for find_by_email action)",
            },
            "phone": {
                "type": "string",
                "description": "Phone number (for find_by_phone action)",
            },
        },
        "required": ["action"],
    }

    async def execute(
        self,
        action: str,
        user_id: str | None = None,
        query: str | None = None,
        department_id: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        **kw: Any,
    ) -> str:
        del kw
        if action == "user":
            if not user_id:
                return _missing("user_id", "user action")
            return await _run(self._client.info_user, user_id)

        if action == "search":
            if not query:
                return _missing("query", "search action")
            return await _run(self._client.search_users, query)

        if action == "departments":
            parent = department_id or "0"
            return await _run(self._client.contact_departments, parent)

        if action == "department_users":
            if not department_id:
                return _missing("department_id", "department_users action")
            return await _run(self._client.contact_department_users, department_id)

        if action == "find_by_email":
            if not email:
                return _missing("email", "find_by_email action")
            return await _run(self._client.contact_find_by_email, email)

        if action == "find_by_phone":
            if not phone:
                return _missing("phone", "find_by_phone action")
            return await _run(self._client.contact_find_by_phone, phone)

        return _unknown(
            "action",
            action,
            "user, search, departments, department_users, find_by_email, or find_by_phone",
        )


def _resolve_due_date(value: str) -> str:
    """Convert natural date strings or RFC3339 to epoch seconds string.

    Supports: "today", "tomorrow", "next monday", RFC3339, or raw epoch.
    Returns epoch seconds as a string (what Feishu task.v2 Due expects).
    """
    from datetime import datetime, timedelta  # noqa: PLC0415

    lowered = value.strip().lower()
    now = datetime.now().astimezone()

    if lowered == "today":
        dt = now.replace(hour=23, minute=59, second=59, microsecond=0)
        return str(int(dt.timestamp()))

    if lowered == "tomorrow":
        dt = (now + timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=0)
        return str(int(dt.timestamp()))

    if lowered.startswith("next "):
        day_name = lowered[5:].strip()
        day_map = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        target_weekday = day_map.get(day_name)
        if target_weekday is not None:
            days_ahead = target_weekday - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            dt = (now + timedelta(days=days_ahead)).replace(
                hour=23, minute=59, second=59, microsecond=0
            )
            return str(int(dt.timestamp()))

    # Try parsing as RFC3339
    try:
        dt = datetime.fromisoformat(value)
        return str(int(dt.timestamp()))
    except (ValueError, TypeError):
        pass

    # Already epoch seconds -- pass through
    return value


class FeishuAuthTool(Tool):
    """Initiate or complete Feishu OAuth re-authorization in chat.

    Two-step flow:
    1. action="start" — generate auth URL and send to user via Feishu card.
    2. action="exchange" — user sends back the code; exchange for tokens.
    Also: action="status" — check current token status.
    """

    name = "feishu_auth"
    description = (
        "Manage Feishu OAuth authorization. Actions: "
        "status (check token validity), "
        "start (generate auth URL and send to user), "
        "exchange (exchange authorization code for tokens)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "start", "exchange"],
                "description": "Operation to perform",
            },
            "code": {
                "type": "string",
                "description": "Authorization code from user (for exchange action)",
            },
        },
        "required": ["action"],
    }

    @property
    def owner_only(self) -> bool:
        return True

    def __init__(self, app_id: str, app_secret: str, token_dir: str) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._token_dir = token_dir
        self._last_redirect_uri: str | None = None

    async def execute(
        self,
        action: str,
        code: str | None = None,
        **kw: Any,
    ) -> str:
        del kw
        if action == "status":
            return self._check_status()

        if action == "start":
            return await self._start_auth()

        if action == "exchange":
            if not code:
                return _missing("code", "exchange action")
            return await self._exchange_code(code)

        return _unknown("action", action, "status, start, or exchange")

    def _check_status(self) -> str:
        """Check current token status."""
        import json
        import time
        from pathlib import Path

        token_dir = Path(self._token_dir).expanduser()
        lines: list[str] = []

        for name, fmt in [
            ("access_token", lambda ttl: f"TTL={ttl}s ({ttl / 60:.1f}min)"),
            ("refresh_token", lambda ttl: f"TTL={ttl}s ({ttl / 86400:.1f}d)"),
        ]:
            path = token_dir / f"{name}.json"
            if not path.exists():
                lines.append(f"❌ {name}: not found")
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                ttl = data["expires_epoch"] - int(time.time())
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                lines.append(f"❌ {name}: corrupt file ({exc}). Re-authorize required.")
                continue
            if ttl > 0:
                lines.append(f"✅ {name}: valid, {fmt(ttl)}")
            else:
                suffix = (
                    " Re-authorization required (use action='start')."
                    if name == "refresh_token"
                    else ""
                )
                lines.append(f"❌ {name}: expired {-ttl}s ago.{suffix}")

        return "\n".join(lines)

    async def _start_auth(self) -> str:
        """Generate auth URL and send to user."""
        from src.feishu.remote_auth import generate_auth_url, get_gateway_redirect_uri

        # Use gateway callback URL if available (auto-exchange, no manual code copy)
        gateway_uri = get_gateway_redirect_uri()
        prefix = (
            "[IMPORTANT: Forward the ENTIRE message below to the user verbatim, "
            "including the full URL. Do NOT summarize or omit the link.]\n\n"
        )
        if gateway_uri:
            self._last_redirect_uri = gateway_uri
            auth_url, state = generate_auth_url(app_id=self._app_id, redirect_uri=gateway_uri)
            # Register state with the OAuth callback server for CSRF verification
            if self._register_oauth_state(state, gateway_uri):
                msg = (
                    "🔑 **飞书授权链接已生成**\n\n"
                    "**操作步骤：**\n"
                    "1. 点击链接进行授权\n"
                    "2. 授权完成后 token 会自动保存（无需手动复制 code）\n\n"
                    f"**授权链接：**\n{auth_url}\n\n"
                    "_提示：授权成功后我会自动通知你。_"
                )
                return prefix + msg

        self._last_redirect_uri = None
        auth_url, _state = generate_auth_url(app_id=self._app_id)
        msg = (
            "🔑 **飞书授权链接已生成**\n\n"
            "**操作步骤：**\n"
            "1. 点击链接进行授权\n"
            "2. 授权后页面会跳转到 localhost（显示无法访问是正常的）\n"
            "3. 复制浏览器地址栏中 `code=` 后面的那串字符\n"
            "4. 把 code 发给我，我来完成授权\n\n"
            f"**授权链接：**\n{auth_url}\n\n"
            "_提示：code 有效期很短，请尽快操作。_"
        )

        return prefix + msg

    def _register_oauth_state(self, state: str, redirect_uri: str) -> bool:
        """Register state so the gateway callback can validate it strictly."""
        try:
            from src.feishu.oauth_callback import register_oauth_state

            register_oauth_state(state, token_dir=self._token_dir, redirect_uri=redirect_uri)
            return True
        except Exception:
            return False

    async def _exchange_code(self, code: str) -> str:
        """Exchange authorization code for tokens."""
        import asyncio

        from src.feishu.remote_auth import exchange_auth_code

        # redirect_uri must match what was used in _start_auth
        redirect_uri = self._last_redirect_uri
        kwargs: dict[str, Any] = {
            "code": code,
            "app_id": self._app_id,
            "app_secret": self._app_secret,
            "token_dir": self._token_dir,
        }
        if redirect_uri:
            kwargs["redirect_uri"] = redirect_uri

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: exchange_auth_code(**kwargs))

        if result["ok"]:
            return (
                f"✅ 授权成功！\n"
                f"- access_token TTL: {result['access_token_ttl']}s "
                f"({result['access_token_ttl'] / 60:.0f}min)\n"
                f"- refresh_token TTL: {result['refresh_token_ttl']}s "
                f"({result['refresh_token_ttl'] / 86400:.1f}d)\n\n"
                "飞书工具已恢复可用。"
            )
        else:
            return f"❌ 授权失败: {result['error']}"
