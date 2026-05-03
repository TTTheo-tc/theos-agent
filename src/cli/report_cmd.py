"""CLI sub-commands for ``theos report``."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import typer

from src.cli.display import console

report_app = typer.Typer(help="Generate activity reports from EventStore")


def _get_db_path() -> Path:
    from src.config.loader import load_config

    config = load_config()
    db_name = config.agents.orchestrator.event_store.db_name
    return config.workspace_path / db_name


async def _generate(period: str) -> str:
    from src.reporting.generator import ReportGenerator
    from src.reporting.metrics import MetricsCollector
    from src.store.database import Database

    db_path = _get_db_path()
    if not db_path.exists():
        return f"No database found at {db_path}. Enable event_store first."

    db = Database(db_path)
    await db.connect()
    try:
        collector = MetricsCollector(db)
        if period == "daily":
            metrics = await collector.daily()
            title = f"TheOS Daily Report — {datetime.now().strftime('%Y-%m-%d')}"
        else:
            metrics = await collector.weekly()
            title = f"TheOS Weekly Report — week ending {datetime.now().strftime('%Y-%m-%d')}"
        return ReportGenerator.render(metrics, title=title)
    finally:
        await db.close()


@report_app.command("daily")
def daily():
    """Generate a daily activity report."""
    report = asyncio.run(_generate("daily"))
    console.print(report)


@report_app.command("weekly")
def weekly():
    """Generate a weekly activity report."""
    report = asyncio.run(_generate("weekly"))
    console.print(report)
