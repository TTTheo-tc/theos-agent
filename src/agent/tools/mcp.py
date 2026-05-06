"""MCP client: connects to MCP servers and wraps their tools as native TheOS tools."""

import asyncio
from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Any

import httpx
from loguru import logger

from src.agent.tools.base import Tool
from src.agent.tools.registry import ToolRegistry
from src.security.credential_injector import (
    CredentialInjector,
    EncryptedSecretResolver,
    build_default_registry,
)
from src.security.secret_refs import resolve_mapping_refs


def _prepare_http_endpoint(
    url: str,
    headers: dict[str, str] | None,
    credential_injector: CredentialInjector,
) -> tuple[str, dict[str, str]]:
    """Prepare MCP HTTP transport inputs with secret ref resolution."""
    return credential_injector.prepare_url_and_headers(url, headers or {})


def _prepare_stdio_env(env: dict[str, str] | None) -> dict[str, str] | None:
    """Resolve secret references inside MCP stdio environment variables."""
    return resolve_mapping_refs(env)


class MCPToolWrapper(Tool):
    """Wraps a single MCP server tool as a TheOS Tool."""

    def __init__(self, session: Any, server_name: str, tool_def: Any, tool_timeout: int = 30) -> None:
        self._session = session
        self._original_name = tool_def.name
        self._name = f"mcp_{server_name}_{tool_def.name}"
        self._description = tool_def.description or tool_def.name
        self._parameters = tool_def.inputSchema or {"type": "object", "properties": {}}
        self._tool_timeout = tool_timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> str:
        from mcp import types

        try:
            result = await asyncio.wait_for(
                self._session.call_tool(self._original_name, arguments=kwargs),
                timeout=self._tool_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("MCP tool '{}' timed out after {}s", self._name, self._tool_timeout)
            return f"(MCP tool call timed out after {self._tool_timeout}s)"
        parts = []
        for block in result.content:
            if isinstance(block, types.TextContent):
                parts.append(block.text)
            else:
                parts.append(str(block))
        return "\n".join(parts) or "(no output)"


async def connect_mcp_servers(
    mcp_servers: dict[str, Any],
    registry: ToolRegistry,
    stack: AsyncExitStack,
    *,
    on_server_catalog: Callable[..., None] | None = None,
    on_server_failure: Callable[..., None] | None = None,
) -> None:
    """Connect to configured MCP servers and register their tools."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    credential_injector = CredentialInjector(
        build_default_registry(),
        EncryptedSecretResolver(),
    )

    for name, cfg in mcp_servers.items():
        transport = "http" if cfg.url else "stdio" if cfg.command else "unknown"
        try:
            if cfg.command:
                params = StdioServerParameters(
                    command=cfg.command,
                    args=cfg.args,
                    env=_prepare_stdio_env(cfg.env) or None,
                )
                read, write = await stack.enter_async_context(stdio_client(params))
            elif cfg.url:
                from mcp.client.streamable_http import streamable_http_client

                prepared_url, prepared_headers = _prepare_http_endpoint(
                    cfg.url,
                    cfg.headers or {},
                    credential_injector,
                )
                # Always provide an explicit httpx client so MCP HTTP transport does not
                # inherit httpx's default 5s timeout and preempt the higher-level tool timeout.
                http_client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=prepared_headers or None,
                        follow_redirects=True,
                        timeout=None,
                    )
                )
                read, write, _ = await stack.enter_async_context(
                    streamable_http_client(prepared_url, http_client=http_client)
                )
            else:
                logger.warning("MCP server '{}': no command or url configured, skipping", name)
                continue

            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            tools = await session.list_tools()
            catalog_tools = []
            for tool_def in tools.tools:
                wrapper = MCPToolWrapper(session, name, tool_def, tool_timeout=cfg.tool_timeout)
                registry.register(wrapper)
                catalog_tools.append(
                    {
                        "server": name,
                        "tool_name": tool_def.name,
                        "wrapper_name": wrapper.name,
                        "description": tool_def.description or tool_def.name,
                        "parameters": tool_def.inputSchema or {"type": "object", "properties": {}},
                    }
                )
                logger.debug("MCP: registered tool '{}' from server '{}'", wrapper.name, name)

            if on_server_catalog is not None:
                on_server_catalog(name, transport=transport, tools=catalog_tools)
            logger.info("MCP server '{}': connected, {} tools registered", name, len(tools.tools))
        except Exception:
            if on_server_failure is not None:
                on_server_failure(name, transport=transport, error="connection failed")
            logger.opt(exception=True).warning("MCP server '{}': failed to connect", name)
