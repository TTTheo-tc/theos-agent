import json
from datetime import datetime, timedelta

from typer.testing import CliRunner

from src.cli.commands import app

runner = CliRunner()


def test_cron_add_rejects_invalid_timezone(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("src.config.loader.get_data_dir", lambda: tmp_path)

    result = runner.invoke(
        app,
        [
            "cron",
            "add",
            "--name",
            "demo",
            "--message",
            "hello",
            "--cron",
            "0 9 * * *",
            "--tz",
            "America/Vancovuer",
        ],
    )

    assert result.exit_code == 1
    assert "Error: unknown timezone 'America/Vancovuer'" in result.stdout
    assert not (tmp_path / "cron" / "jobs.json").exists()


def test_cron_add_marks_one_shot_jobs_for_delete_after_run(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("src.config.loader.get_data_dir", lambda: tmp_path)
    run_at = (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat()

    result = runner.invoke(
        app,
        [
            "cron",
            "add",
            "--name",
            "demo",
            "--message",
            "hello",
            "--at",
            run_at,
        ],
    )

    assert result.exit_code == 0
    store = json.loads((tmp_path / "cron" / "jobs.json").read_text(encoding="utf-8"))
    assert store["jobs"][0]["deleteAfterRun"] is True
