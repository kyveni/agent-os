from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agentos.gateway.boot import _discover_configured_mcp_servers
from agentos.gateway.config import GatewayConfig
from agentos.mcp.streamable_http import FileOAuthStorage
from agentos.tools.registry import ToolRegistry

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "test_data"


def _oauth_config(tmp_path) -> GatewayConfig:
    return GatewayConfig(
        state_dir=str(tmp_path),
        mcp={
            "enabled": True,
            "servers": [
                {
                    "name": "robinhood-trading",
                    "transport": "streamable_http",
                    "url": "https://agent.robinhood.com/mcp/trading",
                    "oauth": True,
                }
            ],
        },
    )


def _base_oauth_config(tmp_path: Any) -> GatewayConfig:
    return GatewayConfig(
        state_dir=str(tmp_path),
        mcp={
            "enabled": True,
            "servers": [
                {
                    "name": "base-mcp",
                    "transport": "streamable_http",
                    "url": "https://mcp.base.org",
                    "oauth": True,
                }
            ],
        },
    )


@pytest.mark.asyncio
async def test_boot_skips_oauth_server_until_user_authenticates(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.mcp import discovery

    discover = AsyncMock(return_value=[])
    monkeypatch.setattr(discovery, "discover_and_register", discover)
    monkeypatch.setattr(FileOAuthStorage, "is_authenticated", AsyncMock(return_value=False))

    await _discover_configured_mcp_servers(_oauth_config(tmp_path), ToolRegistry())

    discover.assert_not_awaited()


@pytest.mark.asyncio
async def test_boot_reconnects_oauth_server_after_authentication(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.mcp import discovery

    discover = AsyncMock(return_value=["mcp_portfolio"])
    monkeypatch.setattr(discovery, "discover_and_register", discover)
    monkeypatch.setattr(FileOAuthStorage, "is_authenticated", AsyncMock(return_value=True))

    await _discover_configured_mcp_servers(_oauth_config(tmp_path), ToolRegistry())

    discover.assert_awaited_once()


@pytest.mark.asyncio
async def test_boot_skips_base_mcp_until_user_authenticates(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.mcp import discovery

    discover = AsyncMock(return_value=[])
    monkeypatch.setattr(discovery, "discover_and_register", discover)
    monkeypatch.setattr(FileOAuthStorage, "is_authenticated", AsyncMock(return_value=False))

    await _discover_configured_mcp_servers(_base_oauth_config(tmp_path), ToolRegistry())

    discover.assert_not_awaited()


@pytest.mark.asyncio
async def test_boot_reconnects_base_mcp_after_authentication(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.mcp import discovery

    discover = AsyncMock(return_value=["mcp_send_calls"])
    monkeypatch.setattr(discovery, "discover_and_register", discover)
    monkeypatch.setattr(FileOAuthStorage, "is_authenticated", AsyncMock(return_value=True))

    await _discover_configured_mcp_servers(_base_oauth_config(tmp_path), ToolRegistry())

    discover.assert_awaited_once()


def test_base_mcp_oauth_metadata_regression_fixture() -> None:
    """Verify the Base MCP authorization-server metadata fixture is well-formed.

    The fixture was captured from the live endpoint at commit time. If upstream
    Base MCP changes its metadata, this test fails and the fixture must be
    refreshed before the docs claim about OAuth compatibility becomes stale.
    """
    fixture_path = FIXTURE_DIR / "base_mcp_oauth_metadata.json"
    assert fixture_path.exists(), f"Missing fixture: {fixture_path}"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert payload["issuer"] == "https://mcp.base.org"
    assert payload["authorization_endpoint"].startswith("https://mcp.base.org/")
    assert payload["token_endpoint"].startswith("https://mcp.base.org/")
    assert payload["registration_endpoint"].startswith("https://mcp.base.org/")
    assert "code" in payload["response_types_supported"]
    assert "authorization_code" in payload["grant_types_supported"]
    assert "S256" in payload["code_challenge_methods_supported"]
    assert "agent_wallet:transact" in payload["scopes_supported"]
    assert "agent_wallet:escalate" in payload["scopes_supported"]
