"""Regression tests for trusted-proxy auth fix (#568).

The original trusted-proxy auth did a substring match against the
client-supplied X-Forwarded-For header, which any network peer could
spoof. The fix checks the real transport peer IP (request.client.host)
against the trusted proxy set instead.

See https://github.com/use-agent-os/agent-os/issues/568
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from starlette.datastructures import Headers

from agentos.gateway.config import GatewayConfig


class MockRequest:
    """Minimal mock for starlette Request."""

    def __init__(self, client_host: str | None, headers: dict | None = None) -> None:
        self.client = Mock(host=client_host) if client_host else None
        self.headers = Headers(headers or {})


class TestTrustedProxyAuth:
    """The trusted-proxy auth mode must check the real transport peer IP,
    not the client-supplied X-Forwarded-For header."""

    @pytest.fixture
    def config(self, tmp_path) -> GatewayConfig:
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            '[auth]\nmode = "trusted-proxy"\ntrusted_proxy = "10.0.0.5"\n',
            encoding="utf-8",
        )
        return GatewayConfig.load(cfg_path)

    @pytest.fixture
    def middleware(self, config):
        from agentos.gateway.middleware import AuthMiddleware
        return AuthMiddleware(app=Mock(), config=config)

    @pytest.mark.asyncio
    async def test_trusted_peer_passes(self, middleware):
        """A request from a trusted proxy IP passes authentication."""
        request = MockRequest(client_host="10.0.0.5")
        call_next = Mock(return_value="ok")
        result = await middleware.dispatch(request, call_next)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_untrusted_peer_rejected(self, middleware):
        """A request from an untrusted IP is rejected with 401."""
        request = MockRequest(client_host="192.168.1.100")
        call_next = Mock(return_value="ok")
        result = await middleware.dispatch(request, call_next)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_spoofed_x_forwarded_for_rejected(self, middleware):
        """Sending X-Forwarded-For: <trusted-proxy> from an untrusted peer
        must NOT pass auth — the real peer IP is checked, not the header."""
        request = MockRequest(
            client_host="192.168.1.100",
            headers={"x-forwarded-for": "10.0.0.5"},
        )
        call_next = Mock(return_value="ok")
        result = await middleware.dispatch(request, call_next)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_no_client_rejected(self, middleware):
        """A request without a client IP (e.g. Unix socket) is rejected."""
        request = MockRequest(client_host=None)
        call_next = Mock(return_value="ok")
        result = await middleware.dispatch(request, call_next)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_no_proxy_configured_rejected(self, tmp_path):
        """When trusted_proxy is unset, all requests are rejected."""
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            '[auth]\nmode = "trusted-proxy"\n',
            encoding="utf-8",
        )
        config = GatewayConfig.load(cfg_path)
        from agentos.gateway.middleware import AuthMiddleware
        mw = AuthMiddleware(app=Mock(), config=config)

        request = MockRequest(client_host="10.0.0.5")
        call_next = Mock(return_value="ok")
        result = await mw.dispatch(request, call_next)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_multi_proxy_trusted_peer_passes(self, tmp_path):
        """Comma-separated trusted proxies — any matching peer passes."""
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            '[auth]\nmode = "trusted-proxy"\ntrusted_proxy = "10.0.0.5, 10.0.0.6"\n',
            encoding="utf-8",
        )
        config = GatewayConfig.load(cfg_path)
        from agentos.gateway.middleware import AuthMiddleware
        mw = AuthMiddleware(app=Mock(), config=config)

        request = MockRequest(client_host="10.0.0.6")
        call_next = Mock(return_value="ok")
        result = await mw.dispatch(request, call_next)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_multi_proxy_untrusted_peer_rejected(self, tmp_path):
        """Comma-separated trusted proxies — non-matching peer rejected."""
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            '[auth]\nmode = "trusted-proxy"\ntrusted_proxy = "10.0.0.5, 10.0.0.6"\n',
            encoding="utf-8",
        )
        config = GatewayConfig.load(cfg_path)
        from agentos.gateway.middleware import AuthMiddleware
        mw = AuthMiddleware(app=Mock(), config=config)

        request = MockRequest(client_host="10.0.0.7")
        call_next = Mock(return_value="ok")
        result = await mw.dispatch(request, call_next)
        assert result.status_code == 401
