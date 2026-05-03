"""Tests for RuntimeRoleConfig adapter."""

from src.agent.delegation.runtime import RuntimeRoleConfig
from src.config.schema import AgentRoleConfig


class TestRuntimeRoleConfig:
    def test_from_agent_role_basic(self):
        role_cfg = AgentRoleConfig(
            description="Explorer",
            model="minimax/MiniMax-M2.5",
            prompt="You are an explorer.",
            max_iterations=10,
            tools=["read_file", "list_dir"],
        )
        rt = RuntimeRoleConfig.from_agent_role("explorer", role_cfg, "default/model")
        assert rt.name == "explorer"
        assert rt.description == "Explorer"
        assert rt.system_prompt == "You are an explorer."
        assert rt.model == "minimax/MiniMax-M2.5"
        assert rt.max_iterations == 10
        assert rt.allowed_tools == {"read_file", "list_dir"}
        assert rt.allow_nested_spawn is False
        assert rt.timeout_seconds is None

    def test_empty_model_uses_default(self):
        role_cfg = AgentRoleConfig(description="test", model="", prompt="p")
        rt = RuntimeRoleConfig.from_agent_role("test", role_cfg, "fallback/model")
        assert rt.model == "fallback/model"

    def test_empty_tools_means_none(self):
        role_cfg = AgentRoleConfig(description="test", prompt="p")
        rt = RuntimeRoleConfig.from_agent_role("test", role_cfg, "m")
        assert rt.allowed_tools is None

    def test_spawn_in_tools_enables_nested(self):
        role_cfg = AgentRoleConfig(
            description="planner",
            prompt="plan",
            tools=["read_file", "agent", "subagent_wait", "subagent_kill", "subagents_list"],
        )
        rt = RuntimeRoleConfig.from_agent_role("planner", role_cfg, "m")
        assert rt.allow_nested_spawn is True
        assert "agent" in rt.allowed_tools

    def test_spawn_not_in_tools_disables_nested(self):
        role_cfg = AgentRoleConfig(
            description="worker",
            prompt="work",
            tools=["read_file", "write_file"],
        )
        rt = RuntimeRoleConfig.from_agent_role("worker", role_cfg, "m")
        assert rt.allow_nested_spawn is False
