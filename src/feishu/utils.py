from __future__ import annotations

import json
from pathlib import Path
from typing import Any

JsonData = dict[str, Any] | list[Any]


def read_json(path: str | Path) -> JsonData:
    with Path(path).expanduser().open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str | Path, data: JsonData, *, indent: int = 4, ensure_ascii: bool = False) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=indent,
            ensure_ascii=ensure_ascii,
        )
