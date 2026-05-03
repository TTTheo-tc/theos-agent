"""Tests for team/delegation orchestration (spawn subagents with roles)."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.agent.subagent import SubagentManager
from src.bus.queue import MessageBus
from src.config.schema import AgentRoleConfig, Config
from src.providers.base import LLMProvider, LLMResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeProvider(LLMProvider):
    """Minimal provider that returns a canned response (no tool calls)."""

    def __init__(self, reply: str = "done"):
        super().__init__(api_key="fake")
        self._reply = reply

    def get_default_model(self) -> str:
        return "fake/test-model"

    async def chat(self, **kwargs) -> LLMResponse:
        return LLMResponse(content=self._reply)


ROLES = {
    "explorer": AgentRoleConfig(
        description="Fast exploration agent",
        model="minimax/MiniMax-M2.5",
        prompt="You are an explorer.",
        max_iterations=10,
        tools=["read_file", "list_dir"],
    ),
    "executor": AgentRoleConfig(
        description="Implementation agent",
        model="anthropic/claude-opus-4-6",
        prompt="You are an executor.",
        max_iterations=20,
        tools=["read_file", "write_file", "edit_file", "list_dir", "exec"],
    ),
    "reviewer": AgentRoleConfig(
        description="Code review agent",
        model="openai-codex/gpt-5.4",
        prompt="You are a reviewer.",
        max_iterations=10,
        tools=["read_file", "list_dir"],
    ),
}


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
def manager(bus, tmp_path):
    return SubagentManager(
        provider=FakeProvider(reply="Explorer found 3 Python files."),
        workspace=tmp_path,
        bus=bus,
        roles=ROLES,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSubagentSpawn:
    """Verify subagent spawning with roles."""

    @pytest.mark.asyncio
    async def test_spawn_explorer_returns_started_message(self, manager):
        result = await manager.spawn(
            task="List all Python files in src/",
            role="explorer",
        )
        assert "started" in result.lower()

    @pytest.mark.asyncio
    async def test_spawn_unknown_role_rejected(self, manager):
        result = await manager.spawn(
            task="Do something",
            role="nonexistent",
        )
        assert "Unknown role" in result

    @pytest.mark.asyncio
    async def test_spawn_without_role_uses_default_model(self, manager):
        result = await manager.spawn(task="General task")
        assert "started" in result.lower()

    @pytest.mark.asyncio
    async def test_subagent_completes_and_announces(self, manager, bus):
        """Spawn explorer, wait for it to finish, verify bus got the announcement."""
        await manager.spawn(
            task="Find all test files",
            role="explorer",
            origin_channel="test",
            origin_chat_id="123",
            session_key="test:123",
        )

        # The subagent runs in background; wait for the result on the inbound queue
        msg = await asyncio.wait_for(bus.inbound.get(), timeout=5.0)

        assert msg.channel == "system"
        assert msg.sender_id == "subagent"
        assert msg.session_key_override == "test:123"
        assert "Explorer found 3 Python files" in msg.content

    @pytest.mark.asyncio
    async def test_role_config_selects_correct_model(self, bus, tmp_path):
        """Verify that the role's model is passed to provider.chat()."""
        provider = FakeProvider(reply="reviewed")
        provider.chat = AsyncMock(return_value=LLMResponse(content="reviewed"))

        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            roles=ROLES,
        )

        await mgr.spawn(task="Review code", role="reviewer")
        # Let the background task run
        await asyncio.sleep(0.2)

        provider.chat.assert_called_once()
        call_kwargs = provider.chat.call_args.kwargs
        assert call_kwargs["model"] == "openai-codex/gpt-5.4"

    @pytest.mark.asyncio
    async def test_role_limits_registered_tools(self, bus, tmp_path):
        """Explorer role should only get read_file and list_dir tools."""
        provider = FakeProvider(reply="done")
        # Patch chat to inspect the tools passed
        tool_names_seen = []
        original_chat = provider.chat

        async def spy_chat(**kwargs):
            tools = kwargs.get("tools", [])
            tool_names_seen.extend(t["function"]["name"] for t in tools)
            return await original_chat(**kwargs)

        provider.chat = spy_chat

        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            roles=ROLES,
        )

        await mgr.spawn(task="Explore codebase", role="explorer")
        await asyncio.sleep(0.2)

        assert "read_file" in tool_names_seen
        assert "list_dir" in tool_names_seen
        assert "write_file" not in tool_names_seen
        assert "exec" not in tool_names_seen


class TestFacadeDelegation:
    """Verify SubagentManager delegates to SubagentExecutor."""

    @pytest.mark.asyncio
    async def test_facade_exposes_executor(self, manager):
        assert hasattr(manager, "executor")

    @pytest.mark.asyncio
    async def test_facade_list_tasks(self, manager):
        await manager.spawn(task="t", role="explorer", session_key="sess1")
        tasks = manager.executor.list_tasks("sess1")
        assert len(tasks) >= 1

    @pytest.mark.asyncio
    async def test_facade_wait(self, manager):
        await manager.spawn(task="t", role="explorer", session_key="sess2")
        # Give the background task time to complete
        await asyncio.sleep(0.3)
        tasks = manager.executor.list_tasks("sess2")
        result = await manager.executor.wait(tasks[0].task_id)
        assert result is not None


class TestBuildModelChoices:
    """Verify the curated model list builder."""

    @pytest.fixture(autouse=True)
    def _isolate_model_choices(self, monkeypatch):
        from src.cli.init_providers import (
            PROVIDER_DISCOVERED_MODELS,
            PROVIDER_MODEL_SOURCE,
            PROVIDER_TOP_MODELS,
        )

        PROVIDER_TOP_MODELS.clear()
        PROVIDER_MODEL_SOURCE.clear()
        PROVIDER_DISCOVERED_MODELS.clear()
        monkeypatch.setattr("src.config.loader.load_config", lambda: Config())
        monkeypatch.setattr("src.cli.init_providers._api_keys_by_provider", lambda: {})
        monkeypatch.setattr("src.cli.init_providers._get_openai_codex_access_token", lambda: None)
        monkeypatch.setattr(
            "src.cli.init_providers.fetch_models_for_provider",
            lambda *args, **kwargs: pytest.fail("unexpected live model discovery"),
        )
        yield
        PROVIDER_TOP_MODELS.clear()
        PROVIDER_MODEL_SOURCE.clear()
        PROVIDER_DISCOVERED_MODELS.clear()

    def test_returns_models_for_configured_providers(self):
        from src.cli.init_providers import build_model_choices

        choices = build_model_choices(["anthropic", "deepseek"])
        model_ids = [mid for mid, _ in choices]

        assert "anthropic/claude-sonnet-4-6" in model_ids
        assert "deepseek/deepseek-chat" in model_ids
        # Should NOT include unconfigured providers
        assert all(not mid.startswith("minimax/") for mid in model_ids)

    def test_empty_providers_returns_empty(self):
        from src.cli.init_providers import build_model_choices

        assert build_model_choices([]) == []

    def test_oauth_provider_included(self):
        from src.cli.init_providers import build_model_choices

        choices = build_model_choices(["openai-codex"])
        model_ids = [mid for mid, _ in choices]
        assert "openai-codex/gpt-5.4" in model_ids
