"""Tests for stock analysis subprocess bridge."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agent.tools.stock import StockAnalysisTool


def _stock_config(**overrides):
    defaults = {
        "stock_list": ["AAPL"],
        "model": "",
        "tavily_api_key": "",
        "tushare_token": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_stock_tool_reports_missing_vendor_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.agent.tools.stock._DSA_DIR", tmp_path / "missing")
    tool = StockAnalysisTool(
        stock_config=_stock_config(),
        provider_keys={},
        brave_api_key=None,
    )

    result = await tool.execute(stocks="AAPL", ignored="ok")

    assert result == "Error: daily_stock_analysis not found. Run: git submodule update --init"


def test_stock_tool_build_env_maps_config_values() -> None:
    tool = StockAnalysisTool(
        stock_config=_stock_config(
            model="openai/gpt-test",
            tavily_api_key="tvly",
            tushare_token="tushare",
        ),
        provider_keys={"openai": "sk-openai", "deepseek": "sk-deepseek"},
        brave_api_key="brave",
        channel_env={"THEOS_CHANNEL": "test"},
    )

    env = tool._build_env("AAPL,MSFT")

    assert env["STOCK_LIST"] == "AAPL,MSFT"
    assert env["OPENAI_API_KEY"] == "sk-openai"
    assert env["DEEPSEEK_API_KEY"] == "sk-deepseek"
    assert env["LITELLM_MODEL"] == "openai/gpt-test"
    assert env["BRAVE_API_KEY"] == "brave"
    assert env["TAVILY_API_KEY"] == "tvly"
    assert env["TUSHARE_TOKEN"] == "tushare"
    assert env["THEOS_CHANNEL"] == "test"
