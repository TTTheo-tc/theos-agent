"""Tests for pre-chat hook context persistence behavior."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.loop import AgentLoop
from src.bus.events import InboundMessage
from src.bus.queue import MessageBus
from src.config.schema import Config
from src.providers.base import LLMResponse
from src.session.manager import Session


def _make_loop(tmp_path: Path) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    cfg.agents.defaults.memory_window = 10
    return AgentLoop(bus=bus, provider=provider, config=cfg)


class TestPreChatHistory:
    def test_extract_instinct_routing(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        domains, primary = loop._extract_instinct_routing(
            "🧠 [Instinct] Domain routing activated.\n【脑干】core\n【finance/general】\nfoo\n【paper/reading】"
        )

        assert domains == ["finance/general", "paper/reading"]
        assert primary == "finance/general"

    def test_extract_instinct_skills(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        skills = loop._extract_instinct_skills(
            "推荐 Skills（按需 read_file 加载 SKILL.md）:\n  - summarize: fetch URLs\n  → read: /tmp/x\n  - reference: clone repo"
        )

        assert skills == ["summarize", "reference"]

    def test_extract_instinct_routing_prefers_structured_sidecar(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        hook_ctx = (
            "🧠 [Instinct] Domain routing activated.\n"
            "【finance/general】\n"
            '<!-- instinct-routing:{"domains":["coding/general","paper/reading"],'
            '"skills":["systematic-debugging"],"selected_primary":"coding/general"} -->'
        )

        domains, primary = loop._extract_instinct_routing(hook_ctx)

        assert domains == ["coding/general", "paper/reading"]
        assert primary == "coding/general"

    def test_extract_instinct_skills_prefers_structured_sidecar(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        hook_ctx = (
            "推荐 Skills（按需 read_file 加载 SKILL.md）:\n"
            "  - summarize: fetch URLs\n"
            '<!-- instinct-routing:{"domains":["coding/general"],'
            '"skills":["systematic-debugging","writing-plans"],'
            '"selected_primary":"coding/general"} -->'
        )

        skills = loop._extract_instinct_skills(hook_ctx)

        assert skills == ["systematic-debugging", "writing-plans"]

    @pytest.mark.asyncio
    async def test_prechat_injection_is_not_persisted_in_session_history(
        self, tmp_path: Path
    ) -> None:
        loop = _make_loop(tmp_path)
        loop.provider.chat = AsyncMock(return_value=LLMResponse(content="ok", tool_calls=[]))
        loop.tools.get_definitions = MagicMock(return_value=[])
        loop.hooks.run_pre_chat = AsyncMock(return_value="reflex context")

        msg = InboundMessage(channel="cli", sender_id="u1", chat_id="chat1", content="hello")
        await loop._process_message(msg)
        await loop._process_message(msg)

        session = loop.sessions.get_or_create("cli:chat1")
        persisted_user_contents = [
            m.get("content", "")
            for m in session.messages
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]

        assert all("[🧠 Instinct]" not in c for c in persisted_user_contents)
        assert all("reflex context" not in c for c in persisted_user_contents)

    @pytest.mark.asyncio
    async def test_user_turns_are_persisted_for_follow_up_context(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        # Use a callable side_effect: the first two main inference calls return
        # the scripted responses; any extra calls (e.g. background fact
        # extraction in loop_finalize) get a benign default.
        responses = iter(
            [
                LLMResponse(content="我不能直接写入系统日历。", tool_calls=[]),
                LLMResponse(content="是的，主要是缺少对应日历系统的接入能力。", tool_calls=[]),
            ]
        )

        async def _chat(**kwargs):
            return next(responses, LLMResponse(content="", tool_calls=[]))

        loop.provider.chat = AsyncMock(side_effect=_chat)
        loop.tools.get_definitions = MagicMock(return_value=[])
        loop.hooks.run_pre_chat = AsyncMock(return_value=None)

        first = InboundMessage(
            channel="telegram",
            sender_id="u1",
            chat_id="chat1",
            content="你能在我的日历里添加吗？",
        )
        second = InboundMessage(
            channel="telegram",
            sender_id="u1",
            chat_id="chat1",
            content="你是缺少什么权限呢？",
        )

        await loop._process_message(first)
        await loop._process_message(second)

        session = loop.sessions.get_or_create("telegram:chat1")
        roles = [m["role"] for m in session.messages[-4:]]
        contents = [m["content"] for m in session.messages[-4:]]

        assert roles == ["user", "assistant", "user", "assistant"]
        assert contents[0] == "你能在我的日历里添加吗？"
        assert contents[2] == "你是缺少什么权限呢？"

        history = session.get_history(max_messages=10)
        assert history[-2]["role"] == "user"
        assert history[-2]["content"] == "你是缺少什么权限呢？"

    def test_save_turn_truncates_oversized_tool_call_arguments(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        session = Session(key="cli:chat1")
        huge_args = "x" * 5000
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "feishu_create", "arguments": huge_args},
                    }
                ],
            }
        ]

        loop._save_turn(session, messages, skip=0, user_message="hello")

        persisted = session.messages[-1]["tool_calls"][0]["function"]["arguments"]
        assert json.loads(persisted) == {"_note": "tool arguments too large to include"}
        assert len(persisted) < len(huge_args)
