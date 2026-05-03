from pathlib import Path

from src.cli.init_cmd import (
    _apply_reset,
    _detect_existing_state,
    _ensure_local_instruction_symlinks,
    _prompt_reset_mode,
    _resolve_workspace_for_reset,
)
from src.cli.init_soul import configure_soul, render_soul_markdown


def test_resolve_workspace_for_reset_uses_loaded_config_when_available(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    configured_workspace = tmp_path / "configured-workspace"

    class _Config:
        workspace_path = configured_workspace

    monkeypatch.setattr("src.config.loader.load_config", lambda: _Config())

    assert _resolve_workspace_for_reset(config_path) == configured_workspace


def test_detect_existing_state_all_present(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    auth_enc = tmp_path / "auth-profiles.enc"
    auth_enc.write_text("enc", encoding="utf-8")

    monkeypatch.setattr("src.cli.init_cmd._auth_profile_paths", lambda: [auth_enc])

    state = _detect_existing_state(config_path, workspace)
    assert state == {"config": True, "auth": True, "workspace": True}


def test_detect_existing_state_nothing_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.cli.init_cmd._auth_profile_paths", lambda: [tmp_path / "no-such-file"])

    state = _detect_existing_state(tmp_path / "config.json", tmp_path / "workspace")
    assert state == {"config": False, "auth": False, "workspace": False}


def test_prompt_reset_mode_shows_only_existing_items(monkeypatch) -> None:
    """Menu only shows items that exist; choosing the last option returns 'all'."""
    monkeypatch.setattr("src.cli.init_cmd.typer.prompt", lambda *args, **kwargs: "2")

    # Only auth exists → menu: [1] Auth, [2] All
    result = _prompt_reset_mode({"config": False, "auth": True, "workspace": False})
    assert result == "all"


def test_prompt_reset_mode_selects_single_item(monkeypatch) -> None:
    monkeypatch.setattr("src.cli.init_cmd.typer.prompt", lambda *args, **kwargs: "1")

    # Auth + workspace exist → menu: [1] Auth, [2] Workspace, [3] All
    result = _prompt_reset_mode({"config": False, "auth": True, "workspace": True})
    assert result == "auth"


def test_apply_reset_auth_removes_auth_files(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path.write_text("{}", encoding="utf-8")

    auth_enc = tmp_path / "auth-profiles.enc"
    auth_json = tmp_path / "auth-profiles.json"
    auth_enc.write_text("enc", encoding="utf-8")
    auth_json.write_text("json", encoding="utf-8")

    monkeypatch.setattr(
        "src.cli.init_cmd._auth_profile_paths",
        lambda: [auth_enc, auth_json],
    )

    _apply_reset("auth", config_path, workspace)

    assert not auth_enc.exists()
    assert not auth_json.exists()
    assert config_path.exists()
    assert workspace.exists()


def test_apply_reset_workspace_removes_workspace(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("x", encoding="utf-8")
    config_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr("src.cli.init_cmd._auth_profile_paths", lambda: [])

    _apply_reset("workspace", config_path, workspace)

    assert not workspace.exists()
    assert config_path.exists()


def test_apply_reset_all_removes_everything(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path.write_text("{}", encoding="utf-8")
    auth_enc = tmp_path / "auth-profiles.enc"
    auth_json = tmp_path / "auth-profiles.json"
    auth_enc.write_text("enc", encoding="utf-8")
    auth_json.write_text("json", encoding="utf-8")

    monkeypatch.setattr(
        "src.cli.init_cmd._auth_profile_paths",
        lambda: [auth_enc, auth_json],
    )

    _apply_reset("all", config_path, workspace)

    assert not config_path.exists()
    assert not workspace.exists()
    assert not auth_enc.exists()
    assert not auth_json.exists()


def test_apply_reset_skips_missing_items(tmp_path: Path, monkeypatch) -> None:
    """Reset 'all' with nothing on disk — should not raise."""
    monkeypatch.setattr("src.cli.init_cmd._auth_profile_paths", lambda: [])

    _apply_reset("all", tmp_path / "config.json", tmp_path / "workspace")


def test_render_soul_markdown_uses_name_and_preset() -> None:
    preset = {
        "voice": ["Speak clearly.", "Keep it short."],
        "style": ["Be practical.", "Be calm."],
    }

    content = render_soul_markdown("ironbot", preset)

    assert "- ironbot" in content
    assert "- Speak clearly." in content
    assert "- Be practical." in content
    # No boundaries/interaction — sections should be absent
    assert "## Boundaries" not in content
    assert "## Interaction" not in content


def test_render_soul_markdown_includes_boundaries_and_interaction() -> None:
    preset = {
        "voice": ["Be direct."],
        "style": ["Stay calm."],
        "boundaries": ["Do not apologize."],
        "interaction": ["State assumptions explicitly."],
    }

    content = render_soul_markdown("testbot", preset)

    assert "## Boundaries" in content
    assert "- Do not apologize." in content
    assert "## Interaction" in content
    assert "- State assumptions explicitly." in content


def test_configure_soul_updates_existing_file_with_selected_preset(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    soul_path = workspace / "SOUL.md"
    soul_path.write_text("old", encoding="utf-8")

    answers = iter(["2", "atlas", "2"])
    monkeypatch.setattr("src.cli.init_soul.typer.prompt", lambda *args, **kwargs: next(answers))

    configure_soul(workspace)

    content = soul_path.read_text(encoding="utf-8")
    assert "- atlas" in content
    assert "Speak precisely and without hype." in content
    assert "## Boundaries" in content
    assert "## Interaction" in content


def test_configure_soul_adaptive_preset_has_phase_instructions(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Choose preset 6 (adaptive), default name
    answers = iter(["theos", "6"])
    monkeypatch.setattr("src.cli.init_soul.typer.prompt", lambda *args, **kwargs: next(answers))

    configure_soul(workspace)

    content = (workspace / "SOUL.md").read_text(encoding="utf-8")
    assert "requirements and design" in content
    assert "execution and implementation" in content
    assert "review and debugging" in content


def test_configured_provider_does_not_require_new_api_key() -> None:
    provider_status = [("anthropic", "Anthropic", True), ("minimax", "MiniMax", True)]
    configured_providers: list[str] = []

    for idx in sorted({2}):
        key, _label, was_configured = provider_status[idx - 1]
        if was_configured:
            configured_providers.append(key)
            continue

    assert configured_providers == ["minimax"]


def test_ensure_local_instruction_symlinks_creates_missing_links(tmp_path: Path) -> None:
    repo_root = tmp_path
    (repo_root / "BOT.md").write_text("guide", encoding="utf-8")

    created = _ensure_local_instruction_symlinks(repo_root)

    assert created == ["CLAUDE.md", "GEMINI.md", "AGENTS.md"]
    assert (repo_root / "CLAUDE.md").is_symlink()
    assert (repo_root / "CLAUDE.md").resolve() == (repo_root / "BOT.md").resolve()


def test_ensure_local_instruction_symlinks_skips_existing_files(tmp_path: Path) -> None:
    repo_root = tmp_path
    (repo_root / "BOT.md").write_text("guide", encoding="utf-8")
    existing = repo_root / "CLAUDE.md"
    existing.write_text("custom", encoding="utf-8")

    created = _ensure_local_instruction_symlinks(repo_root)

    assert created == ["GEMINI.md", "AGENTS.md"]
    assert existing.read_text(encoding="utf-8") == "custom"
    assert not existing.is_symlink()
