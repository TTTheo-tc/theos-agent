"""MCP (Model Context Protocol) server connection manager.

Extracted from AgentLoop to reduce loop.py complexity.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from src.agent.tools.registry import ToolRegistry


class MCPManager:
    """Manages lazy connection to MCP servers."""

    def __init__(self, mcp_servers: dict | None = None):
        self._servers = mcp_servers or {}
        self._stack: AsyncExitStack | None = None
        self._connected = False
        self._connecting = False
        self._catalog: dict[str, dict[str, Any]] = {
            name: self._build_server_entry(name, cfg) for name, cfg in self._servers.items()
        }

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def server_count(self) -> int:
        return len(self._servers)

    def catalog_snapshot(self) -> list[dict[str, Any]]:
        """Return configured server metadata plus any discovered MCP tools."""
        return [self._catalog[name].copy() for name in sorted(self._catalog)]

    async def connect(self, registry: "ToolRegistry") -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._connected or self._connecting or not self._servers:
            return
        self._connecting = True
        from src.agent.tools.mcp import connect_mcp_servers

        try:
            self._stack = AsyncExitStack()
            await self._stack.__aenter__()
            await connect_mcp_servers(
                self._servers,
                registry,
                self._stack,
                on_server_catalog=self._record_server_catalog,
                on_server_failure=self._record_server_failure,
            )
            self._connected = True
        except Exception:
            logger.opt(exception=True).warning(
                "Failed to connect MCP servers (will retry next message)"
            )
            if self._stack:
                try:
                    await self._stack.aclose()
                except Exception:
                    pass
                self._stack = None
        finally:
            self._connecting = False

    async def close(self) -> None:
        """Close MCP connections."""
        if self._stack:
            try:
                await self._stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._stack = None

    def _record_server_catalog(
        self,
        server_name: str,
        *,
        transport: str,
        tools: list[dict[str, Any]],
    ) -> None:
        """Record successful MCP discovery for one server."""
        entry = self._catalog.get(server_name, {"server": server_name})
        entry.update(
            {
                "server": server_name,
                "transport": transport,
                "connected": True,
                "status": "connected",
                "error": None,
                "tool_count": len(tools),
                "tools": tools,
            }
        )
        self._catalog[server_name] = entry

    def _record_server_failure(
        self,
        server_name: str,
        *,
        transport: str,
        error: str | None = None,
    ) -> None:
        """Record MCP connection failure while preserving configured metadata."""
        entry = self._catalog.get(server_name, {"server": server_name})
        entry.update(
            {
                "server": server_name,
                "transport": transport,
                "connected": False,
                "status": "failed",
                "error": error,
                "tool_count": len(entry.get("tools", [])),
                "tools": entry.get("tools", []),
            }
        )
        self._catalog[server_name] = entry

    def _build_server_entry(self, server_name: str, cfg: Any) -> dict[str, Any]:
        """Build baseline configured metadata before any MCP connection happens."""
        return {
            "server": server_name,
            "transport": self._transport_for(cfg),
            "connected": False,
            "status": "configured",
            "error": None,
            "tool_count": 0,
            "tools": [],
        }

    def _transport_for(self, cfg: Any) -> str:
        """Infer transport name from MCP config."""
        if getattr(cfg, "url", ""):
            return "http"
        if getattr(cfg, "command", ""):
            return "stdio"
        return "unknown"
