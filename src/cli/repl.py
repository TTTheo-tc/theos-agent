"""Terminal / prompt_toolkit helpers for the interactive REPL."""

import asyncio
import os
import select
import sys
import threading
from contextlib import suppress
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML, FormattedText
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit
_IN_TERMINAL_PAGE = False
_ESCAPE_SEQUENCE_TIMEOUT = 0.025
_THEOS_PROMPT_STYLE = Style.from_dict(
    {
        "bottom-toolbar": "noreverse bg:default",
        "bottom-toolbar.text": "noreverse #7EE7D4 bg:default",
    }
)


def enter_terminal_page() -> bool:
    """Enter the terminal alternate screen for a page-like CLI session."""
    global _IN_TERMINAL_PAGE
    if _IN_TERMINAL_PAGE:
        return True
    try:
        if not sys.stdout.isatty():
            return False
        sys.stdout.write("\x1b[?1049h\x1b[H\x1b[2J")
        sys.stdout.flush()
        _IN_TERMINAL_PAGE = True
        return True
    except Exception:
        return False


def exit_terminal_page() -> None:
    """Leave the terminal alternate screen if it is active."""
    global _IN_TERMINAL_PAGE
    if not _IN_TERMINAL_PAGE:
        return
    try:
        sys.stdout.write("\x1b[?1049l")
        sys.stdout.flush()
    except Exception:
        pass
    _IN_TERMINAL_PAGE = False


def flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios

        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _is_standalone_escape(fd: int) -> bool:
    """Return True for Esc, False for arrow/function sequences."""
    try:
        ready, _, _ = select.select([fd], [], [], _ESCAPE_SEQUENCE_TIMEOUT)
        if ready:
            os.read(fd, 32)
            return False
    except Exception:
        return False
    return True


def _wait_for_escape_key_blocking(cancel_event: threading.Event) -> bool:
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return False
    except Exception:
        return False

    try:
        import termios
        import tty

        original_attrs = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    except Exception:
        return False

    try:
        while not cancel_event.is_set():
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                continue
            data = os.read(fd, 1)
            if data == b"\x1b" and _is_standalone_escape(fd):
                return True
        return False
    except Exception:
        return False
    finally:
        with suppress(Exception):
            termios.tcsetattr(fd, termios.TCSADRAIN, original_attrs)


async def wait_for_escape_key(cancel_event: threading.Event) -> bool:
    """Wait for a standalone Esc key while a CLI turn is running."""
    return await asyncio.to_thread(_wait_for_escape_key_blocking, cancel_event)


def restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    exit_terminal_page()
    if _SAVED_TERM_ATTRS is None:
        return
    with suppress(Exception):
        import termios

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)


def init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Save terminal state so we can restore it on exit
    try:
        import termios

        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    history_file = Path.home() / ".theos" / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,  # Enter submits (single line mode)
    )


def is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def read_interactive_input(
    prompt: str | None = None, *, bottom_toolbar: str | None = None
) -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)

    Parameters
    ----------
    prompt : str, optional
        Custom prompt text. Defaults to the branded ``theos›`` prompt.
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call init_prompt_session() first")
    prompt_html = (
        HTML(f"<b fg='ansiyellow'>{prompt}</b> ")
        if prompt
        else HTML("<b fg='ansicyan'>theos</b><b fg='ansiwhite'>›</b> ")
    )
    toolbar = FormattedText([("class:bottom-toolbar.text", f" {bottom_toolbar} ")]) if bottom_toolbar else None
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                prompt_html,
                bottom_toolbar=toolbar,
                style=_THEOS_PROMPT_STYLE,
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc
