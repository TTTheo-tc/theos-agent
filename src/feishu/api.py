"""Feishu/Lark read API layer using lark-oapi SDK.

Replaces feishu-sync's httpx raw REST calls with lark-oapi builder-pattern SDK calls.
All functions accept a pre-built ``lark.Client`` so callers own auth configuration.

Token precedence for user-scoped APIs:
    1. ``ctx_current_token`` ContextVar (set by upstream orchestration)
    2. App/tenant token managed internally by the lark.Client
"""

from __future__ import annotations

import json
from contextvars import ContextVar

import lark_oapi as lark
from lark_oapi import RequestOption
from lark_oapi.api.contact.v3 import GetUserRequest
from lark_oapi.api.docx.v1 import (
    GetDocumentRequest,
    ListDocumentBlockRequest,
    RawContentDocumentRequest,
)
from lark_oapi.api.drive.v1 import (
    CreateExportTaskRequest,
    DownloadExportTaskRequest,
    DownloadFileRequest,
    DownloadMediaRequest,
    ExportTask,
    GetExportTaskRequest,
    ListFileCommentRequest,
    ListFileRequest,
)
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
)
from lark_oapi.api.search.v2 import (
    SearchDocWikiRequest,
    SearchDocWikiRequestBody,
    WikiFilter,
)
from lark_oapi.api.sheets.v3 import QuerySpreadsheetSheetRequest
from lark_oapi.api.wiki.v2 import (
    GetNodeSpaceRequest,
    ListSpaceNodeRequest,
    ListSpaceRequest,
)

# ---------------------------------------------------------------------------
# Context variable for injecting user access token
# ---------------------------------------------------------------------------

ctx_current_token: ContextVar[str | None] = ContextVar("ctx_current_token", default=None)


def _unmarshal(obj) -> dict | list:
    """Safely convert a lark-oapi SDK object to a Python dict/list.

    ``lark.JSON.marshal()`` may return either a JSON string or an already-
    deserialized Python object depending on the SDK version.  This helper
    normalises the result so callers always get native Python types.
    """
    raw = lark.JSON.marshal(obj)
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


# Legacy compat — used by extra/bitable.py (httpx raw-call style, to be migrated)
DEFAULT_TIMEOUT = 60.0

# Singleton client for legacy compat layer
_compat_client: lark.Client | None = None


def feishu_auth_header() -> dict[str, str]:
    """Build auth header for legacy httpx-based callers (extra/bitable.py etc).

    Uses the user token from ContextVar if available, otherwise falls back
    to the singleton client's internal app token mechanism via a raw request.
    """
    token = ctx_current_token.get()
    if token:
        return {"Authorization": f"Bearer {token}"}
    # Fallback: get tenant token from the singleton client
    if _compat_client is not None:
        from lark_oapi.core.token import get_tenant_access_token

        try:
            t = get_tenant_access_token(_compat_client.config)
            return {"Authorization": f"Bearer {t}"}
        except Exception:
            pass
    return {}


def _request_option() -> RequestOption | None:
    """Build a ``RequestOption`` with the best available token.

    With ``enable_set_token=True`` on the client, the SDK will NOT
    auto-fetch a tenant token.  So we must always provide one:
    - user access token (preferred — has user-level permissions), or
    - tenant access token (fallback — app-level permissions only).
    """
    user_token = ctx_current_token.get()
    if user_token:
        return RequestOption.builder().user_access_token(user_token).build()
    # Fallback: manually obtain tenant token so SDK still works
    if _compat_client is not None:
        try:
            from lark_oapi.core.token.manager import TokenManager

            tenant_token = TokenManager.get_self_tenant_token(_compat_client.config)
            return RequestOption.builder().tenant_access_token(tenant_token).build()
        except Exception:
            pass
    return None


def _check(response, action: str = "API call") -> None:
    """Raise on non-success SDK response."""
    if not response.success():
        from src.feishu.errors import FeishuAPIError

        raise FeishuAPIError(
            f"Feishu {action} failed: code={response.code} msg={response.msg}",
            code=response.code,
            response=response,
        )


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def make_client(app_id: str, app_secret: str) -> lark.Client:
    """Create a lark-oapi client with the given credentials."""
    global _compat_client
    client = (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .enable_set_token(True)
        .log_level(lark.LogLevel.WARNING)
        .build()
    )
    _compat_client = client  # expose for legacy feishu_auth_header()
    return client


# ---------------------------------------------------------------------------
# URL parsing (pure string logic, no API calls)
# ---------------------------------------------------------------------------


def parse_url(url: str) -> dict:
    """Parse a Feishu URL into component tokens.

    Handles wiki, docx, doc, base (bitable), sheets, minutes, folder, file URLs.
    Copied from feishu-sync for compatibility.
    """
    url_base = url.split("?")[0]
    parts = url_base.split("/")
    wiki_id = None
    doc_id = None
    app_token = None  # for bitable /base/ URLs
    spreadsheet_token = None  # for sheet /sheets/ URLs
    ret: dict = {"url": url_base}

    if "wiki" in parts:
        wiki_id = parts[parts.index("wiki") + 1]
    elif "base" in parts:
        # Bitable direct URL: /base/<app_token>
        app_token = parts[parts.index("base") + 1]
    elif "sheets" in parts:
        # Sheet direct URL: /sheets/<spreadsheet_token>
        spreadsheet_token = parts[parts.index("sheets") + 1]
    elif "docx" in parts:
        doc_id = parts[parts.index("docx") + 1]
        ret["doc_type"] = "docx"
    elif "docs" in parts:
        doc_id = parts[parts.index("docs") + 1]
        ret["doc_type"] = "doc"
    elif "minutes" in parts:
        idx = parts.index("minutes")
        if idx + 1 < len(parts) and parts[idx + 1]:
            ret["minutes_token"] = parts[idx + 1]

    folder_token = None
    file_token = None
    if "folder" in parts:
        idx = parts.index("folder")
        if idx + 1 < len(parts):
            folder_token = parts[idx + 1]
    elif "file" in parts:
        idx = parts.index("file")
        if idx + 1 < len(parts):
            file_token = parts[idx + 1]

    ret.setdefault("minutes_token", None)
    ret.update(
        {
            "doc_id": doc_id,
            "wiki_id": wiki_id,
            "app_token": app_token,
            "spreadsheet_token": spreadsheet_token,
            "folder_token": folder_token,
            "file_token": file_token,
        }
    )
    return ret


# ---------------------------------------------------------------------------
# Page / Document operations  (docx.v1, wiki.v2)
# ---------------------------------------------------------------------------


def info_page(client: lark.Client, doc_type: str, token: str) -> dict:
    """Get wiki node or document metadata.

    For wiki tokens, calls wiki.v2.space.get_node.
    For docx tokens, calls docx.v1.document.get.

    Args:
        doc_type: ``"wiki"`` | ``"docx"`` | ``"doc"``
        token: The wiki node token or document id.

    Returns:
        Node/document metadata dict.
    """
    option = _request_option()

    if doc_type == "wiki":
        request = GetNodeSpaceRequest.builder().token(token).obj_type("wiki").build()
        response = (
            client.wiki.v2.space.get_node(request, option)
            if option
            else client.wiki.v2.space.get_node(request)
        )
        _check(response, "get_node")
        node = response.data.node
        return _unmarshal(node)
    else:
        # docx or doc — get document meta
        request = GetDocumentRequest.builder().document_id(token).build()
        response = (
            client.docx.v1.document.get(request, option)
            if option
            else client.docx.v1.document.get(request)
        )
        _check(response, "get_document")
        doc = response.data.document
        result = _unmarshal(doc)
        result["obj_type"] = doc_type
        result["obj_token"] = token
        return result


def read_page(client: lark.Client, document_id: str) -> list[dict]:
    """Read all blocks of a docx document (paginated).

    Args:
        document_id: The docx document ID.

    Returns:
        List of block dicts.
    """
    option = _request_option()
    all_items: list[dict] = []
    page_token: str | None = None

    while True:
        builder = ListDocumentBlockRequest.builder().document_id(document_id).page_size(500)
        if page_token:
            builder = builder.page_token(page_token)
        request = builder.build()

        response = (
            client.docx.v1.document_block.list(request, option)
            if option
            else client.docx.v1.document_block.list(request)
        )
        _check(response, "list_document_blocks")

        data = response.data
        if data.items:
            all_items.extend(_unmarshal(data.items))
        if not data.has_more:
            break
        page_token = data.page_token
        if not page_token:
            break

    return all_items


def read_page_raw(client: lark.Client, document_id: str) -> str:
    """Read plain-text content of a docx document.

    Args:
        document_id: The docx document ID.

    Returns:
        Plain-text string.
    """
    option = _request_option()
    request = RawContentDocumentRequest.builder().document_id(document_id).build()
    response = (
        client.docx.v1.document.raw_content(request, option)
        if option
        else client.docx.v1.document.raw_content(request)
    )
    _check(response, "raw_content")
    return response.data.content


def read_comments(client: lark.Client, file_token: str, file_type: str) -> list[dict]:
    """Read all comments on a document (paginated).

    Uses drive.v1.file_comment.list.

    Args:
        file_token: The file/document token.
        file_type: ``"docx"`` | ``"doc"`` | ``"sheet"`` etc.

    Returns:
        List of comment dicts.
    """
    option = _request_option()
    comments: list[dict] = []
    page_token: str | None = None

    while True:
        builder = (
            ListFileCommentRequest.builder()
            .file_token(file_token)
            .file_type(file_type)
            .page_size(50)
        )
        if page_token:
            builder = builder.page_token(page_token)
        request = builder.build()

        response = (
            client.drive.v1.file_comment.list(request, option)
            if option
            else client.drive.v1.file_comment.list(request)
        )
        _check(response, "list_file_comments")

        data = response.data
        if data.items:
            comments.extend(_unmarshal(data.items))
        if not data.has_more:
            break
        page_token = data.page_token
        if not page_token:
            break

    return comments


# ---------------------------------------------------------------------------
# Wiki operations  (search.v2, wiki.v2)
# ---------------------------------------------------------------------------


def search_wiki(
    client: lark.Client,
    query: str,
    space_id: str | None = None,
    page_size: int = 40,
) -> list[dict]:
    """Search wiki nodes using search.v2.doc_wiki.

    Args:
        query: Search keyword.
        space_id: Limit search to a specific wiki space (optional).
        page_size: Max results per page.

    Returns:
        List of matching wiki-node dicts.
    """
    option = _request_option()

    body_builder = SearchDocWikiRequestBody.builder().query(query).page_size(page_size)
    if space_id:
        wiki_filter = WikiFilter.builder().space_ids([space_id]).build()
        body_builder = body_builder.wiki_filter(wiki_filter)

    request = SearchDocWikiRequest.builder().request_body(body_builder.build()).build()

    response = (
        client.search.v2.doc_wiki.search(request, option)
        if option
        else client.search.v2.doc_wiki.search(request)
    )
    _check(response, "search_wiki")
    items = response.data.items
    if not items:
        return []
    return _unmarshal(items)


def search_docs(
    query: str,
    *,
    doc_types: list[str] | None = None,
    creator_ids: list[str] | None = None,
    folder_tokens: list[str] | None = None,
    only_title: bool = False,
    sort_type: str | None = None,
    wiki_space_ids: list[str] | None = None,
    page_size: int = 20,
    max_items: int = 100,
) -> list[dict]:
    """Global cloud-document search (not limited to wiki).

    POST /open-apis/search/v2/doc_wiki/search

    Note: requires user_access_token.

    Args:
        query: Search keyword (0-50 chars).
        doc_types: Filter by doc type. Values: DOC, SHEET, BITABLE, MINDNOTE,
            FILE, WIKI, DOCX, FOLDER, CATALOG, SLIDES, SHORTCUT (max 10).
        creator_ids: Filter by creator OpenID (max 20).
        folder_tokens: Limit search to specific folders (max 50).
        only_title: If True, search titles only.
        sort_type: Sort order. Values: DEFAULT_TYPE, OPEN_TIME, EDIT_TIME,
            EDIT_TIME_ASC, ENTITY_CREATE_TIME_ASC, ENTITY_CREATE_TIME_DESC,
            CREATE_TIME, CREATE_TIME_ASC.
        wiki_space_ids: Limit search to specific wiki spaces.
        page_size: Results per page (1-100, default 20).
        max_items: Maximum total results (default 100).

    Returns:
        List of result dicts with title_highlighted, summary_highlighted,
        entity_type, result_meta, etc.
    """
    import httpx  # noqa: PLC0415

    headers = feishu_auth_header()
    url = "https://open.feishu.cn/open-apis/search/v2/doc_wiki/search"
    body: dict = {"query": query}

    # eu_nc region requires explicit doc_types or returns empty results.
    # doc_types and wiki_filter conflict, so only add defaults when no wiki_space_ids.
    effective_doc_types = doc_types
    if not effective_doc_types and not wiki_space_ids:
        effective_doc_types = [
            "BITABLE",
            "CATALOG",
            "DOC",
            "DOCX",
            "FILE",
            "MINDNOTE",
            "SHEET",
            "SHORTCUT",
            "SLIDES",
        ]

    doc_filter: dict = {}
    if effective_doc_types:
        doc_filter["doc_types"] = effective_doc_types
    if creator_ids:
        doc_filter["creator_ids"] = creator_ids
    if folder_tokens:
        doc_filter["folder_tokens"] = folder_tokens
    if only_title:
        doc_filter["only_title"] = True
    if sort_type:
        doc_filter["sort_type"] = sort_type
    if doc_filter:
        body["doc_filter"] = doc_filter

    if wiki_space_ids:
        body["wiki_filter"] = {"space_ids": wiki_space_ids}

    page_token = None
    results: list[dict] = []
    while len(results) < max_items:
        body["page_size"] = min(page_size, max_items - len(results))
        if page_token:
            body["page_token"] = page_token

        resp = httpx.post(url, headers=headers, json=body, timeout=DEFAULT_TIMEOUT)
        if resp.status_code != 200:
            msg = f"search_docs HTTP {resp.status_code}: {resp.text}"
            raise RuntimeError(msg)
        data = resp.json()
        if data["code"] != 0:
            msg = f"search_docs API error: code={data['code']}, {resp.text}"
            raise RuntimeError(msg)

        payload = data.get("data", {})
        results.extend(payload.get("res_units", []))

        if not payload.get("has_more", False):
            break
        page_token = payload.get("page_token")
        if not page_token:
            break
    return results


def list_nodes(
    client: lark.Client,
    space_id: str,
    parent_node_token: str | None = None,
) -> list[dict]:
    """List child nodes of a wiki space or parent node (paginated).

    Args:
        space_id: The wiki space ID.
        parent_node_token: Parent node; ``None`` for root-level nodes.

    Returns:
        List of node dicts.
    """
    option = _request_option()
    children: list[dict] = []
    page_token: str | None = None

    while True:
        builder = ListSpaceNodeRequest.builder().space_id(space_id).page_size(50)
        if parent_node_token:
            builder = builder.parent_node_token(parent_node_token)
        if page_token:
            builder = builder.page_token(page_token)
        request = builder.build()

        response = (
            client.wiki.v2.space_node.list(request, option)
            if option
            else client.wiki.v2.space_node.list(request)
        )
        _check(response, "list_space_nodes")

        data = response.data
        if data.items:
            children.extend(_unmarshal(data.items))
        if not data.has_more:
            break
        page_token = data.page_token
        if not page_token:
            break

    return children


def list_spaces(client: lark.Client) -> list[dict]:
    """List all accessible wiki spaces (paginated).

    Returns:
        List of space dicts.
    """
    option = _request_option()
    spaces: list[dict] = []
    page_token: str | None = None

    while True:
        builder = ListSpaceRequest.builder().page_size(50)
        if page_token:
            builder = builder.page_token(page_token)
        request = builder.build()

        response = (
            client.wiki.v2.space.list(request, option)
            if option
            else client.wiki.v2.space.list(request)
        )
        _check(response, "list_spaces")

        data = response.data
        if data.items:
            spaces.extend(_unmarshal(data.items))
        if not data.has_more:
            break
        page_token = data.page_token
        if not page_token:
            break

    return spaces


# ---------------------------------------------------------------------------
# User operations  (contact.v3, search.v1 via raw request)
# ---------------------------------------------------------------------------


def info_user(client: lark.Client, user_id: str) -> dict:
    """Get user info by user ID.

    Args:
        user_id: An open_id, union_id, or user_id.

    Returns:
        User info dict.
    """
    option = _request_option()
    request = GetUserRequest.builder().user_id(user_id).build()
    response = (
        client.contact.v3.user.get(request, option)
        if option
        else client.contact.v3.user.get(request)
    )
    _check(response, "get_user")
    return _unmarshal(response.data.user)


def search_users(client: lark.Client, query: str, page_size: int = 20) -> list[dict]:
    """Search users by keyword.

    NOTE: The ``/open-apis/search/v1/user`` endpoint requires a *user_access_token*.
    There is no typed SDK binding for search v1, so we use a raw ``BaseRequest``.

    Args:
        query: Search keyword (name, email, etc.).
        page_size: Max results per page (max 200).

    Returns:
        List of user dicts.
    """
    option = _request_option()
    users: list[dict] = []
    page_token: str | None = None

    while True:
        req = lark.BaseRequest()
        req.http_method = lark.HttpMethod.GET
        req.uri = "/open-apis/search/v1/user"
        req.token_types = {lark.AccessTokenType.USER}
        req.add_query("query", query)
        req.add_query("page_size", str(min(page_size, 200)))
        if page_token:
            req.add_query("page_token", page_token)

        response = client.request(req, option)
        _check(response, "search_users")

        data = json.loads(response.raw.content).get("data", {})
        users.extend(data.get("users", []))

        if not data.get("has_more", False):
            break
        page_token = data.get("page_token")
        if not page_token:
            break

    return users


# ---------------------------------------------------------------------------
# Messaging  (im.v1)
# ---------------------------------------------------------------------------


def send_message(
    client: lark.Client,
    receive_id: str,
    msg_type: str,
    content: str,
    receive_id_type: str = "open_id",
) -> dict:
    """Send a message as the bot.

    Args:
        receive_id: Target user or chat ID.
        msg_type: ``"text"`` | ``"interactive"`` | ``"image"`` | ``"file"`` etc.
        content: JSON-encoded content string.
        receive_id_type: ``"open_id"`` | ``"chat_id"`` | ``"union_id"`` etc.

    Returns:
        Sent message data dict.
    """
    request = (
        CreateMessageRequest.builder()
        .receive_id_type(receive_id_type)
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .msg_type(msg_type)
            .content(content)
            .build()
        )
        .build()
    )
    response = client.im.v1.message.create(request)
    _check(response, "send_message")
    return _unmarshal(response.data)


# ---------------------------------------------------------------------------
# Drive operations  (drive.v1)
# ---------------------------------------------------------------------------


def list_folder_files(client: lark.Client, folder_token: str, page_size: int = 50) -> list[dict]:
    """List files in a drive folder (paginated).

    Args:
        folder_token: The folder token.
        page_size: Max results per page.

    Returns:
        List of file metadata dicts.
    """
    option = _request_option()
    files: list[dict] = []
    page_token: str | None = None

    while True:
        builder = ListFileRequest.builder().folder_token(folder_token).page_size(page_size)
        if page_token:
            builder = builder.page_token(page_token)
        request = builder.build()

        response = (
            client.drive.v1.file.list(request, option)
            if option
            else client.drive.v1.file.list(request)
        )
        _check(response, "list_folder_files")

        data = response.data
        if data.files:
            files.extend(_unmarshal(data.files))
        if not data.has_more:
            break
        page_token = data.page_token
        if not page_token:
            break

    return files


def download_file_content(client: lark.Client, file_token: str) -> bytes:
    """Download a file from drive by file token.

    Args:
        file_token: The file token.

    Returns:
        Raw binary content.
    """
    option = _request_option()
    request = DownloadFileRequest.builder().file_token(file_token).build()
    response = (
        client.drive.v1.file.download(request, option)
        if option
        else client.drive.v1.file.download(request)
    )
    _check(response, "download_file")
    data = response.file
    if hasattr(data, "read"):
        data = data.read()
    return data


def download_media_content(client: lark.Client, file_token: str) -> bytes:
    """Download a media file (images/attachments embedded in documents).

    Args:
        file_token: The media file token.

    Returns:
        Raw binary content.
    """
    option = _request_option()
    request = DownloadMediaRequest.builder().file_token(file_token).build()
    response = (
        client.drive.v1.media.download(request, option)
        if option
        else client.drive.v1.media.download(request)
    )
    _check(response, "download_media")
    data = response.file
    if hasattr(data, "read"):
        data = data.read()
    return data


def create_export_task(
    client: lark.Client,
    file_token: str,
    file_extension: str,
    type: str,
) -> str:
    """Create an export task for a document/spreadsheet.

    Args:
        file_token: The document/spreadsheet token.
        file_extension: Target format — ``"docx"`` | ``"pdf"`` | ``"xlsx"`` | ``"csv"``.
        type: Source doc type — ``"doc"`` | ``"docx"`` | ``"sheet"`` | ``"bitable"``.

    Returns:
        The export task ticket string.
    """
    option = _request_option()
    export_task = (
        ExportTask.builder().file_extension(file_extension).token(file_token).type(type).build()
    )
    request = CreateExportTaskRequest.builder().request_body(export_task).build()
    response = (
        client.drive.v1.export_task.create(request, option)
        if option
        else client.drive.v1.export_task.create(request)
    )
    _check(response, "create_export_task")
    return response.data.ticket


def get_export_task_result(client: lark.Client, ticket: str, token: str) -> dict:
    """Query an export task result.

    Args:
        ticket: The task ticket from :func:`create_export_task`.
        token: The original document token.

    Returns:
        Result dict with ``job_status``, ``file_token``, ``file_name`` etc.
    """
    option = _request_option()
    request = GetExportTaskRequest.builder().ticket(ticket).token(token).build()
    response = (
        client.drive.v1.export_task.get(request, option)
        if option
        else client.drive.v1.export_task.get(request)
    )
    _check(response, "get_export_task")
    return _unmarshal(response.data.result)


def download_export_file(client: lark.Client, file_token: str) -> bytes:
    """Download the output file of a completed export task.

    Args:
        file_token: The export result file token.

    Returns:
        Raw binary content.
    """
    option = _request_option()
    request = DownloadExportTaskRequest.builder().file_token(file_token).build()
    response = (
        client.drive.v1.export_task.download(request, option)
        if option
        else client.drive.v1.export_task.download(request)
    )
    _check(response, "download_export_file")
    data = response.file
    if hasattr(data, "read"):
        data = data.read()
    return data


# ---------------------------------------------------------------------------
# Spreadsheet operations  (sheets.v3)
# ---------------------------------------------------------------------------


def list_spreadsheet_sheets(client: lark.Client, spreadsheet_token: str) -> list[dict]:
    """List all sheets in a spreadsheet.

    Args:
        spreadsheet_token: The spreadsheet token.

    Returns:
        List of sheet dicts (sheet_id, title, index, etc.).
    """
    option = _request_option()
    request = QuerySpreadsheetSheetRequest.builder().spreadsheet_token(spreadsheet_token).build()
    response = (
        client.sheets.v3.spreadsheet_sheet.query(request, option)
        if option
        else client.sheets.v3.spreadsheet_sheet.query(request)
    )
    _check(response, "query_spreadsheet_sheets")
    sheets = response.data.sheets
    if not sheets:
        return []
    return _unmarshal(sheets)
