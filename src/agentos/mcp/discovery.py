"""MCP tool discovery and registration into AgentOS ToolRegistry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

from agentos.mcp.client import MCPClient
from agentos.mcp.types import MCPServerConfig, MCPToolDef
from agentos.tools.registry import ToolRegistry
from agentos.tools.schema_sanitize import sanitize_input_schema
from agentos.tools.types import ToolSpec

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ActiveMCPClient:
    """Tracked MCP client with the owner that controls its lifecycle."""

    owner: str
    server_name: str
    transport: str
    client: MCPClient
    registered_tools: tuple[str, ...] = ()
    # Tool definitions keyed by registered name (e.g. "mcp_lookup"),
    # kept for re-registration when a colliding owner disconnects.
    tool_defs: dict[str, MCPToolDef] = field(default_factory=dict)
    tool_timeout: float = 10.0

    async def close(self) -> None:
        await self.client.close()


# Module-level registry to keep clients alive for tool handlers.
_active_clients: list[ActiveMCPClient] = []


def active_clients_snapshot() -> tuple[ActiveMCPClient, ...]:
    """Return active MCP clients without exposing mutable runtime state."""
    return tuple(_active_clients)


async def close_active_clients(owner: str | None = None) -> int:
    """Close active MCP clients, optionally scoped to one owner/server name."""
    remaining: list[ActiveMCPClient] = []
    closing: list[ActiveMCPClient] = []
    for entry in _active_clients:
        if owner is None or entry.owner == owner or entry.server_name == owner:
            closing.append(entry)
        else:
            remaining.append(entry)
    _active_clients[:] = remaining

    closed = 0
    for entry in closing:
        try:
            await entry.close()
            closed += 1
        except Exception:
            pass
    return closed


async def disconnect_and_unregister(owner: str, registry: ToolRegistry) -> int:
    """Close one MCP server and remove the tools registered by that server.

    When multiple active servers share a same-named tool, the handler
    from the most recently registered surviving server is re-registered
    so it continues to point at a live client.
    """
    snapshot = active_clients_snapshot()
    leaving = [
        entry
        for entry in snapshot
        if entry.owner == owner or entry.server_name == owner
    ]
    remaining = [
        entry
        for entry in snapshot
        if not (entry.owner == owner or entry.server_name == owner)
    ]

    # Collect all tool names owned by still-active servers.
    still_alive: dict[str, ActiveMCPClient] = {}
    for entry in remaining:
        for name in entry.registered_tools:
            # The last entry in iteration wins (most recent registration
            # is authoritative — it overwrote earlier entries).
            still_alive[name] = entry

    for entry in leaving:
        for name in entry.registered_tools:
            survivor = still_alive.get(name)
            if survivor is not None:
                # Re-register using the surviving server's client so the
                # handler does not point at the closing server.
                tool_def = survivor.tool_defs.get(name)
                if tool_def is not None:
                    _make_tool_handler(
                        survivor.client,
                        name.removeprefix("mcp_"),
                        tool_def,
                        registry,
                        survivor.tool_timeout,
                    )
            else:
                registry.unregister(name)
    return await close_active_clients(owner)


def create_client(config: MCPServerConfig) -> MCPClient:
    """Factory: create the appropriate MCPClient for the given transport."""
    if config.transport == "stdio":
        from agentos.mcp.stdio import MCPStdioClient

        return MCPStdioClient(config)
    elif config.transport == "sse":
        from agentos.mcp.sse import MCPSSEClient

        return MCPSSEClient(config)
    elif config.transport == "streamable_http":
        from agentos.mcp.streamable_http import MCPStreamableHTTPClient

        return MCPStreamableHTTPClient(config)
    else:
        raise ValueError(f"Unknown MCP transport: {config.transport!r}")


def _make_tool_handler(
    client: MCPClient,
    tool_name: str,
    tool_def: MCPToolDef,
    registry: ToolRegistry,
    timeout_seconds: float,
) -> None:
    """Register a single MCP tool into the registry with an mcp_ prefix."""
    # The server's schema goes out verbatim in every provider request, so it is
    # normalized once here rather than per turn. A shape one backend tolerates
    # can make another reject the whole call, tools and all.
    schema, fixes = sanitize_input_schema(tool_def.input_schema)
    if fixes:
        log.info("mcp.schema_sanitized", tool=tool_name, fixes=fixes)
    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    spec = ToolSpec(
        name=f"mcp_{tool_name}",
        description=tool_def.description,
        parameters=properties,
        required=required,
    )

    async def handler(**kwargs: Any) -> str:
        try:
            result = await asyncio.wait_for(
                client.call_tool(tool_name, kwargs),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            return f"MCP tool '{tool_name}' timed out after {timeout_seconds}s"
        return result.content

    registry.register(spec, handler)


async def discover_and_register(
    config: MCPServerConfig,
    registry: ToolRegistry,
    *,
    owner: str | None = None,
) -> list[str]:
    """Connect to MCP server, list tools, register each as a AgentOS tool.

    Returns list of registered tool names.
    The client is kept alive in module-level _active_clients so tool handlers can use it.
    """
    client = create_client(config)
    entry: ActiveMCPClient | None = None

    registered: list[str] = []
    tool_defs: dict[str, MCPToolDef] = {}
    try:
        await client.connect()
        tools = await client.list_tools()
        for t in tools:
            _make_tool_handler(
                client,
                t.name,
                t,
                registry,
                timeout_seconds=config.tool_timeout_seconds,
            )
            registered_name = f"mcp_{t.name}"
            registered.append(registered_name)
            tool_defs[registered_name] = t
        entry = ActiveMCPClient(
            owner=owner or config.name,
            server_name=config.name,
            transport=config.transport,
            client=client,
            registered_tools=tuple(registered),
            tool_defs=tool_defs,
            tool_timeout=config.tool_timeout_seconds,
        )
        _active_clients.append(entry)
    except BaseException:
        if entry is not None:
            try:
                _active_clients.remove(entry)
            except ValueError:
                pass
        await client.close()
        raise
    return registered
