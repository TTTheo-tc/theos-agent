from __future__ import annotations

import json

from src.feishu.utils import read_json, write_json


def test_write_json_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "data.json"

    write_json(path, {"name": "TheOS"})

    assert path.exists()
    assert read_json(path) == {"name": "TheOS"}


def test_write_json_preserves_unicode_by_default(tmp_path):
    path = tmp_path / "data.json"

    write_json(path, {"title": "飞书"})

    assert "飞书" in path.read_text(encoding="utf-8")


def test_write_json_can_emit_ascii_escapes(tmp_path):
    path = tmp_path / "data.json"

    write_json(path, {"title": "飞书"}, ensure_ascii=True)

    assert json.loads(path.read_text(encoding="utf-8")) == {"title": "飞书"}
    assert "飞书" not in path.read_text(encoding="utf-8")


def test_json_helpers_expand_user_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    write_json("~/data.json", {"ok": True})

    assert (tmp_path / "data.json").exists()
    assert read_json("~/data.json") == {"ok": True}
