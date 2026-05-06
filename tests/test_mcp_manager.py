"""Tests for MCPManager connection state bookkeeping."""

from __future__ import annotations

import pytest

from src.agent.mcp_manager import MCPManager


class _RuntimeErrorGroupStack:
    async def aclose(self) -> None:
        raise ExceptionGroup("close failed", [RuntimeError("cancel scope cleanup")])


class _RuntimeErrorStack:
    async def aclose(self) -> None:
        raise RuntimeError("cancel scope cleanup")


class _ValueErrorStack:
    async def aclose(self) -> None:
        raise ValueError("close failed")


class _MixedExceptionGroupStack:
    async def aclose(self) -> None:
        raise ExceptionGroup(
            "close failed",
            [RuntimeError("cancel scope cleanup"), ValueError("unexpected")],
        )


def _connected_manager() -> MCPManager:
    manager = MCPManager({"github": {"command": "mcp-github"}})
    manager._record_server_catalog(
        "github",
        transport="stdio",
        tools=[
            {
                "wrapper_name": "mcp_github_list_pull_requests",
                "tool_name": "list_pull_requests",
                "description": "List PRs",
            }
        ],
    )
    manager._connected = True
    manager._connecting = True
    return manager


async def test_close_resets_connected_catalog_entries() -> None:
    manager = _connected_manager()

    await manager.close()

    assert manager.connected is False
    assert manager._connecting is False
    snapshot = manager.catalog_snapshot()
    assert snapshot[0]["connected"] is False
    assert snapshot[0]["status"] == "closed"
    assert snapshot[0]["tool_count"] == 1
    assert snapshot[0]["tools"][0]["tool_name"] == "list_pull_requests"


async def test_close_resets_state_when_stack_close_fails() -> None:
    manager = _connected_manager()
    manager._stack = _ValueErrorStack()

    with pytest.raises(ValueError):
        await manager.close()

    assert manager.connected is False
    assert manager._connecting is False
    assert manager._stack is None
    assert manager.catalog_snapshot()[0]["status"] == "closed"


async def test_close_suppresses_runtime_error_groups() -> None:
    manager = _connected_manager()
    manager._stack = _RuntimeErrorGroupStack()

    await manager.close()

    assert manager.connected is False
    assert manager._stack is None
    assert manager.catalog_snapshot()[0]["status"] == "closed"


async def test_close_suppresses_runtime_errors() -> None:
    manager = _connected_manager()
    manager._stack = _RuntimeErrorStack()

    await manager.close()

    assert manager.connected is False
    assert manager._stack is None
    assert manager.catalog_snapshot()[0]["status"] == "closed"


async def test_close_reraises_unexpected_errors_inside_groups() -> None:
    manager = _connected_manager()
    manager._stack = _MixedExceptionGroupStack()

    with pytest.raises(ExceptionGroup):
        await manager.close()

    assert manager.connected is False
    assert manager._stack is None
    assert manager.catalog_snapshot()[0]["status"] == "closed"
