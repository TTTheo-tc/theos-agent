"""Tests for /instinct slash commands."""

import json
from unittest.mock import MagicMock

import pytest

from src.bus.events import InboundMessage


@pytest.fixture
def mock_loop(tmp_path):
    loop = MagicMock()
    loop.workspace = tmp_path
    return loop


def _msg(content: str) -> InboundMessage:
    return InboundMessage(channel="cli", sender_id="owner", chat_id="test", content=content)


@pytest.mark.asyncio
async def test_status_empty(mock_loop):
    from src.agent.instinct_commands import handle_instinct_command

    result = await handle_instinct_command(mock_loop, _msg("/instinct status"))
    assert result is not None
    assert "Active rules: 0" in result.content
    assert "Probation rules: 0" in result.content
    assert "Events: 0" in result.content
    assert "Dream sessions: 0" in result.content


@pytest.mark.asyncio
async def test_status_with_rules(mock_loop, tmp_path):
    rules_dir = tmp_path / "memory" / "instinct" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "ACTIVE.md").write_text(
        "# Active Rules\n\n- [R1] test rule  <!-- scope:domain_boost -->\n"
    )
    (rules_dir / "PROBATION.md").write_text("# Probation\n\n- [R2] new rule\n- [R3] another\n")

    from src.agent.instinct_commands import handle_instinct_command

    result = await handle_instinct_command(mock_loop, _msg("/instinct status"))
    assert "Active rules: 1" in result.content
    assert "Probation rules: 2" in result.content


@pytest.mark.asyncio
async def test_status_with_events_and_dreams(mock_loop, tmp_path):
    instinct_dir = tmp_path / "memory" / "instinct"
    events_dir = instinct_dir / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "ev1.json").write_text("{}")
    (events_dir / "ev2.json").write_text("{}")

    dreams_dir = instinct_dir / "dreams"
    dreams_dir.mkdir(parents=True)
    (dreams_dir / "2026-01-01-abc").mkdir()

    from src.agent.instinct_commands import handle_instinct_command

    result = await handle_instinct_command(mock_loop, _msg("/instinct status"))
    assert "Events: 2" in result.content
    assert "Dream sessions: 1" in result.content


@pytest.mark.asyncio
async def test_help(mock_loop):
    from src.agent.instinct_commands import handle_instinct_command

    result = await handle_instinct_command(mock_loop, _msg("/instinct"))
    assert "/instinct status" in result.content
    assert "/instinct evolve-run" in result.content
    assert "/instinct dream-run" in result.content


@pytest.mark.asyncio
async def test_help_unknown_subcommand(mock_loop):
    from src.agent.instinct_commands import handle_instinct_command

    result = await handle_instinct_command(mock_loop, _msg("/instinct foobar"))
    assert "/instinct status" in result.content


@pytest.mark.asyncio
async def test_dream_review_empty(mock_loop):
    from src.agent.instinct_commands import handle_instinct_command

    result = await handle_instinct_command(mock_loop, _msg("/instinct dream-review"))
    assert "No dream sessions" in result.content


@pytest.mark.asyncio
async def test_dream_review_list(mock_loop, tmp_path):
    dreams_dir = tmp_path / "memory" / "instinct" / "dreams"
    session_dir = dreams_dir / "2026-01-01-abc"
    session_dir.mkdir(parents=True)
    (session_dir / "dream_eval.json").write_text(
        json.dumps({"status": "completed", "topic": "testing"})
    )

    from src.agent.instinct_commands import handle_instinct_command

    result = await handle_instinct_command(mock_loop, _msg("/instinct dream-review"))
    assert "2026-01-01-abc" in result.content
    assert "completed" in result.content


@pytest.mark.asyncio
async def test_dream_review_specific(mock_loop, tmp_path):
    dreams_dir = tmp_path / "memory" / "instinct" / "dreams"
    session_dir = dreams_dir / "2026-01-01-abc"
    session_dir.mkdir(parents=True)
    (session_dir / "dream-review.md").write_text("# Dream Review\nAll good.")

    from src.agent.instinct_commands import handle_instinct_command

    result = await handle_instinct_command(mock_loop, _msg("/instinct dream-review 2026-01-01-abc"))
    assert "Dream Review" in result.content
    assert "All good" in result.content


@pytest.mark.asyncio
async def test_dream_review_not_found(mock_loop, tmp_path):
    dreams_dir = tmp_path / "memory" / "instinct" / "dreams"
    dreams_dir.mkdir(parents=True)

    from src.agent.instinct_commands import handle_instinct_command

    result = await handle_instinct_command(mock_loop, _msg("/instinct dream-review nonexistent"))
    assert "No dream session matching" in result.content


@pytest.mark.asyncio
async def test_dream_apply_missing_args(mock_loop):
    from src.agent.instinct_commands import handle_instinct_command

    result = await handle_instinct_command(mock_loop, _msg("/instinct dream-apply"))
    assert "Usage:" in result.content


@pytest.mark.asyncio
async def test_dream_apply_success(mock_loop, tmp_path):
    dreams_dir = tmp_path / "memory" / "instinct" / "dreams"
    session_dir = dreams_dir / "2026-01-01-abc"
    sandbox_dir = session_dir / "sandbox"
    sandbox_dir.mkdir(parents=True)
    # Create the artifact file so existence check passes
    (sandbox_dir / "my-artifact").write_text("artifact content")
    eval_path = session_dir / "dream_eval.json"
    eval_path.write_text(json.dumps({"status": "completed"}))

    from src.agent.instinct_commands import handle_instinct_command

    result = await handle_instinct_command(
        mock_loop, _msg("/instinct dream-apply 2026-01-01-abc my-artifact")
    )
    assert "marked as applied" in result.content

    # Verify eval was updated
    data = json.loads(eval_path.read_text())
    assert "my-artifact" in data["applied_artifacts"]
    assert data["reviewed_by_user"] is True


@pytest.mark.asyncio
async def test_dream_apply_syncs_index(mock_loop, tmp_path):
    """dream-apply must sync reviewed_by_user back to DREAM_INDEX.jsonl."""
    dreams_dir = tmp_path / "memory" / "instinct" / "dreams"
    session_dir = dreams_dir / "2026-01-01-abc"
    sandbox_dir = session_dir / "sandbox"
    sandbox_dir.mkdir(parents=True)
    (sandbox_dir / "my-artifact").write_text("content")
    eval_path = session_dir / "dream_eval.json"
    eval_path.write_text(json.dumps({"status": "completed"}))

    # Write an index entry with reviewed_by_user=false
    index_path = tmp_path / "memory" / "instinct" / "DREAM_INDEX.jsonl"
    index_path.write_text(
        json.dumps({"session_id": "2026-01-01-abc", "reviewed_by_user": False}) + "\n"
    )

    from src.agent.instinct_commands import handle_instinct_command

    await handle_instinct_command(
        mock_loop, _msg("/instinct dream-apply 2026-01-01-abc my-artifact")
    )

    # Verify index was updated
    entry = json.loads(index_path.read_text().strip())
    assert entry["reviewed_by_user"] is True


@pytest.mark.asyncio
async def test_dream_apply_artifact_not_found(mock_loop, tmp_path):
    dreams_dir = tmp_path / "memory" / "instinct" / "dreams"
    session_dir = dreams_dir / "2026-01-01-abc"
    session_dir.mkdir(parents=True)
    eval_path = session_dir / "dream_eval.json"
    eval_path.write_text(json.dumps({"status": "completed"}))

    from src.agent.instinct_commands import handle_instinct_command

    result = await handle_instinct_command(
        mock_loop, _msg("/instinct dream-apply 2026-01-01-abc missing-file")
    )
    assert "not found" in result.content


@pytest.mark.asyncio
async def test_dream_apply_session_not_found(mock_loop, tmp_path):
    from src.agent.instinct_commands import handle_instinct_command

    result = await handle_instinct_command(mock_loop, _msg("/instinct dream-apply nonexistent art"))
    assert "not found" in result.content
