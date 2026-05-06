"""Tests for Generator-Verifier mode (AgentFS, ExploreTool, GenVerLoop)."""

import asyncio
import json
from contextlib import suppress
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.agentfs import AgentFS
from src.agent.loop import AgentLoop
from src.agent.tools.context import ToolContext
from src.agent.tools.explore import ExploreTool
from src.agent.tools.registry import ToolRegistry
from src.bus.events import InboundMessage
from src.bus.queue import MessageBus
from src.config.schema import DEFAULT_GENVER_VERIFIER_COMMANDS, Config, GenVerConfig
from src.genver.handoff import HandoffPayload
from src.genver.loop import GenVerLoop
from src.genver.runner import prepare_genver_tools
from src.genver.verifier import Verifier
from src.genver.workspace import resolve_task_workspace
from src.providers.base import LLMResponse

# ---------------------------------------------------------------------------
# AgentFS
# ---------------------------------------------------------------------------


class TestAgentFS:
    def test_write_and_read(self, tmp_path: Path):
        fs = AgentFS(tmp_path, subdir=".test")
        data = {"summary": "hello", "findings": [1, 2, 3]}
        path = fs.write("report", data)
        assert path.exists()
        assert fs.read("report") == data

    def test_write_and_read_utf8(self, tmp_path: Path):
        fs = AgentFS(tmp_path, subdir=".test")
        data = {"summary": "你好", "findings": ["café"]}

        fs.write("unicode", data)

        assert fs.read("unicode") == data

    def test_nested_artifact_names_stay_inside_root(self, tmp_path: Path):
        fs = AgentFS(tmp_path, subdir=".test")
        path = fs.write("runtime/report", {"ok": True})

        assert path == fs.root / "runtime" / "report.json"
        assert fs.read("runtime/report") == {"ok": True}

    def test_artifact_names_keep_existing_suffix_text(self, tmp_path: Path):
        fs = AgentFS(tmp_path, subdir=".test")
        path = fs.write("runtime/report.v1", {"ok": True})

        assert path == fs.root / "runtime" / "report.v1.json"

    def test_read_missing(self, tmp_path: Path):
        fs = AgentFS(tmp_path, subdir=".test")
        assert fs.read("nonexistent") is None

    @pytest.mark.parametrize(
        "name",
        [
            "",
            ".",
            "../outside",
            "runtime/../../outside",
            "/tmp/x",
            r"..\outside",
            "C:/tmp/x",
            " runtime/report",
            "runtime/report ",
            "runtime/./report",
            "runtime//report",
            "runtime/",
        ],
    )
    def test_rejects_unsafe_artifact_names(self, tmp_path: Path, name: str):
        fs = AgentFS(tmp_path, subdir=".test")

        with pytest.raises(ValueError):
            fs.write(name, {"bad": True})
        with pytest.raises(ValueError):
            fs.read(name)

    def test_rejects_symlink_artifact(self, tmp_path: Path):
        fs = AgentFS(tmp_path, subdir=".test")
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        runtime = fs.root / "runtime"
        runtime.mkdir()
        (runtime / "report.json").symlink_to(outside)

        with pytest.raises(ValueError):
            fs.write("runtime/report", {"bad": True})
        with pytest.raises(ValueError):
            fs.read("runtime/report")
        assert json.loads(outside.read_text(encoding="utf-8")) == {}

    def test_rejects_symlink_root(self, tmp_path: Path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (tmp_path / ".test").symlink_to(outside, target_is_directory=True)

        with pytest.raises(ValueError):
            AgentFS(tmp_path, subdir=".test")

    def test_clear(self, tmp_path: Path):
        fs = AgentFS(tmp_path, subdir=".test")
        fs.write("a", {"x": 1})
        fs.write("b", {"y": 2})
        fs.clear()
        assert fs.read("a") is None
        assert fs.read("b") is None
        assert fs.root.exists()  # Directory still exists after clear


def test_prepare_genver_tools_includes_subagent_orchestration_for_fresh_registry(tmp_path: Path):
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    base_tools = ToolRegistry()
    cfg = GenVerConfig()
    subagent_manager = MagicMock()
    subagent_manager.executor = MagicMock()

    tools = prepare_genver_tools(
        config=cfg,
        base_tools=base_tools,
        workspace=tmp_path / "root",
        task_workspace=tmp_path / "task",
        provider=provider,
        default_model="test-model",
        restrict_to_workspace=False,
        subagent_manager=subagent_manager,
    )

    assert tools.has("agent")
    assert tools.has("subagent_wait")
    assert tools.has("subagent_kill")
    assert tools.has("subagents_list")


# ---------------------------------------------------------------------------
# ExploreTool
# ---------------------------------------------------------------------------


class MockToolRegistry:
    def __init__(self):
        self._tools = {}

    def get_definitions(self):
        return []

    def has(self, name):
        return name in self._tools

    def register(self, tool):
        self._tools[tool.name] = tool

    async def execute(self, name, params, context=None):
        del params, context
        return f"result of {name}"


@pytest.mark.asyncio
async def test_explore_tool_returns_pointer(tmp_path: Path):
    """ExploreTool should return a workspace pointer, not raw content."""
    provider = AsyncMock()
    provider.chat = AsyncMock(
        return_value=LLMResponse(
            content=json.dumps(
                {
                    "summary": "Found 3 API endpoints",
                    "findings": ["GET /api", "POST /api"],
                    "files": ["src/api.py"],
                }
            ),
            finish_reason="stop",
        )
    )
    agentfs = AgentFS(tmp_path, subdir=".genver")

    tool = ExploreTool(
        provider=provider,
        workspace=tmp_path,
        agentfs=agentfs,
        explorer_model="test-model",
    )

    result = await tool.execute(task="Find API endpoints")
    assert "Exploration complete" in result
    assert "explore_" in result
    # Verify data was written to agentfs
    # Extract the result name from the output
    for part in result.split():
        if part.startswith("explore_"):
            data = agentfs.read(part)
            assert data is not None
            assert data["summary"] == "Found 3 API endpoints"
            break


@pytest.mark.asyncio
async def test_explore_tool_handles_non_json(tmp_path: Path):
    """ExploreTool should gracefully handle non-JSON model output."""
    provider = AsyncMock()
    provider.chat = AsyncMock(
        return_value=LLMResponse(content="plain text response", finish_reason="stop")
    )
    agentfs = AgentFS(tmp_path, subdir=".genver")

    tool = ExploreTool(
        provider=provider,
        workspace=tmp_path,
        agentfs=agentfs,
        explorer_model="test-model",
    )

    result = await tool.execute(task="Explore something")
    assert "Exploration complete" in result


# ---------------------------------------------------------------------------
# GenVerLoop
# ---------------------------------------------------------------------------


def _make_genver_config(**overrides) -> GenVerConfig:
    defaults = {
        "generator_model": "test-gen",
        "verifier_model": "test-ver",
        "explorer_model": "test-exp",
        "max_retries": 3,
        "generator_max_iterations": 5,
        "verifier_max_iterations": 3,
        "verifier_commands": ["pytest -x"],
        "workspace_subdir": ".genver",
    }
    defaults.update(overrides)
    return GenVerConfig(**defaults)


def _basic_handoff(path: str = "src/app.py") -> HandoffPayload:
    return HandoffPayload(
        intent_summary="Implemented the requested change.",
        files_changed=[path],
        risk_assessment="low",
    )


@pytest.mark.asyncio
async def test_genver_passes_first_try(tmp_path: Path):
    """GenVer loop should return after first attempt if verifier passes."""
    provider = AsyncMock()
    provider.chat = AsyncMock(
        return_value=LLMResponse(content="Fixed the bug", finish_reason="stop")
    )

    config = _make_genver_config()
    tools = MockToolRegistry()

    loop = GenVerLoop(
        config=config,
        provider=provider,
        workspace=tmp_path,
        generator_tools=tools,
        default_model="test-model",
    )
    # Mock _run_verifier to return pass
    loop._verifier.run_verification = AsyncMock(
        return_value={"passed": True, "errors": [], "commands": [], "usage": {}}
    )
    loop._extract_handoff = lambda _messages: _basic_handoff()

    messages = [{"role": "user", "content": "Fix the bug"}]
    content, tools_used, msgs, usage = await loop.run(messages)

    assert "Implemented the requested change." in content
    assert "Verification passed on attempt 1." in content
    assert loop._verifier.run_verification.call_count == 1
    assert loop.last_review_report_name == "runtime/verify_report_1"
    assert (tmp_path / ".genver" / "runtime" / "verify_report_1.json").exists()


@pytest.mark.asyncio
async def test_genver_retries_on_failure(tmp_path: Path):
    """GenVer loop should retry when verifier reports failure."""
    provider = AsyncMock()
    provider.chat = AsyncMock(
        side_effect=[
            LLMResponse(content="First attempt fix", finish_reason="stop"),
            LLMResponse(content="Second attempt fix", finish_reason="stop"),
        ]
    )

    config = _make_genver_config(max_retries=3)
    tools = MockToolRegistry()

    loop = GenVerLoop(
        config=config,
        provider=provider,
        workspace=tmp_path,
        generator_tools=tools,
        default_model="test-model",
    )
    # First call fails, second passes
    loop._verifier.run_verification = AsyncMock(
        side_effect=[
            {
                "passed": False,
                "errors": ["AssertionError in test_foo"],
                "commands": [],
                "usage": {},
            },
            {"passed": True, "errors": [], "commands": [], "usage": {}},
        ]
    )
    loop._extract_handoff = lambda _messages: _basic_handoff()

    messages = [{"role": "user", "content": "Fix the bug"}]
    content, _, msgs, _ = await loop.run(messages)

    assert "Implemented the requested change." in content
    assert "Verification passed on attempt 2." in content
    assert loop._verifier.run_verification.call_count == 2
    # Verify error feedback was injected into messages
    feedback_msgs = [
        m
        for m in msgs
        if m.get("role") == "user" and "[Verifier Report — attempt" in m.get("content", "")
    ]
    assert len(feedback_msgs) == 1


@pytest.mark.asyncio
async def test_genver_success_summary_uses_handoff_and_report(tmp_path: Path):
    """If generator ends on handoff, GenVer should still return a useful summary."""
    provider = AsyncMock()
    provider.chat = AsyncMock(return_value=LLMResponse(content=None, finish_reason="stop"))

    config = _make_genver_config()
    tools = MockToolRegistry()
    loop = GenVerLoop(
        config=config,
        provider=provider,
        workspace=tmp_path,
        generator_tools=tools,
        default_model="test-model",
    )
    loop._extract_handoff = lambda _messages: HandoffPayload(
        intent_summary="Created smoke_genver.txt with one line of content.",
        files_changed=[str(tmp_path / "smoke_genver.txt")],
        risk_assessment="low",
    )
    loop._verifier.run_verification = AsyncMock(
        return_value={
            "passed": True,
            "errors": [],
            "commands": [],
            "checks_performed": ["Verified file contents"],
            "suggestions": ["Could shorten filename"],
            "review_evidence": [],
            "project_dir": str(tmp_path),
            "usage": {},
        }
    )

    content, _, _, _ = await loop.run([{"role": "user", "content": "Create the file"}])

    assert "Created smoke_genver.txt" in content
    assert "Verification passed on attempt 1." in content
    assert "Review artifact:" in content
    report = AgentFS(tmp_path, subdir=".genver").read("runtime/verify_report_1")
    assert report is not None
    assert report["passed"] is True
    assert (
        report["handoff"]["intent_summary"] == "Created smoke_genver.txt with one line of content."
    )


def test_build_review_evidence_uses_file_snapshot(tmp_path: Path):
    """Verifier prompt should include actual file evidence, not just handoff metadata."""
    target = tmp_path / "module.py"
    target.write_text("def answer():\n    return 42\n", encoding="utf-8")

    loop = GenVerLoop(
        config=_make_genver_config(),
        provider=AsyncMock(),
        workspace=tmp_path,
        generator_tools=MockToolRegistry(),
        default_model="test-model",
    )
    handoff = HandoffPayload(
        intent_summary="Added answer helper.",
        files_changed=[str(target)],
        risk_assessment="low",
    )

    section, evidence = loop._verifier._build_review_evidence(handoff, str(tmp_path))

    assert "Direct Review Evidence" in section
    assert "return 42" in section
    assert evidence[0]["source"] == "file_snapshot"


def test_handoff_to_verifier_prompt_excludes_generator_narrative():
    handoff = HandoffPayload(
        intent_summary="Generator claims it fixed the caching bug.",
        files_changed=["src/cache.py"],
        risk_assessment="medium",
        vulnerability_focus=["cache invalidation"],
        diff_summary="Refactored the cache layer and fixed all edge cases.",
        test_commands=["pytest tests/test_cache.py -q"],
    )

    prompt = handoff.to_verifier_prompt()

    assert "src/cache.py" in prompt
    assert "pytest tests/test_cache.py -q" in prompt
    assert "Generator claims it fixed the caching bug." not in prompt
    assert "Refactored the cache layer" not in prompt
    assert "cache invalidation" not in prompt


def test_handoff_summary_remains_available_for_orchestrator():
    handoff = HandoffPayload(
        intent_summary="Add a smoke file.",
        files_changed=["smoke.txt"],
        risk_assessment="low",
    )

    assert handoff.summary == "Add a smoke file."


def test_agent_loop_derives_request_specific_subdir_for_broad_genver_request(tmp_path: Path):
    target = resolve_task_workspace(
        tmp_path,
        "帮我搭建一个金融模型，类似彭博社那种的",
    )

    assert target != tmp_path / "finance"
    assert target.parent == tmp_path
    assert target.exists()


def test_agent_loop_uses_repo_name_as_genver_subdir(tmp_path: Path):
    target = resolve_task_workspace(
        tmp_path,
        "参考 https://github.com/ZhuLinsen/daily_stock_analysis，帮我建立一个股票追踪分析系统",
    )

    assert target == tmp_path / "daily_stock_analysis"
    assert target.exists()


def test_detect_project_dir_prefers_task_workspace_for_prefixed_handoff_paths(tmp_path: Path):
    loop = GenVerLoop(
        config=_make_genver_config(),
        provider=AsyncMock(),
        workspace=tmp_path / "daily_stock_analysis",
        generator_tools=MockToolRegistry(),
        default_model="test-model",
    )
    loop.workspace.mkdir(parents=True, exist_ok=True)
    (loop.workspace / "config.py").write_text("x = 1\n", encoding="utf-8")
    (loop.workspace / "server.py").write_text("x = 2\n", encoding="utf-8")
    handoff = HandoffPayload(
        intent_summary="done",
        files_changed=["daily_stock_analysis/config.py", "daily_stock_analysis/server.py"],
        risk_assessment="low",
    )

    project_dir = loop._verifier._detect_project_dir(handoff)

    assert project_dir == str(loop.workspace)


def test_provider_error_content_detects_codex_errors():
    assert Verifier.is_provider_error_content("Error calling Codex: boom")
    assert Verifier.is_provider_error_content("Error calling LLM: boom")
    assert not Verifier.is_provider_error_content("All good")


@pytest.mark.asyncio
async def test_genver_no_verifier_commands(tmp_path: Path):
    """GenVer with empty verifier_commands should skip verification."""
    provider = AsyncMock()
    provider.chat = AsyncMock(return_value=LLMResponse(content="Done", finish_reason="stop"))

    config = _make_genver_config(verifier_commands=[])
    tools = MockToolRegistry()

    loop = GenVerLoop(
        config=config,
        provider=provider,
        workspace=tmp_path,
        generator_tools=tools,
        default_model="test-model",
    )
    messages = [{"role": "user", "content": "Do something"}]
    content, _, _, _ = await loop.run(messages)
    assert content == "Done"
    assert provider.chat.call_count == 1


@pytest.mark.asyncio
async def test_genver_requires_submit_for_review_handoff_before_verifier(tmp_path: Path):
    provider = AsyncMock()
    provider.chat = AsyncMock(
        side_effect=[
            LLMResponse(content="implemented but no handoff", finish_reason="stop"),
            LLMResponse(content="implemented with handoff", finish_reason="stop"),
        ]
    )
    loop = GenVerLoop(
        config=_make_genver_config(),
        provider=provider,
        workspace=tmp_path,
        generator_tools=MockToolRegistry(),
        default_model="test-model",
    )
    extracted = [
        None,
        HandoffPayload(intent_summary="done", files_changed=["src/app.py"], risk_assessment="low"),
    ]
    loop._extract_handoff = lambda _messages: extracted.pop(0)
    loop._verifier.run_verification = AsyncMock(
        return_value={"passed": True, "errors": [], "commands": [], "usage": {}}
    )

    content, _, messages, _ = await loop.run([{"role": "user", "content": "Fix src/app.py bug"}])

    assert loop._verifier.run_verification.call_count == 1
    assert any(
        "submit_for_review" in m.get("content", "") for m in messages if m.get("role") == "user"
    )
    assert "Verification passed on attempt 2." in content


@pytest.mark.asyncio
async def test_agent_loop_passes_task_subdir_to_genver_loop(tmp_path: Path, monkeypatch):
    bus = MessageBus()
    provider = AsyncMock()
    provider.get_default_model.return_value = "test-model"
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    cfg.agents.mode = "genver"
    loop = AgentLoop(bus=bus, provider=provider, config=cfg)
    captured: dict[str, Path] = {}

    async def _fake_run(self, messages):
        captured["workspace"] = self.workspace
        return "done", [], messages, {}

    monkeypatch.setattr("src.genver.pipeline.GenVerPipeline.run", _fake_run)

    try:
        msg = InboundMessage(
            channel="cli",
            sender_id="u1",
            chat_id="chat1",
            content="帮我搭建一个金融模型，类似彭博社那种的",
        )
        tool_ctx = ToolContext(
            channel="cli",
            chat_id="chat1",
            session_key="cli:chat1",
            sender_id="u1",
            sender_is_owner=True,
        )
        ctx = loop._get_context_for_session("cli:chat1")
        await loop._run_inference_inner(
            msg,
            [{"role": "user", "content": msg.content}],
            None,
            tool_ctx,
            True,
            "cli:chat1",
            ctx,
        )

        assert captured["workspace"] != tmp_path / "finance"
        assert captured["workspace"].parent == tmp_path
    finally:
        await loop.close_mcp()
        await loop._memory.close_dbs()


@pytest.mark.asyncio
async def test_genver_does_not_force_scope_clarification(tmp_path: Path):
    provider = AsyncMock()
    provider.chat = AsyncMock(return_value=LLMResponse(content="scoped fix", finish_reason="stop"))

    ask_user = AsyncMock(return_value="should not be called")
    loop = GenVerLoop(
        config=_make_genver_config(),
        provider=provider,
        workspace=tmp_path,
        generator_tools=MockToolRegistry(),
        default_model="test-model",
        ask_user=ask_user,
    )
    loop._verifier.run_verification = AsyncMock(
        return_value={"passed": True, "errors": [], "commands": [], "usage": {}}
    )
    loop._extract_handoff = lambda _messages: _basic_handoff("src/finance/main.py")

    content, _, messages, _ = await loop.run(
        [{"role": "user", "content": "帮我搭建一个金融模型，类似彭博社那种的"}]
    )

    assert ask_user.call_count == 0
    assert provider.chat.call_count == 1
    assert "Verification passed on attempt 1." in content
    assert not any("[Scope Clarification]" in m.get("content", "") for m in messages)


@pytest.mark.asyncio
async def test_genver_verifier_repair_surfaces_codex_provider_error(tmp_path: Path):
    provider = AsyncMock()
    provider.chat = AsyncMock(return_value=LLMResponse(content="attempt fix", finish_reason="stop"))

    loop = GenVerLoop(
        config=_make_genver_config(max_retries=2),
        provider=provider,
        workspace=tmp_path,
        generator_tools=MockToolRegistry(),
        default_model="test-model",
    )
    loop._extract_handoff = lambda _messages: HandoffPayload(
        intent_summary="done",
        files_changed=["src/app.py"],
        risk_assessment="low",
    )
    loop._verifier.run_verification = AsyncMock(
        side_effect=[
            {"passed": False, "errors": ["broken"], "commands": [], "usage": {}},
            {"passed": False, "errors": ["still broken"], "commands": [], "usage": {}},
        ]
    )
    loop._extract_handoff = lambda _messages: _basic_handoff()
    loop._verifier.run_repair = AsyncMock(
        return_value={
            "content": "Error calling Codex: timeout",
            "errors": ["Error calling Codex: timeout"],
            "tools_used": [],
            "usage": {},
            "provider_error": True,
        }
    )

    content, _, _, _ = await loop.run([{"role": "user", "content": "Fix"}])

    assert "Verifier repair failed" in content
    assert "Error calling Codex: timeout" in content


@pytest.mark.asyncio
async def test_genver_exhausts_retries(tmp_path: Path):
    """On the second failed review, verifier should repair directly and finish."""
    provider = AsyncMock()
    provider.chat = AsyncMock(return_value=LLMResponse(content="attempt fix", finish_reason="stop"))

    config = _make_genver_config(max_retries=2)
    tools = MockToolRegistry()

    loop = GenVerLoop(
        config=config,
        provider=provider,
        workspace=tmp_path,
        generator_tools=tools,
        default_model="test-model",
    )
    loop._verifier.run_verification = AsyncMock(
        return_value={"passed": False, "errors": ["still broken"], "commands": [], "usage": {}}
    )
    loop._extract_handoff = lambda _messages: _basic_handoff()
    loop._verifier.run_repair = AsyncMock(
        return_value={
            "content": "Verifier repaired the remaining issue.",
            "errors": [],
            "tools_used": ["edit_file"],
            "usage": {},
            "project_dir": str(tmp_path),
        }
    )

    messages = [{"role": "user", "content": "Fix"}]
    content, _, _, _ = await loop.run(messages)

    assert "Verifier repaired the remaining issue." in content
    assert "Verifier applied final fixes after review attempt 2." in content
    assert loop._verifier.run_verification.call_count == 2
    assert loop._verifier.run_repair.call_count == 1


@pytest.mark.asyncio
async def test_genver_asks_user_after_verifier_repair_before_final_round(tmp_path: Path):
    """After verifier repair on attempt 2, the user can request one final round."""
    provider = AsyncMock()
    provider.chat = AsyncMock(
        side_effect=[
            LLMResponse(content="attempt fix", finish_reason="stop"),
            LLMResponse(content="second attempt fix", finish_reason="stop"),
            LLMResponse(content="final optimization", finish_reason="stop"),
        ]
    )

    config = _make_genver_config(max_retries=3)
    tools = MockToolRegistry()

    ask_user = AsyncMock(return_value="Polish the naming and keep the same behavior")
    loop = GenVerLoop(
        config=config,
        provider=provider,
        workspace=tmp_path,
        generator_tools=tools,
        default_model="test-model",
        ask_user=ask_user,
    )
    loop._verifier.run_verification = AsyncMock(
        side_effect=[
            {"passed": False, "errors": ["still broken"], "commands": [], "usage": {}},
            {"passed": False, "errors": ["edge case still broken"], "commands": [], "usage": {}},
            {"passed": True, "errors": [], "commands": [], "usage": {}},
        ]
    )
    loop._extract_handoff = lambda _messages: _basic_handoff()
    loop._verifier.run_repair = AsyncMock(
        return_value={
            "content": "Verifier applied a direct fix for the edge case.",
            "errors": [],
            "tools_used": ["edit_file"],
            "usage": {},
            "project_dir": str(tmp_path),
        }
    )

    messages = [{"role": "user", "content": "Fix"}]
    content, _, msgs, _ = await loop.run(messages)

    assert ask_user.call_count == 1
    assert "Verification passed on attempt 3." in content
    assert loop._verifier.run_verification.call_count == 3
    assert loop._verifier.run_repair.call_count == 1
    assert any("[User Optimization Request]" in m.get("content", "") for m in msgs)


@pytest.mark.asyncio
async def test_genver_finishes_after_verifier_repair_when_user_is_done(tmp_path: Path):
    """If the user is done after verifier repair, GenVer should stop there."""
    provider = AsyncMock()
    provider.chat = AsyncMock(return_value=LLMResponse(content="attempt fix", finish_reason="stop"))

    config = _make_genver_config(max_retries=3)
    tools = MockToolRegistry()

    ask_user = AsyncMock(return_value="done")
    loop = GenVerLoop(
        config=config,
        provider=provider,
        workspace=tmp_path,
        generator_tools=tools,
        default_model="test-model",
        ask_user=ask_user,
    )
    loop._verifier.run_verification = AsyncMock(
        return_value={"passed": False, "errors": ["broken"], "commands": [], "usage": {}}
    )
    loop._extract_handoff = lambda _messages: _basic_handoff()
    loop._verifier.run_repair = AsyncMock(
        return_value={
            "content": "Verifier fixed the remaining blocker.",
            "errors": [],
            "tools_used": ["edit_file"],
            "usage": {},
            "project_dir": str(tmp_path),
        }
    )

    messages = [{"role": "user", "content": "Fix"}]
    content, _, _, _ = await loop.run(messages)

    assert "Verifier fixed the remaining blocker." in content
    assert ask_user.call_count == 1
    assert loop._verifier.run_verification.call_count == 2
    assert loop._verifier.run_repair.call_count == 1


@pytest.mark.asyncio
async def test_genver_ask_user_publishes_to_current_channel_and_session(tmp_path: Path):
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    loop = AgentLoop(bus=bus, provider=provider, config=cfg)

    try:
        task = asyncio.create_task(
            loop._genver.ask_user(
                "Need clarification",
                channel="telegram",
                chat_id="12345",
                session_key="telegram:12345",
            )
        )

        outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        assert outbound.channel == "telegram"
        assert outbound.chat_id == "12345"
        assert outbound.metadata["_genver_ask"] is True

        checkpoint = loop.turns.latest("telegram:12345")
        assert checkpoint is None

        pending = loop._genver.get_pending_question("telegram:12345")
        assert pending is not None
        pending.set_result("Use the API version")
        answer = await asyncio.wait_for(task, timeout=1.0)

        assert answer == "Use the API version"
        assert loop._genver.get_pending_question("telegram:12345") is None
    finally:
        await loop.close_mcp()
        await loop._memory.close_dbs()


@pytest.mark.asyncio
async def test_genver_ask_user_records_waiting_checkpoint_when_turn_id_present(tmp_path: Path):
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    loop = AgentLoop(bus=bus, provider=provider, config=cfg)

    try:
        task = asyncio.create_task(
            loop._genver.ask_user(
                "Need clarification",
                channel="telegram",
                chat_id="12345",
                session_key="telegram:12345",
                turn_id="turn-1",
            )
        )

        await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        checkpoint = loop.turns.latest("telegram:12345")
        assert checkpoint is not None
        assert checkpoint.status == "waiting_user"
        assert checkpoint.metadata["question"] == "Need clarification"

        pending = loop._genver.get_pending_question("telegram:12345")
        assert pending is not None
        pending.set_result("Use the API version")
        await asyncio.wait_for(task, timeout=1.0)
    finally:
        await loop.close_mcp()
        await loop._memory.close_dbs()


@pytest.mark.asyncio
async def test_agent_loop_run_routes_pending_genver_answers_by_session(tmp_path: Path):
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    loop = AgentLoop(bus=bus, provider=provider, config=cfg)
    loop._connect_mcp = AsyncMock()
    loop._dispatcher.dispatch = AsyncMock()

    pending: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    loop._genver._pending_questions["telegram:chat1"] = pending

    run_task = asyncio.create_task(loop.run())
    try:
        await bus.publish_inbound(
            InboundMessage(
                channel="telegram",
                sender_id="u1",
                chat_id="chat1",
                content="继续按这个方向做",
            )
        )

        answer = await asyncio.wait_for(pending, timeout=1.0)
        assert answer == "继续按这个方向做"
        loop._dispatcher.dispatch.assert_not_awaited()
    finally:
        run_task.cancel()
        with suppress(asyncio.CancelledError):
            await run_task
        await loop.close_mcp()
        await loop._memory.close_dbs()


# ---------------------------------------------------------------------------
# Pipeline mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_genver_pipeline_mode_skips_verification(tmp_path: Path):
    """pipeline_mode=True should run generator only and skip verification entirely."""
    provider = AsyncMock()
    provider.chat = AsyncMock(
        return_value=LLMResponse(content="Implemented the change", finish_reason="stop")
    )

    config = _make_genver_config()
    tools = MockToolRegistry()

    loop = GenVerLoop(
        config=config,
        provider=provider,
        workspace=tmp_path,
        generator_tools=tools,
        default_model="test-model",
        pipeline_mode=True,
    )
    loop._extract_handoff = lambda _messages: HandoffPayload(
        intent_summary="done",
        files_changed=["src/app.py"],
        risk_assessment="low",
    )

    messages = [{"role": "user", "content": "Implement feature X"}]
    content, tools_used, msgs, usage = await loop.run(messages)

    # Verifier never called — no verify_report file in runtime dir
    runtime_dir = tmp_path / ".genver" / "runtime"
    verify_files = list(runtime_dir.glob("verify_report_*"))
    assert verify_files == [], f"Expected no verify reports but found: {verify_files}"

    # Handoff was extracted
    assert loop.last_handoff is not None
    assert loop.last_handoff.intent_summary == "done"

    # Content returned from generator
    assert content == "Implemented the change"


@pytest.mark.asyncio
async def test_genver_pipeline_mode_returns_without_handoff(tmp_path: Path):
    """pipeline_mode=True with no handoff should still return content without error."""
    provider = AsyncMock()
    provider.chat = AsyncMock(
        return_value=LLMResponse(content="Simple text response", finish_reason="stop")
    )

    config = _make_genver_config()
    tools = MockToolRegistry()

    loop = GenVerLoop(
        config=config,
        provider=provider,
        workspace=tmp_path,
        generator_tools=tools,
        default_model="test-model",
        pipeline_mode=True,
    )

    messages = [{"role": "user", "content": "Do something simple"}]
    content, tools_used, msgs, usage = await loop.run(messages)

    # Content returned
    assert content == "Simple text response"

    # No handoff, but no error either
    assert loop.last_handoff is None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_genver_config_defaults():
    """GenVerConfig should have sensible defaults."""
    config = GenVerConfig()
    assert config.max_retries == 3
    assert config.generator_max_iterations == 60
    assert config.verifier_max_iterations == 30
    assert config.verifier_commands == DEFAULT_GENVER_VERIFIER_COMMANDS
    assert config.workspace_subdir == ".genver"
    assert config.generator_model == ""
    assert config.verifier_model == ""
    assert config.explorer_model == ""


def test_handoff_dev_log_default_empty():
    from src.genver.handoff import HandoffPayload

    h = HandoffPayload(intent_summary="test", files_changed=["a.py"], risk_assessment="low")
    assert h.dev_log == []


def test_parse_handoff_extracts_dev_log():
    from src.genver.handoff import parse_handoff

    h = parse_handoff(
        {
            "intent_summary": "test",
            "files_changed": ["a.py"],
            "risk_assessment": "low",
            "target_commit_hash": "abc123",
            "dev_log": ["ran pytest: 3 passed", "ran ruff: clean"],
        }
    )
    assert h.dev_log == ["ran pytest: 3 passed", "ran ruff: clean"]
    assert h.target_commit_hash == "abc123"


def test_parse_handoff_dev_log_absent():
    from src.genver.handoff import parse_handoff

    h = parse_handoff(
        {"intent_summary": "test", "files_changed": ["a.py"], "risk_assessment": "low"}
    )
    assert h.dev_log == []


def test_agents_config_mode_default():
    """AgentsConfig.mode should default to the slim single-agent runtime."""
    from src.config.schema import AgentsConfig

    config = AgentsConfig()
    assert config.mode == "single"
    assert isinstance(config.genver, GenVerConfig)
