"""Regression tests for MCP server URL SSRF validation.

Ensures MCP transports reject cloud metadata endpoints while allowing
legitimate local/deployed MCP server URLs.
"""

from __future__ import annotations

import pytest

from agentos.tools.ssrf import SSRFBlockedError, UnsupportedURLSchemeError, validate_mcp_server_url


class TestMCPSSRFValidation:
    """Cloud metadata endpoints are blocked."""

    def test_metadata_ip_literal(self) -> None:
        with pytest.raises(SSRFBlockedError):
            validate_mcp_server_url("http://169.254.169.254/")

    def test_metadata_hostname_google(self) -> None:
        with pytest.raises(SSRFBlockedError):
            validate_mcp_server_url("http://metadata.google.internal/")

    def test_metadata_hostname_aws(self) -> None:
        with pytest.raises(SSRFBlockedError):
            validate_mcp_server_url("http://169.254.169.254/latest/meta-data/")

    def test_metadata_hostname_azure(self) -> None:
        with pytest.raises(SSRFBlockedError):
            validate_mcp_server_url("http://169.254.169.254/metadata/instance")

    """Legitimate MCP server URLs pass through."""

    def test_localhost_allowed(self) -> None:
        # No exception = pass
        validate_mcp_server_url("http://127.0.0.1:8000/")

    def test_lan_allowed(self) -> None:
        validate_mcp_server_url("http://192.168.1.100:8080/")

    def test_localhost_hostname_allowed(self) -> None:
        validate_mcp_server_url("http://localhost:8931/")

    def test_public_url_allowed(self) -> None:
        validate_mcp_server_url("https://mcp.example.com/tools")

    def test_https_schema_allowed(self) -> None:
        validate_mcp_server_url("https://mcp.myorg.internal/")

    """Scheme validation."""

    def test_unsupported_scheme_rejected(self) -> None:
        with pytest.raises(UnsupportedURLSchemeError):
            validate_mcp_server_url("ftp://mcp.example.com/")

    def test_no_scheme_raises(self) -> None:
        with pytest.raises(UnsupportedURLSchemeError):
            validate_mcp_server_url("mcp.example.com")

    """Edge cases."""

    def test_none_url_passes(self) -> None:
        # Will fail later on connect with its own error
        validate_mcp_server_url(None)

    def test_empty_url_passes(self) -> None:
        validate_mcp_server_url("")
