"""Tests for hook runner behavior."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.loop import AgentLoop
from src.bus.events import InboundMessage
from src.bus.queue import MessageBus
from src.config.schema import Config
from src.hooks.runner import HookRunner
from src.providers.base import LLMResponse


def _make_test_config(workspace: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(workspace)
    return cfg


def _write_hook(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


class TestHookRunner:
    @pytest.mark.asyncio
    async def test_timeout_terminates_hook_subprocess(self, tmp_path: Path) -> None:
        hook = tmp_path / "pre-chat"
        marker = tmp_path / "marker.txt"
        _write_hook(
            hook,
            """#!/usr/bin/env bash
sleep 1
echo leaked > "$(dirname "$0")/marker.txt"
""",
        )

        with pytest.raises(asyncio.TimeoutError):
            await HookRunner._run(hook, stdin="hello", timeout=0.1)

        await asyncio.sleep(1.2)
        assert not marker.exists()

    @pytest.mark.asyncio
    async def test_nonzero_exit_is_treated_as_failure(self, tmp_path: Path) -> None:
        hook = tmp_path / "pre-chat"
        _write_hook(
            hook,
            """#!/usr/bin/env bash
echo partial-output
echo bad >&2
exit 2
""",
        )

        with pytest.raises(RuntimeError, match="status 2"):
            await HookRunner._run(hook, stdin="hello", timeout=3)

    @pytest.mark.asyncio
    async def test_pre_chat_drops_output_on_failed_hook(self, tmp_path: Path) -> None:
        hook = tmp_path / "pre-chat"
        _write_hook(
            hook,
            """#!/usr/bin/env bash
echo should-not-be-used
exit 3
""",
        )
        runner = HookRunner(tmp_path)

        out = await runner.run_pre_chat("hello")

        assert out is None

    @pytest.mark.asyncio
    async def test_post_chat_payload_includes_task_context(self, tmp_path: Path) -> None:
        hook = tmp_path / "post-chat"
        payload_path = tmp_path / "payload.json"
        _write_hook(
            hook,
            f"""#!/usr/bin/env bash
cat > "{payload_path}"
""",
        )
        runner = HookRunner(tmp_path)

        await runner.run_post_chat(
            "cli:test",
            response="ok",
            error=None,
            status="success",
            user_message="analyze this repo",
            tools_used=["read_file", "grep"],
            usage={"input_tokens": 12, "output_tokens": 34},
            duration_ms=56.7,
            routing_domains=["coding/general"],
            selected_primary="coding/general",
            workspace=tmp_path,
            reflector_active=True,
        )

        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        assert payload["session_key"] == "cli:test"
        assert payload["status"] == "success"
        assert payload["user_message"] == "analyze this repo"
        assert payload["tools_used"] == ["read_file", "grep"]
        assert payload["usage"] == {"input_tokens": 12, "output_tokens": 34}
        assert payload["routing_domains"] == ["coding/general"]
        assert payload["selected_primary"] == "coding/general"
        assert payload["artifacts"] == []
        assert payload["tests"] == []
        assert payload["reflector_active"] is True


@pytest.mark.asyncio
async def test_lifecycle_error_post_chat_passes_explicit_empty_fields(
    tmp_path: Path,
) -> None:
    """TurnLifecycle fires post-chat hook with expected fields when _process_message fails."""
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat = AsyncMock(return_value=LLMResponse(content="ok", tool_calls=[]))
    cfg = _make_test_config(tmp_path)
    loop = AgentLoop(bus=bus, provider=provider, config=cfg)
    loop._process_message = AsyncMock(side_effect=RuntimeError("boom"))
    loop.hooks.run_post_chat = AsyncMock(return_value=None)

    msg = InboundMessage(channel="cli", sender_id="u1", chat_id="chat1", content="trigger failure")
    await loop._lifecycle.handle_message(msg)
    await asyncio.sleep(0)

    loop.hooks.run_post_chat.assert_awaited_once()
    _, kwargs = loop.hooks.run_post_chat.await_args
    assert kwargs["error"] == "boom"
    assert kwargs["status"] == "failed"
    assert kwargs["user_message"] == "trigger failure"
    assert kwargs["tools_used"] == []
    assert kwargs["usage"] == {}
    assert kwargs["duration_ms"] is None
    assert kwargs["routing_domains"] == []
    assert kwargs["selected_primary"] is None
    assert kwargs["artifacts"] == []
    assert kwargs["tests"] == []


@pytest.mark.asyncio
async def test_process_message_blocks_inbound_secret_before_model_call(tmp_path: Path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat = AsyncMock(return_value=LLMResponse(content="should not run", tool_calls=[]))
    cfg = _make_test_config(tmp_path)
    loop = AgentLoop(bus=bus, provider=provider, config=cfg)
    loop.tools.get_definitions = MagicMock(return_value=[])

    msg = InboundMessage(
        channel="cli",
        sender_id="u1",
        chat_id="chat1",
        content="my key is sk-ant-secret123",
    )
    response = await loop._process_message(msg)

    assert response is not None
    assert "safety checks" in response.content.lower()
    provider.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_genver_process_message_uses_task_workspace_for_context_and_hooks(
    tmp_path: Path,
) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    cfg = _make_test_config(tmp_path)
    cfg.agents.mode = "genver"
    cfg.agents.genver.generator_model = "test-model"
    cfg.agents.genver.verifier_model = "test-model"
    cfg.agents.genver.explorer_model = "test-model"
    cfg.agents.genver.max_retries = 1
    cfg.agents.genver.generator_max_iterations = 1
    cfg.agents.genver.verifier_max_iterations = 1
    cfg.agents.genver.verifier_commands = []
    loop = AgentLoop(bus=bus, provider=provider, config=cfg)
    loop.tools.get_definitions = MagicMock(return_value=[])
    loop.hooks.run_pre_chat = AsyncMock(return_value=None)
    loop.hooks.run_post_chat = AsyncMock(return_value=None)

    captured: dict[str, object] = {}

    async def _fake_run_genver(initial_messages, **_kwargs):
        captured["messages"] = initial_messages
        return "done", [], initial_messages, {}

    loop._run_genver_loop = AsyncMock(side_effect=_fake_run_genver)

    msg = InboundMessage(
        channel="cli",
        sender_id="u1",
        chat_id="chat1",
        content="参考https://github.com/ZhuLinsen/daily_stock_analysis，帮我建立一个股票追踪分析系统",
    )
    await loop._process_message(msg)
    await asyncio.sleep(0)

    task_workspace = tmp_path / "daily_stock_analysis"
    system_prompt = captured["messages"][0]["content"]

    assert str(task_workspace) in system_prompt
    loop.hooks.run_pre_chat.assert_awaited_once()
    assert loop.hooks.run_pre_chat.await_args.kwargs["workspace"] == task_workspace
    loop.hooks.run_post_chat.assert_awaited_once()
    assert loop.hooks.run_post_chat.await_args.kwargs["workspace"] == task_workspace


@pytest.mark.asyncio
async def test_genver_process_message_bypasses_non_code_request(tmp_path: Path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    cfg = _make_test_config(tmp_path)
    cfg.agents.mode = "genver"
    cfg.agents.genver.generator_model = "test-model"
    cfg.agents.genver.verifier_model = "test-model"
    cfg.agents.genver.explorer_model = "test-model"
    cfg.agents.genver.max_retries = 1
    cfg.agents.genver.generator_max_iterations = 1
    cfg.agents.genver.verifier_max_iterations = 1
    cfg.agents.genver.verifier_commands = []
    loop = AgentLoop(bus=bus, provider=provider, config=cfg)
    loop.tools.get_definitions = MagicMock(return_value=[])
    loop.hooks.run_pre_chat = AsyncMock(return_value=None)
    loop.hooks.run_post_chat = AsyncMock(return_value=None)
    loop._memory.build_structured_recall = AsyncMock(return_value=None)
    loop._memory.maybe_compact = AsyncMock(side_effect=lambda messages, **kw: messages)
    loop._memory.persist_structured_memory = AsyncMock()
    loop._run_genver_loop = AsyncMock(return_value=("genver", [], [], {}))
    loop._run_agent_loop = AsyncMock(return_value=("analysis", [], [], {}))
    loop._save_turn = MagicMock()
    loop._finalizer.save_turn = MagicMock()
    loop.sessions.save = MagicMock()

    msg = InboundMessage(
        channel="telegram",
        sender_id="u1",
        chat_id="chat1",
        content="帮我分析下tsla的股票",
    )
    response = await loop._process_message(msg)
    await asyncio.sleep(0)

    assert response is not None
    assert response.content == "analysis"
    loop._run_agent_loop.assert_awaited_once()
    loop._run_genver_loop.assert_not_awaited()
    assert loop.hooks.run_pre_chat.await_args.kwargs["workspace"] == tmp_path
    assert loop.hooks.run_post_chat.await_args.kwargs["workspace"] == tmp_path
