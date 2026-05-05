"""Tests for Feishu spreadsheet read/write APIs and tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.feishu.api_sheets import (
    _build_range_str,
    _escape_md_cell,
    _raw_get,
    _raw_post,
    _raw_put,
    append_sheet_values,
    get_sheet_metadata,
    parse_sheet_url,
    read_sheet_values,
    values_to_markdown,
    write_sheet_values,
)

# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


class TestParseSheetUrl:
    def test_basic_url(self):
        url = "https://example.feishu.cn/sheets/shtcnABC123"
        result = parse_sheet_url(url)
        assert result["spreadsheet_token"] == "shtcnABC123"
        assert result["sheet_id"] is None

    def test_url_with_query_sheet(self):
        url = "https://example.feishu.cn/sheets/shtcnABC123?sheet=abc456"
        result = parse_sheet_url(url)
        assert result["spreadsheet_token"] == "shtcnABC123"
        assert result["sheet_id"] == "abc456"

    def test_url_with_path_sheet(self):
        url = "https://example.feishu.cn/sheets/shtcnABC123/def789"
        result = parse_sheet_url(url)
        assert result["spreadsheet_token"] == "shtcnABC123"
        assert result["sheet_id"] == "def789"

    def test_url_with_sheet_id_query(self):
        url = "https://example.feishu.cn/sheets/shtcnABC123?sheetId=qwerty"
        result = parse_sheet_url(url)
        assert result["spreadsheet_token"] == "shtcnABC123"
        assert result["sheet_id"] == "qwerty"

    def test_url_with_spreadsheet_token_query(self):
        url = "https://example.feishu.cn/other?spreadsheetToken=shtcnXYZ&sheet=s1"
        result = parse_sheet_url(url)
        assert result["spreadsheet_token"] == "shtcnXYZ"
        assert result["sheet_id"] == "s1"

    def test_bare_token(self):
        result = parse_sheet_url("shtcnABC123")
        assert result["spreadsheet_token"] == "shtcnABC123"
        assert result["sheet_id"] is None

    def test_query_overrides_path(self):
        url = "https://example.feishu.cn/sheets/shtcnABC123/pathSheet?sheet=querySheet"
        result = parse_sheet_url(url)
        assert result["spreadsheet_token"] == "shtcnABC123"
        # Query param takes precedence
        assert result["sheet_id"] == "querySheet"


# ---------------------------------------------------------------------------
# Range string builder
# ---------------------------------------------------------------------------


class TestBuildRangeStr:
    def test_with_range(self):
        assert _build_range_str("sheet1", "A1:C10") == "sheet1!A1:C10"

    def test_without_range(self):
        assert _build_range_str("sheet1", "") == "sheet1"

    def test_no_range_arg(self):
        assert _build_range_str("sheet1") == "sheet1"


# ---------------------------------------------------------------------------
# Markdown formatting
# ---------------------------------------------------------------------------


class TestValuesToMarkdown:
    def test_basic_table(self):
        values = [["Name", "Age", "City"], ["Alice", 30, "Beijing"], ["Bob", 25, "Shanghai"]]
        md = values_to_markdown(values)
        assert "| Name | Age | City |" in md
        assert "| --- | --- | --- |" in md
        assert "| Alice | 30 | Beijing |" in md
        assert "| Bob | 25 | Shanghai |" in md

    def test_with_sheet_name(self):
        values = [["A"], ["1"]]
        md = values_to_markdown(values, sheet_name="Sheet1")
        assert "**Sheet: Sheet1**" in md

    def test_with_range(self):
        values = [["A"], ["1"]]
        md = values_to_markdown(values, range_str="A1:A2")
        assert "A1:A2" in md

    def test_with_name_and_range(self):
        values = [["A"], ["1"]]
        md = values_to_markdown(values, sheet_name="Sheet1", range_str="A1:A2")
        assert "**Sheet: Sheet1 [A1:A2]**" in md

    def test_empty_values(self):
        md = values_to_markdown([])
        assert "*(empty)*" in md

    def test_short_rows_padded(self):
        values = [["A", "B", "C"], ["1"]]
        md = values_to_markdown(values)
        lines = md.strip().split("\n")
        # Data row should have 3 cells even though input only has 1
        data_line = lines[-1]
        assert data_line.count("|") == 4  # 3 cells + bookend pipes

    def test_escape_pipe(self):
        assert _escape_md_cell("a|b") == "a\\|b"

    def test_escape_newline(self):
        assert _escape_md_cell("a\nb") == "a b"

    def test_none_cell(self):
        assert _escape_md_cell(None) == ""


# ---------------------------------------------------------------------------
# API function tests (mocked)
# ---------------------------------------------------------------------------


def _mock_client_response(data: dict):
    """Create a mock lark client that returns data for raw requests."""
    mock_client = MagicMock()
    response_content = json.dumps({"code": 0, "msg": "success", "data": data}).encode()
    mock_response = MagicMock()
    mock_response.success.return_value = True
    mock_response.raw.content = response_content
    mock_client.request.return_value = mock_response
    return mock_client


@patch("src.feishu.api_sheets._request_option", return_value=None)
@patch("src.feishu.api_sheets.ctx_current_token")
class TestReadSheetValues:
    def test_calls_api(self, mock_ctx, mock_opt):
        mock_ctx.get.return_value = None
        data = {"valueRange": {"values": [["a", "b"], [1, 2]], "range": "s1!A1:B2"}}
        client = _mock_client_response(data)

        result = read_sheet_values(client, "shtcnXYZ", "sheet1", range="A1:B2")

        assert result["values"] == [["a", "b"], [1, 2]]
        assert result["rows"] == 2
        assert result["cols"] == 2
        client.request.assert_called_once()

    def test_empty_range(self, mock_ctx, mock_opt):
        mock_ctx.get.return_value = None
        data = {"valueRange": {"values": [["x"]], "range": "sheet1"}}
        client = _mock_client_response(data)

        result = read_sheet_values(client, "shtcnXYZ", "sheet1")

        assert result["rows"] == 1
        # Verify the URI doesn't have a trailing !
        call_args = client.request.call_args
        req_obj = call_args[0][0]
        assert "sheet1" in req_obj.uri
        assert "!" not in req_obj.uri


@patch("src.feishu.api_sheets.ctx_current_token")
class TestRawRequest:
    def test_raw_get_uses_tenant_token_without_user_token(self, mock_ctx):
        import lark_oapi as lark

        mock_ctx.get.return_value = None
        client = _mock_client_response({"ok": True})

        with patch("src.feishu.api_sheets._request_option", return_value=None):
            result = _raw_get(client, "/uri")

        req_obj = client.request.call_args.args[0]
        assert result == {"ok": True}
        assert req_obj.http_method == lark.HttpMethod.GET
        assert req_obj.token_types == {lark.AccessTokenType.TENANT}
        assert len(client.request.call_args.args) == 1

    def test_raw_get_passes_request_option_and_user_token_type(self, mock_ctx):
        import lark_oapi as lark

        mock_ctx.get.return_value = "user-token"
        client = _mock_client_response({"ok": True})
        option = object()

        with patch("src.feishu.api_sheets._request_option", return_value=option):
            _raw_get(client, "/uri")

        req_obj = client.request.call_args.args[0]
        assert req_obj.token_types == {lark.AccessTokenType.USER}
        assert client.request.call_args.args[1] is option

    def test_raw_write_wrappers_set_method_uri_and_body(self, mock_ctx):
        import lark_oapi as lark

        mock_ctx.get.return_value = None
        put_client = _mock_client_response({"updatedCells": 1})
        post_client = _mock_client_response({"tableRange": "s1!A1:B2"})
        body = {"valueRange": {"range": "s1!A1:B1", "values": [["a", "b"]]}}

        with patch("src.feishu.api_sheets._request_option", return_value=None):
            _raw_put(put_client, "/put-uri", body)
            _raw_post(post_client, "/post-uri", body)

        put_req = put_client.request.call_args.args[0]
        post_req = post_client.request.call_args.args[0]
        assert put_req.http_method == lark.HttpMethod.PUT
        assert put_req.uri == "/put-uri"
        assert put_req.body is body
        assert post_req.http_method == lark.HttpMethod.POST
        assert post_req.uri == "/post-uri"
        assert post_req.body is body


@patch("src.feishu.api_sheets._request_option", return_value=None)
@patch("src.feishu.api_sheets.ctx_current_token")
class TestWriteSheetValues:
    def test_calls_api(self, mock_ctx, mock_opt):
        mock_ctx.get.return_value = None
        data = {"spreadsheetToken": "shtcnXYZ", "updatedCells": 4}
        client = _mock_client_response(data)

        result = write_sheet_values(client, "shtcnXYZ", "sheet1", "A1:B2", [[1, 2], [3, 4]])

        assert result.get("updatedCells") == 4
        client.request.assert_called_once()
        req_obj = client.request.call_args[0][0]
        assert req_obj.body["valueRange"]["range"] == "sheet1!A1:B2"


@patch("src.feishu.api_sheets._request_option", return_value=None)
@patch("src.feishu.api_sheets.ctx_current_token")
class TestAppendSheetValues:
    def test_calls_api(self, mock_ctx, mock_opt):
        mock_ctx.get.return_value = None
        data = {"tableRange": "sheet1!A1:B3"}
        client = _mock_client_response(data)

        append_sheet_values(client, "shtcnXYZ", "sheet1", [["a", "b"]])

        client.request.assert_called_once()
        req_obj = client.request.call_args[0][0]
        assert "values_append" in req_obj.uri


@patch("src.feishu.api_sheets.list_spreadsheet_sheets")
@patch("src.feishu.api_sheets._request_option", return_value=None)
@patch("src.feishu.api_sheets.ctx_current_token")
class TestGetSheetMetadata:
    def test_returns_metadata(self, mock_ctx, mock_opt, mock_list_sheets):
        mock_ctx.get.return_value = None
        mock_list_sheets.return_value = [
            {"sheet_id": "s1", "title": "Sheet1", "index": 0},
        ]
        data = {"spreadsheet": {"title": "My Sheet", "spreadsheet_token": "shtcnXYZ"}}
        client = _mock_client_response(data)

        result = get_sheet_metadata(client, "shtcnXYZ")

        assert result["title"] == "My Sheet"
        assert result["spreadsheet_token"] == "shtcnXYZ"
        assert len(result["sheets"]) == 1
        assert result["sheets"][0]["sheet_id"] == "s1"


# ---------------------------------------------------------------------------
# Tool schema / execution tests
# ---------------------------------------------------------------------------


class TestFeishuSheetTool:
    def test_schema(self):
        from src.agent.tools.feishu import FeishuSheetTool

        tool = FeishuSheetTool(client=MagicMock())
        schema = tool.to_schema()
        assert schema["function"]["name"] == "feishu_sheet"
        params = schema["function"]["parameters"]
        assert "action" in params["properties"]
        assert "url" in params["properties"]
        assert "range" in params["properties"]
        assert "values" in params["properties"]
        values = params["properties"]["values"]
        assert values["items"]["type"] == "array"
        assert "items" in values["items"]
        assert set(params["required"]) == {"action", "url"}

    @pytest.mark.asyncio
    async def test_read(self):
        from src.agent.tools.feishu import FeishuSheetTool

        mock_client = MagicMock()
        mock_client.read_sheet.return_value = "| A |\n|---|\n| 1 |"
        tool = FeishuSheetTool(client=mock_client)

        result = await tool.execute(action="read", url="https://x.feishu.cn/sheets/abc")

        assert "| A |" in result
        mock_client.read_sheet.assert_called_once_with("https://x.feishu.cn/sheets/abc", range="")

    @pytest.mark.asyncio
    async def test_write(self):
        from src.agent.tools.feishu import FeishuSheetTool

        mock_client = MagicMock()
        mock_client.write_sheet.return_value = {"updatedCells": 2}
        tool = FeishuSheetTool(client=mock_client)

        result = await tool.execute(
            action="write",
            url="https://x.feishu.cn/sheets/abc",
            range="A1:B1",
            values=[["hello", "world"]],
        )

        assert "updatedCells" in result
        mock_client.write_sheet.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_missing_range(self):
        from src.agent.tools.feishu import FeishuSheetTool

        tool = FeishuSheetTool(client=MagicMock())
        result = await tool.execute(
            action="write", url="https://x.feishu.cn/sheets/abc", values=[["a"]]
        )
        assert "Error" in result
        assert "range" in result.lower()

    @pytest.mark.asyncio
    async def test_write_missing_values(self):
        from src.agent.tools.feishu import FeishuSheetTool

        tool = FeishuSheetTool(client=MagicMock())
        result = await tool.execute(
            action="write", url="https://x.feishu.cn/sheets/abc", range="A1:A1"
        )
        assert "Error" in result
        assert "values" in result.lower()

    @pytest.mark.asyncio
    async def test_append(self):
        from src.agent.tools.feishu import FeishuSheetTool

        mock_client = MagicMock()
        mock_client.append_sheet.return_value = {"tableRange": "sheet1!A1:B3"}
        tool = FeishuSheetTool(client=mock_client)

        result = await tool.execute(
            action="append",
            url="https://x.feishu.cn/sheets/abc",
            values=[["new", "row"]],
        )

        assert "tableRange" in result
        mock_client.append_sheet.assert_called_once()

    @pytest.mark.asyncio
    async def test_info(self):
        from src.agent.tools.feishu import FeishuSheetTool

        mock_client = MagicMock()
        mock_client.sheet_info.return_value = {
            "title": "Test",
            "spreadsheet_token": "abc",
            "sheets": [],
        }
        tool = FeishuSheetTool(client=mock_client)

        result = await tool.execute(action="info", url="https://x.feishu.cn/sheets/abc")

        assert "Test" in result
        mock_client.sheet_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        from src.agent.tools.feishu import FeishuSheetTool

        tool = FeishuSheetTool(client=MagicMock())
        result = await tool.execute(action="delete", url="https://x.feishu.cn/sheets/abc")
        assert "Error" in result
        assert "unknown action" in result.lower()

    def test_risk_level(self):
        from src.agent.tools.feishu import FeishuSheetTool

        tool = FeishuSheetTool(client=MagicMock())
        assert tool.risk_level == "medium"
