"""Browser automation tool using Playwright (async API).

Playwright is an optional dependency.  When it is not installed the tool
returns a clear error message instead of crashing.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
import urllib.parse
from contextlib import suppress
from pathlib import Path
from typing import Any

from src.agent.tools.base import Tool

# ---------------------------------------------------------------------------
# Optional playwright import
# ---------------------------------------------------------------------------

_HAS_PLAYWRIGHT = False
_async_playwright: Any = None  # will be the async_playwright callable if available

try:
    from playwright.async_api import async_playwright as _pw_factory

    _async_playwright = _pw_factory
    _HAS_PLAYWRIGHT = True
except ImportError:  # pragma: no cover — tested via mock
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_TEXT_CHARS = 50_000
_SCREENSHOT_DIR_NAME = "browser_screenshots"
_DEFAULT_TIMEOUT_MS = 30_000
_NAVIGATE_TIMEOUT_MS = 60_000

# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

_ACTIONS = (
    "open",
    "screenshot",
    "get_text",
    "click",
    "type_text",
    "evaluate",
    "close",
    # Navigation / info actions
    "hover",
    "scroll",
    "press",
    "wait",
    "is_visible",
    "get_title",
    "get_url",
    # Cookie actions
    "cookie_get",
    "cookie_set",
    "cookie_clear",
    # Tab actions
    "tab_list",
    "tab_new",
    "tab_switch",
    "tab_close",
    # Semantic locator & viewport
    "find",
    "snapshot",
    "set_viewport",
    "set_device",
    # Session persistence
    "save_state",
    "load_state",
)

# Actions that are safe in readonly mode (no mutation of page content)
_READONLY_ACTIONS = frozenset(
    {
        "open",
        "screenshot",
        "get_text",
        "get_title",
        "get_url",
        "is_visible",
        "wait",
        "scroll",
        "close",
        "cookie_get",
        "tab_list",
        "find",
        "snapshot",
        "set_viewport",
        "set_device",
    }
)


# ---------------------------------------------------------------------------
# BrowserTool
# ---------------------------------------------------------------------------


class BrowserTool(Tool):
    """Control a headless browser — open pages, click, type, screenshot, run JS."""

    def __init__(
        self,
        workspace: Path | str,
        config: Any = None,
        readonly: bool = False,
    ) -> None:
        self._workspace = Path(workspace)
        self._screenshot_dir = self._workspace / _SCREENSHOT_DIR_NAME
        self._config = config
        self._readonly = readonly
        self._allowed_domains: list[str] = getattr(config, "allowed_domains", []) if config else []
        self._action_timeout: int = (
            getattr(config, "action_timeout_ms", _DEFAULT_TIMEOUT_MS)
            if config
            else _DEFAULT_TIMEOUT_MS
        )
        self._nav_timeout: int = (
            getattr(config, "navigate_timeout_ms", _NAVIGATE_TIMEOUT_MS)
            if config
            else _NAVIGATE_TIMEOUT_MS
        )
        self._viewport_width: int = (
            getattr(config, "default_viewport_width", 1280) if config else 1280
        )
        self._viewport_height: int = (
            getattr(config, "default_viewport_height", 720) if config else 720
        )
        # Lazy-initialised browser state
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._pages: list[Any] = []
        self._active_page_index: int = 0
        self._lock = asyncio.Lock()

    # -- Tool metadata -------------------------------------------------------

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Control a headless browser. Actions: open (navigate to URL), "
            "screenshot (capture page image, annotate=true for labeled elements), "
            "get_text (extract visible text), "
            "click (click element by CSS selector), type_text (type into input), "
            "evaluate (run JavaScript), hover (hover over element), "
            "scroll (scroll page), press (press keyboard key), "
            "wait (wait for selector/text/timeout), is_visible (check element visibility), "
            "get_title (page title), get_url (current URL), "
            "cookie_get/cookie_set/cookie_clear (manage cookies), "
            "tab_list/tab_new/tab_switch/tab_close (manage tabs), "
            "find (semantic locator: by role/text/label/placeholder/testid), "
            "snapshot (accessibility tree), "
            "set_viewport (resize viewport), set_device (emulate device), "
            "save_state/load_state (persist/restore browser session, encrypted), "
            "close (shut down browser)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "Browser action to perform.",
                },
                "url": {
                    "type": "string",
                    "description": "URL to navigate to (for 'open').",
                },
                "selector": {
                    "type": "string",
                    "description": (
                        "CSS selector for the target element "
                        "(for 'click', 'type_text', 'hover', 'is_visible', 'wait')."
                    ),
                },
                "text": {
                    "type": "string",
                    "description": (
                        "Text to type (for 'type_text'), or text to wait for (for 'wait')."
                    ),
                },
                "script": {
                    "type": "string",
                    "description": "JavaScript code to execute (for 'evaluate').",
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture full scrollable page (for 'screenshot', default true).",
                },
                "timeout_ms": {
                    "type": "integer",
                    "minimum": 1000,
                    "maximum": 120000,
                    "description": "Timeout in milliseconds (default 30000).",
                },
                "key": {
                    "type": "string",
                    "description": "Key to press (for 'press'), e.g. 'Enter', 'Escape', 'Tab'.",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                    "description": "Scroll direction (for 'scroll', default 'down').",
                },
                "pixels": {
                    "type": "integer",
                    "description": "Number of pixels to scroll (for 'scroll', default 300).",
                },
                # Cookie parameters
                "name": {
                    "type": "string",
                    "description": (
                        "Cookie name (for 'cookie_get' to filter, " "or required for 'cookie_set')."
                    ),
                },
                "value": {
                    "type": "string",
                    "description": "Cookie value (for 'cookie_set').",
                },
                "domain": {
                    "type": "string",
                    "description": "Cookie domain (for 'cookie_set').",
                },
                "path": {
                    "type": "string",
                    "description": "Cookie path (for 'cookie_set', default '/').",
                },
                "httpOnly": {
                    "type": "boolean",
                    "description": "Cookie httpOnly flag (for 'cookie_set', default false).",
                },
                "secure": {
                    "type": "boolean",
                    "description": "Cookie secure flag (for 'cookie_set', default false).",
                },
                "expires": {
                    "type": "number",
                    "description": (
                        "Cookie expiration as Unix timestamp in seconds "
                        "(for 'cookie_set', optional)."
                    ),
                },
                # Tab parameters
                "index": {
                    "type": "integer",
                    "description": (
                        "Tab index (for 'tab_switch' required, for 'tab_close' optional — "
                        "defaults to active tab)."
                    ),
                },
                # Semantic locator parameters (for 'find')
                "by": {
                    "type": "string",
                    "enum": ["role", "text", "label", "placeholder", "testid"],
                    "description": (
                        "Locator strategy for 'find': role, text, label, " "placeholder, or testid."
                    ),
                },
                "do": {
                    "type": "string",
                    "enum": ["click", "fill", "get_text"],
                    "description": (
                        "Sub-action to perform on the found element "
                        "(for 'find'): click, fill, or get_text."
                    ),
                },
                "fill_value": {
                    "type": "string",
                    "description": "Text to fill (for 'find' with do='fill').",
                },
                # Snapshot parameters
                "interactive_only": {
                    "type": "boolean",
                    "description": (
                        "If true, snapshot includes only interactive elements "
                        "(for 'snapshot', default false)."
                    ),
                },
                "compact": {
                    "type": "boolean",
                    "description": (
                        "If true, strip whitespace-only text nodes from snapshot "
                        "(for 'snapshot', default true)."
                    ),
                },
                # Viewport / device parameters
                "width": {
                    "type": "integer",
                    "description": "Viewport width in pixels (for 'set_viewport').",
                },
                "height": {
                    "type": "integer",
                    "description": "Viewport height in pixels (for 'set_viewport').",
                },
                "device_name": {
                    "type": "string",
                    "description": (
                        "Playwright device name (for 'set_device'), " "e.g. 'iPhone 13', 'Pixel 5'."
                    ),
                },
                # Session persistence parameters
                "session_name": {
                    "type": "string",
                    "description": (
                        "Name for the browser session state " "(for 'save_state' and 'load_state')."
                    ),
                },
                # Annotated screenshot
                "annotate": {
                    "type": "boolean",
                    "description": (
                        "If true, overlay numbered labels on interactive elements "
                        "(for 'screenshot', default false). Requires Pillow."
                    ),
                },
            },
            "required": ["action"],
        }

    @property
    def risk_level(self) -> str:
        return "medium"

    # -- Lifecycle -----------------------------------------------------------

    async def _ensure_browser(self) -> Any:
        """Lazily start playwright + browser and return the active page."""
        async with self._lock:
            if self._page is not None and not self._page.is_closed():
                return self._page

            if not _HAS_PLAYWRIGHT:
                raise RuntimeError(
                    "playwright is not installed. "
                    "Run: pip install playwright && python -m playwright install chromium"
                )

            pw = await _async_playwright().start()
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={
                    "width": self._viewport_width,
                    "height": self._viewport_height,
                },
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            self._playwright = pw
            self._browser = browser
            self._context = context
            self._page = page
            self._pages = [page]
            self._active_page_index = 0
            return page

    async def _close_browser(self) -> str:
        """Shut down the browser and playwright."""
        async with self._lock:
            errors: list[str] = []
            if self._context is not None:
                try:
                    await self._context.close()
                except Exception as exc:
                    errors.append(f"context close: {exc}")
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception as exc:
                    errors.append(f"browser close: {exc}")
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception as exc:
                    errors.append(f"playwright stop: {exc}")
            self._page = None
            self._pages = []
            self._active_page_index = 0
            self._context = None
            self._browser = None
            self._playwright = None

        if errors:
            return json.dumps({"status": "closed_with_errors", "errors": errors})
        return json.dumps({"status": "closed"})

    # -- Execute dispatch ----------------------------------------------------

    async def execute(self, **kwargs: Any) -> str:
        action: str = kwargs.get("action", "")
        if not action:
            return json.dumps({"error": "action is required"})
        if action not in _ACTIONS:
            return json.dumps(
                {"error": f"Unknown action: {action!r}. Must be one of {list(_ACTIONS)}"}
            )

        # Readonly guard
        if self._readonly and action not in _READONLY_ACTIONS:
            return json.dumps({"error": f"Action '{action}' blocked: readonly mode"})

        if not _HAS_PLAYWRIGHT:
            return json.dumps(
                {
                    "error": (
                        "playwright is not installed. "
                        "Run: pip install playwright && python -m playwright install chromium"
                    )
                }
            )

        timeout_ms: int = kwargs.get("timeout_ms", self._action_timeout)

        try:
            if action == "close":
                return await self._close_browser()
            if action == "open":
                return await self._action_open(kwargs.get("url", ""), timeout_ms)
            if action == "screenshot":
                return await self._action_screenshot(
                    kwargs.get("full_page", True),
                    annotate=kwargs.get("annotate", False),
                )
            if action == "get_text":
                return await self._action_get_text()
            if action == "click":
                return await self._action_click(kwargs.get("selector", ""), timeout_ms)
            if action == "type_text":
                return await self._action_type_text(
                    kwargs.get("selector", ""), kwargs.get("text", ""), timeout_ms
                )
            if action == "evaluate":
                return await self._action_evaluate(kwargs.get("script", ""))
            # Navigation / info actions
            if action == "hover":
                return await self._action_hover(kwargs.get("selector", ""), timeout_ms)
            if action == "scroll":
                return await self._action_scroll(
                    kwargs.get("direction", "down"), kwargs.get("pixels", 300)
                )
            if action == "press":
                return await self._action_press(kwargs.get("key", ""))
            if action == "wait":
                return await self._action_wait(
                    kwargs.get("selector"),
                    kwargs.get("text"),
                    kwargs.get("timeout_ms"),
                )
            if action == "is_visible":
                return await self._action_is_visible(kwargs.get("selector", ""))
            if action == "get_title":
                return await self._action_get_title()
            if action == "get_url":
                return await self._action_get_url()
            # Cookie actions
            if action == "cookie_get":
                return await self._action_cookie_get(kwargs.get("name"))
            if action == "cookie_set":
                return await self._action_cookie_set(
                    name=kwargs.get("name", ""),
                    value=kwargs.get("value", ""),
                    domain=kwargs.get("domain", ""),
                    path=kwargs.get("path", "/"),
                    http_only=kwargs.get("httpOnly", False),
                    secure=kwargs.get("secure", False),
                    expires=kwargs.get("expires"),
                )
            if action == "cookie_clear":
                return await self._action_cookie_clear()
            # Tab actions
            if action == "tab_list":
                return await self._action_tab_list()
            if action == "tab_new":
                return await self._action_tab_new(kwargs.get("url"))
            if action == "tab_switch":
                return await self._action_tab_switch(kwargs.get("index"))
            if action == "tab_close":
                return await self._action_tab_close(kwargs.get("index"))
            # Semantic locator & viewport actions
            if action == "find":
                return await self._action_find(
                    by=kwargs.get("by", ""),
                    value=kwargs.get("value", ""),
                    do=kwargs.get("do", ""),
                    fill_value=kwargs.get("fill_value", ""),
                    timeout_ms=timeout_ms,
                )
            if action == "snapshot":
                return await self._action_snapshot(
                    interactive_only=kwargs.get("interactive_only", False),
                    compact=kwargs.get("compact", True),
                )
            if action == "set_viewport":
                return await self._action_set_viewport(
                    width=kwargs.get("width"),
                    height=kwargs.get("height"),
                )
            if action == "set_device":
                return await self._action_set_device(
                    device_name=kwargs.get("device_name", ""),
                )
            # Session persistence
            if action == "save_state":
                return await self._action_save_state(
                    session_name=kwargs.get("session_name", ""),
                )
            if action == "load_state":
                return await self._action_load_state(
                    session_name=kwargs.get("session_name", ""),
                )
        except Exception as exc:
            return json.dumps({"error": str(exc)})

        return json.dumps({"error": f"Unhandled action: {action}"})  # pragma: no cover

    # -- URL validation (SSRF + domain allowlist) ---------------------------

    async def _validate_url(self, url: str) -> str | None:
        """Return error message if URL is blocked, None if allowed."""
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""

        if not host:
            return "Blocked: no hostname in URL"

        # SSRF: block private IPs (async DNS)
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(host, None)
            for info in infos:
                ip = ipaddress.ip_address(info[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    return f"Blocked: {host} resolves to private IP {ip}"
        except socket.gaierror:
            return f"Blocked: cannot resolve {host}"

        # Domain allowlist (empty = allow all)
        if (
            self._allowed_domains
            and host not in self._allowed_domains
            and not any(host.endswith("." + d) for d in self._allowed_domains)
        ):
            return f"Blocked: {host} not in allowed_domains"

        return None

    # -- Individual actions --------------------------------------------------

    async def _action_open(self, url: str, timeout_ms: int) -> str:
        if not url:
            return json.dumps({"error": "url is required for 'open' action"})

        # SSRF + domain allowlist check
        block_reason = await self._validate_url(url)
        if block_reason:
            return json.dumps({"error": block_reason})

        page = await self._ensure_browser()
        resp = await page.goto(
            url, timeout=min(timeout_ms, self._nav_timeout), wait_until="domcontentloaded"
        )

        return json.dumps(
            {
                "status": "ok",
                "url": page.url,
                "title": await page.title(),
                "http_status": resp.status if resp else None,
            }
        )

    async def _action_screenshot(self, full_page: bool, annotate: bool = False) -> str:
        page = await self._ensure_browser()
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        filename = f"screenshot_{int(time.time() * 1000)}.png"
        path = self._screenshot_dir / filename

        if not annotate:
            await page.screenshot(path=str(path), full_page=full_page)
            return json.dumps(
                {
                    "status": "ok",
                    "path": str(path),
                    "url": page.url,
                    "full_page": full_page,
                }
            )

        # Annotated screenshot: capture image + a11y snapshot, overlay labels
        img_bytes = await page.screenshot(full_page=full_page)

        # Get interactive elements from a11y snapshot
        raw = await page.locator("body").aria_snapshot()
        annotations: list[dict[str, Any]] = []
        label_num = 0
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            lower = stripped.lstrip("- ").lower()
            is_interactive = any(
                lower.startswith(r)
                for r in (
                    "button",
                    "link",
                    "textbox",
                    "checkbox",
                    "radio",
                    "combobox",
                    "menuitem",
                    "tab",
                    "switch",
                    "slider",
                    "spinbutton",
                    "searchbox",
                )
            )
            if is_interactive:
                annotations.append({"label": label_num, "element": stripped})
                label_num += 1

        try:
            import io

            from PIL import Image, ImageDraw, ImageFont  # type: ignore[import-untyped]

            img = Image.open(io.BytesIO(img_bytes))
            draw = ImageDraw.Draw(img)
            # Try to use a small default font
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
            except (OSError, IOError):
                font = ImageFont.load_default()

            # Draw numbered labels at evenly spaced positions along the left margin
            for i, ann in enumerate(annotations):
                y = int((i + 0.5) * (img.height / max(len(annotations), 1)))
                x = 10
                label_text = str(ann["label"])
                # Draw background circle
                bbox = draw.textbbox((x, y), label_text, font=font)
                cx = (bbox[0] + bbox[2]) // 2
                cy = (bbox[1] + bbox[3]) // 2
                r = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) // 2 + 4
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill="red")
                draw.text((x, y), label_text, fill="white", font=font)

            img.save(str(path))
        except ImportError:
            # Pillow not installed — save raw screenshot, note in response
            path.write_bytes(img_bytes)
            return json.dumps(
                {
                    "status": "ok",
                    "path": str(path),
                    "url": page.url,
                    "full_page": full_page,
                    "annotated": False,
                    "annotations": annotations,
                    "warning": "Pillow not installed; labels not drawn",
                }
            )

        return json.dumps(
            {
                "status": "ok",
                "path": str(path),
                "url": page.url,
                "full_page": full_page,
                "annotated": True,
                "annotations": annotations,
            }
        )

    async def _action_get_text(self) -> str:
        page = await self._ensure_browser()
        text = await page.inner_text("body")
        truncated = False
        if len(text) > _MAX_TEXT_CHARS:
            text = text[:_MAX_TEXT_CHARS]
            truncated = True
        return json.dumps(
            {
                "status": "ok",
                "url": page.url,
                "text": text,
                "length": len(text),
                "truncated": truncated,
            },
            ensure_ascii=False,
        )

    async def _action_click(self, selector: str, timeout_ms: int) -> str:
        if not selector:
            return json.dumps({"error": "selector is required for 'click' action"})
        page = await self._ensure_browser()
        await page.click(selector, timeout=timeout_ms)
        return json.dumps(
            {
                "status": "ok",
                "selector": selector,
                "url": page.url,
            }
        )

    async def _action_type_text(self, selector: str, text: str, timeout_ms: int) -> str:
        if not selector:
            return json.dumps({"error": "selector is required for 'type_text' action"})
        if not text:
            return json.dumps({"error": "text is required for 'type_text' action"})
        page = await self._ensure_browser()
        await page.fill(selector, text, timeout=timeout_ms)
        return json.dumps(
            {
                "status": "ok",
                "selector": selector,
                "text_length": len(text),
                "url": page.url,
            }
        )

    async def _action_evaluate(self, script: str) -> str:
        if not script:
            return json.dumps({"error": "script is required for 'evaluate' action"})
        page = await self._ensure_browser()
        result = await page.evaluate(script)
        # Serialize the result — it may be any JSON-compatible value
        try:
            serialized = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            serialized = str(result)
        return json.dumps(
            {
                "status": "ok",
                "result": (
                    json.loads(serialized)
                    if isinstance(result, (dict, list, str, int, float, bool, type(None)))
                    else serialized
                ),
                "url": page.url,
            },
            ensure_ascii=False,
        )

    # -- Navigation / info actions -------------------------------------------

    async def _action_hover(self, selector: str, timeout_ms: int) -> str:
        if not selector:
            return json.dumps({"error": "selector is required for 'hover' action"})
        page = await self._ensure_browser()
        await page.hover(selector, timeout=timeout_ms)
        return json.dumps(
            {
                "status": "ok",
                "selector": selector,
                "url": page.url,
            }
        )

    async def _action_scroll(self, direction: str, pixels: int) -> str:
        page = await self._ensure_browser()
        dx, dy = 0, 0
        if direction == "down":
            dy = pixels
        elif direction == "up":
            dy = -pixels
        elif direction == "right":
            dx = pixels
        elif direction == "left":
            dx = -pixels
        else:
            return json.dumps(
                {"error": f"Invalid direction: {direction!r}. Use up/down/left/right."}
            )
        await page.evaluate(f"window.scrollBy({dx}, {dy})")
        return json.dumps(
            {
                "status": "ok",
                "direction": direction,
                "pixels": pixels,
                "url": page.url,
            }
        )

    async def _action_press(self, key: str) -> str:
        if not key:
            return json.dumps({"error": "key is required for 'press' action"})
        page = await self._ensure_browser()
        await page.keyboard.press(key)
        return json.dumps(
            {
                "status": "ok",
                "key": key,
                "url": page.url,
            }
        )

    async def _action_wait(
        self,
        selector: str | None,
        text: str | None,
        timeout_ms: int | None,
    ) -> str:
        page = await self._ensure_browser()
        effective_timeout = timeout_ms or self._action_timeout

        if selector:
            await page.wait_for_selector(selector, timeout=effective_timeout)
            return json.dumps(
                {
                    "status": "ok",
                    "waited_for": "selector",
                    "selector": selector,
                    "url": page.url,
                }
            )

        if text:
            # Wait until the text appears in the page body
            await page.wait_for_function(
                f"document.body?.innerText?.includes({json.dumps(text)})",
                timeout=effective_timeout,
            )
            return json.dumps(
                {
                    "status": "ok",
                    "waited_for": "text",
                    "text": text,
                    "url": page.url,
                }
            )

        # Bare wait — sleep for the given timeout (or a short default)
        sleep_ms = timeout_ms or 1000
        await asyncio.sleep(sleep_ms / 1000.0)
        return json.dumps(
            {
                "status": "ok",
                "waited_for": "timeout",
                "timeout_ms": sleep_ms,
                "url": page.url,
            }
        )

    async def _action_is_visible(self, selector: str) -> str:
        if not selector:
            return json.dumps({"error": "selector is required for 'is_visible' action"})
        page = await self._ensure_browser()
        visible = await page.locator(selector).is_visible()
        return json.dumps(
            {
                "status": "ok",
                "selector": selector,
                "visible": visible,
                "url": page.url,
            }
        )

    async def _action_get_title(self) -> str:
        page = await self._ensure_browser()
        title = await page.title()
        return json.dumps(
            {
                "status": "ok",
                "title": title,
                "url": page.url,
            }
        )

    async def _action_get_url(self) -> str:
        page = await self._ensure_browser()
        return json.dumps(
            {
                "status": "ok",
                "url": page.url,
            }
        )

    # -- Cookie actions ------------------------------------------------------

    async def _action_cookie_get(self, name: str | None = None) -> str:
        await self._ensure_browser()
        cookies = await self._context.cookies()
        if name:
            cookies = [c for c in cookies if c.get("name") == name]
        return json.dumps({"status": "ok", "cookies": cookies}, ensure_ascii=False)

    async def _action_cookie_set(
        self,
        name: str,
        value: str,
        domain: str,
        path: str = "/",
        http_only: bool = False,
        secure: bool = False,
        expires: float | None = None,
    ) -> str:
        if not name:
            return json.dumps({"error": "name is required for 'cookie_set' action"})
        if not value:
            return json.dumps({"error": "value is required for 'cookie_set' action"})
        if not domain:
            return json.dumps({"error": "domain is required for 'cookie_set' action"})

        await self._ensure_browser()
        cookie: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "httpOnly": http_only,
            "secure": secure,
        }
        if expires is not None:
            cookie["expires"] = expires
        await self._context.add_cookies([cookie])
        return json.dumps({"status": "ok", "cookie": cookie})

    async def _action_cookie_clear(self) -> str:
        await self._ensure_browser()
        await self._context.clear_cookies()
        return json.dumps({"status": "ok"})

    # -- Tab actions ---------------------------------------------------------

    async def _action_tab_list(self) -> str:
        await self._ensure_browser()
        tabs: list[dict[str, Any]] = []
        for i, page in enumerate(self._pages):
            title = ""
            if not page.is_closed():
                with suppress(Exception):
                    title = await page.title()
            tabs.append(
                {
                    "index": i,
                    "url": page.url if not page.is_closed() else "(closed)",
                    "title": title,
                    "active": i == self._active_page_index,
                }
            )
        return json.dumps({"status": "ok", "tabs": tabs}, ensure_ascii=False)

    async def _action_tab_new(self, url: str | None = None) -> str:
        await self._ensure_browser()
        new_page = await self._context.new_page()
        self._pages.append(new_page)
        self._active_page_index = len(self._pages) - 1
        self._page = new_page

        if url:
            block_reason = await self._validate_url(url)
            if block_reason:
                return json.dumps({"error": block_reason})
            await new_page.goto(url, timeout=self._nav_timeout, wait_until="domcontentloaded")

        return json.dumps(
            {
                "status": "ok",
                "index": self._active_page_index,
                "url": new_page.url,
                "tab_count": len(self._pages),
            }
        )

    async def _action_tab_switch(self, index: int | None) -> str:
        await self._ensure_browser()
        if index is None:
            return json.dumps({"error": "index is required for 'tab_switch' action"})
        if not isinstance(index, int) or index < 0 or index >= len(self._pages):
            return json.dumps(
                {"error": f"Invalid tab index: {index}. Valid range: 0\u2013{len(self._pages) - 1}"}
            )
        page = self._pages[index]
        if page.is_closed():
            return json.dumps({"error": f"Tab {index} is closed"})

        self._active_page_index = index
        self._page = page
        await page.bring_to_front()
        return json.dumps(
            {
                "status": "ok",
                "index": index,
                "url": page.url,
                "title": await page.title(),
            }
        )

    async def _action_tab_close(self, index: int | None = None) -> str:
        await self._ensure_browser()
        if index is None:
            index = self._active_page_index
        if not isinstance(index, int) or index < 0 or index >= len(self._pages):
            return json.dumps(
                {"error": f"Invalid tab index: {index}. Valid range: 0\u2013{len(self._pages) - 1}"}
            )

        if len(self._pages) <= 1:
            return json.dumps({"error": "Cannot close the last tab. Use 'close' to shut down."})

        page = self._pages.pop(index)
        if not page.is_closed():
            await page.close()

        # Adjust active index
        if index < self._active_page_index:
            self._active_page_index -= 1
        elif index == self._active_page_index:
            self._active_page_index = min(self._active_page_index, len(self._pages) - 1)
        self._page = self._pages[self._active_page_index]

        return json.dumps(
            {
                "status": "ok",
                "closed_index": index,
                "active_index": self._active_page_index,
                "tab_count": len(self._pages),
            }
        )

    # -- Semantic locator (find) ---------------------------------------------

    _BY_MAP = {
        "role": "get_by_role",
        "text": "get_by_text",
        "label": "get_by_label",
        "placeholder": "get_by_placeholder",
        "testid": "get_by_test_id",
    }

    async def _action_find(
        self,
        by: str,
        value: str,
        do: str,
        fill_value: str,
        timeout_ms: int,
    ) -> str:
        if not by:
            return json.dumps({"error": "by is required for 'find' action"})
        if by not in self._BY_MAP:
            return json.dumps({"error": f"Invalid by: {by!r}. Must be one of {list(self._BY_MAP)}"})
        if not value:
            return json.dumps({"error": "value is required for 'find' action"})
        if not do:
            return json.dumps({"error": "do is required for 'find' action"})
        if do not in ("click", "fill", "get_text"):
            return json.dumps({"error": f"Invalid do: {do!r}. Must be click, fill, or get_text"})

        # Readonly guard for mutating sub-actions
        if self._readonly and do in ("click", "fill"):
            return json.dumps({"error": f"find do='{do}' blocked: readonly mode"})

        page = await self._ensure_browser()
        method_name = self._BY_MAP[by]
        locator = getattr(page, method_name)(value)

        if do == "click":
            await locator.click(timeout=timeout_ms)
            return json.dumps(
                {"status": "ok", "by": by, "value": value, "do": "click", "url": page.url}
            )
        if do == "fill":
            if not fill_value:
                return json.dumps({"error": "fill_value is required when do='fill'"})
            await locator.fill(fill_value, timeout=timeout_ms)
            return json.dumps(
                {
                    "status": "ok",
                    "by": by,
                    "value": value,
                    "do": "fill",
                    "fill_length": len(fill_value),
                    "url": page.url,
                }
            )
        # do == "get_text"
        text = await locator.inner_text(timeout=timeout_ms)
        truncated = False
        if len(text) > _MAX_TEXT_CHARS:
            text = text[:_MAX_TEXT_CHARS]
            truncated = True
        return json.dumps(
            {
                "status": "ok",
                "by": by,
                "value": value,
                "do": "get_text",
                "text": text,
                "length": len(text),
                "truncated": truncated,
                "url": page.url,
            },
            ensure_ascii=False,
        )

    # -- Accessibility snapshot -----------------------------------------------

    async def _action_snapshot(
        self,
        interactive_only: bool = False,
        compact: bool = True,
    ) -> str:
        page = await self._ensure_browser()
        raw = await page.locator("body").aria_snapshot()

        # Parse into structured entries with element references
        entries: list[dict[str, Any]] = []
        ref_counter = 0
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if compact and not stripped.replace("-", "").strip():
                continue

            entry: dict[str, Any] = {"ref": f"@e{ref_counter}", "raw": stripped}
            ref_counter += 1

            # Detect interactive roles from ARIA snapshot format
            # Typical format: "- role 'name'" or "- role 'name': text"
            lower = stripped.lstrip("- ").lower()
            is_interactive = any(
                lower.startswith(r)
                for r in (
                    "button",
                    "link",
                    "textbox",
                    "checkbox",
                    "radio",
                    "combobox",
                    "menuitem",
                    "tab",
                    "switch",
                    "slider",
                    "spinbutton",
                    "searchbox",
                )
            )
            entry["interactive"] = is_interactive

            if interactive_only and not is_interactive:
                ref_counter -= 1  # undo increment
                continue
            entries.append(entry)

        return json.dumps(
            {
                "status": "ok",
                "url": page.url,
                "element_count": len(entries),
                "tree": entries,
            },
            ensure_ascii=False,
        )

    # -- Viewport / device emulation -----------------------------------------

    async def _action_set_viewport(
        self,
        width: int | None,
        height: int | None,
    ) -> str:
        if width is None or height is None:
            return json.dumps({"error": "width and height are required for 'set_viewport' action"})
        if not isinstance(width, int) or width < 1:
            return json.dumps({"error": f"Invalid width: {width}"})
        if not isinstance(height, int) or height < 1:
            return json.dumps({"error": f"Invalid height: {height}"})

        page = await self._ensure_browser()
        await page.set_viewport_size({"width": width, "height": height})
        self._viewport_width = width
        self._viewport_height = height
        return json.dumps(
            {
                "status": "ok",
                "width": width,
                "height": height,
                "url": page.url,
            }
        )

    async def _action_set_device(self, device_name: str) -> str:
        if not device_name:
            return json.dumps({"error": "device_name is required for 'set_device' action"})

        # Access device descriptors from the running playwright instance
        await self._ensure_browser()
        devices = getattr(self._playwright, "devices", None) or {}
        if device_name not in devices:
            available = sorted(devices.keys())[:20] if devices else []
            return json.dumps(
                {
                    "error": f"Unknown device: {device_name!r}",
                    "available_sample": available,
                }
            )

        device_cfg = devices[device_name]
        page = self._page

        # Apply viewport
        vp = device_cfg.get("viewport", {})
        if vp:
            await page.set_viewport_size(vp)
            self._viewport_width = vp.get("width", self._viewport_width)
            self._viewport_height = vp.get("height", self._viewport_height)

        # Apply user agent via context-level method if available,
        # otherwise fall back to JS override
        ua = device_cfg.get("user_agent", "")
        if ua:
            try:
                await self._context.set_extra_http_headers({"User-Agent": ua})
            except Exception:
                # Fallback: override navigator.userAgent via JS
                await page.evaluate(
                    f"Object.defineProperty(navigator, 'userAgent', "
                    f"{{get: () => {json.dumps(ua)}}})"
                )

        return json.dumps(
            {
                "status": "ok",
                "device_name": device_name,
                "viewport": vp,
                "user_agent": ua,
                "url": page.url,
            }
        )

    # -- Session persistence -------------------------------------------------

    async def _action_save_state(self, session_name: str) -> str:
        if not session_name:
            return json.dumps({"error": "session_name is required for 'save_state' action"})

        await self._ensure_browser()

        cookies = await self._context.cookies()
        origins: dict[str, dict[str, Any]] = {}
        tabs: list[dict[str, Any]] = []
        for page in self._pages:
            if page.is_closed():
                continue
            url = page.url
            if url.startswith(("http://", "https://")):
                split = urllib.parse.urlsplit(url)
                origin = f"{split.scheme}://{split.netloc}"
                if origin not in origins:
                    origins[origin] = {
                        "local_storage": await page.evaluate("() => ({...localStorage})")
                    }
                tabs.append(
                    {
                        "url": url,
                        "session_storage": await page.evaluate("() => ({...sessionStorage})"),
                    }
                )

        data = json.dumps({"cookies": cookies, "origins": origins, "tabs": tabs})

        # Encrypt — fail if no master key available
        try:
            from src.security.crypto import encrypt
            from src.security.keychain import resolve_master_key

            key = resolve_master_key()
            encrypted = encrypt(data.encode(), key)
        except Exception as exc:
            return json.dumps({"error": f"Encryption failed: {exc}"})

        state_dir = self._workspace / "browser_state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / f"{session_name}.json.enc").write_bytes(encrypted)

        return json.dumps({"status": "ok", "session_name": session_name})

    async def _action_load_state(self, session_name: str) -> str:
        if not session_name:
            return json.dumps({"error": "session_name is required for 'load_state' action"})

        path = self._workspace / "browser_state" / f"{session_name}.json.enc"
        if not path.exists():
            return json.dumps({"error": f"No saved state: {session_name}"})

        try:
            from src.security.crypto import decrypt
            from src.security.keychain import resolve_master_key

            key = resolve_master_key()
            data = json.loads(decrypt(path.read_bytes(), key))
        except Exception as exc:
            return json.dumps({"error": f"Decryption failed: {exc}"})

        await self._ensure_browser()

        # Restore cookies
        if data.get("cookies"):
            await self._context.add_cookies(data["cookies"])

        # Restore localStorage per origin
        for origin, storage in data.get("origins", {}).items():
            page = await self._context.new_page()
            try:
                await page.goto(origin, timeout=self._nav_timeout, wait_until="domcontentloaded")
                for k, v in storage.get("local_storage", {}).items():
                    await page.evaluate(f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)})")
            finally:
                await page.close()

        return json.dumps({"status": "ok", "session_name": session_name})
