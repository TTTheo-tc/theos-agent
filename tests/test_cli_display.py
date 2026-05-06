from src.cli.display import format_agent_toolbar, format_token_usage_line


def test_format_agent_toolbar_includes_session_usage():
    toolbar = format_agent_toolbar(
        model="openai-codex/gpt-5.5",
        mode="single mode",
        tools=61,
        session_usage={"prompt_tokens": 2000, "completion_tokens": 500, "total_tokens": 2500},
    )

    assert "TheOS gpt-5.5" in toolbar
    assert "single mode" in toolbar
    assert "61 tools" in toolbar
    assert "2.5k tok" in toolbar
    assert "Esc stop" in toolbar
    assert "/help" in toolbar


def test_format_token_usage_line_includes_turn_and_session_totals():
    line = format_token_usage_line(
        {"prompt_tokens": 1200, "completion_tokens": 345, "total_tokens": 1545},
        session_usage={"prompt_tokens": 3000, "completion_tokens": 900, "total_tokens": 3900},
    )

    assert line is not None
    assert "usage" in line
    assert "in 1.2k" in line
    assert "out 345" in line
    assert "turn 1.5k" in line
    assert "session 3.9k" in line


def test_format_token_usage_line_includes_cache_when_present():
    line = format_token_usage_line(
        {
            "prompt_tokens": 1200,
            "completion_tokens": 345,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 50,
            "total_tokens": 1545,
        }
    )

    assert line is not None
    assert "cache 800 read/50 write" in line


def test_format_token_usage_line_ignores_empty_usage():
    assert format_token_usage_line(None) is None
    assert format_token_usage_line({}) is None
    assert (
        format_token_usage_line(
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )
        is None
    )
