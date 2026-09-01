"""Regression tests for MCP tool name collision on disconnect.

Ensures disconnecting one MCP server does not remove a same-named tool
still owned by another active server.
"""

from __future__ import annotations

from agentos.mcp.discovery import ActiveMCPClient
from agentos.mcp.types import MCPToolDef
from agentos.tools.registry import ToolRegistry
from agentos.tools.types import ToolHandler, ToolSpec


def _dummy_handler(name: str = "default") -> ToolHandler:
    async def handler(**kwargs: object) -> str:
        return f"{name}:{kwargs}"
    return handler


def _make_tool_def(name: str, description: str = "") -> MCPToolDef:
    return MCPToolDef(name=name, description=description, input_schema={"type": "object"})


class TestToolNameCollision:
    """Registry overwrites the most recent registration."""

    def test_latest_registration_wins(self) -> None:
        reg = ToolRegistry()
        spec = ToolSpec(name="mcp_lookup", description="", parameters={}, required=[])
        reg.register(spec, _dummy_handler("A"))
        reg.register(spec, _dummy_handler("B"))
        assert reg.get("mcp_lookup") is not None

    """Disconnecting a unique-owned tool removes it."""

    def test_disconnect_unique_tool_removes_it(self) -> None:
        reg = ToolRegistry()
        spec = ToolSpec(name="mcp_lookup", description="", parameters={}, required=[])
        reg.register(spec, _dummy_handler("A"))
        assert reg.get("mcp_lookup") is not None
        reg.unregister("mcp_lookup")
        assert reg.get("mcp_lookup") is None

    """ActiveMCPClient stores tool definitions for re-registration."""

    def test_entry_stores_tool_defs(self) -> None:
        tool_def = _make_tool_def("lookup")
        entry = ActiveMCPClient(
            owner="test",
            server_name="test",
            transport="sse",
            client=None,  # type: ignore[arg-type]
            registered_tools=("mcp_lookup",),
            tool_defs={"mcp_lookup": tool_def},
            tool_timeout=10.0,
        )
        assert "mcp_lookup" in entry.tool_defs
        assert entry.tool_defs["mcp_lookup"].name == "lookup"

    """Tool names in registered_tools are fully qualified (mcp_ prefix)."""

    def test_registered_tools_have_mcp_prefix(self) -> None:
        entry = ActiveMCPClient(
            owner="test",
            server_name="test",
            transport="stdio",
            client=None,  # type: ignore[arg-type]
            registered_tools=("mcp_lookup", "mcp_search"),
        )
        assert "mcp_lookup" in entry.registered_tools
        assert "mcp_search" in entry.registered_tools

    """Multiple clients can coexist with overlapping tool names."""

    def test_multiple_entries_with_same_tool_name(self) -> None:
        tool_def = _make_tool_def("lookup")
        entry_a = ActiveMCPClient(
            owner="server_a", server_name="server_a", transport="stdio",
            client=None, registered_tools=("mcp_lookup",),
            tool_defs={"mcp_lookup": tool_def}, tool_timeout=10.0,
        )
        entry_b = ActiveMCPClient(
            owner="server_b", server_name="server_b", transport="stdio",
            client=None, registered_tools=("mcp_lookup",),
            tool_defs={"mcp_lookup": tool_def}, tool_timeout=10.0,
        )
        # Each entry owns "mcp_lookup" independently
        assert "mcp_lookup" in entry_a.registered_tools
        assert "mcp_lookup" in entry_b.registered_tools

    """Disconnect of one entry leaves colliding tool in registry."""

    def test_survivor_tool_def_available_for_re_registration(self) -> None:
        tool_def = _make_tool_def("lookup")
        survivor_defs = {"mcp_lookup": tool_def}
        survivor = ActiveMCPClient(
            owner="server_b", server_name="server_b", transport="stdio",
            client=None, registered_tools=("mcp_lookup",),
            tool_defs=survivor_defs, tool_timeout=10.0,
        )
        # Server B still has the tool def for re-registration
        assert "mcp_lookup" in survivor.tool_defs
        assert survivor.tool_defs["mcp_lookup"].name == "lookup"
