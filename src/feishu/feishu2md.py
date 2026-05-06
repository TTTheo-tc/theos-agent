"""
官方文档（block 格式）：
    https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/docx-overview
参考实现（转 markdown）：
    [一日一技 | 我开发的这款小工具，轻松助你将飞书文档转为 Markdown](https://sspai.com/post/73386)
    关键源码：https://github.com/Wsine/feishu2md/blob/main/core/parser.go

多维表格字段类型：
    https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/guide
"""

from __future__ import annotations

import contextlib
import json
import urllib.parse
from typing import Callable

from loguru import logger


class FeishuParser:
    """飞书文档解析器，将飞书JSON格式转换为Markdown"""

    def __init__(
        self,
        source_docs: dict[str, list[dict]] | None = None,
        bitable_renderer: Callable[[str], str] | None = None,
        sheet_renderer: Callable[[str], str] | None = None,
        sub_page_list_renderer: Callable[[str], str] | None = None,
        output_annotations: dict | None = None,
        mention_user_resolver: Callable[[str], str] | None = None,
    ):
        self.block_map: dict[str, dict] = {}
        self.source_docs = source_docs or {}
        self.bitable_renderer = bitable_renderer
        self.sheet_renderer = sheet_renderer
        self.sub_page_list_renderer = sub_page_list_renderer
        self.output_annotations = output_annotations
        self.mention_user_resolver = mention_user_resolver
        # 为每个 source document 构建 block_map
        self.source_block_maps: dict[str, dict[str, dict]] = {}
        for doc_id, blocks in self.source_docs.items():
            block_map = {}
            for block in blocks:
                if "block_id" in block:
                    block_map[block["block_id"]] = block
            self.source_block_maps[doc_id] = block_map

    def parse_document(self, doc: list[dict]) -> str:
        """解析飞书文档"""
        if not doc:
            logger.error("not any blocks")
            return ""

        # 构建block映射表
        for block in doc:
            if "block_id" in block:
                self.block_map[block["block_id"]] = block

        # 找到根节点（page类型）
        root_block = None
        for block in doc:
            if block.get("block_type") == 1:  # page type
                root_block = block
                break

        if not root_block:
            logger.error("no root block")
            return ""

        return self.parse_block(root_block, 0)

    def parse_block(self, block: dict, indent_level: int = 0) -> str:
        """
        递归解析单个block

        https://open.feishu.cn/document/docs/docs/data-structure/block#e8ce4e8e
        """
        block_type = block.get("block_type")

        if block_type == 1:  # page
            return self.parse_page_block(block)
        if block_type == 2:  # text
            return self.parse_text_block(block.get("text", {}))
        if isinstance(block_type, int) and 3 <= block_type <= 11:  # heading1..heading9
            return self.parse_heading_block(block, block_type - 2)
        if block_type == 12:  # bullet
            return self.parse_bullet_block(block, indent_level)
        if block_type == 13:  # ordered
            return self.parse_ordered_block(block, indent_level)
        if block_type == 14:  # code
            return self.parse_code_block(block)
        if block_type == 15:  # quote
            return self.parse_quote_block(block)
        if block_type == 16:  # equation
            return self.parse_equation_block(block)
        if block_type == 17:  # todo
            return self.parse_todo_block(block, indent_level)
        if block_type == 18:  # bitable
            return self.parse_bitable_block(block)
        if block_type == 19:  # callout
            return self.parse_callout_block(block)
        if block_type == 20:  # chatcard
            return self.parse_chatcard_block(block)
        if block_type == 21:  # diagram
            return self.parse_diagram_block(block)
        if block_type == 22:  # divider
            return self.parse_divider_block(block)
        if block_type == 23:  # file
            return self.parse_file_block(block)
        if block_type == 24:  # grid
            return self.parse_grid_block(block)
        if block_type == 25:  # grid column
            return self.parse_grid_column_block(block)
        if block_type == 26:  # iframe
            return self.parse_iframe_block(block)
        if block_type == 27:  # image
            return self.parse_image_block(block)
        if block_type == 28:  # isv
            return self.parse_isv_block(block)
        if block_type == 29:  # mindnote
            return self.parse_mindnote_block(block)
        if block_type == 30:  # sheet
            return self.parse_sheet_block(block)
        if block_type == 31:  # table
            return self.parse_table_block(block)
        if block_type == 32:  # table cell
            return self.parse_table_cell_block(block)
        if block_type == 33:  # view
            return self.parse_view_block(block)
        if block_type == 34:  # quote container
            return self.parse_quote_container_block(block)
        if block_type == 35:  # task
            return self.parse_task_block(block)
        if block_type == 36:  # okr
            return self.parse_okr_block(block)
        if block_type == 37:  # okr objective
            return self.parse_okr_objective_block(block)
        if block_type == 38:  # okr key result
            return self.parse_okr_key_result_block(block)
        if block_type == 39:  # okr progress
            return self.parse_okr_progress_block(block)
        if block_type == 40:  # addons
            return self.parse_addons_block(block)
        if block_type == 41:  # jira issue
            return self.parse_jira_issue_block(block)
        if block_type == 42:  # wiki catalog
            return self.parse_wiki_catalog_block(block)
        if block_type == 43:  # board
            return self.parse_board_block(block)
        if block_type == 44:  # agenda
            return self.parse_agenda_block(block)
        if block_type == 45:  # agenda item
            return self.parse_agenda_item_block(block)
        if block_type == 46:  # agenda item title
            return self.parse_agenda_item_title_block(block)
        if block_type == 47:  # agenda item content
            return self.parse_agenda_item_content_block(block)
        if block_type == 48:  # link preview
            return self.parse_link_preview_block(block)
        if block_type == 49:  # source synced
            return self.parse_source_synced_block(block)
        if block_type == 50:  # reference synced
            return self.parse_reference_synced_block(block)
        if block_type == 51:  # sub_page_list
            return self.parse_sub_page_list_block(block)
        if block_type == 52:  # ai template
            return self.parse_ai_template_block(block)
        if block_type == 53:  # reference_base (embedded bitable reference)
            return self.parse_reference_base_block(block)
        if block_type == 999:  # undefined
            return self.parse_undefined_block(block)
        # 未知类型，记录并尝试提取有用内容
        block_id = block.get("block_id", "")
        logger.warning(f"unknown block type: {block_type}, block_id: {block_id}")
        children_content = self.parse_children(block.get("children", []), indent_level)
        if children_content.strip():
            return children_content
        return f"[Feishu Block (type={block_type}, block_id={block_id})]"

    def parse_page_block(self, block: dict) -> str:
        """解析页面block"""
        result = []

        # 解析页面标题
        page_data = block.get("page", {})
        if page_data:
            result.append("# " + self.parse_text_elements(page_data.get("elements", [])))
            result.append("")

        # 当前已有的行数（用于 annotation 计算）
        current_line = len(result)

        # 解析子块
        children = block.get("children", [])
        for i, child_id in enumerate(children):
            if child_id not in self.block_map:
                continue
            child_block = self.block_map[child_id]
            start_line = current_line
            child_content = self.parse_block(child_block, 0)
            if child_content.strip():
                result.append(child_content)
                # 每个 append 的 child_content 在 "\n".join 后占据的行数
                end_line = start_line + child_content.count("\n") + 1
                current_line = end_line

                if self.output_annotations is not None:
                    self.output_annotations.setdefault("blocks", []).append(
                        {
                            "block_id": child_id,
                            "child_index": i,
                            "parent_id": block.get("block_id"),
                            "md_start_line": start_line,
                            "md_end_line": end_line,
                            "block_type": child_block.get("block_type"),
                        }
                    )

        return "\n".join(result)

    def parse_text_block(self, text_data: dict) -> str:
        """解析文本block"""
        elements = text_data.get("elements", [])
        return self.parse_text_elements(elements) + "\n"

    def parse_text_elements(self, elements: list[dict]) -> str:
        """解析文本元素列表"""
        if not elements:
            return ""

        result = []
        for element in elements:
            if element:  # 检查元素不为空
                result.append(self.parse_text_element(element))
        return "".join(result)

    def parse_text_element(self, element: dict) -> str:
        """解析单个文本元素"""
        if "text_run" in element:
            return self.parse_text_run(element["text_run"])
        if "mention_doc" in element:
            mention = element["mention_doc"]
            title = mention.get("title", "")
            url = mention.get("url", "")
            if url:
                with contextlib.suppress(Exception):
                    url = urllib.parse.unquote(url)
            return f"[{title}]({url})" if url else title
        if "mention_user" in element:
            mention = element["mention_user"]
            user_id = mention.get("user_id", "")
            # Try to get display name if available
            name = mention.get("name", "") or mention.get("user_name", "")
            if not name and user_id and self.mention_user_resolver:
                with contextlib.suppress(Exception):
                    name = self.mention_user_resolver(user_id)
            return f"@{name or user_id}"
        if "file" in element:
            file_data = element["file"]
            file_token = file_data.get("file_token", "")
            name = file_data.get("name", "file")
            return f"[{name}](file_token={file_token})"
        if "reminder" in element:
            reminder = element["reminder"]
            expire_time = reminder.get("expire_time", "")
            time_str = expire_time if expire_time else "unset"
            return f"[Reminder: {time_str}]"
        if "equation" in element:
            content = element["equation"].get("content", "")
            return f"${content.rstrip()}$"
        # Generic fallback: try to extract any text content from unknown elements
        for _key, value in element.items():
            if isinstance(value, dict):
                # Try common text field names
                for text_field in ["content", "text", "name", "title", "value"]:
                    if text_field in value and isinstance(value[text_field], str):
                        return value[text_field]
        return ""

    def parse_text_run(self, text_run: dict) -> str:
        """解析TextRun元素"""
        content = text_run.get("content", "")
        style = text_run.get("text_element_style", {})

        # Link wrapping (innermost)
        if style.get("link"):
            url = style["link"].get("url", "")
            if url:
                with contextlib.suppress(Exception):
                    url = urllib.parse.unquote(url)
                content = f"[{content}]({url})"

        # Formatting styles — independent, can stack
        if style.get("inline_code"):
            content = f"`{content}`"
        elif style.get("bold"):
            content = f"**{content}**"
        if style.get("italic"):
            content = f"_{content}_"
        if style.get("strikethrough"):
            content = f"~~{content}~~"
        if style.get("underline"):
            content = f"<u>{content}</u>"

        return content

    def parse_heading_block(self, block: dict, level: int) -> str:
        """解析标题block"""
        heading_key = f"heading{level}"
        heading_data = block.get(heading_key, {})

        prefix = "#" * level + " "
        title = self.parse_text_elements(heading_data.get("elements", []))

        result = [prefix + title]

        # 解析子块
        children_content = self.parse_children(block.get("children", []), 0)
        if children_content.strip():
            result.append(children_content)

        return "\n".join(result)

    def parse_bullet_block(self, block: dict, indent_level: int) -> str:
        """解析无序列表block"""
        bullet_data = block.get("bullet", {})
        content = self.parse_text_elements(bullet_data.get("elements", []))

        indent = "    " * indent_level
        result = [f"{indent}- {content}"]

        # 解析子块
        children_content = self.parse_children(block.get("children", []), indent_level + 1)
        if children_content.strip():
            result.append(children_content)

        return "\n".join(result)

    def parse_ordered_block(self, block: dict, indent_level: int) -> str:
        """解析有序列表block"""
        ordered_data = block.get("ordered", {})
        content = self.parse_text_elements(ordered_data.get("elements", []))

        # 计算序号
        order = self.calculate_order(block)

        indent = "    " * indent_level
        result = [f"{indent}{order}. {content}"]

        # 解析子块
        children_content = self.parse_children(
            block.get("children", []),
            indent_level + 1,
        )
        if children_content.strip():
            result.append(children_content)

        return "\n".join(result)

    def calculate_order(self, block: dict) -> int:
        """计算有序列表的序号"""
        parent_id = block.get("parent_id")
        if not parent_id or parent_id not in self.block_map:
            return 1

        parent = self.block_map[parent_id]
        children = parent.get("children", [])

        order = 1
        for child_id in children:
            if child_id == block["block_id"]:
                break
            if child_id not in self.block_map:
                continue
            child_block = self.block_map[child_id]
            if child_block.get("block_type") == 13:  # ordered type
                order += 1

        return order

    def parse_code_block(self, block: dict) -> str:
        """解析代码block"""
        code_data = block.get("code", {})
        content = self.parse_text_elements(code_data.get("elements", []))

        # 获取语言类型
        language = ""
        style = code_data.get("style", {})
        if style and "language" in style:
            lang_id = style["language"]
            language = self.get_language_string(lang_id)

        return f"```{language}\n{content.strip()}\n```\n"

    def get_language_string(self, lang_id: int) -> str:
        """根据语言ID获取语言字符串"""
        lang_map = {
            1: "",  # PlainText
            2: "abap",  # ABAP
            3: "ada",  # Ada
            4: "apache",  # Apache
            5: "apex",  # Apex
            6: "assembly",  # Assembly
            7: "bash",  # Bash
            8: "csharp",  # CSharp
            9: "cpp",  # CPlusPlus
            10: "c",  # C
            11: "cobol",  # COBOL
            12: "css",  # CSS
            13: "coffeescript",  # CoffeeScript
            14: "d",  # D
            15: "dart",  # Dart
            16: "delphi",  # Delphi
            17: "django",  # Django
            18: "dockerfile",  # Dockerfile
            19: "erlang",  # Erlang
            20: "fortran",  # Fortran
            21: "foxpro",  # FoxPro
            22: "go",  # Go
            23: "groovy",  # Groovy
            24: "html",  # HTML
            25: "htmlbars",  # HTMLBars
            26: "http",  # HTTP
            27: "haskell",  # Haskell
            28: "json",  # JSON
            29: "java",  # Java
            30: "javascript",  # JavaScript
            31: "julia",  # Julia
            32: "kotlin",  # Kotlin
            33: "latex",  # LaTeX
            34: "lisp",  # Lisp
            35: "logo",  # Logo
            36: "lua",  # Lua
            37: "matlab",  # MATLAB
            38: "makefile",  # Makefile
            39: "markdown",  # Markdown
            40: "nginx",  # Nginx
            41: "objectivec",  # Objective
            42: "openedge-abl",  # OpenEdgeABL
            43: "php",  # PHP
            44: "perl",  # Perl
            45: "postscript",  # PostScript
            46: "powershell",  # Power
            47: "prolog",  # Prolog
            48: "protobuf",  # ProtoBuf
            49: "python",  # Python
            50: "r",  # R
            51: "rpg",  # RPG
            52: "ruby",  # Ruby
            53: "rust",  # Rust
            54: "sas",  # SAS
            55: "scss",  # SCSS
            56: "sql",  # SQL
            57: "scala",  # Scala
            58: "scheme",  # Scheme
            59: "scratch",  # Scratch
            60: "shell",  # Shell
            61: "swift",  # Swift
            62: "thrift",  # Thrift
            63: "typescript",  # TypeScript
            64: "vbscript",  # VBScript
            65: "vbnet",  # Visual
            66: "xml",  # XML
            67: "yaml",  # YAML
            68: "cmake",  # CMake
            69: "diff",  # Diff
            70: "gherkin",  # Gherkin
            71: "graphql",  # GraphQL
            72: "glsl",  # OpenGL Shading language
            73: "properties",  # Properties
            74: "solidity",  # Solidity
            75: "toml",  # TOML
        }
        return lang_map.get(lang_id, "")

    def parse_callout_block(self, block: dict) -> str:
        """解析callout block"""
        emoji = block["callout"]["emoji_id"]
        result = [f"> [!{emoji}]"]

        # 解析子块
        children_content = self.parse_children(block.get("children", []), 0)
        if children_content.strip():
            # 为每行添加引用前缀
            lines = children_content.split("\n")
            quoted_lines = [f"> {line}" if line.strip() else ">" for line in lines]
            result.extend(quoted_lines)

        return "\n".join(result) + "\n"

    def parse_quote_block(self, block: dict) -> str:
        """解析引用block"""
        quote_data = block.get("quote", {})
        content = self.parse_text_elements(quote_data.get("elements", []))
        return f"> {content}"

    def parse_equation_block(self, block: dict) -> str:
        """解析数学公式block"""
        equation_data = block.get("equation", {})
        content = self.parse_text_elements(equation_data.get("elements", []))
        return f"$$\n{content.strip()}\n$$\n"

    def parse_todo_block(self, block: dict, indent_level: int = 0) -> str:
        """解析待办事项block"""
        todo_data = block.get("todo", {})
        content = self.parse_text_elements(todo_data.get("elements", []))

        # 检查是否已完成
        style = todo_data.get("style", {})
        done = style.get("done", False)

        indent = "    " * indent_level
        checkbox = "- [x] " if done else "- [ ] "
        result = [f"{indent}{checkbox}{content}"]

        # 解析子块（嵌套的待办事项或其他内容）
        children_content = self.parse_children(block.get("children", []), indent_level + 1)
        if children_content.strip():
            result.append(children_content)

        return "\n".join(result)

    def parse_bitable_block(self, block: dict) -> str:
        token = block["bitable"]["token"]
        if self.bitable_renderer:
            try:
                return self.bitable_renderer(token)
            except Exception as e:
                logger.warning(f"bitable_renderer failed for {token}: {e}")
                return f"<notice: [bitable]({token}) failed to convert: {e}>"
        return f"<notice: [bitable]({token}) not converted>"

    def parse_chatcard_block(self, block: dict) -> str:
        """解析 chatcard block - 聊天卡片"""
        chat_data = block.get("chat_card") or block.get("chatcard") or {}
        chat_id = chat_data.get("chat_id", "")
        name = chat_data.get("name", "") or chat_data.get("chat_name", "")
        url = chat_data.get("url", "") or chat_data.get("link", "")

        if name and url:
            return f"[Chat: {name}]({url})"
        if name:
            return f"[Chat: {name}]"
        if chat_id:
            return f"[Feishu Chat: {chat_id}]"

        block_id = block.get("block_id", "")
        return f"[Feishu Chat Group (block_id={block_id})]"

    def parse_diagram_block(self, block: dict) -> str:
        """解析 diagram block - 流程图/UML 等图表"""
        diagram_data = block.get("diagram") or {}
        diagram_type = diagram_data.get("diagram_type", "")
        token = diagram_data.get("token", "")

        # Map numeric diagram types to human-readable names
        type_names = {1: "Flowchart", 2: "UML", 3: "Sequence"}
        type_label = (
            type_names.get(diagram_type, "Diagram")
            if isinstance(diagram_type, int)
            else (diagram_type or "Diagram")
        )

        # Try to parse children for any embedded text content
        children_content = self.parse_children(block.get("children", []), 0)
        if children_content.strip():
            return f"**{type_label}**\n{children_content}"

        if token:
            return f"[Feishu {type_label} (token={token})]"

        block_id = block.get("block_id", "")
        return f"[Feishu {type_label} (block_id={block_id})]"

    def parse_divider_block(self, block: dict) -> str:
        """解析分割线block"""
        _ = block
        return "---\n"

    def parse_file_block(self, block: dict) -> str:
        filename = block["file"]["name"]
        token = block["file"]["token"]
        url = (
            f"https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/all/{token}"
        )
        return f"file=[{filename}]({url})"

    def parse_grid_block(self, block: dict) -> str:
        """解析 grid（多列布局）- Markdown 无法做并排，按顺序渲染各列"""
        result = self._parse_child_parts(block.get("children", []), 0)
        return "\n\n".join(result) + "\n" if result else ""

    def parse_grid_column_block(self, block: dict) -> str:
        """解析 grid column - 直接渲染列内内容"""
        return "\n".join(self._parse_child_parts(block.get("children", []), 0))

    def parse_iframe_block(self, block: dict) -> str:
        """解析 iframe block - 嵌入内容，尝试提取 URL"""
        iframe_data = block.get("iframe", {})
        url = iframe_data.get("url", "")
        # component_type_id: 1=哔哩哔哩, 2=西瓜视频, 3=优酷, 4=Airtable,
        #   5=百度地图, 6=其他, 99=unknown
        if url:
            with contextlib.suppress(Exception):
                url = urllib.parse.unquote(url)
            return f"<{url}>"
        return ""

    def parse_image_block(self, block: dict) -> str:
        """解析图片block"""
        image_data = block.get("image", {})
        token = image_data.get("token", "")

        # 直接输出图片token作为链接，不做额外处理
        return f"![](image_token={token})\n"

    def parse_isv_block(self, block: dict) -> str:
        """解析 isv block - 第三方应用插件 (可能含 Mermaid/PlantUML 等)"""
        isv_data = block.get("isv") or {}
        # Some ISV blocks embed diagram source (Mermaid, PlantUML)
        source = isv_data.get("source", "") or isv_data.get("content", "")
        app_name = isv_data.get("app_name", "") or isv_data.get("name", "")
        app_id = isv_data.get("app_id", "")

        # If there's embedded source code (Mermaid/PlantUML), render as fenced block
        if source:
            lang = isv_data.get("language", "") or isv_data.get("type", "")
            label = lang if lang else "isv"
            return f"```{label}\n{source.strip()}\n```\n"

        # Try to parse children for any embedded content
        children_content = self.parse_children(block.get("children", []), 0)
        if children_content.strip():
            if app_name:
                return f"**{app_name}**\n{children_content}"
            return children_content

        if app_name:
            return f"[Feishu Widget: {app_name}]"
        if app_id:
            return f"[Feishu Widget (app_id={app_id})]"

        block_id = block.get("block_id", "")
        return f"[Feishu Widget (block_id={block_id})]"

    def parse_mindnote_block(self, block: dict) -> str:
        """解析 mindnote block - 思维导图"""
        mindnote_data = block.get("mindnote") or {}
        token = mindnote_data.get("token", "")
        title = mindnote_data.get("title", "")

        if title and token:
            return f"[Mindmap: {title} (token={token})]"
        if title:
            return f"[Mindmap: {title}]"
        if token:
            return f"[Feishu Mindmap (token={token})]"

        block_id = block.get("block_id", "")
        return f"[Feishu Mindmap (block_id={block_id})]"

    def parse_sheet_block(self, block: dict) -> str:
        token = block["sheet"]["token"]
        if self.sheet_renderer:
            return self.sheet_renderer(token)
        return f"<notice: sheet={token} not converted>"

    def parse_table_block(self, block: dict) -> str:
        """解析表格block"""
        table_data = block.get("table", {})
        property_data = table_data.get("property", {})

        column_size = property_data.get("column_size", 0)
        row_size = property_data.get("row_size", 0)
        merge_info = property_data.get("merge_info", [])
        cells = table_data.get("cells", [])

        if not column_size or not row_size:
            return "<notice: empty table>"

        # 构建二维网格,标记每个位置的状态
        # None: 待填充, (content, attrs): 该位置的单元格信息, "skip": 被合并覆盖
        grid = [[None] * column_size for _ in range(row_size)]

        # 飞书的cells数组长度 = column_size * row_size
        # 每个cell对应一个位置,但被合并的cell不应该渲染
        for cell_idx in range(len(cells)):
            row_idx = cell_idx // column_size
            col_idx = cell_idx % column_size

            # 如果当前位置已被标记为skip,说明被之前的合并覆盖
            if grid[row_idx][col_idx] == "skip":
                continue

            cell_id = cells[cell_idx]
            merge = merge_info[cell_idx] if cell_idx < len(merge_info) else {}
            col_span = merge.get("col_span", 1)
            row_span = merge.get("row_span", 1)

            # 获取单元格内容
            content = ""
            if cell_id in self.block_map:
                cell_block = self.block_map[cell_id]
                children = cell_block.get("children", [])
                cell_parts = []
                for child_id in children:
                    if child_id in self.block_map:
                        child_block = self.block_map[child_id]
                        child_content = self.parse_block(child_block, 0)
                        if child_content.strip():
                            cell_parts.append(child_content.strip())
                content = " ".join(cell_parts)

            # 构建属性字符串
            attrs = []
            if col_span > 1:
                attrs.append(f'colspan="{col_span}"')
            if row_span > 1:
                attrs.append(f'rowspan="{row_span}"')
            attrs_str = " " + " ".join(attrs) if attrs else ""

            # 在grid中标记当前单元格及其覆盖的区域
            grid[row_idx][col_idx] = (content, attrs_str)
            for r in range(row_idx, min(row_idx + row_span, row_size)):
                for c in range(col_idx, min(col_idx + col_span, column_size)):
                    if r != row_idx or c != col_idx:
                        grid[r][c] = "skip"

        # 生成HTML
        result = ["", '<table border="1">']
        for row in grid:
            row_result = []
            row_result.append("<tr>")
            for cell in row:
                if cell == "skip" or cell is None:
                    continue
                content, attrs_str = cell
                row_result.append(f"<td{attrs_str}>{content}</td>")
            row_result.append("</tr>")
            result.append("".join(row_result))
        result.append("</table>")
        result.append("")

        return "\n".join(result)

    def parse_table_cell_block(self, block: dict) -> str:
        """解析 table cell block - 通常由 parse_table_block 处理"""
        block_id = block.get("block_id", "")
        children_content = self.parse_children(block.get("children", []), 0)
        if children_content.strip():
            return children_content
        # 独立的表格单元格（不在表格内部）- 记录为 notice
        return f"<notice: orphan table_cell block_id={block_id}>"

    def parse_view_block(self, block: dict) -> str:
        """
        1   卡片视图，独占一行的一种视图，在 Card 上可有一些简单交互
        2   预览视图，在当前页面直接预览插入的 Block 内容，而不需要打开新的页面
        3   内联视图
        """
        return "\n".join(self._parse_child_parts(block.get("children", []), 0))

    def parse_quote_container_block(self, block: dict) -> str:
        children = block.get("children", [])
        result = []
        for child_id in children:
            if child_id not in self.block_map:
                continue
            child_block = self.block_map[child_id]
            child_content = self.parse_block(child_block, 0)
            if child_content.strip():
                result.append(child_content)

        return ">   " + ">   ".join(result)

    def parse_task_block(self, block: dict) -> str:
        """解析任务block - 提取任务信息"""
        task_data = block.get("task", {})
        task_id = task_data.get("task_id", "")

        # Try to extract task summary/title
        summary = task_data.get("summary", "")
        title = task_data.get("title", "")
        content = summary or title

        # Build task representation
        result_parts = []
        if content:
            result_parts.append(f"- [ ] {content}")
        elif task_id:
            result_parts.append(f"- [ ] [Task: {task_id}]")
        else:
            result_parts.append("- [ ] [Task]")

        # Parse children if present
        children_content = self.parse_children(block.get("children", []), 1)
        if children_content.strip():
            result_parts.append(children_content)

        return "\n".join(result_parts)

    def parse_okr_block(self, block: dict) -> str:
        """解析OKR block - 尝试提取内容"""
        result_parts = ["**OKR**"]

        # Parse children which may contain objectives
        children_content = self.parse_children(block.get("children", []), 0)
        if children_content.strip():
            result_parts.append(children_content)

        return "\n".join(result_parts)

    def parse_okr_objective_block(self, block: dict) -> str:
        """解析OKR目标block"""
        obj_data = block.get("okr_objective", {})
        content = obj_data.get("content", "") or obj_data.get("objective_title", "")
        result_parts = []

        if content:
            result_parts.append(f"**O:** {content}")
        else:
            result_parts.append("**O:**")

        # Parse children (key results)
        children_content = self.parse_children(block.get("children", []), 0)
        if children_content.strip():
            result_parts.append(children_content)

        return "\n".join(result_parts)

    def parse_okr_key_result_block(self, block: dict) -> str:
        """解析OKR关键结果block"""
        kr_data = block.get("okr_key_result", {})
        content = kr_data.get("content", "") or kr_data.get("kr_title", "")

        if content:
            return f"- **KR:** {content}"
        return "- **KR:**"

    def parse_okr_progress_block(self, block: dict) -> str:
        """解析OKR进度block"""
        progress_data = block.get("okr_progress", {})
        percent = progress_data.get("percent", 0)
        return f"Progress: {percent}%"

    def parse_addons_block(self, block: dict) -> str:
        """解析插件block - 尝试提取内容"""
        addons_data = block.get("addons") or {}

        # Try to get any content
        content = addons_data.get("content", "") or addons_data.get("title", "")
        document_id = addons_data.get("document_id", "") or addons_data.get("token", "")

        if content:
            return f"[Addon: {content}]"

        # Parse children if present
        children_content = self.parse_children(block.get("children", []), 0)
        if children_content.strip():
            return children_content

        if document_id:
            return f"[Feishu Addon (token={document_id})]"

        block_id = block.get("block_id", "")
        return f"[Feishu Addon (block_id={block_id})]"

    def parse_jira_issue_block(self, block: dict) -> str:
        """解析Jira issue block - 提取issue信息"""
        jira_data = block.get("jira_issue", {})
        issue_key = jira_data.get("key", "") or jira_data.get("issue_key", "")
        summary = jira_data.get("summary", "") or jira_data.get("title", "")
        url = jira_data.get("url", "")

        if issue_key and url:
            label = f"{issue_key}: {summary}" if summary else issue_key
            return f"[{label}]({url})"
        if issue_key:
            if summary:
                return f"[Jira {issue_key}: {summary}]"
            return f"[Jira {issue_key}]"

        block_id = block.get("block_id", "")
        return f"[Feishu Jira Issue (block_id={block_id})]"

    def parse_wiki_catalog_block(self, block: dict) -> str:
        """解析wiki目录block"""
        wiki_data = block.get("wiki_catalog") or {}
        wiki_token = wiki_data.get("wiki_token", "")

        # Parse children to get actual content (catalog entries)
        children_content = self.parse_children(block.get("children", []), 0)
        if children_content.strip():
            return children_content

        if wiki_token:
            return f"[Feishu Wiki Catalog (token={wiki_token})]"
        return "[Feishu Wiki Catalog]"

    def parse_board_block(self, block: dict) -> str:
        """解析 board block - 清理版本，只保留文本、ID、类型和连接关系"""
        content = block["board"].get("content")
        token = block["board"]["token"]
        if not content:
            return f"```board\nboard_token={token}\n```\n"
        nodes = content.get("nodes", [])

        cleaned_nodes = []
        for node in nodes:
            cleaned = {"id": node.get("id"), "type": node.get("type")}

            # 保留文本内容
            if "text" in node and "text" in node["text"]:
                text = node["text"]["text"].strip()
                if text:
                    cleaned["text"] = text

            # 保留连接器关系
            if "connector" in node:
                conn = node["connector"]
                # 保留起点连接对象
                if "start_object" in conn and "id" in conn["start_object"]:
                    cleaned["connects_from"] = conn["start_object"]["id"]
                # 保留终点连接对象
                if "end_object" in conn and "id" in conn["end_object"]:
                    cleaned["connects_to"] = conn["end_object"]["id"]

            # 保留思维导图父子关系
            if "mind_map" in node and "parent_id" in node["mind_map"]:
                cleaned["parent_id"] = node["mind_map"]["parent_id"]

            cleaned_nodes.append(cleaned)

        cleaned_content = {"nodes": cleaned_nodes}
        return f"```board\n{json.dumps(cleaned_content, indent=2, ensure_ascii=False)}\n```\n"

    def parse_agenda_block(self, block: dict) -> str:
        """解析议程block - 处理children提取内容"""
        result_parts = []

        # Parse children (agenda items)
        children_content = self.parse_children(block.get("children", []), 0)
        if children_content.strip():
            result_parts.append(children_content)

        if result_parts:
            return "\n".join(result_parts)
        return ""

    def parse_agenda_item_block(self, block: dict) -> str:
        """解析议程项block"""
        result_parts = []

        # Parse children (title, content)
        children_content = self.parse_children(block.get("children", []), 0)
        if children_content.strip():
            result_parts.append(children_content)

        if result_parts:
            return "\n".join(result_parts)
        return ""

    def parse_agenda_item_title_block(self, block: dict) -> str:
        """解析议程项标题block"""
        title_data = block.get("agenda_item_title", {})
        elements = title_data.get("elements", [])
        content = self.parse_text_elements(elements)
        if content:
            return f"### {content}"
        return ""

    def parse_agenda_item_content_block(self, block: dict) -> str:
        """解析议程项内容block"""
        content_data = block.get("agenda_item_content", {})
        elements = content_data.get("elements", [])
        content = self.parse_text_elements(elements)

        # Also parse children
        children_content = self.parse_children(block.get("children", []), 0)

        result_parts = []
        if content:
            result_parts.append(content)
        if children_content.strip():
            result_parts.append(children_content)

        return "\n".join(result_parts)

    def parse_link_preview_block(self, block: dict) -> str:
        """解析链接预览block - 提取URL"""
        preview_data = block.get("link_preview", {})
        url = preview_data.get("url", "")
        title = preview_data.get("title", "")

        if url:
            with contextlib.suppress(Exception):
                url = urllib.parse.unquote(url)
            if title:
                return f"[{title}]({url})"
            return f"<{url}>"
        return ""

    def _parse_child_parts(self, children: list[str], indent_level: int) -> list[str]:
        result = []
        for child_id in children:
            if child_id not in self.block_map:
                continue
            child_block = self.block_map[child_id]
            child_content = self.parse_block(child_block, indent_level)
            if child_content.strip():
                result.append(child_content)
        return result

    def parse_source_synced_block(self, block: dict) -> str:
        result = self._parse_child_parts(block.get("children", []), 0)
        result = "\n".join(result)
        _id = block["block_id"]
        _class = "source_synced"
        return f'<div id="{_id}" class="{_class}">{result}</div>'

    def parse_reference_synced_block(self, block: dict) -> str:
        ref = block["reference_synced"]
        source_block_id = ref["source_block_id"]
        source_doc_id = ref.get("source_document_id")
        _id = block["block_id"]

        # 尝试从 source documents 中查找
        if source_doc_id and source_doc_id in self.source_block_maps:
            source_block_map = self.source_block_maps[source_doc_id]
            if source_block_id in source_block_map:
                source_block = source_block_map[source_block_id]
                # 递归解析 source block 的 children
                children = source_block.get("children", [])
                result_parts = []
                for child_id in children:
                    if child_id in source_block_map:
                        child_block = source_block_map[child_id]
                        # 临时切换 block_map 来解析 source document 中的内容
                        original_block_map = self.block_map
                        self.block_map = source_block_map
                        try:
                            child_content = self.parse_block(child_block, 0)
                            if child_content.strip():
                                result_parts.append(child_content)
                        finally:
                            self.block_map = original_block_map

                if result_parts:
                    result = "\n".join(result_parts)
                    _class = "reference_synced"
                    return f'<div id="{_id}" class="{_class}" ref="{source_block_id}" source_doc="{source_doc_id}">\n{result}\n</div>\n'

        # 如果找不到,记录警告并在文档中标注
        logger.warning(
            f"reference synced block not resolved: block={_id}, "
            f"source_doc={source_doc_id}, source_block={source_block_id}"
        )
        return (
            f"<notice: reference_synced block_id={_id} "
            f"source_doc={source_doc_id} source_block={source_block_id}>"
        )

    def parse_sub_page_list_block(self, block: dict) -> str:
        """解析 sub_page_list block (block_type=51) - 子页面列表

        该 block 用于展示当前页面的子页面列表。如果提供了 sub_page_list_renderer，
        则调用它来获取子页面的 markdown 渲染；否则返回空字符串。
        """
        sub_page_data = block.get("sub_page_list", {})
        wiki_token = sub_page_data.get("wiki_token", "")
        if not wiki_token:
            return ""
        if self.sub_page_list_renderer:
            try:
                return self.sub_page_list_renderer(wiki_token)
            except Exception as e:
                logger.warning(f"sub_page_list_renderer failed for {wiki_token}: {e}")
                return ""
        return ""

    def parse_ai_template_block(self, block: dict) -> str:
        """解析 ai_template block - AI 模板"""
        # Try to parse children for any rendered content
        children_content = self.parse_children(block.get("children", []), 0)
        if children_content.strip():
            return children_content

        block_id = block.get("block_id", "")
        return f"[Feishu AI Template (block_id={block_id})]"

    def parse_reference_base_block(self, block: dict) -> str:
        """解析 reference_base block (嵌入的多维表格引用, block_type=53)

        reference_base 包含:
            - token: "app_token_table_id" 格式
            - view_id: 可选的视图 ID
            - layout_mode: 布局模式
        """
        ref_data = block.get("reference_base", {})
        token = ref_data.get("token", "")
        if not token:
            return "<notice: reference_base block missing token>"
        if self.bitable_renderer:
            try:
                return self.bitable_renderer(token)
            except Exception as e:
                logger.warning(f"bitable_renderer failed for reference_base {token}: {e}")
                return f"<notice: [reference_base]({token}) failed to convert: {e}>"
        return f"<notice: [reference_base]({token}) not converted>"

    def parse_undefined_block(self, block: dict) -> str:
        """解析 undefined block (block_type=999)"""
        block_id = block.get("block_id", "")
        children_content = self.parse_children(block.get("children", []), 0)
        if children_content.strip():
            return children_content
        return f"[Feishu Undefined Block (block_id={block_id})]"

    def parse_children(self, children: list[str], indent_level: int) -> str:
        """解析子块列表"""
        result = []
        for child_id in children:
            if child_id not in self.block_map:
                continue
            child_block = self.block_map[child_id]
            child_content = self.parse_block(child_block, indent_level)
            if child_content.strip():
                result.append(child_content)

        return "\n".join(result)


def feishu2md(
    feishu_doc: dict | list,
    source_docs: dict[str, list[dict]] | None = None,
    bitable_renderer: Callable[[str], str] | None = None,
    sheet_renderer: Callable[[str], str] | None = None,
    sub_page_list_renderer: Callable[[str], str] | None = None,
    output_annotations: dict | None = None,
    mention_user_resolver: Callable[[str], str] | None = None,
) -> str:
    """
    把飞书（feishu/lark）的结构化文档格式，转化为 markdown。

    只保留其中的文本信息（含文本）。图片保留链接即可。不做任何额外的数据下载转化。

    Args:
        feishu_doc: 飞书文档的 JSON 数据
        source_docs: reference_synced 块引用的源文档映射 {doc_id: blocks}
        bitable_renderer: 可选的回调函数，用于渲染嵌入的 bitable 块。
                         签名: (token: str) -> str，返回 Markdown 字符串。
        sheet_renderer: 可选的回调函数，用于渲染嵌入的 sheet 块。
                       签名: (token: str) -> str，返回 Markdown 字符串。
                       token 格式为 spreadsheet_token_sheet_id。
        sub_page_list_renderer: 可选的回调函数，用于渲染子页面列表块。
                               签名: (wiki_token: str) -> str，返回 Markdown 字符串。
        output_annotations: 可选字典，传入时会被填充 block→line-range 映射。
    """
    # 如果输入是dict，转换为list
    if isinstance(feishu_doc, dict):
        if "title" in feishu_doc and "body" in feishu_doc:
            # 旧版文档

            from .feishu2md_old import feishu2md_old  # noqa: PLC0415

            return feishu2md_old(feishu_doc)
        feishu_doc = [feishu_doc]

    parser = FeishuParser(
        source_docs=source_docs,
        bitable_renderer=bitable_renderer,
        sheet_renderer=sheet_renderer,
        sub_page_list_renderer=sub_page_list_renderer,
        output_annotations=output_annotations,
        mention_user_resolver=mention_user_resolver,
    )
    return parser.parse_document(feishu_doc)
