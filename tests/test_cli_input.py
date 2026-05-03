from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prompt_toolkit.formatted_text import HTML

from src.cli import repl


@pytest.fixture
def mock_prompt_session():
    """Mock the global prompt session."""
    mock_session = MagicMock()
    mock_session.prompt_async = AsyncMock()
    with (
        patch("src.cli.repl._PROMPT_SESSION", mock_session),
        patch("src.cli.repl.patch_stdout"),
    ):
        yield mock_session


@pytest.mark.asyncio
async def test_read_interactive_input_async_returns_input(mock_prompt_session):
    """Test that read_interactive_input returns the user input from prompt_session."""
    mock_prompt_session.prompt_async.return_value = "hello world"

    result = await repl.read_interactive_input()

    assert result == "hello world"
    mock_prompt_session.prompt_async.assert_called_once()
    args, _ = mock_prompt_session.prompt_async.call_args
    assert isinstance(args[0], HTML)  # Verify HTML prompt is used


@pytest.mark.asyncio
async def test_read_interactive_input_async_handles_eof(mock_prompt_session):
    """Test that EOFError converts to KeyboardInterrupt."""
    mock_prompt_session.prompt_async.side_effect = EOFError()

    with pytest.raises(KeyboardInterrupt):
        await repl.read_interactive_input()


def test_init_prompt_session_creates_session():
    """Test that init_prompt_session initializes the global session."""
    repl._PROMPT_SESSION = None

    with (
        patch("src.cli.repl.PromptSession") as mock_session,
        patch("src.cli.repl.FileHistory") as _mock_history,
        patch("pathlib.Path.home") as mock_home,
    ):

        mock_home.return_value = MagicMock()

        repl.init_prompt_session()

        assert repl._PROMPT_SESSION is not None
        mock_session.assert_called_once()
        _, kwargs = mock_session.call_args
        assert kwargs["multiline"] is False
        assert kwargs["enable_open_in_editor"] is False
