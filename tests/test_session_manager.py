"""Tests for SessionManager LRU cache and credential scrubbing."""

import json
from datetime import timedelta

import pytest

from src.session.manager import SessionManager


@pytest.fixture
def sm(tmp_path):
    return SessionManager(tmp_path)


def test_get_or_create_new(sm):
    session = sm.get_or_create("test:1")
    assert session.key == "test:1"


def test_cache_hit(sm):
    s1 = sm.get_or_create("test:1")
    s2 = sm.get_or_create("test:1")
    assert s1 is s2


def test_append_metadata_tail_and_reload_latest_metadata(sm):
    session = sm.get_or_create("test:1")
    session.add_message("user", "hello")
    sm.save(session)

    session.last_consolidated = 1
    sm.save(session)

    sm.invalidate("test:1")
    reloaded = sm.get_or_create("test:1")
    assert reloaded.last_consolidated == 1


def test_compact_threshold_applies_to_cumulative_appends(sm):
    sm._COMPACT_THRESHOLD = 4
    session = sm.get_or_create("test:1")
    session.add_message("user", "m1")
    sm.save(session)  # full write

    session.add_message("assistant", "m2")
    sm.save(session)  # append (2 lines)
    assert sm._append_lines_since_compact["test:1"] == 2

    session.add_message("user", "m3")
    sm.save(session)  # would exceed threshold -> full rewrite
    assert sm._append_lines_since_compact["test:1"] == 0

    path = sm._get_session_path("test:1")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4  # metadata + 3 messages


def test_list_sessions_uses_latest_metadata_snapshot(sm):
    session = sm.get_or_create("test:1")
    session.add_message("user", "hello")
    sm.save(session)

    session.updated_at = session.updated_at + timedelta(seconds=1)
    sm.save(session)  # metadata-only append

    listed = sm.list_sessions()
    assert listed[0]["key"] == "test:1"
    assert listed[0]["updated_at"] == session.updated_at.isoformat()


def test_persist_user_message_is_idempotent(sm):
    session = sm.get_or_create("cli:direct")

    first = sm.persist_user_message(session, "hello", turn_id="turn-1")
    second = sm.persist_user_message(session, "hello again", turn_id="turn-1")

    assert first is True
    assert second is False
    assert len([m for m in session.messages if m.get("turn_id") == "turn-1"]) == 1

    path = sm._get_session_path("cli:direct")
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len([line for line in lines if line.get("turn_id") == "turn-1"]) == 1


# ── Credential scrubbing ────────────────────────────────────────────────


def _bare_manager(*, scrub_enabled: bool = True) -> SessionManager:
    """Create a SessionManager without __init__ for unit-testing scrub logic."""
    mgr = SessionManager.__new__(SessionManager)
    mgr._scrub_enabled = scrub_enabled
    return mgr


def test_scrub_message_for_persist_tool_calls():
    mgr = _bare_manager()
    msg = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "1",
                "function": {
                    "name": "http_request",
                    "arguments": '{"headers": {"token": "sk-ant-secret12345678"}}',
                },
            }
        ],
    }
    scrubbed = mgr._scrub_message_for_persist(msg)
    assert "sk-ant-secret12345678" not in scrubbed["tool_calls"][0]["function"]["arguments"]
    # Original must be untouched
    assert "sk-ant-secret12345678" in msg["tool_calls"][0]["function"]["arguments"]


def test_scrub_message_for_persist_tool_result():
    mgr = _bare_manager()
    msg = {"role": "tool", "content": "password=mysecretpassword123"}
    scrubbed = mgr._scrub_message_for_persist(msg)
    assert "mysecretpassword123" not in scrubbed["content"]


def test_scrub_message_disabled():
    mgr = _bare_manager(scrub_enabled=False)
    msg = {"role": "tool", "content": "password=mysecretpassword123"}
    assert mgr._scrub_message_for_persist(msg) is msg


def test_scrub_message_passthrough_normal_message():
    """Non-sensitive messages pass through without modification."""
    mgr = _bare_manager()
    msg = {"role": "user", "content": "hello world"}
    scrubbed = mgr._scrub_message_for_persist(msg)
    assert scrubbed["content"] == "hello world"


def test_save_scrubs_on_full_rewrite(tmp_path):
    """Credentials are scrubbed in the full-rewrite code path."""
    mgr = SessionManager(tmp_path)
    session = mgr.get_or_create("test:scrub")
    session.messages.append({"role": "tool", "content": "token=abcdefghij12345678"})
    mgr.save(session)

    path = mgr._get_session_path("test:scrub")
    raw = path.read_text(encoding="utf-8")
    assert "abcdefghij12345678" not in raw
    # Original session message should be intact
    assert "abcdefghij12345678" in session.messages[0]["content"]


def test_save_scrubs_on_append(tmp_path):
    """Credentials are scrubbed in the append code path."""
    mgr = SessionManager(tmp_path)
    session = mgr.get_or_create("test:append")
    session.messages.append({"role": "user", "content": "first"})
    mgr.save(session)

    # Now append a tool result with a credential
    session.messages.append({"role": "tool", "content": "secret=verysecretvalue99"})
    mgr.save(session)

    path = mgr._get_session_path("test:append")
    raw = path.read_text(encoding="utf-8")
    assert "verysecretvalue99" not in raw
    # Original session message should be intact
    assert "verysecretvalue99" in session.messages[1]["content"]


def test_scrub_message_for_persist_tool_call_missing_function():
    """tool_calls entries without a 'function' key should pass through."""
    mgr = _bare_manager()
    msg = {
        "role": "assistant",
        "tool_calls": [
            {"id": "1", "type": "custom"},  # no 'function' key
            {
                "id": "2",
                "function": {"name": "test", "arguments": "password=longvalue123456"},
            },
        ],
    }
    scrubbed = mgr._scrub_message_for_persist(msg)
    assert scrubbed["tool_calls"][0] == {"id": "1", "type": "custom"}
    assert "longvalue123456" not in scrubbed["tool_calls"][1]["function"]["arguments"]
