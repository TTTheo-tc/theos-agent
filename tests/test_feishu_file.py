"""Tests for Feishu Drive file management API, FeishuClient file methods, and FeishuFileTool."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

from src.agent.tools.feishu import FeishuFileTool
from src.feishu import api_write

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_response(data_obj):
    """Build a mock SDK response that passes _check()."""
    resp = MagicMock()
    resp.success.return_value = True
    resp.code = 0
    resp.msg = "ok"
    resp.data = data_obj
    return resp


def _mock_lark_client():
    """Return a mock lark.Client with drive.v1 sub-services wired up."""
    client = MagicMock()
    client.drive.v1.file.create_folder = MagicMock()
    client.drive.v1.file.move = MagicMock()
    client.drive.v1.file.copy = MagicMock()
    client.drive.v1.file.delete = MagicMock()
    client.drive.v1.file.upload_all = MagicMock()
    client.drive.v1.file.list = MagicMock()
    return client


# ---------------------------------------------------------------------------
# api_write unit tests
# ---------------------------------------------------------------------------


class TestCreateFolder:
    def test_create_folder(self):
        client = _mock_lark_client()
        data = SimpleNamespace(token="fldcnXYZ", url="https://feishu.cn/folder/fldcnXYZ")
        client.drive.v1.file.create_folder.return_value = _ok_response(data)

        with patch("src.feishu.api_write._request_option", return_value=None):
            result = api_write.create_folder(client, "fldcnRoot", "My Folder")

        assert result["token"] == "fldcnXYZ"

    def test_create_folder_passes_request_option(self):
        client = _mock_lark_client()
        data = SimpleNamespace(token="fldcnXYZ")
        client.drive.v1.file.create_folder.return_value = _ok_response(data)
        option = object()

        with patch("src.feishu.api_write._request_option", return_value=option):
            api_write.create_folder(client, "fldcnRoot", "My Folder")

        assert client.drive.v1.file.create_folder.call_args.args[1] is option


class TestMoveFile:
    def test_move_file(self):
        client = _mock_lark_client()
        data = SimpleNamespace(task_id="task_123")
        client.drive.v1.file.move.return_value = _ok_response(data)

        with patch("src.feishu.api_write._request_option", return_value=None):
            result = api_write.move_file(client, "boxcnABC", "fldcnDest")

        assert result["task_id"] == "task_123"

    def test_move_file_uses_one_arg_when_no_option(self):
        client = _mock_lark_client()
        data = SimpleNamespace(task_id="task_123")
        client.drive.v1.file.move.return_value = _ok_response(data)

        with patch("src.feishu.api_write._request_option", return_value=None):
            api_write.move_file(client, "boxcnABC", "fldcnDest")

        assert len(client.drive.v1.file.move.call_args.args) == 1


class TestCopyFile:
    def test_copy_file(self):
        client = _mock_lark_client()
        data = SimpleNamespace(file={"token": "boxcnCopy", "name": "Copy of doc"})
        client.drive.v1.file.copy.return_value = _ok_response(data)

        with patch("src.feishu.api_write._request_option", return_value=None):
            result = api_write.copy_file(client, "boxcnABC", "fldcnDest", new_name="Copy of doc")

        assert result["file"]["name"] == "Copy of doc"

    def test_copy_file_without_name(self):
        client = _mock_lark_client()
        data = SimpleNamespace(file={"token": "boxcnCopy"})
        client.drive.v1.file.copy.return_value = _ok_response(data)

        with patch("src.feishu.api_write._request_option", return_value=None):
            result = api_write.copy_file(client, "boxcnABC", "fldcnDest")

        assert result["file"]["token"] == "boxcnCopy"


class TestDeleteFile:
    def test_delete_file(self):
        client = _mock_lark_client()
        data = SimpleNamespace(task_id="task_del")
        client.drive.v1.file.delete.return_value = _ok_response(data)

        with patch("src.feishu.api_write._request_option", return_value=None):
            result = api_write.delete_file(client, "boxcnABC", "file")

        assert result is True


class TestUploadFile:
    def test_upload_file(self):
        client = _mock_lark_client()
        data = SimpleNamespace(file_token="boxcnUploaded")
        client.drive.v1.file.upload_all.return_value = _ok_response(data)

        with (
            patch("src.feishu.api_write._request_option", return_value=None),
            patch("os.path.getsize", return_value=1024),
            patch("builtins.open", mock_open(read_data=b"file content")),
        ):
            result = api_write.upload_file(client, "report.pdf", "/tmp/report.pdf", "fldcnDest")

        assert result["file_token"] == "boxcnUploaded"

    def test_upload_file_passes_request_option_and_closes_file(self):
        client = _mock_lark_client()
        data = SimpleNamespace(file_token="boxcnUploaded")
        client.drive.v1.file.upload_all.return_value = _ok_response(data)
        option = object()
        file_handle = mock_open(read_data=b"file content").return_value

        with (
            patch("src.feishu.api_write._request_option", return_value=option),
            patch("os.path.getsize", return_value=1024),
            patch("builtins.open", return_value=file_handle),
        ):
            api_write.upload_file(client, "report.pdf", "/tmp/report.pdf", "fldcnDest")

        assert client.drive.v1.file.upload_all.call_args.args[1] is option
        file_handle.close.assert_called_once()


# ---------------------------------------------------------------------------
# Tool tests
# ---------------------------------------------------------------------------


def _mock_feishu_client():
    """Return a mock FeishuClient for tool testing."""
    client = MagicMock()
    client.file_list.return_value = [{"token": "f1", "name": "doc.pdf", "type": "file"}]
    client.file_create_folder.return_value = {"token": "fldcnNew", "url": "https://..."}
    client.file_upload.return_value = {"file_token": "boxcnNew"}
    client.file_move.return_value = {"task_id": "t1"}
    client.file_copy.return_value = {"file": {"token": "boxcnCopy"}}
    client.file_delete.return_value = True
    return client


class TestFeishuFileToolSchema:
    def test_tool_name(self):
        tool = FeishuFileTool(client=MagicMock())
        assert tool.name == "feishu_file"

    def test_tool_parameters_has_action(self):
        tool = FeishuFileTool(client=MagicMock())
        props = tool.parameters["properties"]
        assert "action" in props
        assert set(props["action"]["enum"]) == {
            "list",
            "upload",
            "create_folder",
            "move",
            "copy",
            "delete",
            "import",
        }


class TestFeishuFileToolListAction:
    def test_list_action(self):
        client = _mock_feishu_client()
        tool = FeishuFileTool(client=client)
        result = asyncio.run(tool.execute(action="list", folder_token="fldcnRoot"))
        assert "doc.pdf" in result
        client.file_list.assert_called_once_with("fldcnRoot")

    def test_list_requires_folder_token(self):
        tool = FeishuFileTool(client=MagicMock())
        result = asyncio.run(tool.execute(action="list"))
        assert "Error" in result


class TestFeishuFileToolUploadAction:
    def test_upload_action(self):
        client = _mock_feishu_client()
        tool = FeishuFileTool(client=client)
        result = asyncio.run(
            tool.execute(action="upload", folder_token="fldcnDest", path="/tmp/report.pdf")
        )
        assert "boxcnNew" in result
        client.file_upload.assert_called_once_with("report.pdf", "/tmp/report.pdf", "fldcnDest")

    def test_upload_requires_path(self):
        tool = FeishuFileTool(client=MagicMock())
        result = asyncio.run(tool.execute(action="upload", folder_token="fldcnDest"))
        assert "Error" in result

    def test_upload_with_custom_name(self):
        client = _mock_feishu_client()
        tool = FeishuFileTool(client=client)
        asyncio.run(
            tool.execute(
                action="upload",
                folder_token="fldcnDest",
                path="/tmp/report.pdf",
                name="Q1 Report.pdf",
            )
        )
        client.file_upload.assert_called_once_with("Q1 Report.pdf", "/tmp/report.pdf", "fldcnDest")


class TestFeishuFileToolCreateFolder:
    def test_create_folder_action(self):
        client = _mock_feishu_client()
        tool = FeishuFileTool(client=client)
        result = asyncio.run(
            tool.execute(action="create_folder", folder_token="fldcnRoot", name="Reports")
        )
        assert "fldcnNew" in result
        client.file_create_folder.assert_called_once_with("fldcnRoot", "Reports")


class TestFeishuFileToolMoveAction:
    def test_move_action(self):
        client = _mock_feishu_client()
        tool = FeishuFileTool(client=client)
        result = asyncio.run(
            tool.execute(action="move", file_token="boxcnABC", dest_folder="fldcnDest")
        )
        assert "t1" in result

    def test_move_requires_file_token(self):
        tool = FeishuFileTool(client=MagicMock())
        result = asyncio.run(tool.execute(action="move", dest_folder="fldcnDest"))
        assert "Error" in result


class TestFeishuFileToolDeleteAction:
    def test_delete_action(self):
        client = _mock_feishu_client()
        tool = FeishuFileTool(client=client)
        result = asyncio.run(tool.execute(action="delete", file_token="boxcnABC", file_type="file"))
        assert "true" in result.lower() or "True" in result

    def test_delete_requires_file_type(self):
        tool = FeishuFileTool(client=MagicMock())
        result = asyncio.run(tool.execute(action="delete", file_token="boxcnABC"))
        assert "Error" in result
