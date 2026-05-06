"""Stock analysis tool — subprocess bridge to daily_stock_analysis."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.agent.tools.base import Tool

# Locate the vendored DSA directory
_DSA_DIR = Path(__file__).parent.parent.parent.parent / "vendor" / "daily_stock_analysis"

_MAX_OUTPUT = 30000


class StockAnalysisTool(Tool):
    """Run AI-powered stock analysis via daily_stock_analysis."""

    name = "stock_analysis"
    description = (
        "Analyze stocks with AI — technical indicators, fundamentals, news sentiment. "
        "Returns a formatted analysis report. Supports A-shares (e.g. 600519), "
        "HK stocks (e.g. HK00700), and US equities (e.g. AAPL)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "stocks": {
                "type": "string",
                "description": "Comma-separated stock codes to analyze (e.g. '600519,AAPL,HK00700'). "
                "If omitted, uses the configured stock list.",
            },
            "market_review": {
                "type": "boolean",
                "description": "Include market-wide review (SPX, DJI, etc.). Default false.",
            },
        },
        "required": [],
    }

    def __init__(
        self,
        *,
        stock_config: Any,
        provider_keys: dict[str, str],
        brave_api_key: str | None = None,
        channel_env: dict[str, str] | None = None,
    ):
        self._stock_config = stock_config
        self._provider_keys = provider_keys
        self._brave_api_key = brave_api_key
        self._channel_env = channel_env or {}

    async def execute(
        self,
        stocks: str | None = None,
        market_review: bool = False,
        **kwargs: Any,
    ) -> str:
        del kwargs
        if not _DSA_DIR.is_dir():
            return "Error: daily_stock_analysis not found. Run: git submodule update --init"

        stock_list = stocks or ",".join(self._stock_config.stock_list)
        if not stock_list:
            return "Error: no stocks specified. Pass stocks parameter or configure tools.stock.stockList."

        env = self._build_env(stock_list)
        cmd = [sys.executable, "main.py", "--stocks", stock_list, "--no-notify"]
        if market_review:
            cmd.append("--market-review")

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                cwd=str(_DSA_DIR),
                env=env,
                capture_output=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return "Error: stock analysis timed out after 5 minutes."

        output = result.stdout.decode(errors="replace")
        stderr = result.stderr.decode(errors="replace")

        if result.returncode != 0:
            err_preview = stderr[-2000:] if len(stderr) > 2000 else stderr
            return f"Error: analysis failed (exit {result.returncode}).\n{err_preview}"

        # Try to read the saved report file (more structured than stdout)
        report = self._read_latest_report()
        if report:
            return report[:_MAX_OUTPUT]

        # Fallback to stdout
        if output:
            return output[:_MAX_OUTPUT]
        return "Analysis completed but no output was generated."

    def _build_env(self, stock_list: str) -> dict[str, str]:
        """Build env vars for the DSA subprocess from TheOS config."""
        env = {**os.environ}
        env["STOCK_LIST"] = stock_list

        # LLM provider keys — DSA auto-detects which to use
        key_map = {
            "gemini": "GEMINI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "aihubmix": "AIHUBMIX_KEY",
        }
        for provider, env_var in key_map.items():
            key = self._provider_keys.get(provider, "")
            if key:
                env[env_var] = key

        # Model override
        if self._stock_config.model:
            env["LITELLM_MODEL"] = self._stock_config.model

        # Search keys
        if self._brave_api_key:
            env["BRAVE_API_KEY"] = self._brave_api_key
        if self._stock_config.tavily_api_key:
            env["TAVILY_API_KEY"] = self._stock_config.tavily_api_key

        # Data source
        if self._stock_config.tushare_token:
            env["TUSHARE_TOKEN"] = self._stock_config.tushare_token

        # Channel configs for direct notification (when called via cron)
        env.update(self._channel_env)

        return env

    def _read_latest_report(self) -> str | None:
        """Read the most recent report file from DSA's output directory."""
        reports_dir = _DSA_DIR / "data" / "reports"
        if not reports_dir.is_dir():
            return None
        files = sorted(reports_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not files:
            return None
        return files[0].read_text(encoding="utf-8")
