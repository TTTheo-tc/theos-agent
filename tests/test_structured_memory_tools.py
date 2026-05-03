from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.loop import AgentLoop
from src.agent.tools.context import ToolContext
from src.agent.tools.structured_memory import (
    DomainRuleGetTool,
    ResearchNoteGetTool,
    StructuredMemorySearchTool,
    TaskMemoryGetTool,
)
from src.bus.events import InboundMessage
from src.bus.queue import MessageBus
from src.config.schema import Config
from src.memory.structured import RecordTaskResult, StructuredMemoryStore
from src.providers.base import LLMResponse


async def _seed_store(workspace: Path) -> tuple[StructuredMemoryStore, RecordTaskResult]:
    """Seed a StructuredMemoryStore with one task and return (store, result).

    The caller is responsible for closing the store.
    """
    store = StructuredMemoryStore(workspace)
    await store.ensure_kg()
    result = await store.record_task(
        session_key="cli:test",
        user_message="帮我总结这篇自动驾驶论文",
        response=(
            "建议先看问题定义，再看实验结论。"
            "This paper studies occupancy planning and compares multiple benchmarks."
        ),
        tools_used=["web_search", "web_fetch"],
        routed_skills=["summarize"],
        routing_domains=["paper/reading"],
        selected_primary="paper/reading",
        usage={"input_tokens": 1, "output_tokens": 2},
        duration_ms=10.0,
    )
    return store, result


async def test_structured_memory_search_tool_returns_results(tmp_path: Path) -> None:
    store, result = await _seed_store(tmp_path)
    try:
        tool = StructuredMemorySearchTool(workspace_resolver=lambda _sk: tmp_path)

        output = await tool.execute(
            query="occupancy benchmark", _context=ToolContext(session_key="cli:test")
        )

        assert "[research_note]" in output or "[task]" in output
        assert str(result.task_id) in output or str(result.research_id) in output
        assert not (tmp_path / "memory" / "instinct" / "recall_journal.jsonl").exists()
    finally:
        await store.close()


async def test_research_note_get_tool_returns_json(tmp_path: Path) -> None:
    store, result = await _seed_store(tmp_path)
    try:
        tool = ResearchNoteGetTool(workspace_resolver=lambda _sk: tmp_path)

        output = await tool.execute(
            note_id=str(result.research_id), _context=ToolContext(session_key="cli:test")
        )
        payload = json.loads(output)

        assert payload["id"] == result.research_id
        meta = (
            json.loads(payload["metadata"])
            if isinstance(payload["metadata"], str)
            else payload["metadata"]
        )
        assert meta["task_memory_id"] == result.task_id
    finally:
        await store.close()


async def test_task_memory_get_tool_returns_json(tmp_path: Path) -> None:
    store, result = await _seed_store(tmp_path)
    try:
        tool = TaskMemoryGetTool(workspace_resolver=lambda _sk: tmp_path)

        output = await tool.execute(
            task_id=str(result.task_id), _context=ToolContext(session_key="cli:test")
        )
        payload = json.loads(output)

        assert payload["id"] == result.task_id
        meta = (
            json.loads(payload["metadata"])
            if isinstance(payload["metadata"], str)
            else payload["metadata"]
        )
        assert meta["selected_primary"] == "paper/reading"
    finally:
        await store.close()


async def test_domain_rule_get_tool_returns_json(tmp_path: Path) -> None:
    store, result = await _seed_store(tmp_path)
    try:
        rule_id = str(result.rule_ids[0])
        tool = DomainRuleGetTool(workspace_resolver=lambda _sk: tmp_path)

        output = await tool.execute(
            rule_id=rule_id,
            _context=ToolContext(session_key="cli:test"),
        )
        payload = json.loads(output)

        assert payload["id"] == rule_id
        assert not (tmp_path / "memory" / "instinct" / "recall_journal.jsonl").exists()
        meta = (
            json.loads(payload["metadata"])
            if isinstance(payload["metadata"], str)
            else payload["metadata"]
        )
        assert meta["selected_primary"] == "paper/reading"
    finally:
        await store.close()


async def test_structured_memory_search_tool_filters_object_type(tmp_path: Path) -> None:
    store, _result = await _seed_store(tmp_path)
    try:
        tool = StructuredMemorySearchTool(workspace_resolver=lambda _sk: tmp_path)

        output = await tool.execute(
            query="回测",
            object_type="rule",
            _context=ToolContext(session_key="cli:test"),
        )

        assert output.startswith("No structured memory results found") or "[rule]" in output
    finally:
        await store.close()


async def test_structured_memory_search_tool_prefers_domain(tmp_path: Path) -> None:
    store = StructuredMemoryStore(tmp_path)
    try:
        await store.ensure_kg()
        finance = await store.record_task(
            session_key="cli:test",
            user_message="帮我分析量化策略回测",
            response="建议先做回测，再控制风险。",
            tools_used=["web_search"],
            routed_skills=["summarize"],
            routing_domains=["finance/general"],
            selected_primary="finance/general",
            usage={},
            duration_ms=10.0,
        )
    finally:
        await store.close()

    store2, paper = await _seed_store(tmp_path)
    try:
        tool = StructuredMemorySearchTool(workspace_resolver=lambda _sk: tmp_path)

        output = await tool.execute(
            query="occupancy planning benchmarks",
            domain="paper/reading",
            _context=ToolContext(session_key="cli:test"),
        )

        assert str(paper.research_id) in output or str(paper.task_id) in output
        assert str(finance.task_id) not in output.splitlines()[0]
    finally:
        await store2.close()


async def test_structured_memory_search_finds_by_indexed_terms(
    tmp_path: Path,
) -> None:
    """FTS5 with porter unicode61 indexes English tags/domains but treats CJK
    runs as single tokens.  Verify search works via indexed English terms."""
    store = StructuredMemoryStore(tmp_path)
    try:
        await store.ensure_kg()
        finance = await store.record_task(
            session_key="cli:test",
            user_message="帮我分析量化策略回测",
            response="建议先做回测，再控制风险。",
            tools_used=["web_search"],
            routed_skills=["summarize"],
            routing_domains=["finance/general"],
            selected_primary="finance/general",
            usage={},
            duration_ms=10.0,
        )

        # Search using indexed English terms (tags/domains)
        results = await store.search("web_search finance", object_type="all", max_results=3)

        assert results
        assert results[0]["id"] == finance.task_id or any(
            item["id"] == finance.task_id for item in results
        )
    finally:
        await store.close()


async def test_structured_memory_search_filters_generic_stopword_queries(
    tmp_path: Path,
) -> None:
    seed_store, _result = await _seed_store(tmp_path)
    await seed_store.close()

    store = StructuredMemoryStore(tmp_path)
    try:
        await store.ensure_kg()
        results = await store.search("这个 怎么 分析 一下", object_type="all", max_results=3)

        assert results == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_agent_loop_injects_structured_recall(tmp_path: Path) -> None:
    seed_store, seeded = await _seed_store(tmp_path)
    await seed_store.close()

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat = AsyncMock(return_value=LLMResponse(content="ok", tool_calls=[]))
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    cfg.knowledge_graph.enabled = True
    loop = AgentLoop(bus=bus, provider=provider, config=cfg)
    loop.tools.get_definitions = MagicMock(return_value=[])
    loop.hooks.run_pre_chat = AsyncMock(
        return_value="🧠 [Instinct] Domain routing activated.\n【paper/reading】\n"
    )

    msg = InboundMessage(
        channel="cli", sender_id="u1", chat_id="chat1", content="occupancy planning benchmarks"
    )
    try:
        await loop._process_message(msg)
    finally:
        await loop.close()

    # Inspect the FIRST chat call (main inference). Background fact extraction
    # in loop_finalize fires a second chat call after the response, so
    # await_args (last call) would point to the extraction prompt instead.
    first_call = provider.chat.await_args_list[0]
    sent_messages = first_call.kwargs["messages"]
    recall_blocks = [
        m["content"]
        for m in sent_messages
        if m.get("role") == "user"
        and isinstance(m.get("content"), str)
        and "[Structured Recall]" in m["content"]
    ]
    assert recall_blocks
    assert str(seeded.research_id) in recall_blocks[0] or str(seeded.task_id) in recall_blocks[0]
