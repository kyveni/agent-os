"""Regression tests for ValidatingNetworkBackend / resolve_and_validate (#516 / PR #597).

Tests that the DNS-rebinding TOCTOU fix resolves hostnames at connect time
and validates every resolved address against SSRF rules.

See https://github.com/use-agent-os/agent-os/pull/597
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from agentos.tools import ssrf
from agentos.tools.types import SSRFBlockedError


def _fake_getaddrinfo(ip: str):
    def resolver(hostname: str, port: int | None, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 443))]

    return resolver


class TestResolveAndValidate:
    """resolve_and_validate resolves and validates in one atomic call."""

    def test_public_ip_resolves_successfully(self):
        with patch.object(ssrf.socket, "getaddrinfo", _fake_getaddrinfo("1.2.3.4")):
            result = ssrf.resolve_and_validate("example.com", 443)
            assert len(result) == 1
            assert result[0] == ("1.2.3.4", 443)

    def test_private_ip_raises_ssrf_blocked(self):
        with patch.object(ssrf.socket, "getaddrinfo", _fake_getaddrinfo("10.0.0.1")):
            with pytest.raises(SSRFBlockedError, match="10.0.0.1"):
                ssrf.resolve_and_validate("malicious.internal", 443)

    def test_link_local_raises_ssrf_blocked(self):
        with patch.object(ssrf.socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254")):
            with pytest.raises(SSRFBlockedError, match="169.254.169.254"):
                ssrf.resolve_and_validate("metadata.internal", 443)

    def test_loopback_raises_ssrf_blocked(self):
        with patch.object(ssrf.socket, "getaddrinfo", _fake_getaddrinfo("127.0.0.1")):
            with pytest.raises(SSRFBlockedError, match="127.0.0.1"):
                ssrf.resolve_and_validate("localhost", 443)

    def test_rfc1918_private_192_168_raises(self):
        with patch.object(ssrf.socket, "getaddrinfo", _fake_getaddrinfo("192.168.1.1")):
            with pytest.raises(SSRFBlockedError, match="192.168.1.1"):
                ssrf.resolve_and_validate("internal.router", 443)

    def test_rfc1918_private_172_raises(self):
        with patch.object(ssrf.socket, "getaddrinfo", _fake_getaddrinfo("172.16.0.5")):
            with pytest.raises(SSRFBlockedError, match="172.16.0.5"):
                ssrf.resolve_and_validate("internal.server", 443)

    def test_unspecified_raises(self):
        with patch.object(ssrf.socket, "getaddrinfo", _fake_getaddrinfo("0.0.0.0")):
            with pytest.raises(SSRFBlockedError, match="0.0.0.0"):
                ssrf.resolve_and_validate("anyhost", 80)

    def test_rfc2544_fake_ip_blocked_by_default(self):
        with patch.object(ssrf.socket, "getaddrinfo", _fake_getaddrinfo("198.18.0.2")):
            with pytest.raises(SSRFBlockedError, match="198.18.0.2"):
                ssrf.resolve_and_validate("fake-dns.example", 443)

    def test_rfc2544_fake_ip_allowed_with_trusted_cidr(self):
        with patch.object(ssrf.socket, "getaddrinfo", _fake_getaddrinfo("198.18.0.2")):
            result = ssrf.resolve_and_validate(
                "fake-dns.example", 443,
                trusted_fake_ip_cidrs=["198.18.0.0/15"],
            )
            assert result[0] == ("198.18.0.2", 443)

    def test_multiple_resolved_addresses_all_validated(self):
        def multi_resolver(hostname, port, *args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", port or 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port or 443)),
            ]

        with patch.object(ssrf.socket, "getaddrinfo", multi_resolver):
            with pytest.raises(SSRFBlockedError, match="10.0.0.1"):
                ssrf.resolve_and_validate("mixed.example", 443)

    def test_no_valid_addresses_error(self):
        def empty_resolver(hostname, port, *args, **kwargs):
            return []

        with patch.object(ssrf.socket, "getaddrinfo", empty_resolver):
            with pytest.raises(ValueError, match="Cannot resolve"):
                ssrf.resolve_and_validate("nowhere.example", 443)


class TestValidatingNetworkBackend:
    """ValidatingNetworkBackend wraps resolve_and_validate at connect time."""

    def test_backend_imports(self):
        """ValidatingNetworkBackend is importable and has the right shape."""
        backend = ssrf.ValidatingNetworkBackend()
        assert hasattr(backend, "connect_tcp")
        assert hasattr(backend, "connect_unix_socket")
        assert hasattr(backend, "sleep")

    def test_ssrf_guarded_client_imports(self):
        """ssrf_guarded_client is importable and returns an httpx client."""
        import httpx

        client = ssrf.ssrf_guarded_client(timeout=10)
        assert isinstance(client, httpx.AsyncClient)


class TestRegressions:
    """Existing SSRF behaviour must not regress."""

    def test_public_url_still_passes_validate_http_url(self, monkeypatch):
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _fake_getaddrinfo("140.82.121.3"))
        ssrf.validate_http_url_for_fetch("https://github.com/use-agent-os/agent-os")

    def test_private_url_still_blocked_in_validate_http_url(self, monkeypatch):
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _fake_getaddrinfo("10.0.0.1"))
        with pytest.raises(SSRFBlockedError):
            ssrf.validate_http_url_for_fetch("https://internal.example.com")
