import asyncio
import os
import threading
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
async def test_read_interactive_input_passes_bottom_toolbar(mock_prompt_session):
    """Test that bottom toolbar text is passed to prompt_toolkit."""
    mock_prompt_session.prompt_async.return_value = "hello world"

    result = await repl.read_interactive_input(bottom_toolbar="TheOS ready")

    assert result == "hello world"
    _, kwargs = mock_prompt_session.prompt_async.call_args
    assert "bottom_toolbar" in kwargs
    toolbar = kwargs["bottom_toolbar"]
    assert toolbar is not None
    assert toolbar[0][0] == "class:bottom-toolbar.text"
    assert kwargs["style"] is repl._THEOS_PROMPT_STYLE


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


def test_terminal_page_enter_exit(monkeypatch):
    """Test alternate-screen enter/exit writes the expected terminal controls."""

    class FakeStdout:
        def __init__(self):
            self.output = ""

        def isatty(self):
            return True

        def write(self, value):
            self.output += value

        def flush(self):
            pass

    fake_stdout = FakeStdout()
    repl._IN_TERMINAL_PAGE = False
    monkeypatch.setattr(repl.sys, "stdout", fake_stdout)

    assert repl.enter_terminal_page() is True
    assert "\x1b[?1049h" in fake_stdout.output

    repl.exit_terminal_page()
    assert "\x1b[?1049l" in fake_stdout.output


@pytest.mark.asyncio
async def test_wait_for_escape_key_detects_standalone_escape(monkeypatch):
    """Test running-turn Esc detection without requiring a real TTY."""

    class FakeStdin:
        def __init__(self, fd):
            self._fd = fd

        def fileno(self):
            return self._fd

    import termios
    import tty

    read_fd, write_fd = os.pipe()
    cancel_event = threading.Event()
    monkeypatch.setattr(repl.sys, "stdin", FakeStdin(read_fd))
    monkeypatch.setattr(repl.os, "isatty", lambda _fd: True)
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: [])
    monkeypatch.setattr(termios, "tcsetattr", lambda *_args: None)
    monkeypatch.setattr(tty, "setcbreak", lambda _fd: None)

    try:
        task = asyncio.create_task(repl.wait_for_escape_key(cancel_event))
        await asyncio.sleep(0.01)
        os.write(write_fd, b"\x1b")

        assert await asyncio.wait_for(task, timeout=1) is True
    finally:
        cancel_event.set()
        os.close(read_fd)
        os.close(write_fd)
