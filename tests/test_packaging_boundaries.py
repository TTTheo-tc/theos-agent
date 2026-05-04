from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_core_wheel_excludes_full_runtime_assets() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    build = data["tool"]["hatch"]["build"]
    wheel = build["targets"]["wheel"]
    include = set(build.get("include", []))
    force_include = set(wheel.get("force-include", {}).keys())

    assert not any(item.startswith(("ui/", "bridge/", "instinct/")) for item in include)
    assert not force_include.intersection({"ui", "ui/dist", "bridge", "instinct"})


def test_learning_extra_is_explicit_marker() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "learning" in data["project"]["optional-dependencies"]


def test_sdist_excludes_generated_full_assets() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sdist = data["tool"]["hatch"]["build"]["targets"]["sdist"]

    assert "ui/dist/" in sdist["exclude"]
    assert "ui/node_modules/" in sdist["exclude"]
    assert "bridge/dist/" in sdist["exclude"]
    assert "bridge/node_modules/" in sdist["exclude"]


def test_dockerfile_keeps_core_gateway_and_full_targets_separate() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    base_block = dockerfile.split("FROM base AS core", 1)[0]

    assert "FROM base AS core" in dockerfile
    assert "FROM base AS gateway" in dockerfile
    assert "FROM base AS full" in dockerfile
    assert "uv sync --frozen --no-install-project --no-dev\n" in dockerfile
    assert "--extra gateway" not in base_block
    assert "--all-extras" not in base_block
    assert "nodejs" not in base_block
    assert "uv sync --frozen --no-dev --extra gateway" in dockerfile
    assert "uv sync --frozen --no-dev --all-extras" in dockerfile
