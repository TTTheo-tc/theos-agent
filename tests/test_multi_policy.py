"""Tests for subagent policy config."""

from src.agent.delegation.policy import SubagentPolicyConfig
from src.config.schema import AgentsConfig, Config


class TestSubagentPolicyConfig:
    def test_defaults(self):
        p = SubagentPolicyConfig()
        assert p.max_concurrent == 3
        assert p.max_children_per_agent == 3
        assert p.max_depth == 2
        assert p.timeout_seconds == 900
        assert p.loop_warn_threshold == 3
        assert p.loop_hard_limit == 5
        assert p.keep_completed == 20

    def test_custom_values(self):
        p = SubagentPolicyConfig(max_concurrent=5, max_depth=1)
        assert p.max_concurrent == 5
        assert p.max_depth == 1


class TestAgentsConfigIntegration:
    def test_agents_has_subagents_field(self):
        ac = AgentsConfig()
        assert isinstance(ac.subagents, SubagentPolicyConfig)
        assert ac.subagents.max_concurrent == 3

    def test_config_json_round_trip(self):
        c = Config()
        assert c.agents.subagents.timeout_seconds == 900

    def test_camel_case_alias(self):
        ac = AgentsConfig.model_validate({"subagents": {"maxConcurrent": 10, "maxDepth": 1}})
        assert ac.subagents.max_concurrent == 10
        assert ac.subagents.max_depth == 1
