"""Tests for autonomous execution behavior in AgentLoop."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.loop import AgentLoop
from src.config.schema import Config
from src.providers.base import LLMResponse, ToolCallRequest

pytestmark = pytest.mark.skip(
    reason="Autonomous execution contract is not implemented in the current AgentLoop; keep this file skipped until the feature is reintroduced."
)


def _make_loop(tmp_path: Path) -> AgentLoop:
    bus = MagicMock()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    return AgentLoop(bus=bus, provider=provider, config=cfg)


class TestAutonomousDetection:
    def test_detects_direct_execute_intent(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)

        assert loop._wants_autonomous_execution("不用确认，直接执行并完成")
        assert loop._wants_autonomous_execution("Do not wait, directly execute this task.")
        assert not loop._wants_autonomous_execution("可以先给个方案吗？")

    def test_build_evidence_requirements(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)

        req = loop._build_evidence_requirements(
            "直接执行：扫描 src 并更新 README.md，然后运行 pytest"
        )

        assert req["file_write"] is True
        assert req["source_read"] is True
        assert req["command_exec"] is True


class TestAutonomousLoop:
    @pytest.mark.asyncio
    async def test_interim_reply_is_not_final_in_autonomous_mode(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop.tools.get_definitions = MagicMock(return_value=[])
        loop.provider.chat = AsyncMock(
            side_effect=[
                LLMResponse(content="收到，我会先扫描代码再开始改。", tool_calls=[]),
                LLMResponse(content="已完成重构，更新了 README.md 和 BOT.md。", tool_calls=[]),
            ]
        )

        final_content, _, _ = await loop._run_agent_loop(
            initial_messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "task"},
            ],
            autonomous=True,
        )

        assert "README.md" in final_content
        assert loop.provider.chat.await_count == 2

    @pytest.mark.asyncio
    async def test_interim_reply_can_end_when_not_autonomous(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop.tools.get_definitions = MagicMock(return_value=[])
        loop.provider.chat = AsyncMock(
            return_value=LLMResponse(content="收到，我会先扫描代码再开始改。", tool_calls=[])
        )

        final_content, _, _ = await loop._run_agent_loop(
            initial_messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "task"},
            ],
            autonomous=False,
        )

        assert final_content == "收到，我会先扫描代码再开始改。"
        assert loop.provider.chat.await_count == 1

    @pytest.mark.asyncio
    async def test_generic_completion_does_not_end_autonomous_mode(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop.tools.get_definitions = MagicMock(return_value=[])
        loop.provider.chat = AsyncMock(
            side_effect=[
                LLMResponse(content="已完成。已完成。", tool_calls=[]),
                LLMResponse(
                    content="已完成重构，更新了 README.md 和 BOT.md，并通过测试。", tool_calls=[]
                ),
            ]
        )

        final_content, _, _ = await loop._run_agent_loop(
            initial_messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "task"},
            ],
            autonomous=True,
        )

        assert "README.md" in final_content
        assert loop.provider.chat.await_count == 2

    @pytest.mark.asyncio
    async def test_empty_reply_does_not_end_autonomous_mode(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop.tools.get_definitions = MagicMock(return_value=[])
        loop.provider.chat = AsyncMock(
            side_effect=[
                LLMResponse(content="", tool_calls=[]),
                LLMResponse(content="最终结果：完成并写回文档。", tool_calls=[]),
            ]
        )

        final_content, _, _ = await loop._run_agent_loop(
            initial_messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "task"},
            ],
            autonomous=True,
        )

        assert "最终结果" in final_content
        assert loop.provider.chat.await_count == 2

    @pytest.mark.asyncio
    async def test_requires_write_tool_for_file_update_requests(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop.tools.get_definitions = MagicMock(return_value=[])
        loop.tools.execute = AsyncMock(return_value="Successfully edited README.md")
        loop.provider.chat = AsyncMock(
            side_effect=[
                LLMResponse(content="已完成并更新 README.md", tool_calls=[]),
                LLMResponse(
                    content="",
                    tool_calls=[
                        ToolCallRequest(
                            id="call1",
                            name="edit_file",
                            arguments={
                                "path": "/tmp/README.md",
                                "old_text": "a",
                                "new_text": "b",
                            },
                        )
                    ],
                ),
                LLMResponse(
                    content="已完成，更新了 README.md 与 BOT.md，并已保存。", tool_calls=[]
                ),
            ]
        )

        final_content, tools_used, _ = await loop._run_agent_loop(
            initial_messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "请更新 README.md 和 BOT.md"},
            ],
            autonomous=True,
            evidence_requirements={"file_write": True},
        )

        assert "README.md" in final_content
        assert "edit_file" in tools_used
        assert loop.tools.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_requires_exec_evidence_for_command_tasks(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop.tools.get_definitions = MagicMock(return_value=[])
        loop.tools.execute = AsyncMock(return_value="ok")
        loop.provider.chat = AsyncMock(
            side_effect=[
                LLMResponse(content="已完成，测试都通过。", tool_calls=[]),
                LLMResponse(
                    content="",
                    tool_calls=[
                        ToolCallRequest(
                            id="call2",
                            name="exec",
                            arguments={"command": "pytest -q", "working_dir": "/tmp"},
                        )
                    ],
                ),
                LLMResponse(content="已执行 pytest -q，测试通过。", tool_calls=[]),
            ]
        )

        final_content, tools_used, _ = await loop._run_agent_loop(
            initial_messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "请直接执行测试并汇报结果"},
            ],
            autonomous=True,
            evidence_requirements={"command_exec": True},
        )

        assert "pytest" in final_content
        assert "exec" in tools_used
        assert loop.tools.execute.await_count == 1
