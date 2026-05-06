"""Feishu Spreadsheet API -- read/write cell data.

Uses V2 sheets endpoints via raw ``BaseRequest`` (no typed SDK bindings for V2).
V3 metadata uses the existing ``list_spreadsheet_sheets`` from api.py.
"""

from __future__ import annotations

import json

import lark_oapi as lark

from src.feishu.api import _check, _request_option, ctx_current_token, list_spreadsheet_sheets
from src.feishu.retry import with_retry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_get(client: lark.Client, uri: str) -> dict:
    """Issue a raw GET and return the ``data`` dict from the JSON response."""
    return _raw_request(client, lark.HttpMethod.GET, "GET", uri)


def _raw_put(client: lark.Client, uri: str, body: dict) -> dict:
    """Issue a raw PUT with JSON body and return the ``data`` dict."""
    return _raw_request(client, lark.HttpMethod.PUT, "PUT", uri, body=body)


def _raw_post(client: lark.Client, uri: str, body: dict) -> dict:
    """Issue a raw POST with JSON body and return the ``data`` dict."""
    return _raw_request(client, lark.HttpMethod.POST, "POST", uri, body=body)


def _raw_request(
    client: lark.Client, method, action_method: str, uri: str, body: dict | None = None
) -> dict:
    """Issue a raw sheets request and return the ``data`` dict."""
    req = lark.BaseRequest()
    req.http_method = method
    req.uri = uri
    if body is not None:
        req.body = body
    token = ctx_current_token.get()
    req.token_types = {lark.AccessTokenType.USER} if token else {lark.AccessTokenType.TENANT}
    option = _request_option()
    response = client.request(req, option) if option is not None else client.request(req)
    _check(response, f"{action_method} {uri}")
    return json.loads(response.raw.content).get("data", {})


# ---------------------------------------------------------------------------
# URL / range helpers
# ---------------------------------------------------------------------------


def parse_sheet_url(url: str) -> dict:
    """Parse a Feishu spreadsheet URL into component tokens.

    Supported formats:
    - ``https://xxx.feishu.cn/sheets/<spreadsheetToken>``
    - ``https://xxx.feishu.cn/sheets/<spreadsheetToken>?sheet=<sheetId>``
    - ``https://xxx.feishu.cn/sheets/<spreadsheetToken>/<sheetId>``

    Returns:
        ``{"spreadsheet_token": str, "sheet_id": str | None}``
    """
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    path_parts = parsed.path.split("/")
    query_params = parse_qs(parsed.query)

    spreadsheet_token: str | None = None
    sheet_id: str | None = None

    if "sheets" in path_parts:
        idx = path_parts.index("sheets")
        if idx + 1 < len(path_parts) and path_parts[idx + 1]:
            spreadsheet_token = path_parts[idx + 1]
        if idx + 2 < len(path_parts) and path_parts[idx + 2]:
            sheet_id = path_parts[idx + 2]

    # Query-param overrides
    if "sheet" in query_params:
        sheet_id = query_params["sheet"][0]
    if "sheetId" in query_params:
        sheet_id = query_params["sheetId"][0]
    if "spreadsheetToken" in query_params:
        spreadsheet_token = query_params["spreadsheetToken"][0]

    # Bare token (not a URL)
    if spreadsheet_token is None and "://" not in url:
        spreadsheet_token = url.strip() or None

    return {"spreadsheet_token": spreadsheet_token, "sheet_id": sheet_id}


def _build_range_str(sheet_id: str, range_: str = "") -> str:
    """Build the ``<sheetId>!<range>`` string for V2 endpoints."""
    if range_:
        return f"{sheet_id}!{range_}"
    return sheet_id


# ---------------------------------------------------------------------------
# Core API functions
# ---------------------------------------------------------------------------


def read_sheet_values(
    client: lark.Client,
    spreadsheet_token: str,
    sheet_id: str,
    range: str = "",
) -> dict:
    """Read cell values from a sheet range.

    Uses ``GET /open-apis/sheets/v2/spreadsheets/:token/values/:range``

    Returns:
        ``{"values": [[cell, ...], ...], "rows": N, "cols": N}``
    """
    range_str = _build_range_str(sheet_id, range)
    uri = f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{range_str}"
    data = _raw_get(client, uri)
    vr = data.get("valueRange", {})
    values = vr.get("values", [])
    rows = len(values)
    cols = max((len(row) for row in values), default=0)
    return {"values": values, "rows": rows, "cols": cols}


def write_sheet_values(
    client: lark.Client,
    spreadsheet_token: str,
    sheet_id: str,
    range: str,
    values: list[list],
) -> dict:
    """Write cell values to a sheet range.

    Uses ``PUT /open-apis/sheets/v2/spreadsheets/:token/values``
    """
    range_str = _build_range_str(sheet_id, range)
    uri = f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values"
    body = {
        "valueRange": {
            "range": range_str,
            "values": values,
        },
    }
    return _raw_put(client, uri, body)


def append_sheet_values(
    client: lark.Client,
    spreadsheet_token: str,
    sheet_id: str,
    values: list[list],
) -> dict:
    """Append rows to the end of a sheet.

    Uses ``POST /open-apis/sheets/v2/spreadsheets/:token/values_append``
    """
    range_str = _build_range_str(sheet_id, "")
    uri = f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values_append"
    body = {
        "valueRange": {
            "range": range_str,
            "values": values,
        },
    }
    return _raw_post(client, uri, body)


def get_sheet_metadata(
    client: lark.Client,
    spreadsheet_token: str,
) -> dict:
    """Get spreadsheet metadata (title, sheets list with properties).

    Combines ``GET /open-apis/sheets/v3/spreadsheets/:token`` with
    ``list_spreadsheet_sheets`` for sheet-level detail.
    """
    # V3 metadata for spreadsheet title etc.
    uri = f"/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}"
    meta = _raw_get(client, uri)

    # Detailed per-sheet properties via existing V3 SDK function
    sheets = list_spreadsheet_sheets(client, spreadsheet_token)

    spreadsheet = meta.get("spreadsheet", {})
    return {
        "title": spreadsheet.get("title", ""),
        "spreadsheet_token": spreadsheet.get("spreadsheet_token", spreadsheet_token),
        "sheets": sheets,
    }


# ---------------------------------------------------------------------------
# Markdown formatting
# ---------------------------------------------------------------------------


def _escape_md_cell(value) -> str:
    """Escape a cell value for use inside a markdown table."""
    if value is None:
        return ""
    text = str(value)
    text = text.replace("|", "\\|")
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return text


def values_to_markdown(values: list[list], sheet_name: str = "", range_str: str = "") -> str:
    """Format a 2D values grid as a markdown table.

    First row is treated as the header.
    """
    parts: list[str] = []
    if sheet_name or range_str:
        label = sheet_name or ""
        if range_str:
            label = f"{label} [{range_str}]" if label else range_str
        parts.append(f"**Sheet: {label}**\n")

    if not values:
        parts.append("*(empty)*")
        return "\n".join(parts)

    header = values[0]
    col_count = len(header)

    header_cells = [_escape_md_cell(c) for c in header]
    parts.append("| " + " | ".join(header_cells) + " |")
    parts.append("| " + " | ".join(["---"] * col_count) + " |")

    for row in values[1:]:
        # Pad short rows
        cells = [_escape_md_cell(row[i] if i < len(row) else "") for i in range(col_count)]
        parts.append("| " + " | ".join(cells) + " |")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Async retry-wrapped variants
# ---------------------------------------------------------------------------


async def read_sheet_values_with_retry(
    client: lark.Client,
    spreadsheet_token: str,
    sheet_id: str,
    range: str = "",
    **retry_kwargs,
) -> dict:
    """read_sheet_values with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        read_sheet_values,
        client,
        spreadsheet_token,
        sheet_id,
        range=range,
        action="read_sheet_values",
        **retry_kwargs,
    )


async def write_sheet_values_with_retry(
    client: lark.Client,
    spreadsheet_token: str,
    sheet_id: str,
    range: str,
    values: list[list],
    **retry_kwargs,
) -> dict:
    """write_sheet_values with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        write_sheet_values,
        client,
        spreadsheet_token,
        sheet_id,
        range,
        values,
        action="write_sheet_values",
        **retry_kwargs,
    )


async def append_sheet_values_with_retry(
    client: lark.Client,
    spreadsheet_token: str,
    sheet_id: str,
    values: list[list],
    **retry_kwargs,
) -> dict:
    """append_sheet_values with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        append_sheet_values,
        client,
        spreadsheet_token,
        sheet_id,
        values,
        action="append_sheet_values",
        **retry_kwargs,
    )


async def get_sheet_metadata_with_retry(
    client: lark.Client,
    spreadsheet_token: str,
    **retry_kwargs,
) -> dict:
    """get_sheet_metadata with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        get_sheet_metadata,
        client,
        spreadsheet_token,
        action="get_sheet_metadata",
        **retry_kwargs,
    )
