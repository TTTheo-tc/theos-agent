"""Tests for browser automation tool (playwright mocked)."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — mock playwright objects
# ---------------------------------------------------------------------------


def _mock_response(status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    return resp


def _make_locator_mock(*, inner_text: str = "element text") -> MagicMock:
    """Create a mock Playwright locator with common async methods."""
    loc = MagicMock()
    loc.is_visible = AsyncMock(return_value=True)
    loc.click = AsyncMock()
    loc.fill = AsyncMock()
    loc.inner_text = AsyncMock(return_value=inner_text)
    loc.aria_snapshot = AsyncMock(
        return_value="- heading 'Main'\n- button 'Submit'\n- textbox 'Email'\n"
    )
    return loc


def _mock_page(*, url: str = "about:blank", title: str = "") -> AsyncMock:
    page = AsyncMock()
    page.url = url
    page.is_closed = MagicMock(return_value=False)  # sync method in playwright
    page.title = AsyncMock(return_value=title)
    page.goto = AsyncMock(return_value=_mock_response(200))
    page.screenshot = AsyncMock()
    page.inner_text = AsyncMock(return_value="Hello World")
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.evaluate = AsyncMock(return_value=42)
    page.close = AsyncMock()
    page.hover = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.wait_for_function = AsyncMock()
    page.bring_to_front = AsyncMock()
    page.set_viewport_size = AsyncMock()
    # keyboard mock
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    # locator mock — used by is_visible and snapshot
    locator_mock = _make_locator_mock()
    page.locator = MagicMock(return_value=locator_mock)
    # Semantic locator mocks (get_by_role, get_by_text, etc.)
    semantic_locator = _make_locator_mock()
    page.get_by_role = MagicMock(return_value=semantic_locator)
    page.get_by_text = MagicMock(return_value=semantic_locator)
    page.get_by_label = MagicMock(return_value=semantic_locator)
    page.get_by_placeholder = MagicMock(return_value=semantic_locator)
    page.get_by_test_id = MagicMock(return_value=semantic_locator)
    return page


def _mock_context(page: AsyncMock | None = None) -> AsyncMock:
    ctx = AsyncMock()
    ctx.new_page = AsyncMock(return_value=page or _mock_page())
    ctx.close = AsyncMock()
    ctx.cookies = AsyncMock(return_value=[])
    ctx.add_cookies = AsyncMock()
    ctx.clear_cookies = AsyncMock()
    ctx.set_extra_http_headers = AsyncMock()
    return ctx


def _mock_browser(context: AsyncMock | None = None) -> AsyncMock:
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context or _mock_context())
    browser.close = AsyncMock()
    return browser


def _mock_chromium(browser: AsyncMock | None = None) -> AsyncMock:
    chromium = AsyncMock()
    chromium.launch = AsyncMock(return_value=browser or _mock_browser())
    return chromium


def _mock_playwright_cm(chromium: AsyncMock | None = None) -> AsyncMock:
    """Return a mock that behaves like ``async_playwright().start()``."""
    pw = AsyncMock()
    pw.chromium = chromium or _mock_chromium()
    pw.stop = AsyncMock()
    pw.devices = {
        "iPhone 13": {
            "viewport": {"width": 390, "height": 844},
            "user_agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"
            ),
        },
        "Pixel 5": {
            "viewport": {"width": 393, "height": 851},
            "user_agent": (
                "Mozilla/5.0 (Linux; Android 11; Pixel 5) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.131 Mobile Safari/537.36"
            ),
        },
    }
    return pw


# ---------------------------------------------------------------------------
# Fixture — BrowserTool with mocked playwright
# ---------------------------------------------------------------------------


@pytest.fixture
def tool_and_mocks(tmp_path: Path):
    """Create a BrowserTool with playwright fully mocked.

    Returns (tool, page_mock, pw_mock).
    """
    page = _mock_page(url="https://example.com", title="Example")
    ctx = _mock_context(page)
    browser = _mock_browser(ctx)
    chromium = _mock_chromium(browser)
    pw = _mock_playwright_cm(chromium)

    # Patch the module-level flag and the async_playwright callable
    with (
        patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True),
        patch("src.agent.tools.browser._async_playwright") as mock_ap,
    ):
        mock_ap.return_value.start = AsyncMock(return_value=pw)

        from src.agent.tools.browser import BrowserTool

        tool = BrowserTool(workspace=tmp_path)
        yield tool, page, pw


@pytest.fixture
def tool_ctx_and_mocks(tmp_path: Path):
    """Like tool_and_mocks but also yields the context mock.

    Returns (tool, page_mock, ctx_mock, pw_mock).
    """
    page = _mock_page(url="https://example.com", title="Example")
    ctx = _mock_context(page)
    browser = _mock_browser(ctx)
    chromium = _mock_chromium(browser)
    pw = _mock_playwright_cm(chromium)

    with (
        patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True),
        patch("src.agent.tools.browser._async_playwright") as mock_ap,
    ):
        mock_ap.return_value.start = AsyncMock(return_value=pw)

        from src.agent.tools.browser import BrowserTool

        tool = BrowserTool(workspace=tmp_path)
        yield tool, page, ctx, pw


# ---------------------------------------------------------------------------
# Schema / metadata
# ---------------------------------------------------------------------------


class TestBrowserToolMeta:
    def test_name(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            assert tool.name == "browser"

    def test_schema(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            schema = tool.to_schema()
            assert schema["type"] == "function"
            assert schema["function"]["name"] == "browser"
            params = schema["function"]["parameters"]
            assert "action" in params["properties"]
            assert "action" in params["required"]

    def test_schema_includes_new_actions(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            actions = tool.parameters["properties"]["action"]["enum"]
            for a in ("hover", "scroll", "press", "wait", "is_visible", "get_title", "get_url"):
                assert a in actions

    def test_schema_includes_new_params(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            props = tool.parameters["properties"]
            assert "key" in props
            assert "direction" in props
            assert "pixels" in props

    def test_risk_level(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            assert tool.risk_level == "medium"


# ---------------------------------------------------------------------------
# Playwright not installed
# ---------------------------------------------------------------------------


class TestPlaywrightMissing:
    @pytest.mark.asyncio
    async def test_returns_error_when_not_installed(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", False):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            result = json.loads(await tool.execute(action="open", url="https://example.com"))
            assert "error" in result
            assert "playwright" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_close_when_not_installed(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", False):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            result = json.loads(await tool.execute(action="close"))
            assert "error" in result


# ---------------------------------------------------------------------------
# Action validation
# ---------------------------------------------------------------------------


class TestActionValidation:
    @pytest.mark.asyncio
    async def test_missing_action(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute())
        assert "error" in result
        assert "action is required" in result["error"]

    @pytest.mark.asyncio
    async def test_unknown_action(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="fly"))
        assert "error" in result
        assert "Unknown action" in result["error"]


# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------


class TestOpen:
    @pytest.mark.asyncio
    async def test_open_url(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        page.goto.return_value = _mock_response(200)
        page.title.return_value = "Example Domain"

        # After goto, page.url should reflect the new URL
        def _update_url(*a, **kw):
            page.url = "https://example.com"
            return _mock_response(200)

        page.goto = AsyncMock(side_effect=_update_url)

        result = json.loads(await tool.execute(action="open", url="https://example.com"))
        assert result["status"] == "ok"
        assert result["url"] == "https://example.com"
        assert result["http_status"] == 200

    @pytest.mark.asyncio
    async def test_open_missing_url(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="open"))
        assert "error" in result
        assert "url is required" in result["error"]

    @pytest.mark.asyncio
    async def test_open_empty_url(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="open", url=""))
        assert "error" in result


# ---------------------------------------------------------------------------
# screenshot
# ---------------------------------------------------------------------------


class TestScreenshot:
    @pytest.mark.asyncio
    async def test_screenshot(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="screenshot"))
        assert result["status"] == "ok"
        assert "path" in result
        assert result["path"].endswith(".png")
        assert result["full_page"] is True
        page.screenshot.assert_called_once()

    @pytest.mark.asyncio
    async def test_screenshot_not_full_page(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="screenshot", full_page=False))
        assert result["status"] == "ok"
        assert result["full_page"] is False

    @pytest.mark.asyncio
    async def test_screenshot_dir_created(self, tool_and_mocks, tmp_path: Path):
        tool, _, _ = tool_and_mocks
        ss_dir = tmp_path / "browser_screenshots"
        assert not ss_dir.exists()
        await tool.execute(action="screenshot")
        assert ss_dir.exists()


# ---------------------------------------------------------------------------
# get_text
# ---------------------------------------------------------------------------


class TestGetText:
    @pytest.mark.asyncio
    async def test_get_text(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        page.inner_text.return_value = "Hello World"
        result = json.loads(await tool.execute(action="get_text"))
        assert result["status"] == "ok"
        assert result["text"] == "Hello World"
        assert result["truncated"] is False

    @pytest.mark.asyncio
    async def test_get_text_truncation(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        page.inner_text.return_value = "x" * 100_000
        result = json.loads(await tool.execute(action="get_text"))
        assert result["status"] == "ok"
        assert result["truncated"] is True
        assert result["length"] == 50_000


# ---------------------------------------------------------------------------
# click
# ---------------------------------------------------------------------------


class TestClick:
    @pytest.mark.asyncio
    async def test_click(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="click", selector="#btn"))
        assert result["status"] == "ok"
        assert result["selector"] == "#btn"
        page.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_click_missing_selector(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="click"))
        assert "error" in result
        assert "selector is required" in result["error"]

    @pytest.mark.asyncio
    async def test_click_empty_selector(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="click", selector=""))
        assert "error" in result


# ---------------------------------------------------------------------------
# type_text
# ---------------------------------------------------------------------------


class TestTypeText:
    @pytest.mark.asyncio
    async def test_type_text(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="type_text", selector="#input", text="hello"))
        assert result["status"] == "ok"
        assert result["text_length"] == 5
        page.fill.assert_called_once_with("#input", "hello", timeout=30000)

    @pytest.mark.asyncio
    async def test_type_text_missing_selector(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="type_text", text="hello"))
        assert "error" in result
        assert "selector is required" in result["error"]

    @pytest.mark.asyncio
    async def test_type_text_missing_text(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="type_text", selector="#input"))
        assert "error" in result
        assert "text is required" in result["error"]

    @pytest.mark.asyncio
    async def test_type_text_empty_text(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="type_text", selector="#input", text=""))
        assert "error" in result


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_evaluate(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        page.evaluate.return_value = 42
        result = json.loads(await tool.execute(action="evaluate", script="1 + 1"))
        assert result["status"] == "ok"
        assert result["result"] == 42

    @pytest.mark.asyncio
    async def test_evaluate_string_result(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        page.evaluate.return_value = "hello"
        result = json.loads(await tool.execute(action="evaluate", script="'hello'"))
        assert result["result"] == "hello"

    @pytest.mark.asyncio
    async def test_evaluate_dict_result(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        page.evaluate.return_value = {"key": "value"}
        result = json.loads(await tool.execute(action="evaluate", script="({key: 'value'})"))
        assert result["result"] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_evaluate_null_result(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        page.evaluate.return_value = None
        result = json.loads(await tool.execute(action="evaluate", script="null"))
        assert result["result"] is None

    @pytest.mark.asyncio
    async def test_evaluate_missing_script(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="evaluate"))
        assert "error" in result
        assert "script is required" in result["error"]

    @pytest.mark.asyncio
    async def test_evaluate_empty_script(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="evaluate", script=""))
        assert "error" in result


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:
    @pytest.mark.asyncio
    async def test_close(self, tool_and_mocks):
        tool, _, pw = tool_and_mocks
        # First open to initialise
        await tool.execute(action="open", url="https://example.com")
        result = json.loads(await tool.execute(action="close"))
        assert result["status"] == "closed"

    @pytest.mark.asyncio
    async def test_close_idempotent(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        # Close without ever opening — should still succeed
        result = json.loads(await tool.execute(action="close"))
        assert result["status"] == "closed"

    @pytest.mark.asyncio
    async def test_close_with_errors(self, tool_and_mocks):
        tool, _, pw = tool_and_mocks
        # Open first
        await tool.execute(action="open", url="https://example.com")
        # Make context.close raise
        tool._context.close = AsyncMock(side_effect=RuntimeError("ctx boom"))
        result = json.loads(await tool.execute(action="close"))
        assert result["status"] == "closed_with_errors"
        assert any("ctx boom" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_page_exception_returns_error(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        page.goto.side_effect = Exception("Navigation failed")
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo("93.184.216.34")
            result = json.loads(await tool.execute(action="open", url="https://bad.example"))
        assert "error" in result
        assert "Navigation failed" in result["error"]

    @pytest.mark.asyncio
    async def test_click_exception(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        page.click.side_effect = Exception("Element not found")
        result = json.loads(await tool.execute(action="click", selector="#missing"))
        assert "error" in result
        assert "Element not found" in result["error"]


# ---------------------------------------------------------------------------
# Browser reuse
# ---------------------------------------------------------------------------


class TestBrowserReuse:
    @pytest.mark.asyncio
    async def test_browser_reused_across_calls(self, tool_and_mocks):
        tool, page, pw = tool_and_mocks
        # Two open calls should reuse the same browser
        await tool.execute(action="open", url="https://example.com")
        await tool.execute(action="open", url="https://example.org")
        # chromium.launch should only be called once
        pw.chromium.launch.assert_called_once()

    @pytest.mark.asyncio
    async def test_browser_reinit_after_close(self, tool_and_mocks):
        tool, page, pw = tool_and_mocks
        await tool.execute(action="open", url="https://example.com")
        await tool.execute(action="close")
        # After close, next call should re-init
        # Mark page as closed so _ensure_browser creates a new one
        page.is_closed.return_value = True
        await tool.execute(action="open", url="https://example.com")
        assert pw.chromium.launch.call_count == 2


# ---------------------------------------------------------------------------
# Custom timeout
# ---------------------------------------------------------------------------


class TestTimeout:
    @pytest.mark.asyncio
    async def test_custom_timeout(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        await tool.execute(action="click", selector="#btn", timeout_ms=5000)
        page.click.assert_called_once_with("#btn", timeout=5000)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_browser_in_web_group(self):
        from src.agent.tools.tool_profiles import TOOL_GROUPS

        assert "browser" in TOOL_GROUPS["group:web"]

    def test_browser_in_coding_profile(self):
        from src.agent.tools.tool_profiles import PROFILES

        assert "browser" in PROFILES["coding"]

    def test_registered_in_tool_sets(self, tmp_path: Path):
        from src.agent.tool_sets import register_standard_tools
        from src.agent.tools.registration import ToolRegistrationConfig
        from src.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        register_standard_tools(registry, ToolRegistrationConfig(workspace=tmp_path))
        # browser is a deferred tool (not in ALWAYS_ON_TOOLS), so check both pools.
        assert registry.has("browser")

    def test_not_registered_when_profile_excludes(self, tmp_path: Path):
        from src.agent.tool_sets import register_standard_tools
        from src.agent.tools.registration import ToolRegistrationConfig
        from src.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        register_standard_tools(
            registry, ToolRegistrationConfig(workspace=tmp_path, profile="minimal")
        )
        names = {t.name for t in registry._tools.values()}
        assert "browser" not in names


# ---------------------------------------------------------------------------
# hover
# ---------------------------------------------------------------------------


class TestHover:
    @pytest.mark.asyncio
    async def test_hover(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="hover", selector="#link"))
        assert result["status"] == "ok"
        assert result["selector"] == "#link"
        page.hover.assert_called_once_with("#link", timeout=30000)

    @pytest.mark.asyncio
    async def test_hover_missing_selector(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="hover"))
        assert "error" in result
        assert "selector is required" in result["error"]

    @pytest.mark.asyncio
    async def test_hover_custom_timeout(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        await tool.execute(action="hover", selector="#link", timeout_ms=5000)
        page.hover.assert_called_once_with("#link", timeout=5000)


# ---------------------------------------------------------------------------
# scroll
# ---------------------------------------------------------------------------


class TestScroll:
    @pytest.mark.asyncio
    async def test_scroll_down(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="scroll", direction="down"))
        assert result["status"] == "ok"
        assert result["direction"] == "down"
        assert result["pixels"] == 300
        page.evaluate.assert_called_once_with("window.scrollBy(0, 300)")

    @pytest.mark.asyncio
    async def test_scroll_up(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="scroll", direction="up", pixels=500))
        assert result["status"] == "ok"
        assert result["direction"] == "up"
        assert result["pixels"] == 500
        page.evaluate.assert_called_once_with("window.scrollBy(0, -500)")

    @pytest.mark.asyncio
    async def test_scroll_right(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="scroll", direction="right", pixels=200))
        assert result["status"] == "ok"
        page.evaluate.assert_called_once_with("window.scrollBy(200, 0)")

    @pytest.mark.asyncio
    async def test_scroll_left(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="scroll", direction="left", pixels=100))
        assert result["status"] == "ok"
        page.evaluate.assert_called_once_with("window.scrollBy(-100, 0)")

    @pytest.mark.asyncio
    async def test_scroll_default_direction(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="scroll"))
        assert result["status"] == "ok"
        assert result["direction"] == "down"

    @pytest.mark.asyncio
    async def test_scroll_invalid_direction(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="scroll", direction="diagonal"))
        assert "error" in result
        assert "Invalid direction" in result["error"]


# ---------------------------------------------------------------------------
# press
# ---------------------------------------------------------------------------


class TestPress:
    @pytest.mark.asyncio
    async def test_press(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="press", key="Enter"))
        assert result["status"] == "ok"
        assert result["key"] == "Enter"
        page.keyboard.press.assert_called_once_with("Enter")

    @pytest.mark.asyncio
    async def test_press_missing_key(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="press"))
        assert "error" in result
        assert "key is required" in result["error"]

    @pytest.mark.asyncio
    async def test_press_escape(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="press", key="Escape"))
        assert result["status"] == "ok"
        page.keyboard.press.assert_called_once_with("Escape")


# ---------------------------------------------------------------------------
# wait
# ---------------------------------------------------------------------------


class TestWait:
    @pytest.mark.asyncio
    async def test_wait_for_selector(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="wait", selector="#loading"))
        assert result["status"] == "ok"
        assert result["waited_for"] == "selector"
        assert result["selector"] == "#loading"
        page.wait_for_selector.assert_called_once_with("#loading", timeout=30000)

    @pytest.mark.asyncio
    async def test_wait_for_text(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="wait", text="Ready"))
        assert result["status"] == "ok"
        assert result["waited_for"] == "text"
        assert result["text"] == "Ready"
        page.wait_for_function.assert_called_once()

    @pytest.mark.asyncio
    async def test_wait_bare_timeout(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="wait", timeout_ms=2000))
        assert result["status"] == "ok"
        assert result["waited_for"] == "timeout"
        assert result["timeout_ms"] == 2000

    @pytest.mark.asyncio
    async def test_wait_default_timeout(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="wait"))
        assert result["status"] == "ok"
        assert result["waited_for"] == "timeout"
        assert result["timeout_ms"] == 1000

    @pytest.mark.asyncio
    async def test_wait_selector_priority_over_text(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="wait", selector="#el", text="fallback"))
        assert result["waited_for"] == "selector"
        page.wait_for_selector.assert_called_once()


# ---------------------------------------------------------------------------
# is_visible
# ---------------------------------------------------------------------------


class TestIsVisible:
    @pytest.mark.asyncio
    async def test_is_visible_true(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="is_visible", selector="#elem"))
        assert result["status"] == "ok"
        assert result["visible"] is True
        assert result["selector"] == "#elem"
        page.locator.assert_called_once_with("#elem")

    @pytest.mark.asyncio
    async def test_is_visible_false(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        page.locator.return_value.is_visible = AsyncMock(return_value=False)
        result = json.loads(await tool.execute(action="is_visible", selector="#hidden"))
        assert result["status"] == "ok"
        assert result["visible"] is False

    @pytest.mark.asyncio
    async def test_is_visible_missing_selector(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="is_visible"))
        assert "error" in result
        assert "selector is required" in result["error"]


# ---------------------------------------------------------------------------
# get_title
# ---------------------------------------------------------------------------


class TestGetTitle:
    @pytest.mark.asyncio
    async def test_get_title(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        page.title.return_value = "My Page"
        result = json.loads(await tool.execute(action="get_title"))
        assert result["status"] == "ok"
        assert result["title"] == "My Page"


# ---------------------------------------------------------------------------
# get_url
# ---------------------------------------------------------------------------


class TestGetUrl:
    @pytest.mark.asyncio
    async def test_get_url(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        page.url = "https://example.com/path"
        result = json.loads(await tool.execute(action="get_url"))
        assert result["status"] == "ok"
        assert result["url"] == "https://example.com/path"


# ---------------------------------------------------------------------------
# Config constructor
# ---------------------------------------------------------------------------


class TestConfig:
    def test_default_timeouts_without_config(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            assert tool._action_timeout == 30_000
            assert tool._nav_timeout == 60_000

    def test_config_overrides_timeouts(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            cfg = MagicMock()
            cfg.action_timeout_ms = 5000
            cfg.navigate_timeout_ms = 10000
            cfg.allowed_domains = ["example.com"]
            cfg.default_viewport_width = 1920
            cfg.default_viewport_height = 1080
            tool = BrowserTool(workspace=tmp_path, config=cfg)
            assert tool._action_timeout == 5000
            assert tool._nav_timeout == 10000
            assert tool._allowed_domains == ["example.com"]
            assert tool._viewport_width == 1920
            assert tool._viewport_height == 1080

    def test_readonly_default_false(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            assert tool._readonly is False


# ---------------------------------------------------------------------------
# Readonly mode
# ---------------------------------------------------------------------------


class TestReadonly:
    @pytest.fixture
    def readonly_tool_and_mocks(self, tmp_path: Path):
        """BrowserTool in readonly mode with mocked playwright."""
        page = _mock_page(url="https://example.com", title="Example")
        ctx = _mock_context(page)
        browser = _mock_browser(ctx)
        chromium = _mock_chromium(browser)
        pw = _mock_playwright_cm(chromium)

        with (
            patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True),
            patch("src.agent.tools.browser._async_playwright") as mock_ap,
        ):
            mock_ap.return_value.start = AsyncMock(return_value=pw)

            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path, readonly=True)
            yield tool, page, pw

    @pytest.mark.asyncio
    async def test_readonly_allows_safe_actions(self, readonly_tool_and_mocks):
        tool, page, _ = readonly_tool_and_mocks
        # get_text is in readonly set
        result = json.loads(await tool.execute(action="get_text"))
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readonly_allows_screenshot(self, readonly_tool_and_mocks):
        tool, page, _ = readonly_tool_and_mocks
        result = json.loads(await tool.execute(action="screenshot"))
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readonly_allows_get_title(self, readonly_tool_and_mocks):
        tool, page, _ = readonly_tool_and_mocks
        result = json.loads(await tool.execute(action="get_title"))
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readonly_allows_get_url(self, readonly_tool_and_mocks):
        tool, page, _ = readonly_tool_and_mocks
        result = json.loads(await tool.execute(action="get_url"))
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readonly_blocks_click(self, readonly_tool_and_mocks):
        tool, _, _ = readonly_tool_and_mocks
        result = json.loads(await tool.execute(action="click", selector="#btn"))
        assert "error" in result
        assert "readonly" in result["error"]

    @pytest.mark.asyncio
    async def test_readonly_blocks_type_text(self, readonly_tool_and_mocks):
        tool, _, _ = readonly_tool_and_mocks
        result = json.loads(await tool.execute(action="type_text", selector="#input", text="hi"))
        assert "error" in result
        assert "readonly" in result["error"]

    @pytest.mark.asyncio
    async def test_readonly_blocks_evaluate(self, readonly_tool_and_mocks):
        tool, _, _ = readonly_tool_and_mocks
        result = json.loads(await tool.execute(action="evaluate", script="alert(1)"))
        assert "error" in result
        assert "readonly" in result["error"]

    @pytest.mark.asyncio
    async def test_readonly_blocks_press(self, readonly_tool_and_mocks):
        tool, _, _ = readonly_tool_and_mocks
        result = json.loads(await tool.execute(action="press", key="Enter"))
        assert "error" in result
        assert "readonly" in result["error"]

    @pytest.mark.asyncio
    async def test_readonly_blocks_hover(self, readonly_tool_and_mocks):
        tool, _, _ = readonly_tool_and_mocks
        result = json.loads(await tool.execute(action="hover", selector="#link"))
        assert "error" in result
        assert "readonly" in result["error"]

    @pytest.mark.asyncio
    async def test_readonly_allows_scroll(self, readonly_tool_and_mocks):
        tool, page, _ = readonly_tool_and_mocks
        result = json.loads(await tool.execute(action="scroll", direction="down"))
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readonly_allows_wait(self, readonly_tool_and_mocks):
        tool, page, _ = readonly_tool_and_mocks
        result = json.loads(await tool.execute(action="wait"))
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readonly_allows_is_visible(self, readonly_tool_and_mocks):
        tool, page, _ = readonly_tool_and_mocks
        result = json.loads(await tool.execute(action="is_visible", selector="#elem"))
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readonly_allows_close(self, readonly_tool_and_mocks):
        tool, _, _ = readonly_tool_and_mocks
        result = json.loads(await tool.execute(action="close"))
        assert result["status"] == "closed"


# ---------------------------------------------------------------------------
# BrowserConfig schema
# ---------------------------------------------------------------------------


class TestBrowserConfig:
    def test_browser_config_defaults(self):
        from src.config.schema import BrowserConfig

        cfg = BrowserConfig()
        assert cfg.enabled is True
        assert cfg.allowed_domains == []
        assert cfg.default_viewport_width == 1280
        assert cfg.default_viewport_height == 720
        assert cfg.navigate_timeout_ms == 60_000
        assert cfg.action_timeout_ms == 30_000

    def test_browser_config_on_tools_config(self):
        from src.config.schema import ToolsConfig

        tools = ToolsConfig()
        assert hasattr(tools, "browser")
        assert tools.browser.enabled is True

    def test_browser_config_camel_case(self):
        from src.config.schema import BrowserConfig

        cfg = BrowserConfig.model_validate(
            {
                "allowedDomains": ["example.com"],
                "defaultViewportWidth": 1920,
                "navigateTimeoutMs": 90000,
                "actionTimeoutMs": 15000,
            }
        )
        assert cfg.allowed_domains == ["example.com"]
        assert cfg.default_viewport_width == 1920
        assert cfg.navigate_timeout_ms == 90000
        assert cfg.action_timeout_ms == 15000


# ---------------------------------------------------------------------------
# Helpers — URL validation mocks
# ---------------------------------------------------------------------------


def _make_tool_with_config(
    tmp_path: Path,
    allowed_domains: list[str] | None = None,
):
    """Create a BrowserTool with optional config for SSRF/allowlist tests."""
    from src.agent.tools.browser import BrowserTool

    cfg = MagicMock()
    cfg.allowed_domains = allowed_domains or []
    cfg.action_timeout_ms = 30_000
    cfg.navigate_timeout_ms = 60_000
    cfg.default_viewport_width = 1280
    cfg.default_viewport_height = 720
    return BrowserTool(workspace=tmp_path, config=cfg)


def _fake_getaddrinfo(ip: str):
    """Return an async function that mimics loop.getaddrinfo() returning *ip*."""

    async def _resolver(_host, _port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]

    return _resolver


def _fake_getaddrinfo_gaierror():
    """Return an async function that raises socket.gaierror."""

    async def _resolver(_host, _port):
        raise socket.gaierror("Name or service not known")

    return _resolver


# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------


class TestSSRF:
    @pytest.mark.asyncio
    async def test_blocks_loopback(self, tmp_path: Path):
        tool = _make_tool_with_config(tmp_path)
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo("127.0.0.1")
            result = await tool._validate_url("https://evil.com/steal")
        assert result is not None
        assert "private IP" in result
        assert "127.0.0.1" in result

    @pytest.mark.asyncio
    async def test_blocks_private_10_network(self, tmp_path: Path):
        tool = _make_tool_with_config(tmp_path)
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo("10.0.0.1")
            result = await tool._validate_url("https://internal.corp/admin")
        assert result is not None
        assert "private IP" in result
        assert "10.0.0.1" in result

    @pytest.mark.asyncio
    async def test_blocks_link_local(self, tmp_path: Path):
        tool = _make_tool_with_config(tmp_path)
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo("169.254.169.254")
            result = await tool._validate_url("https://metadata.google.internal/")
        assert result is not None
        assert "private IP" in result

    @pytest.mark.asyncio
    async def test_blocks_192_168(self, tmp_path: Path):
        tool = _make_tool_with_config(tmp_path)
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo("192.168.1.1")
            result = await tool._validate_url("https://router.local/")
        assert result is not None
        assert "private IP" in result

    @pytest.mark.asyncio
    async def test_allows_public_ip(self, tmp_path: Path):
        tool = _make_tool_with_config(tmp_path)
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo("93.184.216.34")
            result = await tool._validate_url("https://example.com/page")
        assert result is None

    @pytest.mark.asyncio
    async def test_blocks_unresolvable_host(self, tmp_path: Path):
        tool = _make_tool_with_config(tmp_path)
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo_gaierror()
            result = await tool._validate_url("https://nonexistent.invalid/")
        assert result is not None
        assert "cannot resolve" in result

    @pytest.mark.asyncio
    async def test_blocks_no_hostname(self, tmp_path: Path):
        tool = _make_tool_with_config(tmp_path)
        result = await tool._validate_url("not-a-url")
        assert result is not None
        assert "no hostname" in result


# ---------------------------------------------------------------------------
# Domain allowlist
# ---------------------------------------------------------------------------


class TestDomainAllowlist:
    @pytest.mark.asyncio
    async def test_blocks_unlisted_domain(self, tmp_path: Path):
        tool = _make_tool_with_config(tmp_path, allowed_domains=["safe.com", "trusted.org"])
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo("93.184.216.34")
            result = await tool._validate_url("https://evil.com/steal")
        assert result is not None
        assert "not in allowed_domains" in result

    @pytest.mark.asyncio
    async def test_allows_exact_domain(self, tmp_path: Path):
        tool = _make_tool_with_config(tmp_path, allowed_domains=["example.com"])
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo("93.184.216.34")
            result = await tool._validate_url("https://example.com/page")
        assert result is None

    @pytest.mark.asyncio
    async def test_allows_subdomain(self, tmp_path: Path):
        tool = _make_tool_with_config(tmp_path, allowed_domains=["example.com"])
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo("93.184.216.34")
            result = await tool._validate_url("https://sub.example.com/page")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_partial_match(self, tmp_path: Path):
        """notexample.com should NOT match allowed_domains=['example.com']."""
        tool = _make_tool_with_config(tmp_path, allowed_domains=["example.com"])
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo("93.184.216.34")
            result = await tool._validate_url("https://notexample.com/page")
        assert result is not None
        assert "not in allowed_domains" in result

    @pytest.mark.asyncio
    async def test_empty_allowlist_permits_all(self, tmp_path: Path):
        """Empty allowed_domains list = allow all (no filter)."""
        tool = _make_tool_with_config(tmp_path, allowed_domains=[])
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo("93.184.216.34")
            result = await tool._validate_url("https://anything.com/page")
        assert result is None


# ---------------------------------------------------------------------------
# Integration: _action_open blocks via _validate_url
# ---------------------------------------------------------------------------


class TestOpenSSRFIntegration:
    @pytest.mark.asyncio
    async def test_action_open_blocks_ssrf(self, tmp_path: Path):
        """_action_open returns error JSON when SSRF is detected."""
        tool = _make_tool_with_config(tmp_path)
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo("127.0.0.1")
            result_str = await tool._action_open("https://evil.com/steal", 30_000)
        result = json.loads(result_str)
        assert "error" in result
        assert "private IP" in result["error"]

    @pytest.mark.asyncio
    async def test_action_open_blocks_domain_allowlist(self, tmp_path: Path):
        """_action_open returns error JSON when domain is not in allowlist."""
        tool = _make_tool_with_config(tmp_path, allowed_domains=["safe.com"])
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo("93.184.216.34")
            result_str = await tool._action_open("https://evil.com/steal", 30_000)
        result = json.loads(result_str)
        assert "error" in result
        assert "not in allowed_domains" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_open_blocks_ssrf(self, tmp_path: Path):
        """Full execute() path blocks SSRF before touching playwright."""
        tool = _make_tool_with_config(tmp_path)
        with (
            patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True),
            patch("asyncio.get_running_loop") as mock_loop,
        ):
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo("10.0.0.5")
            result_str = await tool.execute(action="open", url="https://internal/admin")
        result = json.loads(result_str)
        assert "error" in result
        assert "private IP" in result["error"]


# ---------------------------------------------------------------------------
# Schema includes cookie/tab actions and params
# ---------------------------------------------------------------------------


class TestSchemaNewActions:
    def test_schema_includes_cookie_actions(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            actions = tool.parameters["properties"]["action"]["enum"]
            for a in ("cookie_get", "cookie_set", "cookie_clear"):
                assert a in actions

    def test_schema_includes_tab_actions(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            actions = tool.parameters["properties"]["action"]["enum"]
            for a in ("tab_list", "tab_new", "tab_switch", "tab_close"):
                assert a in actions

    def test_schema_includes_cookie_params(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            props = tool.parameters["properties"]
            for p in ("name", "value", "domain", "path", "httpOnly", "secure", "expires"):
                assert p in props

    def test_schema_includes_tab_index_param(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            props = tool.parameters["properties"]
            assert "index" in props


# ---------------------------------------------------------------------------
# cookie_get
# ---------------------------------------------------------------------------


class TestCookieGet:
    @pytest.mark.asyncio
    async def test_cookie_get_all(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        ctx.cookies.return_value = [
            {"name": "session", "value": "abc123", "domain": "example.com"},
            {"name": "lang", "value": "en", "domain": "example.com"},
        ]
        result = json.loads(await tool.execute(action="cookie_get"))
        assert result["status"] == "ok"
        assert len(result["cookies"]) == 2

    @pytest.mark.asyncio
    async def test_cookie_get_by_name(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        ctx.cookies.return_value = [
            {"name": "session", "value": "abc123", "domain": "example.com"},
            {"name": "lang", "value": "en", "domain": "example.com"},
        ]
        result = json.loads(await tool.execute(action="cookie_get", name="session"))
        assert result["status"] == "ok"
        assert len(result["cookies"]) == 1
        assert result["cookies"][0]["name"] == "session"

    @pytest.mark.asyncio
    async def test_cookie_get_name_not_found(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        ctx.cookies.return_value = [
            {"name": "lang", "value": "en", "domain": "example.com"},
        ]
        result = json.loads(await tool.execute(action="cookie_get", name="missing"))
        assert result["status"] == "ok"
        assert len(result["cookies"]) == 0

    @pytest.mark.asyncio
    async def test_cookie_get_empty(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        ctx.cookies.return_value = []
        result = json.loads(await tool.execute(action="cookie_get"))
        assert result["status"] == "ok"
        assert result["cookies"] == []


# ---------------------------------------------------------------------------
# cookie_set
# ---------------------------------------------------------------------------


class TestCookieSet:
    @pytest.mark.asyncio
    async def test_cookie_set_basic(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        result = json.loads(
            await tool.execute(
                action="cookie_set",
                name="session",
                value="abc123",
                domain="example.com",
            )
        )
        assert result["status"] == "ok"
        assert result["cookie"]["name"] == "session"
        assert result["cookie"]["value"] == "abc123"
        assert result["cookie"]["domain"] == "example.com"
        assert result["cookie"]["path"] == "/"
        assert result["cookie"]["httpOnly"] is False
        assert result["cookie"]["secure"] is False
        ctx.add_cookies.assert_called_once()

    @pytest.mark.asyncio
    async def test_cookie_set_with_options(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        result = json.loads(
            await tool.execute(
                action="cookie_set",
                name="auth",
                value="token123",
                domain=".example.com",
                path="/api",
                httpOnly=True,
                secure=True,
                expires=1700000000,
            )
        )
        assert result["status"] == "ok"
        cookie = result["cookie"]
        assert cookie["httpOnly"] is True
        assert cookie["secure"] is True
        assert cookie["expires"] == 1700000000
        assert cookie["path"] == "/api"

    @pytest.mark.asyncio
    async def test_cookie_set_missing_name(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        result = json.loads(
            await tool.execute(action="cookie_set", value="val", domain="example.com")
        )
        assert "error" in result
        assert "name is required" in result["error"]

    @pytest.mark.asyncio
    async def test_cookie_set_missing_value(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        result = json.loads(await tool.execute(action="cookie_set", name="n", domain="example.com"))
        assert "error" in result
        assert "value is required" in result["error"]

    @pytest.mark.asyncio
    async def test_cookie_set_missing_domain(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        result = json.loads(await tool.execute(action="cookie_set", name="n", value="v"))
        assert "error" in result
        assert "domain is required" in result["error"]


# ---------------------------------------------------------------------------
# cookie_clear
# ---------------------------------------------------------------------------


class TestCookieClear:
    @pytest.mark.asyncio
    async def test_cookie_clear(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        result = json.loads(await tool.execute(action="cookie_clear"))
        assert result["status"] == "ok"
        ctx.clear_cookies.assert_called_once()


# ---------------------------------------------------------------------------
# tab_list
# ---------------------------------------------------------------------------


class TestTabList:
    @pytest.mark.asyncio
    async def test_tab_list_single(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        # Ensure browser is started (creates first tab)
        await tool.execute(action="get_url")
        result = json.loads(await tool.execute(action="tab_list"))
        assert result["status"] == "ok"
        assert len(result["tabs"]) == 1
        assert result["tabs"][0]["active"] is True
        assert result["tabs"][0]["index"] == 0

    @pytest.mark.asyncio
    async def test_tab_list_multiple(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        # Start browser
        await tool.execute(action="get_url")
        # Create second tab
        page2 = _mock_page(url="https://other.com", title="Other")
        ctx.new_page.return_value = page2
        await tool.execute(action="tab_new")
        result = json.loads(await tool.execute(action="tab_list"))
        assert result["status"] == "ok"
        assert len(result["tabs"]) == 2
        # Second tab should be active (tab_new switches to it)
        assert result["tabs"][0]["active"] is False
        assert result["tabs"][1]["active"] is True


# ---------------------------------------------------------------------------
# tab_new
# ---------------------------------------------------------------------------


class TestTabNew:
    @pytest.mark.asyncio
    async def test_tab_new_blank(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        await tool.execute(action="get_url")  # init browser
        page2 = _mock_page(url="about:blank", title="")
        ctx.new_page.return_value = page2
        result = json.loads(await tool.execute(action="tab_new"))
        assert result["status"] == "ok"
        assert result["index"] == 1
        assert result["tab_count"] == 2

    @pytest.mark.asyncio
    async def test_tab_new_with_url(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        await tool.execute(action="get_url")  # init browser

        page2 = _mock_page(url="https://new.example.com", title="New")
        ctx.new_page.return_value = page2

        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _fake_getaddrinfo("93.184.216.34")
            result = json.loads(await tool.execute(action="tab_new", url="https://new.example.com"))
        assert result["status"] == "ok"
        page2.goto.assert_called_once()

    @pytest.mark.asyncio
    async def test_tab_new_switches_active(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        await tool.execute(action="get_url")  # init browser
        assert tool._active_page_index == 0

        page2 = _mock_page(url="about:blank", title="")
        ctx.new_page.return_value = page2
        await tool.execute(action="tab_new")
        assert tool._active_page_index == 1
        assert tool._page is page2


# ---------------------------------------------------------------------------
# tab_switch
# ---------------------------------------------------------------------------


class TestTabSwitch:
    @pytest.mark.asyncio
    async def test_tab_switch(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        await tool.execute(action="get_url")  # init browser
        page2 = _mock_page(url="https://tab2.com", title="Tab 2")
        ctx.new_page.return_value = page2
        await tool.execute(action="tab_new")
        assert tool._active_page_index == 1

        result = json.loads(await tool.execute(action="tab_switch", index=0))
        assert result["status"] == "ok"
        assert result["index"] == 0
        assert tool._active_page_index == 0
        assert tool._page is page
        page.bring_to_front.assert_called_once()

    @pytest.mark.asyncio
    async def test_tab_switch_missing_index(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        await tool.execute(action="get_url")
        result = json.loads(await tool.execute(action="tab_switch"))
        assert "error" in result
        assert "index is required" in result["error"]

    @pytest.mark.asyncio
    async def test_tab_switch_invalid_index(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        await tool.execute(action="get_url")
        result = json.loads(await tool.execute(action="tab_switch", index=5))
        assert "error" in result
        assert "Invalid tab index" in result["error"]

    @pytest.mark.asyncio
    async def test_tab_switch_negative_index(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        await tool.execute(action="get_url")
        result = json.loads(await tool.execute(action="tab_switch", index=-1))
        assert "error" in result
        assert "Invalid tab index" in result["error"]

    @pytest.mark.asyncio
    async def test_tab_switch_closed_tab(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        await tool.execute(action="get_url")
        page2 = _mock_page(url="https://tab2.com", title="Tab 2")
        ctx.new_page.return_value = page2
        await tool.execute(action="tab_new")
        # Mark first tab as closed
        page.is_closed.return_value = True
        result = json.loads(await tool.execute(action="tab_switch", index=0))
        assert "error" in result
        assert "closed" in result["error"]


# ---------------------------------------------------------------------------
# tab_close
# ---------------------------------------------------------------------------


class TestTabClose:
    @pytest.mark.asyncio
    async def test_tab_close_non_active(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        await tool.execute(action="get_url")
        page2 = _mock_page(url="https://tab2.com", title="Tab 2")
        ctx.new_page.return_value = page2
        await tool.execute(action="tab_new")
        # Close first tab (index 0); active is 1
        result = json.loads(await tool.execute(action="tab_close", index=0))
        assert result["status"] == "ok"
        assert result["closed_index"] == 0
        assert result["tab_count"] == 1
        page.close.assert_called_once()
        # Active page should now be page2 at index 0
        assert tool._active_page_index == 0
        assert tool._page is page2

    @pytest.mark.asyncio
    async def test_tab_close_active(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        await tool.execute(action="get_url")
        page2 = _mock_page(url="https://tab2.com", title="Tab 2")
        ctx.new_page.return_value = page2
        await tool.execute(action="tab_new")
        # Active is page2 at index 1; close it (default = active)
        result = json.loads(await tool.execute(action="tab_close"))
        assert result["status"] == "ok"
        assert result["closed_index"] == 1
        assert result["tab_count"] == 1
        page2.close.assert_called_once()
        assert tool._active_page_index == 0
        assert tool._page is page

    @pytest.mark.asyncio
    async def test_tab_close_last_tab_error(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        await tool.execute(action="get_url")
        result = json.loads(await tool.execute(action="tab_close"))
        assert "error" in result
        assert "Cannot close the last tab" in result["error"]

    @pytest.mark.asyncio
    async def test_tab_close_invalid_index(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        await tool.execute(action="get_url")
        result = json.loads(await tool.execute(action="tab_close", index=10))
        assert "error" in result
        assert "Invalid tab index" in result["error"]

    @pytest.mark.asyncio
    async def test_tab_close_adjusts_active_index(self, tool_ctx_and_mocks):
        """Closing a tab before the active one shifts active index down."""
        tool, page, ctx, _ = tool_ctx_and_mocks
        await tool.execute(action="get_url")
        page2 = _mock_page(url="https://tab2.com", title="Tab 2")
        ctx.new_page.return_value = page2
        await tool.execute(action="tab_new")
        page3 = _mock_page(url="https://tab3.com", title="Tab 3")
        ctx.new_page.return_value = page3
        await tool.execute(action="tab_new")
        # Active is tab3 at index 2
        assert tool._active_page_index == 2
        # Close tab at index 0
        result = json.loads(await tool.execute(action="tab_close", index=0))
        assert result["status"] == "ok"
        # Active should shift from 2 to 1
        assert tool._active_page_index == 1
        assert tool._page is page3
        assert result["tab_count"] == 2


# ---------------------------------------------------------------------------
# Readonly mode for cookie/tab actions
# ---------------------------------------------------------------------------


class TestReadonlyCookieTab:
    @pytest.fixture
    def readonly_ctx_tool(self, tmp_path: Path):
        page = _mock_page(url="https://example.com", title="Example")
        ctx = _mock_context(page)
        browser = _mock_browser(ctx)
        chromium = _mock_chromium(browser)
        pw = _mock_playwright_cm(chromium)

        with (
            patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True),
            patch("src.agent.tools.browser._async_playwright") as mock_ap,
        ):
            mock_ap.return_value.start = AsyncMock(return_value=pw)

            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path, readonly=True)
            yield tool, page, ctx

    @pytest.mark.asyncio
    async def test_readonly_allows_cookie_get(self, readonly_ctx_tool):
        tool, page, ctx = readonly_ctx_tool
        ctx.cookies.return_value = []
        result = json.loads(await tool.execute(action="cookie_get"))
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readonly_blocks_cookie_set(self, readonly_ctx_tool):
        tool, _, _ = readonly_ctx_tool
        result = json.loads(
            await tool.execute(action="cookie_set", name="a", value="b", domain="example.com")
        )
        assert "error" in result
        assert "readonly" in result["error"]

    @pytest.mark.asyncio
    async def test_readonly_blocks_cookie_clear(self, readonly_ctx_tool):
        tool, _, _ = readonly_ctx_tool
        result = json.loads(await tool.execute(action="cookie_clear"))
        assert "error" in result
        assert "readonly" in result["error"]

    @pytest.mark.asyncio
    async def test_readonly_allows_tab_list(self, readonly_ctx_tool):
        tool, page, ctx = readonly_ctx_tool
        result = json.loads(await tool.execute(action="tab_list"))
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readonly_blocks_tab_new(self, readonly_ctx_tool):
        tool, _, _ = readonly_ctx_tool
        result = json.loads(await tool.execute(action="tab_new"))
        assert "error" in result
        assert "readonly" in result["error"]

    @pytest.mark.asyncio
    async def test_readonly_blocks_tab_switch(self, readonly_ctx_tool):
        tool, _, _ = readonly_ctx_tool
        result = json.loads(await tool.execute(action="tab_switch", index=0))
        assert "error" in result
        assert "readonly" in result["error"]

    @pytest.mark.asyncio
    async def test_readonly_blocks_tab_close(self, readonly_ctx_tool):
        tool, _, _ = readonly_ctx_tool
        result = json.loads(await tool.execute(action="tab_close"))
        assert "error" in result
        assert "readonly" in result["error"]


# ---------------------------------------------------------------------------
# find (semantic locator)
# ---------------------------------------------------------------------------


class TestFind:
    @pytest.mark.asyncio
    async def test_find_by_role_click(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(
            await tool.execute(action="find", by="role", value="button", do="click")
        )
        assert result["status"] == "ok"
        assert result["by"] == "role"
        assert result["do"] == "click"
        page.get_by_role.assert_called_once_with("button")
        page.get_by_role.return_value.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_find_by_text_get_text(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(
            await tool.execute(action="find", by="text", value="Hello", do="get_text")
        )
        assert result["status"] == "ok"
        assert result["do"] == "get_text"
        assert "text" in result
        page.get_by_text.assert_called_once_with("Hello")
        page.get_by_text.return_value.inner_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_find_by_label_fill(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(
            await tool.execute(
                action="find", by="label", value="Email", do="fill", fill_value="a@b.com"
            )
        )
        assert result["status"] == "ok"
        assert result["do"] == "fill"
        assert result["fill_length"] == 7
        page.get_by_label.assert_called_once_with("Email")
        page.get_by_label.return_value.fill.assert_called_once_with("a@b.com", timeout=30000)

    @pytest.mark.asyncio
    async def test_find_by_placeholder(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(
            await tool.execute(action="find", by="placeholder", value="Search...", do="click")
        )
        assert result["status"] == "ok"
        page.get_by_placeholder.assert_called_once_with("Search...")

    @pytest.mark.asyncio
    async def test_find_by_testid(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(
            await tool.execute(action="find", by="testid", value="submit-btn", do="click")
        )
        assert result["status"] == "ok"
        page.get_by_test_id.assert_called_once_with("submit-btn")

    @pytest.mark.asyncio
    async def test_find_missing_by(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="find", value="x", do="click"))
        assert "error" in result
        assert "by is required" in result["error"]

    @pytest.mark.asyncio
    async def test_find_invalid_by(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="find", by="xpath", value="x", do="click"))
        assert "error" in result
        assert "Invalid by" in result["error"]

    @pytest.mark.asyncio
    async def test_find_missing_value(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="find", by="role", do="click"))
        assert "error" in result
        assert "value is required" in result["error"]

    @pytest.mark.asyncio
    async def test_find_missing_do(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="find", by="role", value="button"))
        assert "error" in result
        assert "do is required" in result["error"]

    @pytest.mark.asyncio
    async def test_find_invalid_do(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(
            await tool.execute(action="find", by="role", value="button", do="hover")
        )
        assert "error" in result
        assert "Invalid do" in result["error"]

    @pytest.mark.asyncio
    async def test_find_fill_missing_fill_value(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="find", by="label", value="Name", do="fill"))
        assert "error" in result
        assert "fill_value is required" in result["error"]


# ---------------------------------------------------------------------------
# snapshot (accessibility tree)
# ---------------------------------------------------------------------------


class TestSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_returns_tree(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="snapshot"))
        assert result["status"] == "ok"
        assert "tree" in result
        assert result["element_count"] > 0
        # Verify element references
        refs = [e["ref"] for e in result["tree"]]
        assert refs[0] == "@e0"
        assert refs[1] == "@e1"

    @pytest.mark.asyncio
    async def test_snapshot_interactive_only(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="snapshot", interactive_only=True))
        assert result["status"] == "ok"
        # heading is NOT interactive; button and textbox ARE
        for entry in result["tree"]:
            assert entry["interactive"] is True

    @pytest.mark.asyncio
    async def test_snapshot_marks_interactive(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="snapshot"))
        assert result["status"] == "ok"
        # Check that button entry is marked interactive
        interactives = [e for e in result["tree"] if e["interactive"]]
        non_interactives = [e for e in result["tree"] if not e["interactive"]]
        assert len(interactives) >= 2  # button, textbox
        assert len(non_interactives) >= 1  # heading

    @pytest.mark.asyncio
    async def test_snapshot_calls_aria_snapshot(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        await tool.execute(action="snapshot")
        page.locator.assert_called_with("body")
        page.locator.return_value.aria_snapshot.assert_called_once()


# ---------------------------------------------------------------------------
# set_viewport
# ---------------------------------------------------------------------------


class TestSetViewport:
    @pytest.mark.asyncio
    async def test_set_viewport(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="set_viewport", width=1920, height=1080))
        assert result["status"] == "ok"
        assert result["width"] == 1920
        assert result["height"] == 1080
        page.set_viewport_size.assert_called_once_with({"width": 1920, "height": 1080})

    @pytest.mark.asyncio
    async def test_set_viewport_updates_internal_state(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        await tool.execute(action="set_viewport", width=800, height=600)
        assert tool._viewport_width == 800
        assert tool._viewport_height == 600

    @pytest.mark.asyncio
    async def test_set_viewport_missing_width(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="set_viewport", height=600))
        assert "error" in result
        assert "width and height are required" in result["error"]

    @pytest.mark.asyncio
    async def test_set_viewport_missing_height(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="set_viewport", width=800))
        assert "error" in result
        assert "width and height are required" in result["error"]

    @pytest.mark.asyncio
    async def test_set_viewport_invalid_width(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="set_viewport", width=0, height=600))
        assert "error" in result
        assert "Invalid width" in result["error"]


# ---------------------------------------------------------------------------
# set_device
# ---------------------------------------------------------------------------


class TestSetDevice:
    @pytest.mark.asyncio
    async def test_set_device_iphone(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        # First open to init browser (so self._playwright is set)
        await tool.execute(action="get_url")
        result = json.loads(await tool.execute(action="set_device", device_name="iPhone 13"))
        assert result["status"] == "ok"
        assert result["device_name"] == "iPhone 13"
        assert result["viewport"] == {"width": 390, "height": 844}
        assert "iPhone" in result["user_agent"]
        page.set_viewport_size.assert_called_with({"width": 390, "height": 844})

    @pytest.mark.asyncio
    async def test_set_device_updates_internal_viewport(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        await tool.execute(action="get_url")
        await tool.execute(action="set_device", device_name="Pixel 5")
        assert tool._viewport_width == 393
        assert tool._viewport_height == 851

    @pytest.mark.asyncio
    async def test_set_device_unknown(self, tool_and_mocks):
        tool, page, _ = tool_and_mocks
        await tool.execute(action="get_url")
        result = json.loads(await tool.execute(action="set_device", device_name="Nokia 3310"))
        assert "error" in result
        assert "Unknown device" in result["error"]

    @pytest.mark.asyncio
    async def test_set_device_missing_name(self, tool_and_mocks):
        tool, _, _ = tool_and_mocks
        result = json.loads(await tool.execute(action="set_device"))
        assert "error" in result
        assert "device_name is required" in result["error"]

    @pytest.mark.asyncio
    async def test_set_device_applies_user_agent(self, tool_ctx_and_mocks):
        tool, page, ctx, _ = tool_ctx_and_mocks
        await tool.execute(action="get_url")
        await tool.execute(action="set_device", device_name="iPhone 13")
        ctx.set_extra_http_headers.assert_called_once()
        ua_header = ctx.set_extra_http_headers.call_args[0][0]["User-Agent"]
        assert "iPhone" in ua_header


# ---------------------------------------------------------------------------
# Schema includes new actions and params
# ---------------------------------------------------------------------------


class TestSchemaFindSnapshot:
    def test_schema_includes_find_snapshot_viewport_device(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            actions = tool.parameters["properties"]["action"]["enum"]
            for a in ("find", "snapshot", "set_viewport", "set_device"):
                assert a in actions

    def test_schema_includes_find_params(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            props = tool.parameters["properties"]
            for p in ("by", "do", "fill_value"):
                assert p in props

    def test_schema_includes_snapshot_params(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            props = tool.parameters["properties"]
            for p in ("interactive_only", "compact"):
                assert p in props

    def test_schema_includes_viewport_device_params(self, tmp_path: Path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            props = tool.parameters["properties"]
            for p in ("width", "height", "device_name"):
                assert p in props


# ---------------------------------------------------------------------------
# Readonly mode allows new actions
# ---------------------------------------------------------------------------


class TestReadonlyNewActions:
    @pytest.fixture
    def readonly_tool_mocks(self, tmp_path: Path):
        page = _mock_page(url="https://example.com", title="Example")
        ctx = _mock_context(page)
        browser = _mock_browser(ctx)
        chromium = _mock_chromium(browser)
        pw = _mock_playwright_cm(chromium)

        with (
            patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True),
            patch("src.agent.tools.browser._async_playwright") as mock_ap,
        ):
            mock_ap.return_value.start = AsyncMock(return_value=pw)

            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path, readonly=True)
            yield tool, page, pw

    @pytest.mark.asyncio
    async def test_readonly_allows_find_get_text(self, readonly_tool_mocks):
        tool, page, _ = readonly_tool_mocks
        result = json.loads(
            await tool.execute(action="find", by="role", value="button", do="get_text")
        )
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readonly_blocks_find_click(self, readonly_tool_mocks):
        tool, _, _ = readonly_tool_mocks
        result = json.loads(
            await tool.execute(action="find", by="role", value="button", do="click")
        )
        assert "error" in result
        assert "readonly" in result["error"]

    @pytest.mark.asyncio
    async def test_readonly_blocks_find_fill(self, readonly_tool_mocks):
        tool, _, _ = readonly_tool_mocks
        result = json.loads(
            await tool.execute(
                action="find", by="label", value="Email", do="fill", fill_value="x@y.com"
            )
        )
        assert "error" in result
        assert "readonly" in result["error"]

    @pytest.mark.asyncio
    async def test_readonly_allows_snapshot(self, readonly_tool_mocks):
        tool, page, _ = readonly_tool_mocks
        result = json.loads(await tool.execute(action="snapshot"))
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readonly_allows_set_viewport(self, readonly_tool_mocks):
        tool, page, _ = readonly_tool_mocks
        result = json.loads(await tool.execute(action="set_viewport", width=800, height=600))
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readonly_allows_set_device(self, readonly_tool_mocks):
        tool, page, _ = readonly_tool_mocks
        await tool.execute(action="get_url")  # init browser
        result = json.loads(await tool.execute(action="set_device", device_name="iPhone 13"))
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# Session persistence (save_state / load_state)
# ---------------------------------------------------------------------------


class TestSessionPersistence:
    @pytest.fixture
    def session_tool_mocks(self, tmp_path: Path):
        """BrowserTool + mocks with page at an http URL (needed for save_state)."""
        page = _mock_page(url="https://example.com", title="Example")
        page.evaluate = AsyncMock(return_value={"key1": "val1"})
        ctx = _mock_context(page)
        # cookies returns realistic data
        ctx.cookies = AsyncMock(
            return_value=[{"name": "sid", "value": "abc", "domain": ".example.com"}]
        )
        browser = _mock_browser(ctx)
        chromium = _mock_chromium(browser)
        pw = _mock_playwright_cm(chromium)

        with (
            patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True),
            patch("src.agent.tools.browser._async_playwright") as mock_ap,
        ):
            mock_ap.return_value.start = AsyncMock(return_value=pw)

            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            yield tool, page, ctx, pw

    @pytest.mark.asyncio
    async def test_save_state_creates_encrypted_file(self, session_tool_mocks, tmp_path):
        tool, page, ctx, _ = session_tool_mocks
        master_key = b"\x01" * 32

        with (patch("src.security.keychain.resolve_master_key", return_value=master_key),):
            # Init browser first
            await tool.execute(action="get_url")
            result = json.loads(await tool.execute(action="save_state", session_name="mysession"))

        assert result["status"] == "ok"
        assert result["session_name"] == "mysession"
        enc_path = tmp_path / "browser_state" / "mysession.json.enc"
        assert enc_path.exists()
        # File should be encrypted (not plain JSON)
        raw = enc_path.read_bytes()
        assert not raw.startswith(b"{")

    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(self, session_tool_mocks, tmp_path):
        tool, page, ctx, _ = session_tool_mocks
        master_key = b"\x01" * 32

        with (patch("src.security.keychain.resolve_master_key", return_value=master_key),):
            await tool.execute(action="get_url")  # init browser
            save_result = json.loads(
                await tool.execute(action="save_state", session_name="roundtrip")
            )
            assert save_result["status"] == "ok"

            load_result = json.loads(
                await tool.execute(action="load_state", session_name="roundtrip")
            )
            assert load_result["status"] == "ok"
            assert load_result["session_name"] == "roundtrip"

        # Verify cookies were restored
        ctx.add_cookies.assert_called()

    @pytest.mark.asyncio
    async def test_save_state_fails_no_master_key(self, session_tool_mocks):
        tool, page, ctx, _ = session_tool_mocks

        with (
            patch(
                "src.security.keychain.resolve_master_key",
                side_effect=RuntimeError("No key"),
            ),
        ):
            await tool.execute(action="get_url")  # init browser
            result = json.loads(await tool.execute(action="save_state", session_name="nokey"))

        assert "error" in result
        assert "Encryption failed" in result["error"]

    @pytest.mark.asyncio
    async def test_load_state_file_not_found(self, session_tool_mocks):
        tool, _, _, _ = session_tool_mocks
        result = json.loads(await tool.execute(action="load_state", session_name="nonexistent"))
        assert "error" in result
        assert "No saved state" in result["error"]

    @pytest.mark.asyncio
    async def test_save_state_requires_session_name(self, session_tool_mocks):
        tool, _, _, _ = session_tool_mocks
        result = json.loads(await tool.execute(action="save_state"))
        assert "error" in result
        assert "session_name is required" in result["error"]

    @pytest.mark.asyncio
    async def test_load_state_requires_session_name(self, session_tool_mocks):
        tool, _, _, _ = session_tool_mocks
        result = json.loads(await tool.execute(action="load_state"))
        assert "error" in result
        assert "session_name is required" in result["error"]

    @pytest.mark.asyncio
    async def test_readonly_blocks_save_state(self, tmp_path):
        page = _mock_page(url="https://example.com", title="Example")
        ctx = _mock_context(page)
        browser = _mock_browser(ctx)
        chromium = _mock_chromium(browser)
        pw = _mock_playwright_cm(chromium)

        with (
            patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True),
            patch("src.agent.tools.browser._async_playwright") as mock_ap,
        ):
            mock_ap.return_value.start = AsyncMock(return_value=pw)

            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path, readonly=True)
            result = json.loads(await tool.execute(action="save_state", session_name="test"))
            assert "error" in result
            assert "readonly" in result["error"]

    @pytest.mark.asyncio
    async def test_readonly_blocks_load_state(self, tmp_path):
        page = _mock_page(url="https://example.com", title="Example")
        ctx = _mock_context(page)
        browser = _mock_browser(ctx)
        chromium = _mock_chromium(browser)
        pw = _mock_playwright_cm(chromium)

        with (
            patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True),
            patch("src.agent.tools.browser._async_playwright") as mock_ap,
        ):
            mock_ap.return_value.start = AsyncMock(return_value=pw)

            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path, readonly=True)
            result = json.loads(await tool.execute(action="load_state", session_name="test"))
            assert "error" in result
            assert "readonly" in result["error"]


# ---------------------------------------------------------------------------
# Annotated screenshot
# ---------------------------------------------------------------------------


class TestAnnotatedScreenshot:
    @pytest.fixture
    def screenshot_tool_mocks(self, tmp_path: Path):
        page = _mock_page(url="https://example.com", title="Example")
        page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nfakeimage")
        locator_mock = _make_locator_mock()
        locator_mock.aria_snapshot = AsyncMock(
            return_value=(
                "- heading 'Main'\n"
                "- button 'Submit'\n"
                "- textbox 'Email'\n"
                "- link 'Home'\n"
                "- paragraph 'Some text'\n"
            )
        )
        page.locator = MagicMock(return_value=locator_mock)
        ctx = _mock_context(page)
        browser = _mock_browser(ctx)
        chromium = _mock_chromium(browser)
        pw = _mock_playwright_cm(chromium)

        with (
            patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True),
            patch("src.agent.tools.browser._async_playwright") as mock_ap,
        ):
            mock_ap.return_value.start = AsyncMock(return_value=pw)

            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            yield tool, page, pw

    @pytest.mark.asyncio
    async def test_annotated_screenshot_without_pillow(self, screenshot_tool_mocks, tmp_path):
        tool, page, _ = screenshot_tool_mocks

        # Mock ImportError for Pillow
        with patch.dict("sys.modules", {"PIL": None, "PIL.Image": None, "PIL.ImageDraw": None}):
            await tool.execute(action="get_url")  # init browser
            result = json.loads(await tool.execute(action="screenshot", annotate=True))

        assert result["status"] == "ok"
        assert result["annotated"] is False
        assert "warning" in result
        assert "Pillow" in result["warning"]
        # Should still return annotations list
        assert len(result["annotations"]) == 3  # button, textbox, link

    @pytest.mark.asyncio
    async def test_annotated_screenshot_with_pillow(self, screenshot_tool_mocks, tmp_path):
        tool, page, _ = screenshot_tool_mocks

        # Create a real minimal PNG-like image mock for Pillow
        mock_img = MagicMock()
        mock_img.height = 600
        mock_img.width = 800
        mock_img.save = MagicMock()
        mock_draw = MagicMock()
        mock_draw.textbbox = MagicMock(return_value=(10, 10, 20, 24))

        mock_image_mod = MagicMock()
        mock_image_mod.open = MagicMock(return_value=mock_img)
        mock_draw_mod = MagicMock()
        mock_draw_mod.Draw = MagicMock(return_value=mock_draw)
        mock_font_mod = MagicMock()
        mock_font_mod.truetype = MagicMock(side_effect=OSError("no font"))
        mock_font_mod.load_default = MagicMock(return_value=MagicMock())

        # Build a PIL mock that supports `from PIL import Image, ImageDraw, ImageFont`
        pil_mock = MagicMock()
        pil_mock.Image = mock_image_mod
        pil_mock.ImageDraw = mock_draw_mod
        pil_mock.ImageFont = mock_font_mod

        with (
            patch.dict(
                "sys.modules",
                {
                    "PIL": pil_mock,
                    "PIL.Image": mock_image_mod,
                    "PIL.ImageDraw": mock_draw_mod,
                    "PIL.ImageFont": mock_font_mod,
                },
            ),
        ):
            await tool.execute(action="get_url")  # init browser
            result = json.loads(await tool.execute(action="screenshot", annotate=True))

        assert result["status"] == "ok"
        assert result["annotated"] is True
        assert len(result["annotations"]) == 3
        # Labels should be numbered 0, 1, 2
        labels = [a["label"] for a in result["annotations"]]
        assert labels == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_non_annotated_screenshot_unchanged(self, screenshot_tool_mocks, tmp_path):
        tool, page, _ = screenshot_tool_mocks
        # Normal screenshot (annotate=False) should work as before
        await tool.execute(action="get_url")  # init browser
        result = json.loads(await tool.execute(action="screenshot"))
        assert result["status"] == "ok"
        assert "annotated" not in result  # not present when annotate=False
        page.screenshot.assert_called()

    @pytest.mark.asyncio
    async def test_schema_includes_session_and_annotate_params(self, tmp_path):
        with patch("src.agent.tools.browser._HAS_PLAYWRIGHT", True):
            from src.agent.tools.browser import BrowserTool

            tool = BrowserTool(workspace=tmp_path)
            props = tool.parameters["properties"]
            assert "session_name" in props
            assert "annotate" in props
