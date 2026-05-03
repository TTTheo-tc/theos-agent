"""Mainline integration tests for AgentLoop._process_message behavior."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from src.agent.loop import AgentLoop
from src.agent.tools.base import Tool
from src.agent.tools.registry import ToolRegistry
from src.bus.events import InboundMessage
from src.bus.queue import MessageBus
from src.config.schema import Config
from src.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from src.safety.layer import SafetyCheckResult

# ---------------------------------------------------------------------------
# EchoTool — deterministic test tool
# ---------------------------------------------------------------------------


class EchoTool(Tool):
    """Echoes back the input text."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echoes the input text back."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to echo"},
            },
            "required": ["text"],
        }

    async def execute(self, **kwargs: Any) -> str:
        return kwargs.get("text", "")


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------


class MockProvider(LLMProvider):
    """LLM provider with a controllable chat mock."""

    def __init__(self) -> None:
        super().__init__()
        self._chat_mock = AsyncMock(
            return_value=LLMResponse(content="default", finish_reason="stop")
        )

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        return await self._chat_mock(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def get_default_model(self) -> str:
        return "mock-model"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_msg(content: str = "hello", channel: str = "test", chat_id: str = "c1") -> InboundMessage:
    return InboundMessage(channel=channel, sender_id="u1", chat_id=chat_id, content=content)


def _make_test_config(workspace: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(workspace)
    return cfg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def bus() -> MessageBus:
    return MessageBus()


@pytest.fixture()
def mock_provider() -> MockProvider:
    return MockProvider()


@pytest_asyncio.fixture()
async def agent(bus: MessageBus, mock_provider: MockProvider, workspace: Path) -> AgentLoop:
    config = _make_test_config(workspace)
    config.agents.defaults.max_tool_iterations = 5
    with patch.object(AgentLoop, "_register_default_tools", return_value=None):
        loop = AgentLoop(
            bus=bus,
            provider=mock_provider,
            config=config,
        )
    # Replace tools with a minimal registry containing only EchoTool
    loop.tools = ToolRegistry()
    loop.tools.register(EchoTool())
    yield loop
    await loop.close_mcp()
    await loop._memory.close_dbs()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_simple_text_reply(agent: AgentLoop, mock_provider: MockProvider):
    """Mock provider returns text -> outbound contains it."""
    mock_provider._chat_mock.return_value = LLMResponse(content="hi there!", finish_reason="stop")

    msg = _make_msg("hello")
    result = await agent._process_message(msg)

    assert result is not None
    assert "hi there!" in result.content


async def test_tool_call_round_trip(agent: AgentLoop, mock_provider: MockProvider):
    """Provider returns tool_call for echo -> tool executes -> provider called again -> final text."""
    mock_provider._chat_mock.side_effect = [
        LLMResponse(
            content="calling echo",
            tool_calls=[ToolCallRequest(id="tc1", name="echo", arguments={"text": "ping"})],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="echo said: ping", finish_reason="stop"),
    ]

    msg = _make_msg("echo ping")
    result = await agent._process_message(msg)

    assert result is not None
    assert "echo said: ping" in result.content
    assert mock_provider._chat_mock.call_count == 2


async def test_max_iterations_guard(agent: AgentLoop, mock_provider: MockProvider):
    """Provider always returns tool_calls -> loop terminates with the task-failure contract."""
    mock_provider._chat_mock.return_value = LLMResponse(
        content="still going",
        tool_calls=[ToolCallRequest(id="tc1", name="echo", arguments={"text": "loop"})],
        finish_reason="tool_calls",
    )

    msg = _make_msg("keep going")
    result = await agent._process_message(msg)

    assert result is not None
    assert "without completing the task" in result.content
    assert mock_provider._chat_mock.await_count == agent.max_iterations


async def test_slash_command_bypass(agent: AgentLoop, mock_provider: MockProvider):
    """/help -> help text returned, provider not called."""
    msg = _make_msg("/help")
    result = await agent._process_message(msg)

    assert result is not None
    assert "commands" in result.content.lower()
    mock_provider._chat_mock.assert_not_called()


async def test_session_persistence(agent: AgentLoop, mock_provider: MockProvider, workspace: Path):
    """After message, JSONL contains both the user message and assistant response."""
    mock_provider._chat_mock.return_value = LLMResponse(content="stored!", finish_reason="stop")

    msg = _make_msg("persist me")
    await agent._process_message(msg)

    sessions_dir = workspace / "sessions"
    assert sessions_dir.exists()
    jsonl_files = list(sessions_dir.glob("*.jsonl"))
    assert len(jsonl_files) > 0
    lines = [json.loads(line) for line in jsonl_files[0].read_text(encoding="utf-8").splitlines()]
    assert any(line.get("role") == "user" and line.get("content") == "persist me" for line in lines)
    assert any(
        line.get("role") == "assistant" and line.get("content") == "stored!" for line in lines
    )


async def test_outbound_safety_scan(agent: AgentLoop, mock_provider: MockProvider):
    """Outbound safety scan sanitizes provider output before delivery."""
    mock_provider._chat_mock.return_value = LLMResponse(
        content="unsafe secret", finish_reason="stop"
    )

    with patch.object(AgentLoop, "_get_safety") as mock_safety:
        mock_layer = mock_safety.return_value
        mock_layer.scan_inbound.return_value = SafetyCheckResult()
        mock_layer.scan_outbound.return_value = SafetyCheckResult(output_text="sanitized output")
        msg = _make_msg("normal input")
        result = await agent._process_message(msg)

    assert result is not None
    assert result.content == "sanitized output"
    mock_provider._chat_mock.assert_awaited_once()


async def test_error_fallback(agent: AgentLoop, mock_provider: MockProvider, bus: MessageBus):
    """Provider raises RuntimeError -> lifecycle catches it -> bus outbound has error message."""
    mock_provider._chat_mock.side_effect = RuntimeError("kaboom")

    msg = _make_msg("trigger error")
    await agent._lifecycle.handle_message(msg)

    # Drain outbound queue
    out = await asyncio.wait_for(bus.consume_outbound(), timeout=2.0)
    assert "sorry, i encountered an error" in out.content.lower()
