"""Tests for agent definition loading from workspace frontmatter files."""

from pathlib import Path

from src.agent.definitions import load_agent_definitions
from src.config.schema import AgentRoleConfig


class TestLoadAgentDefinitions:
    def test_load_from_empty_dir(self, tmp_path: Path):
        result = load_agent_definitions(tmp_path)
        assert result == {}

    def test_load_from_nonexistent_dir(self, tmp_path: Path):
        result = load_agent_definitions(tmp_path / "does_not_exist")
        assert result == {}

    def test_load_single_definition(self, tmp_path: Path):
        md = tmp_path / "researcher.md"
        md.write_text(
            "---\n"
            "description: Research agent\n"
            "model: anthropic/claude-sonnet-4-20250514\n"
            "tools:\n"
            "  - web_search\n"
            "  - web_fetch\n"
            "max_iterations: 30\n"
            "---\n"
            "You are a research assistant.\n"
            "\n"
            "Focus on accuracy.\n"
        )
        result = load_agent_definitions(tmp_path)
        assert "researcher" in result
        cfg = result["researcher"]
        assert isinstance(cfg, AgentRoleConfig)
        assert cfg.description == "Research agent"
        assert cfg.model == "anthropic/claude-sonnet-4-20250514"
        assert cfg.tools == ["web_search", "web_fetch"]
        assert cfg.max_iterations == 30
        assert cfg.prompt == "You are a research assistant.\n\nFocus on accuracy."

    def test_frontmatter_marker_must_be_own_line(self, tmp_path: Path):
        md = tmp_path / "researcher.md"
        md.write_text(
            "---\n"
            "description: Research --- agent\n"
            "---\n"
            "Prompt.\n"
        )
        result = load_agent_definitions(tmp_path)

        assert result["researcher"].description == "Research --- agent"

    def test_frontmatter_marker_allows_trailing_whitespace(self, tmp_path: Path):
        md = tmp_path / "researcher.md"
        md.write_text(
            "--- \n"
            "description: Research agent\n"
            "---\t\n"
            "Prompt.\n"
        )
        result = load_agent_definitions(tmp_path)

        assert result["researcher"].prompt == "Prompt."

    def test_load_multiple_definitions(self, tmp_path: Path):
        (tmp_path / "alpha.md").write_text("---\ndescription: Alpha agent\n---\nAlpha prompt.\n")
        (tmp_path / "beta.md").write_text("---\ndescription: Beta agent\n---\nBeta prompt.\n")
        result = load_agent_definitions(tmp_path)
        assert set(result.keys()) == {"alpha", "beta"}

    def test_ignores_non_md_files(self, tmp_path: Path):
        (tmp_path / "notes.txt").write_text("---\ndescription: Should be ignored\n---\nBody.\n")
        (tmp_path / "valid.md").write_text("---\ndescription: Valid agent\n---\nPrompt.\n")
        result = load_agent_definitions(tmp_path)
        assert list(result.keys()) == ["valid"]

    def test_skips_malformed_frontmatter(self, tmp_path: Path):
        # No frontmatter at all
        (tmp_path / "no_fm.md").write_text("Just plain markdown.\n")
        # Frontmatter without description
        (tmp_path / "no_desc.md").write_text("---\nmodel: foo\n---\nBody.\n")
        # Valid one
        (tmp_path / "good.md").write_text("---\ndescription: Good agent\n---\nPrompt.\n")
        result = load_agent_definitions(tmp_path)
        assert list(result.keys()) == ["good"]

    def test_definition_with_isolation(self, tmp_path: Path):
        md = tmp_path / "worker.md"
        md.write_text(
            "---\n"
            "description: Isolated worker\n"
            "isolation: worktree\n"
            "timeout_seconds: 600\n"
            "---\n"
            "Worker prompt.\n"
        )
        result = load_agent_definitions(tmp_path)
        cfg = result["worker"]
        assert cfg.isolation == "worktree"
        assert cfg.timeout_seconds == 600


class TestAgentDefinitionIntegration:
    def test_runtime_config_from_extended_role(self):
        from src.agent.delegation.runtime import RuntimeRoleConfig
        from src.config.schema import AgentRoleConfig

        role = AgentRoleConfig(
            description="Reviewer",
            model="sonnet",
            prompt="Review code.",
            tools=["read_file", "grep"],
            isolation="worktree",
            timeout_seconds=300,
        )

        runtime = RuntimeRoleConfig.from_agent_role("reviewer", role, "default-model")
        assert runtime.model == "sonnet"
        assert runtime.allowed_tools == {"read_file", "grep"}
        assert runtime.isolation == "worktree"
        assert runtime.timeout_seconds == 300

    def test_runtime_config_defaults_when_no_new_fields(self):
        from src.agent.delegation.runtime import RuntimeRoleConfig
        from src.config.schema import AgentRoleConfig

        role = AgentRoleConfig(description="Basic", prompt="Do stuff.")

        runtime = RuntimeRoleConfig.from_agent_role("basic", role, "default-model")
        assert runtime.isolation is None
        assert runtime.timeout_seconds is None
