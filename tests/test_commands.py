import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from src.cli.commands import app
from src.config.schema import Config
from src.providers.custom_provider import OpenAICompatProvider
from src.providers.factory import make_provider as _make_provider
from src.providers.openai_codex_provider import _strip_model_prefix
from src.providers.registry import find_by_model

runner = CliRunner()


@pytest.fixture
def mock_paths():
    """Mock config/workspace paths for test isolation."""
    with (
        patch("src.config.loader.get_config_path") as mock_cp,
        patch("src.config.loader.save_config") as mock_sc,
        patch("src.config.loader.load_config") as mock_lc,
        patch("src.utils.helpers.get_workspace_path") as mock_ws,
        patch("src.cli.init_cmd.configure_channels", lambda config: None),
        patch("src.cli.init_cmd.configure_soul", lambda workspace: None),
        patch("src.cli.init_cmd._ensure_local_instruction_symlinks", return_value=[]),
        patch("src.cli.init_providers.check_codex_token", return_value="expired"),
        patch("src.cli.init_providers._api_keys_by_provider", return_value={}),
        patch("src.daemon.resolve_service", side_effect=NotImplementedError("test")),
    ):
        base_dir = Path("./test_onboard_data")
        if base_dir.exists():
            shutil.rmtree(base_dir)
        base_dir.mkdir()

        config_file = base_dir / "config.json"
        workspace_dir = base_dir / "workspace"

        mock_cp.return_value = config_file
        mock_ws.return_value = workspace_dir
        mock_lc.return_value = Config()
        mock_sc.side_effect = lambda config: config_file.write_text("{}")

        yield config_file, workspace_dir

        if base_dir.exists():
            shutil.rmtree(base_dir)


def _run_init(user_input: str = "\n\nn\n"):
    return runner.invoke(app, ["init"], input=user_input)


def test_init_fresh_install(mock_paths):
    """Fresh init creates config, workspace templates, and finishes successfully."""
    config_file, workspace_dir = mock_paths

    result = _run_init()

    assert result.exit_code == 0
    assert "Config:" in result.stdout
    assert "(created)" in result.stdout
    assert "Workspace:" in result.stdout
    assert "theos is ready" in result.stdout
    assert config_file.exists()
    assert (workspace_dir / "SOUL.md").exists()
    assert (workspace_dir / "memory" / "MEMORY.md").exists()


def test_init_refreshes_existing_config(mock_paths):
    """Existing config is refreshed in place by the current init flow."""
    config_file, workspace_dir = mock_paths
    config_file.write_text('{"existing": true}')

    result = _run_init()

    assert result.exit_code == 0
    assert "Config:" in result.stdout
    assert "(refreshed)" in result.stdout
    assert workspace_dir.exists()
    assert (workspace_dir / "SOUL.md").exists()


def test_init_existing_workspace_syncs_templates(mock_paths):
    """Existing workspace keeps running through init and gets missing templates synced."""
    config_file, workspace_dir = mock_paths
    workspace_dir.mkdir(parents=True)
    config_file.write_text("{}")

    result = _run_init()

    assert result.exit_code == 0
    assert "Workspace:" in result.stdout
    assert "Created SOUL.md" in result.stdout
    assert (workspace_dir / "SOUL.md").exists()


def test_config_matches_github_copilot_codex_with_hyphen_prefix():
    config = Config()
    config.agents.defaults.model = "github-copilot/gpt-5.3-codex"

    assert config.get_provider_name() == "github_copilot"


def test_config_matches_openai_codex_with_hyphen_prefix():
    config = Config()
    config.agents.defaults.model = "openai-codex/gpt-5.4"

    assert config.get_provider_name() == "openai_codex"


def test_find_by_model_prefers_explicit_prefix_over_generic_codex_keyword():
    spec = find_by_model("github-copilot/gpt-5.3-codex")

    assert spec is not None
    assert spec.name == "github_copilot"


def test_openai_compat_strips_github_copilot_prefix():
    provider = OpenAICompatProvider(
        default_model="github_copilot/gpt-5.3-codex",
        model_prefix_to_strip="github_copilot",
    )

    resolved = provider._resolve_model("github_copilot/gpt-5.3-codex")

    assert resolved == "gpt-5.3-codex"


def test_openai_codex_strip_prefix_supports_hyphen_and_underscore():
    assert _strip_model_prefix("openai-codex/gpt-5.4") == "gpt-5.4"
    assert _strip_model_prefix("openai_codex/gpt-5.4") == "gpt-5.4"


def test_make_provider_uses_auth_profile_key_when_config_key_missing():
    config = Config()
    config.agents.defaults.model = "anthropic/claude-sonnet-4-5"
    config.providers.anthropic.api_key = ""

    with (
        patch(
            "src.auth.store.get_credential_for_provider",
            return_value=("sk-auth-profile", "anthropic:default"),
        ),
        patch(
            "src.providers.anthropic_provider.AnthropicProvider", return_value=SimpleNamespace()
        ) as mock_provider,
    ):
        _make_provider(config)

    kwargs = mock_provider.call_args.kwargs
    assert kwargs["provider_name"] == "anthropic"
    assert kwargs["api_key"] == "sk-auth-profile"


def test_init_installs_daemon_on_supported_platform(mock_paths):
    """Init step 6 installs gateway daemon on supported platforms."""
    from unittest.mock import MagicMock

    mock_svc = MagicMock()
    mock_svc.is_loaded.return_value = False
    mock_svc.status.return_value = {"pid": 9999, "state": "running", "loaded": True}

    with (
        patch("src.daemon.resolve_service", return_value=mock_svc),
        patch("src.daemon.health.wait_for_gateway", return_value=True),
        patch(
            "src.cli.init_cmd._compute_daemon_args",
            return_value=(
                ["/usr/bin/python3", "-m", "src", "gateway"],
                {"PATH": "/usr/bin"},
                "/tmp",
            ),
        ),
    ):
        result = _run_init()

    assert result.exit_code == 0
    mock_svc.install.assert_called_once()
    assert "Gateway" in result.stdout


def test_init_skips_daemon_on_unsupported_platform(mock_paths):
    """Init skips daemon install gracefully on unsupported platforms."""
    with patch("src.daemon.resolve_service", side_effect=NotImplementedError("nope")):
        result = _run_init()

    assert result.exit_code == 0
    assert "theos gateway" in result.stdout


def test_compute_daemon_args_preserves_supported_secret_envs(monkeypatch):
    from src.cli.init_cmd import _compute_daemon_args

    monkeypatch.setenv("SECRETS_MASTER_KEY", "master-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-test")
    monkeypatch.setenv("ANTHROPIC_OAUTH_CLIENT_ID", "oauth-client")

    _program_args, env, _working_dir = _compute_daemon_args()

    assert env["SECRETS_MASTER_KEY"] == "master-key"
    assert env["OPENAI_API_KEY"] == "sk-test"
    assert env["GITHUB_TOKEN"] == "ghp-test"
    assert env["ANTHROPIC_OAUTH_CLIENT_ID"] == "oauth-client"
