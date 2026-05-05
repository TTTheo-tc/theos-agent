"""Smoke test the default core runtime surface.

This is intentionally small: it verifies that core CLI imports, default config,
and default AgentLoop construction work without activating optional subsystems.
Run after a core install and wheel build:

    uv run python scripts/smoke_core_runtime.py --strict-installed
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import re
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]

OPTIONAL_DEP_BLOCKLIST = {
    "akshare",
    "baostock",
    "croniter",
    "dingtalk-stream",
    "duckduckgo-search",
    "efinance",
    "google-search-results",
    "keyring",
    "lark-oapi",
    "matrix-nio",
    "mcp",
    "newspaper3k",
    "numpy",
    "pandas",
    "pillow",
    "pypdf",
    "python-socketio",
    "python-telegram-bot",
    "qq-botpy",
    "readability-lxml",
    "slack-sdk",
    "sqlite-vec",
    "tavily-python",
    "tushare",
    "twscrape",
    "uvicorn",
    "yfinance",
}

OPTIONAL_IMPORT_BLOCKLIST = {
    "akshare",
    "baostock",
    "botpy",
    "croniter",
    "dingtalk_stream",
    "duckduckgo_search",
    "lark_oapi",
    "mcp",
    "nio",
    "numpy",
    "pandas",
    "PIL",
    "pypdf",
    "slack_sdk",
    "socketio",
    "sqlite_vec",
    "starlette",
    "telegram",
    "tushare",
    "twscrape",
    "uvicorn",
    "yfinance",
}

OPTIONAL_RUNTIME_MODULES = {
    "src.channels.manager",
    "src.cli.channels_cmd",
    "src.cli.cron_cmd",
    "src.cli.feishu_auth_cmd",
    "src.cli.gateway_cmd",
    "src.cli.report_cmd",
    "src.cli.ui_cmd",
    "src.dream.runner",
    "src.feishu.client",
    "src.genver.pipeline",
    "src.poller.service",
    "src.ui.server",
}


def _fail(message: str) -> None:
    raise SystemExit(f"core smoke failed: {message}")


def _dep_name(spec: str) -> str:
    return re.split(r"[<>=!~;\[]", spec, maxsplit=1)[0].strip().lower().replace("_", "-")


def check_core_dependencies() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = {_dep_name(spec) for spec in data["project"]["dependencies"]}
    unexpected = sorted(deps & OPTIONAL_DEP_BLOCKLIST)
    if unexpected:
        _fail(f"optional dependencies in core dependency list: {', '.join(unexpected)}")


def check_optional_packages_not_installed() -> None:
    module_names = {
        "akshare",
        "baostock",
        "botpy",
        "croniter",
        "dingtalk_stream",
        "duckduckgo_search",
        "lark_oapi",
        "mcp",
        "nio",
        "pandas",
        "pypdf",
        "slack_sdk",
        "sqlite_vec",
        "starlette",
        "telegram",
        "tushare",
        "twscrape",
        "uvicorn",
        "yfinance",
    }
    installed = sorted(name for name in module_names if importlib.util.find_spec(name))
    if installed:
        _fail(f"optional packages are installed in core environment: {', '.join(installed)}")


def check_cli_help() -> None:
    from src.cli.commands import app

    runner = CliRunner()
    for args in (
        ["--help"],
        ["agent", "--help"],
        ["gateway", "--help"],
        ["cron", "--help"],
        ["channels", "--help"],
        ["channels", "status", "--help"],
        ["channels", "login", "--help"],
        ["report", "--help"],
        ["feishu-auth", "--help"],
        ["ui", "--help"],
    ):
        result = runner.invoke(app, args)
        if result.exit_code != 0:
            _fail(f"`theos {' '.join(args)}` failed:\n{result.output}")


def check_default_config_and_loop() -> None:
    from src.agent.loop import AgentLoop
    from src.bus.queue import MessageBus
    from src.config.schema import Config
    from src.providers.base import LLMProvider, LLMResponse

    class SmokeProvider(LLMProvider):
        async def chat(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            model: str | None = None,
            max_tokens: int = 4096,
            temperature: float = 0.7,
        ) -> LLMResponse:
            return LLMResponse(content="ok")

        def get_default_model(self) -> str:
            return "smoke-model"

    with tempfile.TemporaryDirectory(prefix="theos-core-smoke-") as tmp:
        config = Config()
        config.agents.defaults.workspace = tmp
        loop = AgentLoop(bus=MessageBus(), provider=SmokeProvider(), config=config)
        try:
            diag = loop.get_diagnostics()
            if diag["mode"] != "single":
                _fail(f"default mode is {diag['mode']!r}, expected 'single'")
            if loop._subagents is not None or loop._mcp is not None or loop._genver_handler is not None:
                _fail("optional managers were initialized during default construction")
            if loop.hooks.hooks_dir is not None:
                _fail("hooks were initialized during default construction")
            if loop.learning_enabled or loop.team_enabled or loop.genver_enabled:
                _fail("optional feature gates are enabled by default")
            for tool_name in ("read_file", "list_dir", "glob", "grep", "memory_search", "tool_search"):
                if not loop.tools.has(tool_name):
                    _fail(f"default core tool missing: {tool_name}")
            for tool_name in ("agent", "browser", "stock_analysis", "structured_memory_search"):
                if loop.tools.has(tool_name):
                    _fail(f"optional tool registered by default: {tool_name}")
        finally:
            asyncio.run(loop.close())


def check_no_optional_imports() -> None:
    loaded_external = sorted(
        name for name in OPTIONAL_IMPORT_BLOCKLIST if name in sys.modules
    )
    loaded_internal = sorted(name for name in OPTIONAL_RUNTIME_MODULES if name in sys.modules)
    if loaded_external or loaded_internal:
        parts = []
        if loaded_external:
            parts.append("external=" + ", ".join(loaded_external))
        if loaded_internal:
            parts.append("internal=" + ", ".join(loaded_internal))
        _fail("optional modules imported in core path: " + "; ".join(parts))


def check_wheel_assets() -> None:
    wheels = sorted((ROOT / "dist").glob("theos_agent-*.whl"))
    if not wheels:
        print("wheel asset check skipped: dist/theos_agent-*.whl not found")
        return
    wheel = wheels[-1]
    forbidden_prefixes = (
        "bridge/",
        "instinct/",
        "ui/",
        "src/bridge/",
        "src/instinct/",
        "src/ui/ui_static/",
    )
    with zipfile.ZipFile(wheel) as archive:
        bad = sorted(
            name
            for name in archive.namelist()
            if name.startswith(forbidden_prefixes)
        )
    if bad:
        _fail(f"full runtime assets found in {wheel.name}: {bad[:10]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strict-installed",
        action="store_true",
        help="also assert optional packages are absent from the active environment",
    )
    args = parser.parse_args()

    check_core_dependencies()
    if args.strict_installed:
        check_optional_packages_not_installed()
    check_cli_help()
    check_default_config_and_loop()
    check_no_optional_imports()
    check_wheel_assets()
    print("core smoke OK")


if __name__ == "__main__":
    main()
