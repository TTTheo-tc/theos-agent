"""Tests for the review-round fixes (P0–P5)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.loop_core import run_tool_loop
from src.providers.base import LLMResponse, ToolCallRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockToolRegistry:
    """Minimal ToolRegistry mock that records calls."""

    def __init__(self, results: dict[str, str] | None = None):
        self._results = results or {}
        self.calls: list[tuple[str, dict]] = []

    def get_definitions(self):
        return []

    def get(self, name):
        return None

    async def execute(self, name, params, context=None):
        self.calls.append((name, params))
        return self._results.get(name, f"result of {name}")


def _make_tool_response(tool_calls: list[dict], content: str = "") -> LLMResponse:
    """Build an LLMResponse with tool_calls."""
    tcs = [
        ToolCallRequest(
            id=f"call_{i}",
            name=tc["name"],
            arguments=tc.get("args", {}),
        )
        for i, tc in enumerate(tool_calls)
    ]
    return LLMResponse(content=content, tool_calls=tcs, finish_reason="tool_calls")


# ---------------------------------------------------------------------------
# P0: feishu_delete removed — deletion is handled by instinct domain rule
# ---------------------------------------------------------------------------


class TestFeishuToolReviewFixes:
    def test_rate_limit_error_classified_correctly(self):
        from src.agent.tools.feishu import _classify_for_output

        error_type = _classify_for_output(Exception('response: {"code": 99991400}'))
        assert error_type == "rate_limited"

    def test_client_list_pages_accepts_space_name(self):
        from src.feishu.client import FeishuClient

        client = MagicMock(spec=FeishuClient)
        client.ensure_token = MagicMock()
        client._client = MagicMock()
        client._resolve_space_id = MagicMock(return_value="sp1")

        with patch(
            "src.feishu.client.api.list_nodes", return_value=[{"title": "Root"}]
        ) as mock_list:
            result = FeishuClient.list_pages(client, "Engineering")

        assert result == [{"title": "Root"}]
        mock_list.assert_called_once_with(client._client, "sp1")

    def test_search_wiki_uses_node_to_infer_space(self):
        from src.feishu.client import FeishuClient

        client = MagicMock(spec=FeishuClient)
        client.ensure_token = MagicMock()
        client._client = MagicMock()
        client.info_page = MagicMock(return_value=("", {"space_id": "sp1"}))
        client._resolve_space_id = MagicMock(return_value="sp1")

        with patch(
            "src.feishu.client.api.search_wiki", return_value=[{"title": "Spec"}]
        ) as mock_search:
            result = FeishuClient.search_wiki(
                client,
                "spec",
                node="https://example.feishu.cn/wiki/node123",
            )

        assert result == [{"title": "Spec"}]
        mock_search.assert_called_once_with(client._client, "spec", space_id="sp1")

    def test_feishu_tools_registered(self, tmp_path):
        from src.agent.tool_sets import register_standard_tools
        from src.agent.tools.registration import ToolRegistrationConfig
        from src.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        config = ToolRegistrationConfig(
            workspace=tmp_path,
            channel_env={"feishu_app_id": "id", "feishu_app_secret": "secret"},
            allowed_tools={"feishu_read"},
        )

        with patch("src.feishu.client.make_client"), patch("src.feishu.client.get_access_token"):
            register_standard_tools(registry, config)

        assert "feishu_read" in registry.tool_names


# ---------------------------------------------------------------------------
# P0: feishu_create uses edit_page instead of create_descendant_blocks
# ---------------------------------------------------------------------------


class TestFeishuCreatePath:
    """create_page must NOT call create_descendant_blocks directly."""

    def test_create_page_with_markdown_uses_client_side_converter(self):
        """When markdown is provided, create_page prefers client-side md2blocks + create_descendant_blocks."""
        from src.feishu.client import FeishuClient

        client = MagicMock(spec=FeishuClient)
        client.base_url = "https://example.feishu.cn"
        client.ensure_token = MagicMock()
        client.info_page = MagicMock(
            return_value=(
                "md content",
                {"space_id": "sp1", "node_token": "nt1", "obj_token": "doc1"},
            )
        )
        client._client = MagicMock()
        client._cache_get = MagicMock(return_value=None)
        client._cache_set = MagicMock()
        client._cache_del = MagicMock()

        with (
            patch("src.feishu.api_write.create_wiki_node") as mock_create_node,
            patch("src.feishu.api_write.create_descendant_blocks") as mock_create_desc,
        ):
            mock_create_node.return_value = {
                "node_token": "new_nt",
                "obj_token": "new_doc",
            }

            from src.feishu.client import FeishuClient as RealClient

            result = RealClient.create_page(
                client, "https://example.feishu.cn/wiki/ref1", "Test", markdown="# Hello"
            )

            # Client-side path: create_descendant_blocks SHOULD be called
            mock_create_desc.assert_called_once()
            assert result["content_written"] is True


# ---------------------------------------------------------------------------
# P1: Verification reminder injection after feishu write ops
# ---------------------------------------------------------------------------


class TestVerificationReminder:
    """After feishu_create/feishu_edit, a system reminder must be injected."""

    @pytest.mark.asyncio
    async def test_reminder_injected_after_feishu_edit(self):
        """When feishu_edit is called without feishu_read in the same batch,
        a verification reminder must appear in messages."""
        provider = AsyncMock()
        # First call: model calls feishu_edit
        # Second call: model responds with text (after seeing the reminder)
        provider.chat = AsyncMock(
            side_effect=[
                _make_tool_response(
                    [
                        {
                            "name": "feishu_edit",
                            "args": {"url": "u", "old_string": "a", "new_string": "b"},
                        }
                    ]
                ),
                LLMResponse(content="Done, let me verify.", finish_reason="stop"),
            ]
        )
        tools = MockToolRegistry({"feishu_edit": '{"success": true}'})

        content, used, messages, usage = await run_tool_loop(
            provider=provider,
            messages=[{"role": "user", "content": "edit the page"}],
            tools=tools,
            model="test",
            temperature=0.0,
            max_tokens=1024,
            max_iterations=10,
        )

        # Check that a system verification reminder was injected
        system_msgs = [
            m for m in messages if m["role"] == "user" and "[SYSTEM]" in m.get("content", "")
        ]
        assert len(system_msgs) >= 1
        assert "feishu_read" in system_msgs[0]["content"]
        assert "MUST" in system_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_no_reminder_when_verify_in_same_batch(self):
        """When feishu_edit and feishu_read are in the same batch, no reminder needed."""
        provider = AsyncMock()
        provider.chat = AsyncMock(
            side_effect=[
                _make_tool_response(
                    [
                        {
                            "name": "feishu_edit",
                            "args": {"url": "u", "old_string": "a", "new_string": "b"},
                        },
                        {"name": "feishu_read", "args": {"url": "u"}},
                    ]
                ),
                LLMResponse(content="Verified.", finish_reason="stop"),
            ]
        )
        tools = MockToolRegistry(
            {
                "feishu_edit": '{"success": true}',
                "feishu_read": "page content",
            }
        )

        content, used, messages, usage = await run_tool_loop(
            provider=provider,
            messages=[{"role": "user", "content": "edit and verify"}],
            tools=tools,
            model="test",
            temperature=0.0,
            max_tokens=1024,
            max_iterations=10,
        )

        system_msgs = [
            m for m in messages if m["role"] == "user" and "[SYSTEM]" in m.get("content", "")
        ]
        assert len(system_msgs) == 0

    @pytest.mark.asyncio
    async def test_create_with_only_list_still_needs_read(self):
        """feishu_create + feishu_list is still incomplete without feishu_read."""
        provider = AsyncMock()
        provider.chat = AsyncMock(
            side_effect=[
                _make_tool_response(
                    [
                        {"name": "feishu_create", "args": {"ref_url": "u", "title": "new page"}},
                        {"name": "feishu_list", "args": {"url": "u"}},
                    ]
                ),
                LLMResponse(content="I should keep verifying.", finish_reason="stop"),
            ]
        )
        tools = MockToolRegistry(
            {
                "feishu_create": '{"success": true}',
                "feishu_list": "child pages",
            }
        )

        _, _, messages, _ = await run_tool_loop(
            provider=provider,
            messages=[{"role": "user", "content": "create the page"}],
            tools=tools,
            model="test",
            temperature=0.0,
            max_tokens=1024,
            max_iterations=10,
        )

        system_msgs = [
            m for m in messages if m["role"] == "user" and "[SYSTEM]" in m.get("content", "")
        ]
        assert len(system_msgs) >= 1
        assert "feishu_create requires feishu_read" in system_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_edit_with_only_list_still_needs_read(self):
        """feishu_edit + feishu_list is still incomplete without feishu_read."""
        provider = AsyncMock()
        provider.chat = AsyncMock(
            side_effect=[
                _make_tool_response(
                    [
                        {
                            "name": "feishu_edit",
                            "args": {"url": "u", "old_string": "a", "new_string": "b"},
                        },
                        {"name": "feishu_list", "args": {"url": "u"}},
                    ]
                ),
                LLMResponse(content="I should keep verifying.", finish_reason="stop"),
            ]
        )
        tools = MockToolRegistry(
            {
                "feishu_edit": '{"success": true}',
                "feishu_list": "child pages",
            }
        )

        _, _, messages, _ = await run_tool_loop(
            provider=provider,
            messages=[{"role": "user", "content": "edit the page"}],
            tools=tools,
            model="test",
            temperature=0.0,
            max_tokens=1024,
            max_iterations=10,
        )

        system_msgs = [
            m for m in messages if m["role"] == "user" and "[SYSTEM]" in m.get("content", "")
        ]
        assert len(system_msgs) >= 1
        assert "feishu_edit requires feishu_read" in system_msgs[0]["content"]


# ---------------------------------------------------------------------------
# P3: Progress messages use tool_hint=False
# ---------------------------------------------------------------------------


class TestProgressMessages:
    """Long-running tool progress messages must NOT be filtered by tool_hint config."""

    @pytest.mark.asyncio
    async def test_progress_uses_tool_hint_false(self):
        """Progress callback must be called with tool_hint=False."""
        progress_calls = []

        async def on_progress(content, tool_hint=False):
            progress_calls.append({"content": content, "tool_hint": tool_hint})

        provider = AsyncMock()

        async def slow_chat(**kwargs):
            return _make_tool_response([{"name": "slow_tool"}])

        call_count = 0

        async def chat_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_tool_response([{"name": "slow_tool"}])
            return LLMResponse(content="Done", finish_reason="stop")

        provider.chat = AsyncMock(side_effect=chat_side_effect)

        # Make tool execution take >10s to trigger progress
        async def slow_execute(name, params, context=None):
            await asyncio.sleep(0.05)  # We'll mock _async_sleep instead
            return "done"

        tools = MockToolRegistry()
        tools.execute = slow_execute

        # Patch _async_sleep to return immediately so the progress fires fast
        with patch("src.agent.loop_core._async_sleep", new_callable=AsyncMock, return_value=None):
            content, used, messages, usage = await run_tool_loop(
                provider=provider,
                messages=[{"role": "user", "content": "do something slow"}],
                tools=tools,
                model="test",
                temperature=0.0,
                max_tokens=1024,
                max_iterations=10,
                on_progress=on_progress,
            )

        # Find progress messages (the ⏳ ones)
        progress_msgs = [c for c in progress_calls if "⏳" in c["content"]]
        assert len(progress_msgs) >= 1
        # Must use tool_hint=False
        for msg in progress_msgs:
            assert (
                msg["tool_hint"] is False
            ), f"Progress message should use tool_hint=False, got {msg}"


# ---------------------------------------------------------------------------
# P4: Startup self-check includes builtin skills
# ---------------------------------------------------------------------------


class TestStartupCheck:
    """_run_startup_checks must check both builtin and workspace skills."""

    def test_checks_builtin_skills_dir(self):
        import tempfile
        from pathlib import Path

        # Create a temp dir structure simulating builtin skills
        with tempfile.TemporaryDirectory() as tmpdir:
            builtin_dir = Path(tmpdir) / "builtin_skills"
            builtin_dir.mkdir()
            # Valid skill
            (builtin_dir / "good-skill").mkdir()
            (builtin_dir / "good-skill" / "SKILL.md").write_text("---\nname: good\n---\n")
            # Invalid skill (missing SKILL.md)
            (builtin_dir / "bad-skill").mkdir()

            ws_dir = Path(tmpdir) / "workspace"
            ws_dir.mkdir()
            (ws_dir / "skills").mkdir()

            config = MagicMock()
            config.workspace_path = ws_dir
            config.get_provider_keys = MagicMock(return_value={"key": "val"})
            config.tools.web.search.api_key = ""
            config.tools.web.search.tavily_api_key = ""
            config.channels.feishu.app_id = "id"
            config.channels.feishu.app_secret = "secret"

            from loguru import logger

            warnings_found = []
            handler_id = logger.add(
                lambda msg: warnings_found.append(str(msg)),
                level="WARNING",
                format="{message}",
            )
            try:
                with (
                    patch("src.agent.skills.BUILTIN_SKILLS_DIR", builtin_dir),
                    patch("src.security.secret_refs.resolve_secret_ref", return_value=""),
                ):
                    from src.cli.gateway_cmd import _run_startup_checks

                    _run_startup_checks(config)
            finally:
                logger.remove(handler_id)

            # Should warn about bad-skill missing SKILL.md
            found_bad_skill_warning = any("bad-skill" in w for w in warnings_found)
            assert (
                found_bad_skill_warning
            ), f"Expected warning about bad-skill, got: {warnings_found}"


# ---------------------------------------------------------------------------
# P5: _sanitize_markdown
# ---------------------------------------------------------------------------


class TestSanitizeMarkdown:
    """_sanitize_markdown must fix literal backslash-n sequences."""

    def test_literal_backslash_n_converted(self):
        from src.feishu.edit_arena import _sanitize_markdown

        # Input with literal \n (no real newlines)
        md = "# Title\\n\\nSome text\\n- item 1\\n- item 2"
        result = _sanitize_markdown(md)
        assert "\n" in result
        assert "\\n" not in result
        lines = result.split("\n")
        assert lines[0] == "# Title"

    def test_real_newlines_preserved(self):
        from src.feishu.edit_arena import _sanitize_markdown

        md = "# Title\n\nSome text\n- item 1\n- item 2"
        result = _sanitize_markdown(md)
        assert result == md  # Should be unchanged

    def test_mixed_content_preserved(self):
        from src.feishu.edit_arena import _sanitize_markdown

        # Has real newlines, literal \\n should be left alone
        md = "# Title\nParagraph with \\n in it\n"
        result = _sanitize_markdown(md)
        # Since it has real newlines, the heuristic should NOT replace \\n
        assert result == md
