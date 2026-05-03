from __future__ import annotations

import contextvars
import json
from pathlib import Path
from typing import Any


def read_json(path: str) -> dict:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: dict, *, indent: int = 4, ensure_ascii: bool = False):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=indent,
            ensure_ascii=ensure_ascii,
        )


def save_image(img_bytes: bytes, path: str, already_has_suffix: bool = False) -> str:
    if not already_has_suffix:
        if img_bytes[:3] == b"\xff\xd8\xff":
            suffix = "jpg"
        elif img_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            suffix = "png"
        else:
            suffix = "img"
        path = f"{path}.{suffix}"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("wb") as f:
        f.write(img_bytes)
    return path


class ScopedContextVar:
    def __init__(self, var: contextvars.ContextVar[Any], value: Any | None):
        self.var = var
        self.old_value = None
        self.new_value = value

    def __enter__(self):
        self.old_value = self.var.set(self.new_value)
        return self.new_value

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.var.reset(self.old_value)


class ScopedContextVars:
    """
    上下文管理器，用于临时设置一组 contextvars。
    """

    def __init__(self, updates: dict[contextvars.ContextVar[Any], Any | None]):
        self.updates = updates
        self.old_values = {}

    def __enter__(self) -> list[Any | None]:
        self.old_values.clear()
        new_values = []
        for var, value in self.updates.items():
            old_val = var.set(value)
            self.old_values[var] = old_val
            new_values.append(value)
        return new_values

    def __exit__(self, exc_type, exc_val, exc_tb):
        # 以相反的顺序重置，以正确恢复嵌套设置
        for var, old_val in reversed(self.old_values.items()):
            var.reset(old_val)
