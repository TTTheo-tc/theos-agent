from __future__ import annotations

from src.feishu.extra.bitable import BitableToMarkdown, bitable2md, parse_bitable_url


def test_parse_bitable_url_extracts_query_table_and_view():
    parsed = parse_bitable_url(
        "https://example.feishu.cn/base/app123?table=tbl456&view=vew789"
    )

    assert parsed == {
        "url": "https://example.feishu.cn/base/app123?table=tbl456&view=vew789",
        "app_token": "app123",
        "table_id": "tbl456",
        "view_id": "vew789",
    }


def test_parse_bitable_url_extracts_path_table():
    parsed = parse_bitable_url("https://example.feishu.cn/base/app123/tbl_path?view=vew789")

    assert parsed["app_token"] == "app123"
    assert parsed["table_id"] == "tbl_path"
    assert parsed["view_id"] == "vew789"


def test_bitable_to_markdown_formats_records_in_field_order():
    fields = [
        {"field_id": "fld1", "field_name": "Title", "type": 1},
        {"field_id": "fld2", "field_name": "Lookup", "type": 19},
        {"field_id": "fld3", "field_name": "URL", "type": 15},
    ]
    records = [
        {
            "record_id": "rec1",
            "fields": {
                "Title": "A|B\nC",
                "Lookup": [{"text": "one"}, {"name": "two"}],
                "URL": {"text": "Open", "link": "https://example.com"},
            },
        }
    ]

    markdown = bitable2md(records, fields, table_name="Roadmap")

    assert markdown == (
        "## Roadmap\n"
        "\n"
        "| Title | Lookup | URL |\n"
        "| --- | --- | --- |\n"
        "| A\\|B<br>C | one, two | [Open](https://example.com) |"
    )


def test_bitable_to_markdown_respects_selected_field_names():
    converter = BitableToMarkdown(
        [
            {"field_id": "fld1", "field_name": "Title", "type": 1},
            {"field_id": "fld2", "field_name": "Done", "type": 7},
        ]
    )

    markdown = converter.to_markdown(
        [{"record_id": "rec1", "fields": {"Title": "Ship", "Done": True}}],
        field_names=["Done", "Title"],
    )

    assert markdown == "| Done | Title |\n| --- | --- |\n| Yes | Ship |"
