"""Tests for dream sandbox tool policy."""

from __future__ import annotations

from pathlib import Path

from src.dream.sandbox.tool_policy import DreamToolPolicy


class TestToolPolicyAllowlist:
    def test_allowed_tool_passes(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"))
        result = policy.check_tool("read_file", {"path": "/tmp/dream/foo.txt"})
        assert result.allowed

    def test_blocked_tool_rejected(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"))
        result = policy.check_tool("message", {"content": "hello"})
        assert not result.allowed
        assert "blocked" in result.reason

    def test_unknown_tool_rejected(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"))
        result = policy.check_tool("deploy_nuke", {})
        assert not result.allowed
        assert "not in dream allowlist" in result.reason

    def test_all_allowed_tools_pass(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"))
        for tool in DreamToolPolicy.ALLOWED_TOOLS:
            result = policy.check_tool(tool, {})
            assert result.allowed, f"{tool} should be allowed"

    def test_all_blocked_tools_fail(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"))
        for tool in DreamToolPolicy.BLOCKED_TOOLS:
            result = policy.check_tool(tool, {})
            assert not result.allowed, f"{tool} should be blocked"


class TestPathGuard:
    def test_rejects_traversal_in_bash(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"))
        result = policy.check_tool("bash", {"command": "cat ../../etc/passwd"})
        assert not result.allowed
        assert ".." in result.reason

    def test_allows_normal_bash(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"))
        result = policy.check_tool("bash", {"command": "ls /tmp/dream"})
        assert result.allowed

    def test_rejects_absolute_path_escape_in_bash(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"))
        result = policy.check_tool("bash", {"command": "cat /etc/passwd"})
        assert not result.allowed
        assert "outside dream sandbox" in result.reason

    def test_allows_safe_system_paths_in_bash(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"))
        result = policy.check_tool("bash", {"command": "/usr/bin/env python3"})
        assert result.allowed

    def test_rejects_read_file_outside_sandbox(self, tmp_path):
        sandbox = tmp_path / "dream"
        sandbox.mkdir()
        policy = DreamToolPolicy(sandbox_root=sandbox)
        result = policy.check_tool("read_file", {"file_path": "/etc/passwd"})
        assert not result.allowed
        assert "outside dream sandbox" in result.reason

    def test_allows_read_file_inside_sandbox(self, tmp_path):
        sandbox = tmp_path / "dream"
        sandbox.mkdir()
        policy = DreamToolPolicy(sandbox_root=sandbox)
        result = policy.check_tool("read_file", {"file_path": str(sandbox / "test.txt")})
        assert result.allowed

    def test_rejects_grep_outside_sandbox(self, tmp_path):
        sandbox = tmp_path / "dream"
        sandbox.mkdir()
        policy = DreamToolPolicy(sandbox_root=sandbox)
        result = policy.check_tool("grep", {"path": "/etc/"})
        assert not result.allowed

    def test_rejects_python_traversal(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"))
        result = policy.check_tool("python", {"code": "open('../../etc/passwd').read()"})
        assert not result.allowed

    def test_rejects_prefix_collision(self, tmp_path):
        """Sandbox /tmp/dream must NOT allow /tmp/dream-escape/."""
        sandbox = tmp_path / "dream"
        sandbox.mkdir()
        escape = tmp_path / "dream-escape"
        escape.mkdir()
        policy = DreamToolPolicy(sandbox_root=sandbox)
        result = policy.check_tool("read_file", {"file_path": str(escape / "evil.txt")})
        assert not result.allowed
        assert "outside dream sandbox" in result.reason

    def test_rejects_prefix_collision_in_bash(self, tmp_path):
        sandbox = tmp_path / "dream"
        sandbox.mkdir()
        escape = tmp_path / "dream-escape"
        escape.mkdir()
        policy = DreamToolPolicy(sandbox_root=sandbox)
        result = policy.check_tool("bash", {"command": f"cat {escape}/evil.txt"})
        assert not result.allowed

    def test_rejects_read_file_legacy_path_param(self, tmp_path):
        """read_file(path=...) must also be guarded, not just file_path."""
        sandbox = tmp_path / "dream"
        sandbox.mkdir()
        policy = DreamToolPolicy(sandbox_root=sandbox)
        result = policy.check_tool("read_file", {"path": "/etc/passwd"})
        assert not result.allowed
        assert "outside dream sandbox" in result.reason

    def test_allows_read_file_legacy_path_inside_sandbox(self, tmp_path):
        sandbox = tmp_path / "dream"
        sandbox.mkdir()
        policy = DreamToolPolicy(sandbox_root=sandbox)
        result = policy.check_tool("read_file", {"path": str(sandbox / "ok.txt")})
        assert result.allowed


class TestCostGuard:
    def test_blocks_after_budget_exceeded(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"), budget_usd=1.0)
        policy.record_call("bash", {}, cost_usd=1.5)
        result = policy.check_tool("bash", {"command": "ls"})
        assert not result.allowed
        assert "Budget" in result.reason

    def test_allows_within_budget(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"), budget_usd=10.0)
        policy.record_call("bash", {}, cost_usd=5.0)
        result = policy.check_tool("bash", {"command": "ls"})
        assert result.allowed


class TestLoopGuard:
    def test_blocks_repeated_calls(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"), loop_threshold=3)
        params = {"command": "echo hello"}
        for _ in range(3):
            policy.record_call("bash", params)
        result = policy.check_tool("bash", params)
        assert not result.allowed
        assert "Loop" in result.reason

    def test_allows_different_calls(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"), loop_threshold=3)
        for i in range(5):
            params = {"command": f"echo {i}"}
            policy.record_call("bash", params)
            result = policy.check_tool("bash", params)
            # Each unique call only recorded once, so still below threshold
            assert result.allowed


class TestWebRateGuard:
    def test_blocks_excess_web_queries(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"), max_web_queries=2)
        policy.record_call("web_search", {"query": "a"})
        policy.record_call("web_search", {"query": "b"})
        result = policy.check_tool("web_search", {"query": "c"})
        assert not result.allowed

    def test_allows_within_limit(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"), max_web_queries=5)
        policy.record_call("web_search", {"query": "a"})
        result = policy.check_tool("web_search", {"query": "b"})
        assert result.allowed

    def test_web_fetch_also_counted(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"), max_web_queries=1)
        policy.record_call("web_fetch", {"url": "http://example.com"})
        result = policy.check_tool("web_fetch", {"url": "http://other.com"})
        assert not result.allowed


class TestStopReason:
    def test_initial_stop_reason_empty(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"))
        assert policy.stop_reason == ""

    def test_budget_sets_stop_reason(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"), budget_usd=1.0)
        policy.record_call("bash", {}, cost_usd=2.0)
        result = policy.check_tool("bash", {"command": "ls"})
        assert not result.allowed
        assert policy.stop_reason == "budget_exceeded"

    def test_loop_sets_stop_reason(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"), loop_threshold=3)
        params = {"command": "echo hello"}
        for _ in range(3):
            policy.record_call("bash", params)
        result = policy.check_tool("bash", params)
        assert not result.allowed
        assert policy.stop_reason == "loop_guard_stopped"

    def test_network_sets_stop_reason(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"), max_web_queries=2)
        policy.record_call("web_search", {"query": "a"})
        policy.record_call("web_search", {"query": "b"})
        result = policy.check_tool("web_search", {"query": "c"})
        assert not result.allowed
        assert policy.stop_reason == "network_limit"

    def test_path_sets_stop_reason(self, tmp_path):
        sandbox = tmp_path / "dream"
        sandbox.mkdir()
        policy = DreamToolPolicy(sandbox_root=sandbox)
        result = policy.check_tool("read_file", {"file_path": "/etc/passwd"})
        assert not result.allowed
        assert policy.stop_reason == "path_violation"

    def test_first_rejection_wins(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"), budget_usd=0.0, max_web_queries=0)
        # First rejection: budget_exceeded (bash triggers cost guard first)
        result = policy.check_tool("bash", {"command": "ls"})
        assert not result.allowed
        assert policy.stop_reason == "budget_exceeded"
        # Second rejection: network_limit would fire, but stop_reason stays
        result = policy.check_tool("web_search", {"query": "a"})
        assert not result.allowed
        assert policy.stop_reason == "budget_exceeded"


class TestStats:
    def test_stats_tracking(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"), budget_usd=10.0)
        policy.record_call("bash", {}, cost_usd=2.5)
        policy.record_call("web_search", {"query": "test"}, cost_usd=0.1)
        stats = policy.stats
        assert stats["total_calls"] == 2
        assert stats["cost_used"] == 2.6
        assert stats["web_queries"] == 1
        assert stats["budget_usd"] == 10.0
        assert stats["budget_remaining"] == 7.4

    def test_initial_stats(self):
        policy = DreamToolPolicy(sandbox_root=Path("/tmp/dream"))
        stats = policy.stats
        assert stats["total_calls"] == 0
        assert stats["cost_used"] == 0.0
        assert stats["web_queries"] == 0
