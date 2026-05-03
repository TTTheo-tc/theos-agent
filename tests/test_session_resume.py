"""Tests for session resume semantics and durable turn checkpoints."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.loop import AgentLoop
from src.bus.events import InboundMessage
from src.bus.queue import MessageBus
from src.config.schema import Config


def _make_provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat = AsyncMock(side_effect=RuntimeError("boom"))
    return provider


def _make_loop(tmp_path: Path) -> AgentLoop:
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    return AgentLoop(bus=MessageBus(), provider=_make_provider(), config=cfg)


@pytest.mark.asyncio
async def test_failed_turn_keeps_accepted_user_message_and_failed_checkpoint(tmp_path: Path):
    loop = _make_loop(tmp_path)
    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="persist me")

    with pytest.raises(RuntimeError, match="boom"):
        await loop._process_message(msg, turn_id="turn-1")

    session_path = tmp_path / "sessions" / "cli_direct.jsonl"
    rows = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    assert any(
        row.get("role") == "user"
        and row.get("content") == "persist me"
        and row.get("turn_id") == "turn-1"
        for row in rows
    )

    checkpoint = loop.turns.latest("cli:direct")
    assert checkpoint is not None
    assert checkpoint.status == "failed"
    assert checkpoint.metadata["error"] == "boom"

    await loop.close()


@pytest.mark.asyncio
async def test_resume_reports_latest_checkpoint(tmp_path: Path):
    loop = _make_loop(tmp_path)
    try:
        loop.turns.record("cli:direct", "turn-1", "interrupted", interrupted_from="inferring")
        loop.subagents.store.record(
            "cli:direct",
            "sub-1",
            "interrupted",
            label="explore repo",
            role="explorer",
        )
        session = loop.sessions.get_or_create("cli:direct")
        session.add_message("user", "hello")
        loop.sessions.save(session)

        msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/resume")
        response = await loop._handle_slash_commands(msg, session, session.key)

        assert response is not None
        assert "turn-1" in response.content
        assert "interrupted" in response.content
        assert "messages on record: 1" in response.content
        assert "- recoverable: yes" in response.content
        assert "explore repo" in response.content
        assert "Re-send the last request" in response.content
    finally:
        await loop.close()


@pytest.mark.asyncio
async def test_resume_with_active_background_and_no_turn_checkpoint(tmp_path: Path):
    loop = _make_loop(tmp_path)
    try:
        loop.subagents.store.record(
            "cli:direct",
            "sub-1",
            "running",
            label="scan logs",
            role="explorer",
        )
        session = loop.sessions.get_or_create("cli:direct")
        session.add_message("user", "hello")
        loop.sessions.save(session)

        msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/resume")
        response = await loop._handle_slash_commands(msg, session, session.key)

        assert response is not None
        assert "has no recoverable turn checkpoints" in response.content
        assert "- recoverable: yes" in response.content
        assert "scan logs" in response.content
        assert "Inspect the active background tasks" in response.content
    finally:
        await loop.close()
