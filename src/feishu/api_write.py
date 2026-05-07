"""Feishu/Lark write API layer using lark-oapi SDK.

Provides document editing, wiki node creation, and markdown-to-block conversion.
Rate-limited at 3 requests/second to respect Feishu API limits.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

import lark_oapi as lark
from lark_oapi.api.docx.v1 import (
    BatchDeleteDocumentBlockChildrenRequest,
    BatchDeleteDocumentBlockChildrenRequestBody,
    ConvertDocumentRequest,
    ConvertDocumentRequestBody,
    CreateDocumentBlockChildrenRequest,
    CreateDocumentBlockChildrenRequestBody,
    CreateDocumentBlockDescendantRequest,
    CreateDocumentBlockDescendantRequestBody,
)
from lark_oapi.api.drive.v1 import (
    BaseMember,
    CopyFileRequest,
    CopyFileRequestBody,
    CreateFolderFileRequest,
    CreateFolderFileRequestBody,
    CreatePermissionMemberRequest,
    DeleteFileRequest,
    DeletePermissionMemberRequest,
    ListPermissionMemberRequest,
    MoveFileRequest,
    MoveFileRequestBody,
    TransferOwnerPermissionMemberRequest,
    UpdatePermissionMemberRequest,
    UploadAllFileRequest,
    UploadAllFileRequestBody,
)
from lark_oapi.api.wiki.v2 import (
    CreateSpaceNodeRequest,
    Node,
)

from src.feishu.api import _call_with_option, _check, _request_option, _unmarshal, ctx_current_token
from src.feishu.retry import with_retry

# ---------------------------------------------------------------------------
# Preemptive throttle (prevents 429s proactively)
# ---------------------------------------------------------------------------


class PreemptiveThrottle:
    """Simple sleep-based rate limiter.

    Default: 3 requests/second (Feishu write API limit).
    Prevents rate-limit errors proactively; the retry module handles them reactively.
    """

    def __init__(self, max_per_second: float = 3.0) -> None:
        self._min_interval = 1.0 / max_per_second
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()


# Keep RateLimiter as alias for backward compatibility
RateLimiter = PreemptiveThrottle

_rate_limiter = PreemptiveThrottle(max_per_second=3.0)


# ---------------------------------------------------------------------------
# Drive file management (create folder, move, copy, delete, upload)
# ---------------------------------------------------------------------------


def create_folder(client: lark.Client, folder_token: str, name: str) -> dict:
    """Create a subfolder inside *folder_token*.

    Uses ``POST /open-apis/drive/v1/files/create_folder``
    (SDK: ``drive.v1.file.create_folder``).

    Args:
        client: lark-oapi client.
        folder_token: Parent folder token.
        name: Name for the new subfolder.

    Returns:
        Dict with ``token`` (new folder token) and ``url``.
    """
    _rate_limiter.wait()
    option = _request_option()

    body = CreateFolderFileRequestBody.builder().folder_token(folder_token).name(name).build()
    request = CreateFolderFileRequest.builder().request_body(body).build()

    response = _call_with_option(client.drive.v1.file.create_folder, request, option)
    _check(response, "create_folder")
    return _unmarshal(response.data)


def move_file(client: lark.Client, file_token: str, dest_folder: str, file_type: str = "") -> dict:
    """Move a file or folder to *dest_folder*.

    Uses ``POST /open-apis/drive/v1/files/:file_token/move``
    (SDK: ``drive.v1.file.move``).

    Args:
        client: lark-oapi client.
        file_token: The file/folder token to move.
        dest_folder: Destination folder token.
        file_type: Optional type hint (``"file"`` | ``"docx"`` | ``"folder"`` etc.).

    Returns:
        Move result dict.
    """
    _rate_limiter.wait()
    option = _request_option()

    body_builder = MoveFileRequestBody.builder().folder_token(dest_folder)
    if file_type:
        body_builder = body_builder.type(file_type)
    request = (
        MoveFileRequest.builder().file_token(file_token).request_body(body_builder.build()).build()
    )

    response = _call_with_option(client.drive.v1.file.move, request, option)
    _check(response, "move_file")
    return _unmarshal(response.data)


def copy_file(
    client: lark.Client, file_token: str, dest_folder: str, new_name: str = "", file_type: str = ""
) -> dict:
    """Copy a file to *dest_folder*.

    Uses ``POST /open-apis/drive/v1/files/:file_token/copy``
    (SDK: ``drive.v1.file.copy``).

    Args:
        client: lark-oapi client.
        file_token: The file token to copy.
        dest_folder: Destination folder token.
        new_name: Name for the copy (uses original name if empty).
        file_type: Optional type hint.

    Returns:
        Copied file metadata dict.
    """
    _rate_limiter.wait()
    option = _request_option()

    body_builder = CopyFileRequestBody.builder().folder_token(dest_folder)
    if new_name:
        body_builder = body_builder.name(new_name)
    if file_type:
        body_builder = body_builder.type(file_type)
    request = (
        CopyFileRequest.builder().file_token(file_token).request_body(body_builder.build()).build()
    )

    response = _call_with_option(client.drive.v1.file.copy, request, option)
    _check(response, "copy_file")
    return _unmarshal(response.data)


def delete_file(client: lark.Client, file_token: str, file_type: str) -> bool:
    """Delete (move to trash) a file or folder.

    Uses ``DELETE /open-apis/drive/v1/files/:file_token?type=<type>``
    (SDK: ``drive.v1.file.delete``).

    Args:
        client: lark-oapi client.
        file_token: The file/folder token.
        file_type: ``"file"`` | ``"docx"`` | ``"sheet"`` | ``"folder"`` etc.

    Returns:
        ``True`` on success.
    """
    _rate_limiter.wait()
    option = _request_option()

    request = DeleteFileRequest.builder().file_token(file_token).type(file_type).build()

    response = _call_with_option(client.drive.v1.file.delete, request, option)
    _check(response, "delete_file")
    return True


def upload_file(
    client: lark.Client,
    file_name: str,
    file_path: str,
    parent_token: str,
    parent_type: str = "explorer",
) -> dict:
    """Upload a local file to Feishu Drive (single-part, < 20 MB).

    Uses ``POST /open-apis/drive/v1/files/upload_all``
    (SDK: ``drive.v1.file.upload_all``).

    Args:
        client: lark-oapi client.
        file_name: Display name in Drive.
        file_path: Local filesystem path to the file.
        parent_token: Destination folder token.
        parent_type: ``"explorer"`` (default) for My Drive folders.

    Returns:
        Uploaded file metadata dict with ``file_token``.
    """
    import os

    _rate_limiter.wait()
    option = _request_option()

    file_size = os.path.getsize(file_path)
    file_obj = open(file_path, "rb")  # noqa: SIM115

    body = (
        UploadAllFileRequestBody.builder()
        .file_name(file_name)
        .parent_type(parent_type)
        .parent_node(parent_token)
        .size(file_size)
        .file(file_obj)
        .build()
    )
    request = UploadAllFileRequest.builder().request_body(body).build()

    try:
        response = _call_with_option(client.drive.v1.file.upload_all, request, option)
    finally:
        file_obj.close()

    _check(response, "upload_file")
    return _unmarshal(response.data)


# ---------------------------------------------------------------------------
# Comment operations (drive.v1)
# ---------------------------------------------------------------------------


def add_comment(
    client: lark.Client,
    file_token: str,
    file_type: str,
    content: str,
    reply_id: str | None = None,
) -> dict:
    """Add a comment to a document (or reply to existing comment).

    Uses raw ``BaseRequest`` because lark-oapi SDK does not expose
    a typed builder for ``POST /open-apis/drive/v1/files/:file_token/comments``.

    Args:
        client: lark-oapi client.
        file_token: The file/document token.
        file_type: ``"docx"`` | ``"doc"`` | ``"sheet"`` etc.
        content: Plain-text comment body.
        reply_id: If set, reply to this comment ID instead of creating a new thread.

    Returns:
        Created comment dict.
    """
    _rate_limiter.wait()

    body: dict = {
        "file_type": file_type,
        "content": {"elements": [{"type": "text_run", "text_run": {"text": content}}]},
    }
    if reply_id:
        body["reply_id"] = reply_id

    uri = f"/open-apis/drive/v1/files/{file_token}/comments"
    return _raw_request(client, lark.HttpMethod.POST, uri, "add_comment", body=body)


def resolve_comment(
    client: lark.Client,
    file_token: str,
    file_type: str,
    comment_id: str,
    is_solved: bool = True,
) -> dict:
    """Resolve or unresolve a comment.

    Uses ``PATCH /open-apis/drive/v1/files/:file_token/comments/:comment_id``.

    Args:
        client: lark-oapi client.
        file_token: The file/document token.
        file_type: ``"docx"`` | ``"doc"`` | ``"sheet"`` etc.
        comment_id: The comment ID to resolve/unresolve.
        is_solved: ``True`` to resolve, ``False`` to unresolve.

    Returns:
        Updated comment dict.
    """
    _rate_limiter.wait()

    body: dict = {
        "file_type": file_type,
        "is_solved": is_solved,
    }

    uri = f"/open-apis/drive/v1/files/{file_token}/comments/{comment_id}"
    return _raw_request(client, lark.HttpMethod.PATCH, uri, "resolve_comment", body=body)


def delete_comment(
    client: lark.Client,
    file_token: str,
    file_type: str,
    comment_id: str,
) -> bool:
    """Delete a comment.

    Uses ``DELETE /open-apis/drive/v1/files/:file_token/comments/:comment_id``.

    Args:
        client: lark-oapi client.
        file_token: The file/document token.
        file_type: ``"docx"`` | ``"doc"`` | ``"sheet"`` etc.
        comment_id: The comment ID to delete.

    Returns:
        ``True`` on success.
    """
    _rate_limiter.wait()

    uri = f"/open-apis/drive/v1/files/{file_token}/comments/{comment_id}"
    _raw_request(
        client,
        lark.HttpMethod.DELETE,
        uri,
        "delete_comment",
        query={"file_type": file_type},
    )
    return True


def _raw_request(
    client: lark.Client,
    method: Any,
    uri: str,
    action: str,
    *,
    body: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
) -> dict:
    req = lark.BaseRequest()
    req.http_method = method
    req.uri = uri
    if body is not None:
        req.body = body
    for key, value in (query or {}).items():
        req.add_query(key, value)

    token = ctx_current_token.get()
    req.token_types = {lark.AccessTokenType.USER} if token else {lark.AccessTokenType.TENANT}

    option = _request_option()
    response = client.request(req, option) if option is not None else client.request(req)
    _check(response, action)

    raw = response.raw.content
    return json.loads(raw).get("data", {}) if raw else {}


# ---------------------------------------------------------------------------
# Async retry-wrapped comment variants
# ---------------------------------------------------------------------------


async def add_comment_with_retry(
    client: lark.Client,
    file_token: str,
    file_type: str,
    content: str,
    reply_id: str | None = None,
    **retry_kwargs,
) -> dict:
    """add_comment with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        add_comment,
        client,
        file_token,
        file_type,
        content,
        reply_id=reply_id,
        action="add_comment",
        **retry_kwargs,
    )


async def resolve_comment_with_retry(
    client: lark.Client,
    file_token: str,
    file_type: str,
    comment_id: str,
    is_solved: bool = True,
    **retry_kwargs,
) -> dict:
    """resolve_comment with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        resolve_comment,
        client,
        file_token,
        file_type,
        comment_id,
        is_solved=is_solved,
        action="resolve_comment",
        **retry_kwargs,
    )


async def delete_comment_with_retry(
    client: lark.Client,
    file_token: str,
    file_type: str,
    comment_id: str,
    **retry_kwargs,
) -> bool:
    """delete_comment with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        delete_comment,
        client,
        file_token,
        file_type,
        comment_id,
        action="delete_comment",
        **retry_kwargs,
    )


# ---------------------------------------------------------------------------
# Async retry-wrapped file management variants
# ---------------------------------------------------------------------------


async def create_folder_with_retry(
    client: lark.Client,
    folder_token: str,
    name: str,
    **retry_kwargs,
) -> dict:
    """create_folder with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        create_folder, client, folder_token, name, action="create_folder", **retry_kwargs
    )


async def move_file_with_retry(
    client: lark.Client,
    file_token: str,
    dest_folder: str,
    file_type: str = "",
    **retry_kwargs,
) -> dict:
    """move_file with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        move_file,
        client,
        file_token,
        dest_folder,
        file_type=file_type,
        action="move_file",
        **retry_kwargs,
    )


async def copy_file_with_retry(
    client: lark.Client,
    file_token: str,
    dest_folder: str,
    new_name: str = "",
    file_type: str = "",
    **retry_kwargs,
) -> dict:
    """copy_file with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        copy_file,
        client,
        file_token,
        dest_folder,
        new_name=new_name,
        file_type=file_type,
        action="copy_file",
        **retry_kwargs,
    )


async def delete_file_with_retry(
    client: lark.Client,
    file_token: str,
    file_type: str,
    **retry_kwargs,
) -> bool:
    """delete_file with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        delete_file, client, file_token, file_type, action="delete_file", **retry_kwargs
    )


async def upload_file_with_retry(
    client: lark.Client,
    file_name: str,
    file_path: str,
    parent_token: str,
    parent_type: str = "explorer",
    **retry_kwargs,
) -> dict:
    """upload_file with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        upload_file,
        client,
        file_name,
        file_path,
        parent_token,
        parent_type=parent_type,
        action="upload_file",
        **retry_kwargs,
    )


# ---------------------------------------------------------------------------
# Wiki node creation
# ---------------------------------------------------------------------------


def create_wiki_node(
    client: lark.Client,
    space_id: str,
    parent_node_token: str | None = None,
    title: str | None = None,
    obj_type: str = "docx",
) -> dict:
    """Create a new wiki node in a knowledge space.

    Args:
        client: lark-oapi client.
        space_id: The wiki space ID.
        parent_node_token: Parent node; ``None`` creates at root level.
        title: Document title.
        obj_type: ``"docx"`` | ``"sheet"`` | ``"mindnote"`` | ``"bitable"`` | ``"slides"``.

    Returns:
        Created node dict (node_token, obj_token, space_id, etc.).
    """
    _rate_limiter.wait()
    option = _request_option()

    node_builder = Node.builder().obj_type(obj_type).node_type("origin")
    if parent_node_token:
        node_builder = node_builder.parent_node_token(parent_node_token)
    if title:
        node_builder = node_builder.title(title)

    request = (
        CreateSpaceNodeRequest.builder()
        .space_id(space_id)
        .request_body(node_builder.build())
        .build()
    )

    response = _call_with_option(client.wiki.v2.space_node.create, request, option)
    _check(response, "create_wiki_node")
    return _unmarshal(response.data.node)


# ---------------------------------------------------------------------------
# Block operations (docx.v1)
# ---------------------------------------------------------------------------


def create_descendant_blocks(
    client: lark.Client,
    document_id: str,
    block_id: str,
    children_ids: list[str],
    descendants: list[dict],
    index: int = -1,
) -> dict:
    """Insert blocks as descendants of a parent block.

    Args:
        client: lark-oapi client.
        document_id: The document ID.
        block_id: The parent block ID to insert under.
        children_ids: List of block IDs for direct children to insert.
        descendants: List of block dicts (the actual block content).
        index: Position to insert at. ``-1`` means append at end.

    Returns:
        API response data dict.
    """
    _rate_limiter.wait()
    option = _request_option()

    body = (
        CreateDocumentBlockDescendantRequestBody.builder()
        .children_id(children_ids)
        .descendants(descendants)
        .index(index)
        .build()
    )

    request = (
        CreateDocumentBlockDescendantRequest.builder()
        .document_id(document_id)
        .block_id(block_id)
        .document_revision_id(-1)
        .client_token(str(uuid.uuid4()))
        .request_body(body)
        .build()
    )

    response = _call_with_option(client.docx.v1.document_block_descendant.create, request, option)
    _check(response, "create_descendant_blocks")
    return _unmarshal(response.data)


BLOCK_BATCH_SIZE = 50


def create_descendant_blocks_batched(
    client: lark.Client,
    document_id: str,
    block_id: str,
    children_ids: list[str],
    descendants: list[dict],
) -> list[dict]:
    """Insert blocks in batches of 50 to prevent timeout on large documents.

    Falls back to single-call ``create_descendant_blocks`` when the block count
    is within limits.  Returns a list of per-batch results.
    """
    if len(children_ids) <= BLOCK_BATCH_SIZE:
        return [create_descendant_blocks(client, document_id, block_id, children_ids, descendants)]

    # Build a lookup: id -> descendant dict
    desc_by_id = {d.get("block_id"): d for d in descendants if isinstance(d, dict)}
    results = []

    for i in range(0, len(children_ids), BLOCK_BATCH_SIZE):
        batch_ids = children_ids[i : i + BLOCK_BATCH_SIZE]
        # Collect descendants reachable from this batch of children
        batch_descs = _collect_descendants(batch_ids, desc_by_id)
        result = create_descendant_blocks(client, document_id, block_id, batch_ids, batch_descs)
        results.append(result)

    return results


def _collect_descendants(root_ids: list[str], all_descs: dict[str, dict]) -> list[dict]:
    """Collect all descendant dicts reachable from root_ids via BFS."""
    from collections import deque

    collected: list[dict] = []
    queue: deque[str] = deque(root_ids)
    seen: set[str] = set()
    while queue:
        bid = queue.popleft()
        if bid in seen:
            continue
        seen.add(bid)
        desc = all_descs.get(bid)
        if desc:
            collected.append(desc)
            # md2blocks uses "children", descendant API uses "children_id"
            child_ids = desc.get("children") or desc.get("children_id") or []
            queue.extend(child_ids)
    return collected


def create_block_children(
    client: lark.Client,
    document_id: str,
    block_id: str,
    children: list[dict],
    index: int = -1,
) -> dict:
    """Insert blocks as direct children of a parent block (Children API).

    Unlike ``create_descendant_blocks``, this uses the Children API which
    writes one level at a time and is more resilient to partial failures.

    Args:
        client: lark-oapi client.
        document_id: The document ID.
        block_id: The parent block ID.
        children: List of block dicts to insert as direct children.
        index: Position to insert at. ``-1`` means append at end.

    Returns:
        API response data dict.
    """
    _rate_limiter.wait()
    option = _request_option()

    body = CreateDocumentBlockChildrenRequestBody.builder().children(children).index(index).build()

    request = (
        CreateDocumentBlockChildrenRequest.builder()
        .document_id(document_id)
        .block_id(block_id)
        .document_revision_id(-1)
        .client_token(str(uuid.uuid4()))
        .request_body(body)
        .build()
    )

    response = _call_with_option(client.docx.v1.document_block_children.create, request, option)
    _check(response, "create_block_children")
    return _unmarshal(response.data)


def delete_blocks(
    client: lark.Client,
    document_id: str,
    block_id: str,
    start_index: int,
    end_index: int,
) -> dict:
    """Delete a range of child blocks from a parent.

    Args:
        client: lark-oapi client.
        document_id: The document ID.
        block_id: The parent block ID.
        start_index: Start index (inclusive).
        end_index: End index (exclusive).

    Returns:
        API response data dict.
    """
    _rate_limiter.wait()
    option = _request_option()

    body = (
        BatchDeleteDocumentBlockChildrenRequestBody.builder()
        .start_index(start_index)
        .end_index(end_index)
        .build()
    )

    request = (
        BatchDeleteDocumentBlockChildrenRequest.builder()
        .document_id(document_id)
        .block_id(block_id)
        .document_revision_id(-1)
        .client_token(str(uuid.uuid4()))
        .request_body(body)
        .build()
    )

    response = _call_with_option(
        client.docx.v1.document_block_children.batch_delete, request, option
    )
    _check(response, "delete_blocks")
    return _unmarshal(response.data)


# ---------------------------------------------------------------------------
# Markdown to Feishu blocks
# ---------------------------------------------------------------------------


def convert_markdown_to_blocks(client: lark.Client, markdown: str) -> dict:
    """Convert Markdown text to Feishu block structures.

    Uses ``POST /open-apis/docx/v1/documents/blocks/convert`` (SDK: docx.v1.document.convert).

    The result dict contains:
    - ``children_id`` / ``first_level_block_ids``: top-level block IDs
    - ``descendants`` / ``blocks``: all block dicts

    Field names are normalized so the result can be passed directly to
    :func:`create_descendant_blocks`.

    Args:
        client: lark-oapi client.
        markdown: Markdown text to convert.

    Returns:
        Converted block structure dict.
    """
    _rate_limiter.wait()

    body = ConvertDocumentRequestBody.builder().content(markdown).content_type("markdown").build()

    request = ConvertDocumentRequest.builder().request_body(body).build()

    # Convert is a stateless API that works with tenant token.
    # User tokens may lack the docx:document.block:convert scope,
    # so we explicitly use a tenant token here.
    from lark_oapi.core.model.request_option import RequestOption as _ReqOption
    from lark_oapi.core.token.manager import TokenManager as _TokMgr

    try:
        tenant_token = _TokMgr.get_self_tenant_token(client.config)
        option = _ReqOption.builder().tenant_access_token(tenant_token).build()
        response = client.docx.v1.document.convert(request, option)
    except Exception:
        # Fallback: try without explicit option (SDK auto-fetch)
        response = client.docx.v1.document.convert(request)
    _check(response, "convert_markdown_to_blocks")

    result = _unmarshal(response.data)

    # Normalize field names for create_descendant_blocks compatibility
    if "first_level_block_ids" in result and "children_id" not in result:
        result["children_id"] = result["first_level_block_ids"]
    if "blocks" in result and "descendants" not in result:
        result["descendants"] = result["blocks"]

    return result


def markdown_to_blocks(markdown: str) -> dict:
    """Convert Markdown to Feishu block structure (client-side).

    Uses the local ``md2blocks`` converter which handles complex markdown,
    nested lists, large tables (auto-split to 9x9), callouts, etc.
    Falls back to server-side ``convert_markdown_to_blocks`` on error when
    a *client* is available.

    Returns:
        Dict with ``children_id`` and ``descendants`` keys, ready for
        :func:`create_descendant_blocks`.
    """
    from src.feishu.md2blocks import markdown_to_feishu_blocks

    children_ids, descendants = markdown_to_feishu_blocks(markdown)
    return {"children_id": children_ids, "descendants": descendants}


def markdown_to_blocks_with_fallback(client: lark.Client, markdown: str) -> dict:
    """Convert Markdown to Feishu blocks, preferring client-side conversion.

    Tries the local ``md2blocks`` converter first.  If it produces no blocks
    (edge-case), falls back to the server-side API.

    Args:
        client: lark-oapi client (used only for server-side fallback).
        markdown: Markdown text to convert.

    Returns:
        Dict with ``children_id`` and ``descendants`` keys.
    """
    from loguru import logger

    try:
        result = markdown_to_blocks(markdown)
        if result.get("children_id"):
            return result
        # Empty result — fall through to server-side
        logger.debug("client-side converter produced no blocks, trying server-side")
    except Exception as exc:
        logger.warning(f"client-side markdown conversion failed ({exc}), trying server-side")

    return convert_markdown_to_blocks(client, markdown)


# ---------------------------------------------------------------------------
# Wiki node deletion (raw request — no SDK binding available)
# ---------------------------------------------------------------------------


def delete_wiki_node(
    client: lark.Client,
    space_id: str,
    node_token: str,
) -> dict:
    """Delete (move to trash) a wiki node.

    The lark-oapi SDK does not expose ``space_node.delete``, so this uses
    a raw ``BaseRequest`` to call ``DELETE /open-apis/wiki/v2/spaces/:space_id/nodes/:node_token``.

    Args:
        client: lark-oapi client.
        space_id: The wiki space ID.
        node_token: The node token to delete.

    Returns:
        API response data dict.
    """
    _rate_limiter.wait()

    uri = f"/open-apis/wiki/v2/spaces/{space_id}/nodes/{node_token}"
    return _raw_request(client, lark.HttpMethod.DELETE, uri, "delete_wiki_node")


# ---------------------------------------------------------------------------
# Permission management
# ---------------------------------------------------------------------------


def detect_member_type(member_id: str) -> str:
    """Detect Feishu member type from ID prefix.

    - ``ou_`` -> ``"openid"``
    - ``oc_`` -> ``"chatid"``
    - otherwise -> ``"userid"``
    """
    if member_id.startswith("ou_"):
        return "openid"
    if member_id.startswith("oc_"):
        return "chatid"
    return "userid"


def add_permission_member(
    client: lark.Client,
    file_token: str,
    file_type: str,
    member_type: str,
    member_id: str,
    perm: str = "full_access",
) -> dict:
    """Grant permission to a member on a document.

    Uses ``POST /open-apis/drive/v1/permissions/:token/members``
    (SDK: ``drive.v1.permission_member.create``).

    Args:
        client: lark-oapi client.
        file_token: The document/file token.
        file_type: ``"doc"`` | ``"docx"`` | ``"sheet"`` | ``"bitable"`` | ``"wiki"`` etc.
        member_type: ``"openid"`` | ``"chatid"`` | ``"userid"`` | ``"departmentid"``.
        member_id: The member identifier.
        perm: ``"full_access"`` | ``"edit"`` | ``"view"``.

    Returns:
        Created permission member dict.
    """
    _rate_limiter.wait()
    option = _request_option()

    body = BaseMember.builder().member_type(member_type).member_id(member_id).perm(perm).build()

    request = (
        CreatePermissionMemberRequest.builder()
        .token(file_token)
        .type(file_type)
        .need_notification(False)
        .request_body(body)
        .build()
    )

    response = _call_with_option(client.drive.v1.permission_member.create, request, option)
    _check(response, "add_permission_member")
    data = response.data
    return _unmarshal(data.member) if data is not None else {}


def list_permission_members(
    client: lark.Client,
    file_token: str,
    file_type: str,
) -> list[dict]:
    """List all collaborators on a document.

    Uses ``GET /open-apis/drive/v1/permissions/:token/members``
    (SDK: ``drive.v1.permission_member.list``).

    Args:
        client: lark-oapi client.
        file_token: The document/file token.
        file_type: ``"doc"`` | ``"docx"`` | ``"sheet"`` | ``"bitable"`` | ``"wiki"`` etc.

    Returns:
        List of permission member dicts.
    """
    _rate_limiter.wait()
    option = _request_option()

    request = ListPermissionMemberRequest.builder().token(file_token).type(file_type).build()

    response = _call_with_option(client.drive.v1.permission_member.list, request, option)
    _check(response, "list_permission_members")
    items = response.data.items
    if not items:
        return []
    return _unmarshal(items)


def update_permission_member(
    client: lark.Client,
    file_token: str,
    file_type: str,
    member_type: str,
    member_id: str,
    perm: str,
) -> dict:
    """Update a collaborator's permission level.

    Uses ``PUT /open-apis/drive/v1/permissions/:token/members/:member_id``
    (SDK: ``drive.v1.permission_member.update``).

    Args:
        client: lark-oapi client.
        file_token: The document/file token.
        file_type: ``"doc"`` | ``"docx"`` | ``"sheet"`` | ``"bitable"`` | ``"wiki"`` etc.
        member_type: ``"openid"`` | ``"chatid"`` | ``"userid"`` | ``"departmentid"``.
        member_id: The member identifier.
        perm: ``"full_access"`` | ``"edit"`` | ``"view"``.

    Returns:
        Updated permission member dict.
    """
    _rate_limiter.wait()
    option = _request_option()

    body = BaseMember.builder().member_type(member_type).perm(perm).build()

    request = (
        UpdatePermissionMemberRequest.builder()
        .token(file_token)
        .type(file_type)
        .member_id(member_id)
        .need_notification(False)
        .request_body(body)
        .build()
    )

    response = _call_with_option(client.drive.v1.permission_member.update, request, option)
    _check(response, "update_permission_member")
    return _unmarshal(response.data.member)


def remove_permission_member(
    client: lark.Client,
    file_token: str,
    file_type: str,
    member_type: str,
    member_id: str,
) -> bool:
    """Remove a collaborator from a document.

    Uses ``DELETE /open-apis/drive/v1/permissions/:token/members/:member_id``
    (SDK: ``drive.v1.permission_member.delete``).

    Args:
        client: lark-oapi client.
        file_token: The document/file token.
        file_type: ``"doc"`` | ``"docx"`` | ``"sheet"`` | ``"bitable"`` | ``"wiki"`` etc.
        member_type: ``"openid"`` | ``"chatid"`` | ``"userid"`` | ``"departmentid"``.
        member_id: The member identifier.

    Returns:
        ``True`` on success.
    """
    _rate_limiter.wait()
    option = _request_option()

    request = (
        DeletePermissionMemberRequest.builder()
        .token(file_token)
        .type(file_type)
        .member_id(member_id)
        .member_type(member_type)
        .build()
    )

    response = _call_with_option(client.drive.v1.permission_member.delete, request, option)
    _check(response, "remove_permission_member")
    return True


def transfer_owner(
    client: lark.Client,
    file_token: str,
    file_type: str,
    new_owner_id: str,
    new_owner_type: str = "openid",
) -> dict:
    """Transfer document ownership.

    Uses ``POST /open-apis/drive/v1/permissions/:token/members/transfer_owner``
    (SDK: ``drive.v1.permission_member.transfer_owner``).

    Args:
        client: lark-oapi client.
        file_token: The document/file token.
        file_type: ``"doc"`` | ``"docx"`` | ``"sheet"`` | ``"bitable"`` | ``"wiki"`` etc.
        new_owner_id: The new owner's identifier.
        new_owner_type: ``"openid"`` | ``"userid"`` etc.

    Returns:
        Transfer result dict.
    """
    _rate_limiter.wait()
    option = _request_option()

    body = (
        BaseMember.builder()
        .member_type(new_owner_type)
        .member_id(new_owner_id)
        .perm("full_access")
        .build()
    )

    request = (
        TransferOwnerPermissionMemberRequest.builder()
        .token(file_token)
        .type(file_type)
        .need_notification(True)
        .request_body(body)
        .build()
    )

    response = _call_with_option(
        client.drive.v1.permission_member.transfer_owner, request, option
    )
    _check(response, "transfer_owner")
    # transfer_owner returns empty data on success
    return {"success": True, "new_owner_id": new_owner_id, "new_owner_type": new_owner_type}


# ---------------------------------------------------------------------------
# Async retry-wrapped variants
# ---------------------------------------------------------------------------


async def create_wiki_node_with_retry(
    client: lark.Client,
    space_id: str,
    parent_node_token: str | None = None,
    title: str | None = None,
    obj_type: str = "docx",
    **retry_kwargs,
) -> dict:
    """create_wiki_node with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        create_wiki_node,
        client,
        space_id,
        parent_node_token=parent_node_token,
        title=title,
        obj_type=obj_type,
        action="create_wiki_node",
        **retry_kwargs,
    )


async def create_descendant_blocks_with_retry(
    client: lark.Client,
    document_id: str,
    block_id: str,
    children_ids: list[str],
    descendants: list[dict],
    index: int = -1,
    **retry_kwargs,
) -> dict:
    """create_descendant_blocks with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        create_descendant_blocks,
        client,
        document_id,
        block_id,
        children_ids,
        descendants,
        index=index,
        action="create_descendant_blocks",
        **retry_kwargs,
    )


async def delete_blocks_with_retry(
    client: lark.Client,
    document_id: str,
    block_id: str,
    start_index: int,
    end_index: int,
    **retry_kwargs,
) -> dict:
    """delete_blocks with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        delete_blocks,
        client,
        document_id,
        block_id,
        start_index,
        end_index,
        action="delete_blocks",
        **retry_kwargs,
    )


async def delete_wiki_node_with_retry(
    client: lark.Client,
    space_id: str,
    node_token: str,
    **retry_kwargs,
) -> dict:
    """delete_wiki_node with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        delete_wiki_node,
        client,
        space_id,
        node_token,
        action="delete_wiki_node",
        **retry_kwargs,
    )


async def add_permission_member_with_retry(
    client: lark.Client,
    file_token: str,
    file_type: str,
    member_type: str,
    member_id: str,
    perm: str = "full_access",
    **retry_kwargs,
) -> dict:
    """add_permission_member with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        add_permission_member,
        client,
        file_token,
        file_type,
        member_type,
        member_id,
        perm=perm,
        action="add_permission_member",
        **retry_kwargs,
    )


async def list_permission_members_with_retry(
    client: lark.Client,
    file_token: str,
    file_type: str,
    **retry_kwargs,
) -> list[dict]:
    """list_permission_members with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        list_permission_members,
        client,
        file_token,
        file_type,
        action="list_permission_members",
        **retry_kwargs,
    )


async def update_permission_member_with_retry(
    client: lark.Client,
    file_token: str,
    file_type: str,
    member_type: str,
    member_id: str,
    perm: str,
    **retry_kwargs,
) -> dict:
    """update_permission_member with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        update_permission_member,
        client,
        file_token,
        file_type,
        member_type,
        member_id,
        perm,
        action="update_permission_member",
        **retry_kwargs,
    )


async def remove_permission_member_with_retry(
    client: lark.Client,
    file_token: str,
    file_type: str,
    member_type: str,
    member_id: str,
    **retry_kwargs,
) -> bool:
    """remove_permission_member with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        remove_permission_member,
        client,
        file_token,
        file_type,
        member_type,
        member_id,
        action="remove_permission_member",
        **retry_kwargs,
    )


async def transfer_owner_with_retry(
    client: lark.Client,
    file_token: str,
    file_type: str,
    new_owner_id: str,
    new_owner_type: str = "openid",
    **retry_kwargs,
) -> dict:
    """transfer_owner with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        transfer_owner,
        client,
        file_token,
        file_type,
        new_owner_id,
        new_owner_type=new_owner_type,
        action="transfer_owner",
        **retry_kwargs,
    )


# ---------------------------------------------------------------------------
# File import (local file → Feishu cloud document)
# ---------------------------------------------------------------------------


def _feishu_http_json(
    method: str,
    url: str,
    action: str,
    **kwargs: Any,
) -> dict:
    import httpx

    from src.feishu.api import DEFAULT_TIMEOUT, feishu_auth_header

    headers = kwargs.pop("headers", None)
    if headers is None:
        headers = feishu_auth_header()
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    resp = httpx.request(method, url, headers=headers, **kwargs)
    if resp.status_code != 200:
        msg = f"{action} HTTP {resp.status_code}: {resp.text}"
        raise RuntimeError(msg)
    data = resp.json()
    if data["code"] != 0:
        msg = f"{action} API error: code={data['code']}, {resp.text}"
        raise RuntimeError(msg)
    return data["data"]


def upload_media_for_import(
    file_path: str,
    file_name: str,
    obj_type: str,
    file_extension: str,
) -> str:
    """Upload a local file as media for import.

    POST /open-apis/drive/v1/medias/upload_all (multipart/form-data)

    Args:
        file_path: Path to the local file.
        file_name: Display name for the file.
        obj_type: Target document type — "docx" or "sheet".
        file_extension: File extension without dot — "docx", "md", "xlsx", etc.

    Returns:
        file_token (valid ~5 minutes).
    """
    from pathlib import Path

    from src.feishu.api import feishu_auth_header

    _rate_limiter.wait()
    url = "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all"
    headers = feishu_auth_header()
    headers.pop("Content-Type", None)  # let httpx set multipart boundary

    extra = json.dumps({"obj_type": obj_type, "file_extension": file_extension})
    file_bytes = Path(file_path).read_bytes()

    data = _feishu_http_json(
        "POST",
        url,
        "upload_media",
        headers=headers,
        data={
            "file_name": file_name,
            "parent_type": "ccm_import_open",
            "parent_node": "",
            "size": str(len(file_bytes)),
            "extra": extra,
        },
        files={"file": (file_name, file_bytes)},
    )
    return data["file_token"]


def create_import_task(
    file_extension: str,
    file_token: str,
    target_type: str,
    file_name: str,
    mount_key: str = "",
) -> str:
    """Create an import task to convert an uploaded file to a cloud document.

    POST /open-apis/drive/v1/import_tasks

    Args:
        file_extension: Extension without dot — "docx", "md", "xlsx", etc.
        file_token: Token from upload_media_for_import().
        target_type: Target document type — "docx" or "sheet".
        file_name: Display name for the resulting document.
        mount_key: Mount point key (empty string for personal space).

    Returns:
        ticket string for polling the import task result.
    """
    _rate_limiter.wait()
    url = "https://open.feishu.cn/open-apis/drive/v1/import_tasks"
    body = {
        "file_extension": file_extension,
        "file_token": file_token,
        "type": target_type,
        "file_name": file_name,
        "point": {
            "mount_type": 1,
            "mount_key": mount_key,
        },
    }
    data = _feishu_http_json("POST", url, "create_import_task", json=body)
    return data["ticket"]


def get_import_task_result(ticket: str) -> dict:
    """Poll the result of an import task.

    GET /open-apis/drive/v1/import_tasks/:ticket

    Args:
        ticket: Ticket from create_import_task().

    Returns:
        Result dict with job_status (0=success, 1=init, 2=processing, 3+=error)
        and on success: token, url, type, etc.
    """
    _rate_limiter.wait()
    url = f"https://open.feishu.cn/open-apis/drive/v1/import_tasks/{ticket}"
    data = _feishu_http_json("GET", url, "get_import_task")
    return data["result"]


def move_docs_to_wiki(
    client: lark.Client,
    space_id: str,
    parent_wiki_token: str,
    obj_type: str,
    obj_token: str,
) -> dict:
    """Move a cloud document into a wiki space as a child node.

    POST /open-apis/wiki/v2/spaces/:space_id/nodes/move_docs_to_wiki

    May be async — check for task_id in the response.

    Args:
        space_id: Wiki space ID.
        parent_wiki_token: Parent node token under which to place the document.
        obj_type: Document type — "docx", "sheet", etc.
        obj_token: Document token to move.

    Returns:
        Response dict containing wiki_token (and possibly task_id if async).
    """
    del client
    _rate_limiter.wait()
    url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/{space_id}/nodes/move_docs_to_wiki"
    body = {
        "parent_wiki_token": parent_wiki_token,
        "obj_type": obj_type,
        "obj_token": obj_token,
    }
    return _feishu_http_json("POST", url, "move_docs_to_wiki", json=body)


def get_wiki_task_result(task_id: str) -> dict:
    """Poll the result of an async wiki task (e.g. move_docs_to_wiki).

    GET /open-apis/wiki/v2/tasks/:task_id?task_type=move

    Args:
        task_id: Task ID from move_docs_to_wiki().

    Returns:
        Task result dict with status and wiki node info.
    """
    _rate_limiter.wait()
    url = f"https://open.feishu.cn/open-apis/wiki/v2/tasks/{task_id}"
    return _feishu_http_json(
        "GET",
        url,
        "get_wiki_task",
        params={"task_type": "move"},
    )


# ---------------------------------------------------------------------------
# Block-level PATCH operations (cell-level table editing)
# ---------------------------------------------------------------------------


def _patch_block_with_retry(
    document_id: str,
    block_id: str,
    body: dict[str, Any],
    *,
    max_retries: int = 3,
) -> dict:
    """PATCH a block with simple retry for rate limit (429) and server errors (5xx).

    Uses raw httpx since the lark-oapi SDK doesn't expose block PATCH.

    Args:
        document_id: The docx document ID.
        block_id: The block ID to patch.
        body: Request body for the PATCH call.
        max_retries: Maximum number of retries on transient errors.

    Returns:
        API response data dict.
    """
    import httpx

    from src.feishu.api import DEFAULT_TIMEOUT, feishu_auth_header

    url = f"https://open.feishu.cn/open-apis/docx/v1/documents/" f"{document_id}/blocks/{block_id}"
    headers = feishu_auth_header()

    for attempt in range(max_retries + 1):
        _rate_limiter.wait()
        resp = httpx.patch(url, headers=headers, json=body, timeout=DEFAULT_TIMEOUT)
        if (resp.status_code == 429 or resp.status_code >= 500) and attempt < max_retries:
            time.sleep(1.0 * (attempt + 1))
            continue
        if resp.status_code != 200:
            msg = f"PATCH block {block_id} failed: {resp.status_code}, {resp.text}"
            raise RuntimeError(msg)
        data = resp.json()
        if data["code"] != 0 and attempt < max_retries:
            time.sleep(1.0 * (attempt + 1))
            continue
        if data["code"] != 0:
            msg = f"PATCH block {block_id} API error: code={data['code']}, {resp.text}"
            raise RuntimeError(msg)
        return data["data"]

    msg = "unreachable"
    raise AssertionError(msg)


def _with_document_revision(key: str, value: dict) -> dict:
    return {
        key: value,
        "document_revision_id": -1,
    }


_TABLE_OPERATION_FIELDS: dict[str, tuple[str, ...]] = {
    "insert_table_row": ("row_index",),
    "delete_table_rows": ("row_start_index", "row_end_index"),
    "insert_table_column": ("column_index",),
    "delete_table_columns": ("column_start_index", "column_end_index"),
    "merge_table_cells": (
        "row_start_index",
        "row_end_index",
        "column_start_index",
        "column_end_index",
    ),
    "unmerge_table_cells": ("row_index", "column_index"),
}


def update_block_text(
    document_id: str,
    block_id: str,
    elements: list[dict],
    client_token: str | None = None,
) -> dict:
    """Update a text block's elements via PATCH.

    PATCH /open-apis/docx/v1/documents/:document_id/blocks/:block_id

    Args:
        document_id: The docx document ID.
        block_id: The text block ID to update.
        elements: New text elements list (text_run, mention_user, etc.).
        client_token: Optional idempotency token.

    Returns:
        Updated block dict.
    """
    body: dict = _with_document_revision(
        "update_text_elements",
        {
            "elements": elements,
        },
    )
    if client_token:
        body["client_token"] = client_token
    return _patch_block_with_retry(document_id, block_id, body)


def update_table(
    document_id: str,
    table_block_id: str,
    operation: str,
    **params: Any,
) -> dict:
    """Perform a table structure or property operation via PATCH.

    PATCH /open-apis/docx/v1/documents/:document_id/blocks/:block_id

    Supported operations:
    - insert_table_row: params row_index (int)
    - delete_table_rows: params row_start_index, row_end_index (int)
    - insert_table_column: params column_index (int)
    - delete_table_columns: params column_start_index, column_end_index (int)
    - merge_table_cells: params row_start_index, row_end_index,
                         column_start_index, column_end_index (int)
    - unmerge_table_cells: params row_index, column_index (int)

    Args:
        document_id: The docx document ID.
        table_block_id: The table block ID (block_type 31).
        operation: Operation name (e.g. "insert_table_row").
        **params: Operation-specific parameters.

    Returns:
        API response data dict.
    """
    fields = _TABLE_OPERATION_FIELDS.get(operation)
    if fields is None:
        msg = f"unsupported table operation: {operation}"
        raise ValueError(msg)

    op_body = _with_document_revision(
        operation,
        {field: params[field] for field in fields},
    )
    return _patch_block_with_retry(document_id, table_block_id, op_body)
