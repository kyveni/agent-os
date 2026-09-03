"""Tests for MCP bridge parameter upper-clamping.

Ensures that resource-exhaustion vectors (max_events, timeout_ms,
history limits, session list limits) are bounded above.
"""

from __future__ import annotations

import asyncio

from agentos.mcp_server.bridge import (
    _MAX_EVENTS_UPPER,
    _MAX_HISTORY_LIMIT_UPPER,
    _MAX_SESSIONS_LIMIT_UPPER,
    _MAX_TIMEOUT_MS_UPPER,
    AgentOSMCPBridge,
)
from tests.test_mcp_server.test_bridge import FakeGatewayClient


class TestBridgeUpperClamps:
    # conversations_list upper-clamps limit.

    async def test_conversations_list_clamps_limit(self) -> None:
        client = FakeGatewayClient()
        bridge = AgentOSMCPBridge(gateway_client_factory=lambda: client)

        await bridge.conversations_list(limit=_MAX_SESSIONS_LIMIT_UPPER * 10)

        call = client.calls[0]
        assert call[0] == "sessions.list"
        assert call[1].get("limit") == _MAX_SESSIONS_LIMIT_UPPER

    async def test_conversations_list_accepts_small_limit(self) -> None:
        client = FakeGatewayClient()
        bridge = AgentOSMCPBridge(gateway_client_factory=lambda: client)

        await bridge.conversations_list(limit=5)

        call = client.calls[0]
        assert call[1].get("limit") == 5

    # messages_read upper-clamps limit.

    async def test_messages_read_clamps_limit(self) -> None:
        client = FakeGatewayClient()
        bridge = AgentOSMCPBridge(gateway_client_factory=lambda: client)

        await bridge.messages_read("agent:main:main", limit=_MAX_HISTORY_LIMIT_UPPER * 10)

        call = client.calls[0]
        assert call[0] == "chat.history"
        assert call[1].get("limit") == _MAX_HISTORY_LIMIT_UPPER

    async def test_messages_read_default_stays_under_clamp(self) -> None:
        client = FakeGatewayClient()
        bridge = AgentOSMCPBridge(gateway_client_factory=lambda: client)

        await bridge.messages_read("agent:main:main")

        call = client.calls[0]
        assert call[1].get("limit") == 1000  # default is below clamp

    """transcript_jsonl upper-clamps limit."""

    async def test_transcript_jsonl_clamps_limit(self) -> None:
        client = FakeGatewayClient()
        bridge = AgentOSMCPBridge(gateway_client_factory=lambda: client)

        await bridge.transcript_jsonl("agent:main:main", limit=_MAX_HISTORY_LIMIT_UPPER * 10)

        call = client.calls[0]
        assert call[1].get("limit") == _MAX_HISTORY_LIMIT_UPPER

    """events_wait upper-clamps max_events and timeout_ms."""

    async def test_events_wait_accepts_large_timeout(self) -> None:
        """The high timeout is clamped internally; a pre-loaded terminal event
        causes the call to return instantly without waiting for the (clamped)
        deadline."""
        client = FakeGatewayClient()
        await client.events.put(
            {
                "event": "session.event.done",
                "payload": {"session_key": "agent:main:main", "stream_seq": 8},
            }
        )
        bridge = AgentOSMCPBridge(gateway_client_factory=lambda: client)

        result = await bridge.events_wait(
            "agent:main:main",
            since_stream_seq=7,
            timeout_ms=_MAX_TIMEOUT_MS_UPPER * 10,
            max_events=_MAX_EVENTS_UPPER,
        )

        assert result["current_stream_seq"] == 8
        assert result["timed_out"] is False

    async def test_events_wait_clamps_effective_deadline(self) -> None:
        """The effective timeout passed to recv_event must be <= the upper
        clamp limit, even when a large timeout_ms is supplied."""
        client = FakeGatewayClient()
        bridge = AgentOSMCPBridge(gateway_client_factory=lambda: client)

        task = asyncio.create_task(
            bridge.events_wait(
                "agent:main:main",
                since_stream_seq=7,
                timeout_ms=_MAX_TIMEOUT_MS_UPPER * 10,
                max_events=1,
            )
        )

        await asyncio.sleep(0.05)

        assert client.last_recv_timeout is not None, "recv_event was never called"
        max_allowed = _MAX_TIMEOUT_MS_UPPER / 1000.0
        assert client.last_recv_timeout <= max_allowed + 0.1, (
            f"effective timeout {client.last_recv_timeout}s exceeds max {max_allowed}s"
        )

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, TimeoutError):
            pass
