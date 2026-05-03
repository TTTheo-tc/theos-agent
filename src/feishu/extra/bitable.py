"""Bitable (multi-dimensional table) support for Feishu.

This module provides API functions and markdown conversion for Feishu Bitable.

API Documentation:
    https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/guide

Field type reference:
    1: 多行文本 (Text)
    2: 数字 (Number)
    3: 单选 (SingleSelect)
    4: 多选 (MultiSelect)
    5: 日期 (DateTime)
    7: 复选框 (Checkbox)
    11: 人员 (User)
    13: 电话号码 (Phone)
    15: 超链接 (URL)
    17: 附件 (Attachment)
    18: 单向关联 (SingleLink)
    19: 查找引用 (Lookup)
    20: 公式 (Formula)
    21: 双向关联 (DuplexLink)
    22: 地理位置 (Location)
    23: 群组 (GroupChat)
    1001: 创建时间 (CreatedTime)
    1002: 最后更新时间 (ModifiedTime)
    1003: 创建人 (CreatedUser)
    1004: 修改人 (ModifiedUser)
    1005: 自动编号 (AutoNumber)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import ClassVar

import httpx

from src.feishu.api import DEFAULT_TIMEOUT, feishu_auth_header


def parse_bitable_url(url: str) -> dict:
    """
    解析多维表格 URL，提取 app_token、table_id 和 view_id。

    支持的 URL 格式:
    - https://xxx.feishu.cn/base/<app_token>?table=<table_id>&view=<view_id>
    - https://xxx.feishu.cn/base/<app_token>/<table_id>?view=<view_id>

    Returns:
        {
            "url": str,
            "app_token": str | None,
            "table_id": str | None,
            "view_id": str | None,
        }
    """
    from urllib.parse import parse_qs, urlparse  # noqa: PLC0415

    parsed = urlparse(url)
    path_parts = parsed.path.split("/")
    query_params = parse_qs(parsed.query)

    ret = {"url": url, "app_token": None, "table_id": None, "view_id": None}

    # Extract app_token from path
    if "base" in path_parts:
        base_idx = path_parts.index("base")
        if base_idx + 1 < len(path_parts):
            ret["app_token"] = path_parts[base_idx + 1]
        # Check if table_id is in path (format: /base/<app_token>/<table_id>)
        if base_idx + 2 < len(path_parts) and path_parts[base_idx + 2]:
            ret["table_id"] = path_parts[base_idx + 2]

    # Extract table_id and view_id from query params
    if "table" in query_params:
        ret["table_id"] = query_params["table"][0]
    if "view" in query_params:
        ret["view_id"] = query_params["view"][0]

    return ret


def list_bitable_tables(app_token: str) -> list[dict]:
    """
    获取多维表格下的所有数据表列表。

    https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table/list
    https://open.feishu.cn/open-apis/bitable/v1/apps/:app_token/tables

    Args:
        app_token: 多维表格的 app_token

    Returns:
        数据表列表，每个数据表包含 table_id, name, revision 等信息
    """
    header = feishu_auth_header()
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables"
    page_token = None
    tables = []
    while True:
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token

        resp = httpx.get(
            url,
            headers=header,
            params=params,
            follow_redirects=True,
            timeout=DEFAULT_TIMEOUT,
        )
        assert resp.status_code == 200, f"error: {resp.status_code}, {resp.text}"
        data = resp.json()
        assert data["code"] == 0, f"code != 0, text: {resp.text}"

        data = data.get("data", {})
        tables.extend(data.get("items", []))

        if not data.get("has_more", False):
            break
        page_token = data.get("page_token")
        if not page_token:
            break
    return tables


def list_bitable_fields(app_token: str, table_id: str) -> list[dict]:
    """
    获取指定数据表的字段定义（表头）。

    https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/list
    https://open.feishu.cn/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields

    Args:
        app_token: 多维表格的 app_token
        table_id: 数据表 ID

    Returns:
        字段列表，每个字段包含 field_id, field_name, type, property 等信息
    """
    header = feishu_auth_header()
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    page_token = None
    fields = []
    while True:
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token

        resp = httpx.get(
            url,
            headers=header,
            params=params,
            follow_redirects=True,
            timeout=DEFAULT_TIMEOUT,
        )
        assert resp.status_code == 200, f"error: {resp.status_code}, {resp.text}"
        data = resp.json()
        assert data["code"] == 0, f"code != 0, text: {resp.text}"

        data = data.get("data", {})
        fields.extend(data.get("items", []))

        if not data.get("has_more", False):
            break
        page_token = data.get("page_token")
        if not page_token:
            break
    return fields


def list_bitable_records(
    app_token: str,
    table_id: str,
    view_id: str | None = None,
) -> list[dict]:
    """
    获取数据表中的所有记录，内置自动分页逻辑。

    https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/list
    https://open.feishu.cn/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records

    Args:
        app_token: 多维表格的 app_token
        table_id: 数据表 ID
        view_id: 可选的视图 ID，用于过滤和排序

    Returns:
        记录列表，每条记录包含 record_id, fields 等信息
    """
    header = feishu_auth_header()
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    page_token = None
    records = []
    while True:
        params = {"page_size": 500}  # Max page size for records is 500
        if page_token:
            params["page_token"] = page_token
        if view_id:
            params["view_id"] = view_id

        resp = httpx.get(
            url,
            headers=header,
            params=params,
            follow_redirects=True,
            timeout=DEFAULT_TIMEOUT,
        )
        assert resp.status_code == 200, f"error: {resp.status_code}, {resp.text}"
        data = resp.json()
        assert data["code"] == 0, f"code != 0, text: {resp.text}"

        data = data.get("data", {})
        records.extend(data.get("items", []))

        if not data.get("has_more", False):
            break
        page_token = data.get("page_token")
        if not page_token:
            break
    return records


def info_bitable(app_token: str) -> dict:
    """
    获取多维表格的元信息。

    https://open.feishu.cn/document/server-docs/docs/bitable-v1/app/get
    https://open.feishu.cn/open-apis/bitable/v1/apps/:app_token

    Args:
        app_token: 多维表格的 app_token

    Returns:
        多维表格元信息，包含 name, revision, is_advanced 等
    """
    header = feishu_auth_header()
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
    resp = httpx.get(url, headers=header, follow_redirects=True, timeout=DEFAULT_TIMEOUT)
    assert resp.status_code == 200, f"error: {resp.status_code}, {resp.text}"
    data = resp.json()
    assert data["code"] == 0, f"code != 0, text: {resp.text}"
    return data["data"]["app"]


class BitableToMarkdown:
    """多维表格转 Markdown 转换器。

    将飞书多维表格的复杂 JSON 数据转换为易读的 Markdown 表格格式。

    字段类型参考：
    https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/guide

    字段类型枚举：
    1: 多行文本 (Text)
    2: 数字 (Number)
    3: 单选 (SingleSelect)
    4: 多选 (MultiSelect)
    5: 日期 (DateTime)
    7: 复选框 (Checkbox)
    11: 人员 (User)
    13: 电话号码 (Phone)
    15: 超链接 (URL)
    17: 附件 (Attachment)
    18: 单向关联 (SingleLink)
    19: 查找引用 (Lookup)
    20: 公式 (Formula)
    21: 双向关联 (DuplexLink)
    22: 地理位置 (Location)
    23: 群组 (GroupChat)
    1001: 创建时间 (CreatedTime)
    1002: 最后更新时间 (ModifiedTime)
    1003: 创建人 (CreatedUser)
    1004: 修改人 (ModifiedUser)
    1005: 自动编号 (AutoNumber)
    """

    # 字段类型到名称的映射
    FIELD_TYPES: ClassVar[dict[int, str]] = {
        1: "Text",
        2: "Number",
        3: "SingleSelect",
        4: "MultiSelect",
        5: "DateTime",
        7: "Checkbox",
        11: "User",
        13: "Phone",
        15: "URL",
        17: "Attachment",
        18: "SingleLink",
        19: "Lookup",
        20: "Formula",
        21: "DuplexLink",
        22: "Location",
        23: "GroupChat",
        1001: "CreatedTime",
        1002: "ModifiedTime",
        1003: "CreatedUser",
        1004: "ModifiedUser",
        1005: "AutoNumber",
    }

    def __init__(self, fields: list[dict]):
        """
        Args:
            fields: 字段定义列表，从 list_bitable_fields API 获取
        """
        self.fields = fields
        # 构建 field_id -> field_info 映射
        self.field_map = {f["field_id"]: f for f in fields}
        # 构建 field_name -> field_info 映射（用于按名称查找）
        self.field_name_map = {f["field_name"]: f for f in fields}

    def format_value(self, value, field_info: dict) -> str:
        """根据字段类型格式化单个值。

        Args:
            value: 原始字段值
            field_info: 字段定义信息

        Returns:
            格式化后的字符串
        """
        if value is None:
            return ""

        field_type = field_info.get("type", 1)

        # 根据字段类型分派到对应的格式化方法
        if field_type == 1:  # Text
            return self._format_text(value)
        if field_type == 2:  # Number
            return self._format_number(value)
        if field_type == 3:  # SingleSelect
            return self._format_single_select(value)
        if field_type == 4:  # MultiSelect
            return self._format_multi_select(value)
        if field_type == 5:  # DateTime
            return self._format_datetime(value)
        if field_type == 7:  # Checkbox
            return self._format_checkbox(value)
        if field_type in {11, 1003, 1004}:  # User, CreatedUser, ModifiedUser
            return self._format_user(value)
        if field_type == 13:  # Phone
            return self._format_phone(value)
        if field_type == 15:  # URL
            return self._format_url(value)
        if field_type == 17:  # Attachment
            return self._format_attachment(value)
        if field_type in {18, 21}:  # SingleLink, DuplexLink
            return self._format_link(value)
        if field_type in {19, 20}:  # Lookup, Formula
            return self._format_lookup_formula(value, field_info)
        if field_type == 22:  # Location
            return self._format_location(value)
        if field_type == 23:  # GroupChat
            return self._format_group_chat(value)
        if field_type in {1001, 1002}:  # CreatedTime, ModifiedTime
            return self._format_datetime(value)
        if field_type == 1005:  # AutoNumber
            return self._format_auto_number(value)

        # 未知类型，尝试通用处理
        return self._format_unknown(value)

    def _format_text(self, value) -> str:
        """格式化多行文本。

        文本值可能是简单字符串，也可能是富文本数组。
        """
        if isinstance(value, str):
            return self._escape_markdown_table(value)
        if isinstance(value, list):
            # 富文本数组
            parts = []
            for item in value:
                if isinstance(item, dict):
                    text = item.get("text", "")
                    parts.append(text)
                elif isinstance(item, str):
                    parts.append(item)
            return self._escape_markdown_table("".join(parts))
        return self._escape_markdown_table(str(value))

    def _format_number(self, value) -> str:
        """格式化数字。"""
        if isinstance(value, (int, float)):
            # 如果是整数，去掉小数点
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value)
        return str(value)

    def _format_single_select(self, value) -> str:
        """格式化单选。

        单选值是选项的名称字符串。
        """
        if isinstance(value, str):
            return self._escape_markdown_table(value)
        if isinstance(value, dict):
            return self._escape_markdown_table(value.get("text", str(value)))
        return str(value)

    def _format_multi_select(self, value) -> str:
        """格式化多选。

        多选值是选项名称字符串的数组。
        """
        if isinstance(value, list):
            items = []
            for item in value:
                if isinstance(item, str):
                    items.append(item)
                elif isinstance(item, dict):
                    items.append(item.get("text", str(item)))
            return self._escape_markdown_table(", ".join(items))
        if isinstance(value, str):
            return self._escape_markdown_table(value)
        return str(value)

    def _format_datetime(self, value) -> str:
        """格式化日期时间。

        日期值是毫秒时间戳。
        """
        if isinstance(value, (int, float)):
            try:
                # 毫秒时间戳转 datetime
                dt = datetime.fromtimestamp(value / 1000)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OSError):
                return str(value)
        return str(value)

    def _format_checkbox(self, value) -> str:
        """格式化复选框。"""
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return str(value)

    def _format_user(self, value) -> str:
        """格式化人员。

        人员值是用户对象数组：[{"id": "ou_xxx", "name": "张三"}, ...]
        """
        if isinstance(value, list):
            names = []
            for user in value:
                if isinstance(user, dict):
                    name = user.get("name") or user.get("en_name") or user.get("id", "")
                    if name:
                        names.append(name)
            return self._escape_markdown_table(", ".join(names))
        if isinstance(value, dict):
            return self._escape_markdown_table(
                value.get("name") or value.get("en_name") or value.get("id", "")
            )
        return str(value)

    def _format_phone(self, value) -> str:
        """格式化电话号码。"""
        if isinstance(value, str):
            return value
        return str(value)

    def _format_url(self, value) -> str:
        """格式化超链接。

        超链接值是包含 text 和 link 的对象。
        """
        if isinstance(value, dict):
            text = value.get("text", "")
            link = value.get("link", "")
            if link and text:
                return f"[{self._escape_markdown_table(text)}]({link})"
            if link:
                return link
            if text:
                return self._escape_markdown_table(text)
            return ""
        if isinstance(value, str):
            return value
        return str(value)

    def _format_attachment(self, value) -> str:
        """格式化附件。

        附件值是文件对象数组：[{"name": "file.pdf", "url": "...", "file_token": "..."}, ...]
        """
        if isinstance(value, list):
            links = []
            for attachment in value:
                if isinstance(attachment, dict):
                    name = attachment.get("name", "file")
                    url = attachment.get("url") or attachment.get("tmp_url", "")
                    file_token = attachment.get("file_token", "")
                    if url:
                        links.append(f"[{self._escape_markdown_table(name)}]({url})")
                    elif file_token:
                        links.append(f"{name} (token={file_token})")
                    else:
                        links.append(name)
            return ", ".join(links)
        return str(value)

    def _format_link(self, value) -> str:
        """格式化关联字段（单向/双向关联）。

        关联值是关联记录的对象数组：[{"record_ids": ["recXXX"], "text": "..."}, ...]
        或简单的 record_id 列表。
        """
        if isinstance(value, list):
            texts = []
            for item in value:
                if isinstance(item, dict):
                    text = item.get("text", "")
                    if text:
                        texts.append(text)
                    elif item.get("record_ids"):
                        texts.extend(item["record_ids"])
                elif isinstance(item, str):
                    texts.append(item)
            return self._escape_markdown_table(", ".join(texts))
        if isinstance(value, dict):
            text = value.get("text", "")
            if text:
                return self._escape_markdown_table(text)
            record_ids = value.get("record_ids", [])
            return ", ".join(record_ids)
        return str(value)

    def _format_lookup_formula(self, value, field_info: dict) -> str:  # noqa: ARG002
        """格式化查找引用和公式字段。

        这些字段的值类型取决于目标字段类型。
        """
        # 尝试根据值类型推断格式化方式
        if isinstance(value, list):
            # 可能是数组结果
            formatted = []
            for item in value:
                formatted.append(self._format_unknown(item))
            return ", ".join(formatted)
        if isinstance(value, bool):
            return self._format_checkbox(value)
        if isinstance(value, (int, float)):
            return self._format_number(value)
        if isinstance(value, str):
            return self._escape_markdown_table(value)
        if isinstance(value, dict):
            # 可能是复杂对象
            if "text" in value:
                return self._escape_markdown_table(value["text"])
            if "name" in value:
                return self._escape_markdown_table(value["name"])
        return self._format_unknown(value)

    def _format_location(self, value) -> str:
        """格式化地理位置。

        位置值包含 name, address, full_address 等字段。
        """
        if isinstance(value, dict):
            full_address = value.get("full_address", "")
            name = value.get("name", "")
            if full_address:
                return self._escape_markdown_table(full_address)
            if name:
                return self._escape_markdown_table(name)
            return str(value)
        if isinstance(value, str):
            return self._escape_markdown_table(value)
        return str(value)

    def _format_group_chat(self, value) -> str:
        """格式化群组。

        群组值是群对象数组：[{"id": "oc_xxx", "name": "群名"}, ...]
        """
        if isinstance(value, list):
            names = []
            for group in value:
                if isinstance(group, dict):
                    name = group.get("name", "") or group.get("id", "")
                    if name:
                        names.append(name)
            return self._escape_markdown_table(", ".join(names))
        if isinstance(value, dict):
            return self._escape_markdown_table(value.get("name", "") or value.get("id", ""))
        return str(value)

    def _format_auto_number(self, value) -> str:
        """格式化自动编号。"""
        if isinstance(value, (int, float)):
            return str(int(value))
        return str(value)

    def _format_unknown(self, value) -> str:
        """处理未知类型的值。"""
        if value is None:
            return ""
        if isinstance(value, str):
            return self._escape_markdown_table(value)
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, dict):
                    # 尝试提取常见字段
                    text = item.get("text") or item.get("name") or item.get("value") or str(item)
                    parts.append(str(text))
                else:
                    parts.append(str(item))
            return self._escape_markdown_table(", ".join(parts))
        if isinstance(value, dict):
            # 尝试提取常见字段
            text = (
                value.get("text")
                or value.get("name")
                or value.get("value")
                or json.dumps(value, ensure_ascii=False)
            )
            return self._escape_markdown_table(str(text))
        return self._escape_markdown_table(str(value))

    def _escape_markdown_table(self, text: str) -> str:
        """转义 Markdown 表格中的特殊字符。

        主要处理 | 和换行符，避免破坏表格结构。
        """
        if not isinstance(text, str):
            text = str(text)
        # 转义管道符
        text = text.replace("|", "\\|")
        # 将换行符替换为 <br>
        return text.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")

    def format_record(self, record: dict, field_names: list[str]) -> list[str]:
        """格式化单条记录为表格行。

        Args:
            record: 记录数据，包含 record_id 和 fields
            field_names: 要输出的字段名称列表（按顺序）

        Returns:
            格式化后的单元格值列表
        """
        fields_data = record.get("fields", {})
        row = []
        for field_name in field_names:
            value = fields_data.get(field_name)
            field_info = self.field_name_map.get(field_name, {"type": 1})
            formatted = self.format_value(value, field_info)
            row.append(formatted)
        return row

    def to_markdown(
        self,
        records: list[dict],
        field_names: list[str] | None = None,
    ) -> str:
        """将记录列表转换为 Markdown 表格。

        Args:
            records: 记录列表
            field_names: 要输出的字段名称列表（按顺序）。
                        如果为 None，则使用所有字段。

        Returns:
            Markdown 表格字符串
        """
        if not records:
            return "*No records*"

        # 确定要输出的字段
        if field_names is None:
            # 按字段定义顺序
            field_names = [f["field_name"] for f in self.fields]

        if not field_names:
            return "*No fields*"

        # 构建表头
        header = "| " + " | ".join(self._escape_markdown_table(n) for n in field_names) + " |"
        separator = "| " + " | ".join(["---"] * len(field_names)) + " |"

        # 构建表格行
        rows = []
        for record in records:
            cells = self.format_record(record, field_names)
            row = "| " + " | ".join(cells) + " |"
            rows.append(row)

        # 组合表格
        table_parts = [header, separator, *rows]
        return "\n".join(table_parts)


def bitable2md(
    records: list[dict],
    fields: list[dict],
    table_name: str | None = None,
    field_names: list[str] | None = None,
) -> str:
    """
    将飞书多维表格数据转换为 Markdown 格式。

    Args:
        records: 记录列表，从 list_bitable_records API 获取
        fields: 字段定义列表，从 list_bitable_fields API 获取
        table_name: 可选的表格名称，用于生成标题
        field_names: 要输出的字段名称列表（按顺序）。
                    如果为 None，则使用所有字段。

    Returns:
        Markdown 格式的表格字符串
    """
    result_parts = []

    # 添加表格名称作为标题
    if table_name:
        result_parts.append(f"## {table_name}")
        result_parts.append("")

    # 转换表格
    converter = BitableToMarkdown(fields)
    table_md = converter.to_markdown(records, field_names)
    result_parts.append(table_md)

    return "\n".join(result_parts)
