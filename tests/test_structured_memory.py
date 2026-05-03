from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.loop import AgentLoop
from src.bus.events import InboundMessage
from src.bus.queue import MessageBus
from src.config.schema import Config
from src.memory.structured import RecordTaskResult, StructuredMemoryStore
from src.memory.structured_models import extract_rules
from src.providers.base import LLMResponse, ToolCallRequest


def _make_test_config(workspace: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(workspace)
    cfg.knowledge_graph.enabled = True
    return cfg


async def test_structured_memory_store_creates_task_rule_and_research_note(tmp_path: Path) -> None:
    store = StructuredMemoryStore(tmp_path)
    try:
        await store.ensure_kg()

        result = await store.record_task(
            session_key="cli:test",
            user_message="帮我总结这篇自动驾驶论文的核心方法",
            response=(
                "建议先看问题定义，再看实验结论。"
                "This paper studies occupancy planning and compares multiple benchmarks. "
                "参考 src/agent/loop.py 和 https://arxiv.org/abs/1234.5678"
            ),
            tools_used=["web_search", "web_fetch"],
            routed_skills=["summarize"],
            routing_domains=["paper/reading"],
            selected_primary="paper/reading",
            usage={"input_tokens": 10, "output_tokens": 20},
            duration_ms=42.0,
        )

        assert isinstance(result, RecordTaskResult)

        # Verify task node in KG
        task_node = await store.get_task_memory(result.task_id)
        assert task_node is not None
        meta = (
            json.loads(task_node["metadata"])
            if isinstance(task_node["metadata"], str)
            else task_node["metadata"]
        )
        # Domains are stored on the node column, not in metadata
        assert "paper/reading" in task_node["domains"]
        assert meta["routed_skills"] == ["summarize"]
        assert "https://arxiv.org/abs/1234.5678" in meta["source_refs"]

        # Verify rule node in KG
        assert result.rule_ids
        rule_node = await store.get_domain_rule(result.rule_ids[0])
        assert rule_node is not None
        rule_meta = (
            json.loads(rule_node["metadata"])
            if isinstance(rule_node["metadata"], str)
            else rule_node["metadata"]
        )
        assert rule_meta["occurrence_count"] == 1

        # Verify research note node in KG
        assert result.research_id is not None
        research_node = await store.get_research_note(result.research_id)
        assert research_node is not None
        research_meta = (
            json.loads(research_node["metadata"])
            if isinstance(research_node["metadata"], str)
            else research_node["metadata"]
        )
        assert research_meta["task_memory_id"] == result.task_id
        assert "paper" in research_node["tags"]

        # Structured backend returns history_entry (format: [timestamp] task-id | domain | title)
        # Title is first_sentence(response), not the user message.
        assert result.history_entry is not None
        assert result.task_id in result.history_entry
        assert "paper/reading" in result.history_entry
    finally:
        await store.close()


async def test_structured_memory_store_upserts_domain_rule(tmp_path: Path) -> None:
    store = StructuredMemoryStore(tmp_path)
    try:
        await store.ensure_kg()

        first = await store.record_task(
            session_key="cli:test",
            user_message="帮我分析量化策略",
            response="建议先做回测，再控制风险。",
            tools_used=["web_search"],
            routed_skills=["summarize"],
            routing_domains=["finance/general"],
            selected_primary="finance/general",
            usage={},
            duration_ms=10.0,
        )
        second = await store.record_task(
            session_key="cli:test",
            user_message="继续分析另一个量化策略",
            response="建议先做回测，再控制风险。",
            tools_used=["web_search"],
            routed_skills=["summarize"],
            routing_domains=["finance/general"],
            selected_primary="finance/general",
            usage={},
            duration_ms=11.0,
        )

        assert first.rule_ids == second.rule_ids
        rule_node = await store.get_domain_rule(first.rule_ids[0])
        assert rule_node is not None
        rule_meta = (
            json.loads(rule_node["metadata"])
            if isinstance(rule_node["metadata"], str)
            else rule_node["metadata"]
        )
        assert rule_meta["occurrence_count"] == 2
        assert len(rule_meta["source_task_ids"]) == 2
    finally:
        await store.close()


async def test_structured_memory_store_marks_older_related_success_as_superseded(
    tmp_path: Path,
) -> None:
    store = StructuredMemoryStore(tmp_path)
    try:
        await store.ensure_kg()

        first = await store.record_task(
            session_key="cli:test",
            user_message="帮我搭建一个金融模型，类似彭博社那种的",
            response="已创建 src/finance/core.py 和 tests/test_finance.py。",
            tools_used=["write_file"],
            routed_skills=[],
            routing_domains=["finance/general"],
            selected_primary="finance/general",
            artifacts=["src/finance/core.py", "tests/test_finance.py"],
            tests=["tests/test_finance.py"],
            usage={},
            duration_ms=10.0,
        )
        second = await store.record_task(
            session_key="cli:test",
            user_message="继续完善这个金融模型，补上终端页面和回测分析",
            response="已更新 src/finance/core.py、static/index.html 和 tests/test_finance.py。",
            tools_used=["write_file"],
            routed_skills=[],
            routing_domains=["finance/general"],
            selected_primary="finance/general",
            artifacts=["src/finance/core.py", "static/index.html", "tests/test_finance.py"],
            tests=["tests/test_finance.py"],
            usage={},
            duration_ms=11.0,
        )

        first_node = await store.get_task_memory(first.task_id)
        second_node = await store.get_task_memory(second.task_id)
        assert first_node is not None
        assert second_node is not None
        first_meta = (
            json.loads(first_node["metadata"])
            if isinstance(first_node["metadata"], str)
            else first_node["metadata"]
        )
        second_meta = (
            json.loads(second_node["metadata"])
            if isinstance(second_node["metadata"], str)
            else second_node["metadata"]
        )

        assert first_node["superseded_by"] == second.task_id
        assert first_meta["is_latest_success"] is False
        assert second_meta["is_latest_success"] is True
        assert second_meta["artifacts"] == [
            "src/finance/core.py",
            "static/index.html",
            "tests/test_finance.py",
        ]
        assert second_meta["tests"] == ["tests/test_finance.py"]
        assert second_node["superseded_by"] is None
        # Structured backend returns history_entry (format: [timestamp] task-id | domain | title)
        assert second.history_entry is not None
        assert second.task_id in second.history_entry
        assert "finance/general" in second.history_entry
    finally:
        await store.close()


async def test_structured_memory_store_failed_task_is_not_latest_success(tmp_path: Path) -> None:
    store = StructuredMemoryStore(tmp_path)
    try:
        await store.ensure_kg()

        result = await store.record_task(
            session_key="cli:test",
            user_message="帮我改一下这个任务",
            response="I reached the maximum number of tool call iterations (60) without completing the task.",
            tools_used=["edit_file"],
            routed_skills=[],
            routing_domains=["coding/general"],
            selected_primary="coding/general",
            usage={},
            duration_ms=12.0,
            status="failed",
        )

        task_node = await store.get_task_memory(result.task_id)
        assert task_node is not None
        meta = (
            json.loads(task_node["metadata"])
            if isinstance(task_node["metadata"], str)
            else task_node["metadata"]
        )
        assert meta["status"] == "failed"
        assert meta["is_latest_success"] is False
        assert result.rule_ids == []
        # Failed tasks should not produce a history entry
        assert result.history_entry is None
    finally:
        await store.close()


async def test_structured_memory_store_persists_successful_remember_requests(
    tmp_path: Path,
) -> None:
    store = StructuredMemoryStore(tmp_path)
    try:
        await store.ensure_kg()

        result = await store.record_task(
            session_key="cli:test",
            user_message="以后我提知识库首页需求时，你要记住保持精炼，不要写太多",
            response="搞定。之后我会随时更新首页，保持精炼。",
            tools_used=["feishu_edit"],
            routed_skills=[],
            routing_domains=["general"],
            selected_primary=None,
            usage={},
            duration_ms=9.0,
            status="success",
        )

        # Structured backend no longer writes MEMORY.md directly — it returns
        # remember_directive for the caller to handle.
        assert result.remember_directive is not None
        assert "保持精炼，不要写太多" in result.remember_directive
        # MEMORY.md should NOT exist (no side effect from structured backend)
        memory_file = tmp_path / "memory" / "MEMORY.md"
        assert not memory_file.exists()
    finally:
        await store.close()


async def test_structured_memory_store_ignores_noise_rules_from_failed_responses(
    tmp_path: Path,
) -> None:
    store = StructuredMemoryStore(tmp_path)
    try:
        await store.ensure_kg()

        result = await store.record_task(
            session_key="cli:test",
            user_message="你给我记住了",
            response=(
                "Error calling LLM: BadRequestError: Failed to parse tool call arguments. "
                "Received Messages=[{'role': 'user', 'content': '你给我记住了'}, "
                "{'role': 'assistant', 'content': '明白，记住了。'}]"
            ),
            tools_used=[],
            routed_skills=[],
            routing_domains=["general"],
            selected_primary=None,
            usage={},
            duration_ms=3.0,
            status="failed",
        )

        assert result.rule_ids == []
    finally:
        await store.close()


def test_extract_rules_ignores_context_specific_content() -> None:
    rules = extract_rules(
        "注意到这个 wiki 下面还有 4 个测试文档，你之前让我删的应该就是这些。"
        "注意：以后验证结构时，逐层 feishu_list 展开确认。"
    )

    assert rules == ["注意：以后验证结构时，逐层 feishu_list 展开确认。"]


@pytest.mark.asyncio
async def test_agent_loop_persists_structured_memory(tmp_path: Path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat = AsyncMock(
        return_value=LLMResponse(content="建议先读摘要，再看实验。", tool_calls=[])
    )
    cfg = _make_test_config(tmp_path)
    loop = AgentLoop(bus=bus, provider=provider, config=cfg)
    loop.tools.get_definitions = MagicMock(return_value=[])
    loop.hooks.run_pre_chat = AsyncMock(
        return_value=(
            "🧠 [Instinct] Domain routing activated.\n"
            "【paper/reading】\n"
            "推荐 Skills（按需 read_file 加载 SKILL.md）:\n"
            "  - summarize: fetch and summarize paper URLs\n"
            "  → read: /tmp/skills/summarize/SKILL.md\n"
        )
    )

    msg = InboundMessage(
        channel="cli",
        sender_id="u1",
        chat_id="chat1",
        content="帮我总结这篇论文",
    )
    try:
        await loop._process_message(msg)
    finally:
        await loop.close()

    # Verify task node was persisted in KG
    store = StructuredMemoryStore(tmp_path)
    try:
        await store.ensure_kg()
        assert store._kg is not None
        tasks = await store._kg.list_nodes("task", limit=5)
        assert tasks
        task_node = tasks[0]
        meta = (
            json.loads(task_node["metadata"])
            if isinstance(task_node["metadata"], str)
            else task_node["metadata"]
        )
        assert meta["selected_primary"] == "paper/reading"
        assert meta["routed_skills"] == ["summarize"]
        assert meta["user_message"] == "帮我总结这篇论文"
    finally:
        await store.close()

    history_text = (tmp_path / "memory" / "HISTORY.md").read_text(encoding="utf-8")
    assert "paper/reading" in history_text


@pytest.mark.asyncio
async def test_agent_loop_persists_failed_task_status_for_max_iterations(tmp_path: Path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat = AsyncMock(
        return_value=LLMResponse(
            content="working",
            tool_calls=[ToolCallRequest(id="tc1", name="missing_tool", arguments={})],
        )
    )
    cfg = _make_test_config(tmp_path)
    cfg.agents.defaults.max_tool_iterations = 1
    loop = AgentLoop(bus=bus, provider=provider, config=cfg)
    loop.tools.get_definitions = MagicMock(return_value=[])
    loop.hooks.run_pre_chat = AsyncMock(return_value=None)

    msg = InboundMessage(
        channel="cli",
        sender_id="u1",
        chat_id="chat1",
        content="帮我继续修改",
    )
    try:
        response = await loop._process_message(msg)
    finally:
        await loop.close()

    assert response is not None
    assert "without completing the task" in response.content

    # Verify task node was persisted in KG
    store = StructuredMemoryStore(tmp_path)
    try:
        await store.ensure_kg()
        assert store._kg is not None
        tasks = await store._kg.list_nodes("task", limit=5, exclude_superseded=False)
        assert tasks
        task_node = tasks[0]
        meta = (
            json.loads(task_node["metadata"])
            if isinstance(task_node["metadata"], str)
            else task_node["metadata"]
        )
        assert meta["status"] == "failed"
        assert meta["is_latest_success"] is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_agent_loop_rewrites_invalid_request_error_with_new_session_hint(
    tmp_path: Path,
) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat = AsyncMock(
        return_value=LLMResponse(
            content=(
                "Error calling LLM: BadRequestError: AnthropicException - "
                '{"type":"error","error":{"type":"invalid_request_error","message":"Error"}}'
            ),
            tool_calls=[],
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
    )
    cfg = _make_test_config(tmp_path)
    loop = AgentLoop(bus=bus, provider=provider, config=cfg)
    loop.tools.get_definitions = MagicMock(return_value=[])
    loop.hooks.run_pre_chat = AsyncMock(return_value=None)

    msg = InboundMessage(
        channel="cli",
        sender_id="u1",
        chat_id="chat1",
        content="帮我继续修改",
    )
    try:
        response = await loop._process_message(msg)
    finally:
        await loop.close()

    assert response is not None
    assert "`/new`" in response.content
    assert "invalid_request_error" not in response.content

    session = loop.sessions.get_or_create("cli:chat1")
    assert "`/new`" in session.messages[-1]["content"]
    assert "invalid_request_error" not in session.messages[-1]["content"]

    # Verify task node was persisted in KG
    store = StructuredMemoryStore(tmp_path)
    try:
        await store.ensure_kg()
        assert store._kg is not None
        tasks = await store._kg.list_nodes("task", limit=5, exclude_superseded=False)
        assert tasks
        task_node = tasks[0]
        meta = (
            json.loads(task_node["metadata"])
            if isinstance(task_node["metadata"], str)
            else task_node["metadata"]
        )
        assert meta["status"] == "failed"
        assert meta["is_latest_success"] is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_agent_loop_rewrites_invalid_tool_schema_error_without_new_session_hint(
    tmp_path: Path,
) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat = AsyncMock(
        return_value=LLMResponse(
            content=(
                "Error calling LLM: HTTP 400: "
                '{"error":{"message":"Invalid schema for function \'feishu_sheet\': '
                'array schema missing items.","type":"invalid_request_error",'
                '"param":"tools[31].parameters","code":"invalid_function_parameters"}}'
            ),
            tool_calls=[],
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
    )
    cfg = _make_test_config(tmp_path)
    loop = AgentLoop(bus=bus, provider=provider, config=cfg)
    loop.tools.get_definitions = MagicMock(return_value=[])
    loop.hooks.run_pre_chat = AsyncMock(return_value=None)

    msg = InboundMessage(
        channel="cli",
        sender_id="u1",
        chat_id="chat1",
        content="帮我看看飞书表格",
    )
    try:
        response = await loop._process_message(msg)
    finally:
        await loop.close()

    assert response is not None
    assert "feishu_sheet" in response.content
    assert "`/new`" not in response.content
    assert "schema" in response.content.lower()

    session = loop.sessions.get_or_create("cli:chat1")
    assert "feishu_sheet" in session.messages[-1]["content"]
    assert "`/new`" not in session.messages[-1]["content"]


@pytest.mark.asyncio
async def test_agent_loop_retries_invalid_request_with_clean_context(tmp_path: Path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    inference_messages: list[list[dict]] = []

    async def _chat(*, messages, **kwargs):
        # Skip the background fact-extraction call (it has a fixed system
        # prompt different from the main inference path).  We only care about
        # the main inference calls here.
        sys_msg = next((m for m in messages if m.get("role") == "system"), None)
        is_extraction = isinstance(sys_msg, dict) and "extract durable facts" in (
            sys_msg.get("content") or ""
        )
        if is_extraction:
            return LLMResponse(content="", tool_calls=[])

        inference_messages.append([{**m} for m in messages])
        if len(inference_messages) == 1:
            return LLMResponse(
                content=(
                    "Error calling LLM: BadRequestError: AnthropicException - "
                    '{"type":"error","error":{"type":"invalid_request_error","message":"Error"}}'
                ),
                tool_calls=[],
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
        return LLMResponse(
            content="好的，我会去删除这些测试文档。",
            tool_calls=[],
            usage={"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
        )

    provider.chat = AsyncMock(side_effect=_chat)
    cfg = _make_test_config(tmp_path)
    loop = AgentLoop(bus=bus, provider=provider, config=cfg)
    loop.tools.get_definitions = MagicMock(return_value=[])
    loop.hooks.run_pre_chat = AsyncMock(return_value="reflex context")

    msg = InboundMessage(
        channel="cli",
        sender_id="u1",
        chat_id="chat1",
        content="帮我继续修改",
    )
    try:
        response = await loop._process_message(msg)
    finally:
        await loop.close()

    assert response is not None
    assert response.content == "好的，我会去删除这些测试文档。"
    assert len(inference_messages) == 2
    assert len(inference_messages[0]) == 2
    assert len(inference_messages[1]) == 2
    assert "reflex context" in inference_messages[0][-1]["content"]
    assert "reflex context" not in inference_messages[1][-1]["content"]

    session = loop.sessions.get_or_create("cli:chat1")
    assert session.messages[-1]["content"] == "好的，我会去删除这些测试文档。"
