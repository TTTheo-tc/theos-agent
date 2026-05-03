from __future__ import annotations

import asyncio
from pathlib import Path

from starlette.testclient import TestClient

from src.store.dashboard_writer import DashboardWriter
from src.ui.events import UIEventBus
from src.ui.server import create_ui_app, start_ui_server


async def test_ui_server_start_and_stop(tmp_path: Path):
    """UI server starts, serves API, and shuts down cleanly."""
    db = DashboardWriter(tmp_path / "test.db")
    await db.connect()
    await db.upsert_session("cli:test", "cli")
    await db.close()

    event_bus = UIEventBus()
    app = create_ui_app(
        db_path=tmp_path / "test.db",
        static_dir=None,
        event_bus=event_bus,
    )

    runner = await start_ui_server(app, host="127.0.0.1", port=18999)
    await asyncio.sleep(0.3)
    await runner.cleanup()


async def test_event_callback_wiring(tmp_path: Path):
    """DashboardWriter callback should push events to UIEventBus subscribers."""
    db = DashboardWriter(tmp_path / "test.db")
    await db.connect()
    await db.upsert_session("cli:test", "cli")

    event_bus = UIEventBus()
    received = []

    async def collect():
        async for evt in event_bus.subscribe():
            received.append(evt)
            break

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.01)

    db.set_event_callback(event_bus.publish)
    await db.emit_event("cli:test", "test_event", payload={"x": 1})

    await asyncio.wait_for(task, timeout=2.0)
    await db.close()

    assert len(received) == 1
    assert received[0]["event_type"] == "test_event"
    assert isinstance(received[0]["id"], int)


def test_static_handler_blocks_sibling_prefix_traversal(tmp_path: Path):
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    secret_dir = tmp_path / "dist-private"
    secret_dir.mkdir()
    (secret_dir / "secret.txt").write_text("top-secret", encoding="utf-8")

    db_path = tmp_path / "test.db"
    db = DashboardWriter(db_path)
    asyncio.run(db.connect())
    asyncio.run(db.close())
    app = create_ui_app(db_path=db_path, static_dir=static_dir)

    with TestClient(app) as client:
        resp = client.get("/..%2Fdist-private%2Fsecret.txt")

    assert resp.status_code == 403
