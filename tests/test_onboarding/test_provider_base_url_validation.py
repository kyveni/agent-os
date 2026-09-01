"""Regression tests for _validate_provider_base_url (#551 / PR #595).

Validates that caller-supplied base_url in upsert_llm_provider is checked
against SSRF vectors — private IPs, cloud metadata, non-http schemes, etc.

See https://github.com/use-agent-os/agent-os/pull/595
"""

from __future__ import annotations

import pytest

from agentos.onboarding.mutations import _validate_provider_base_url


class TestSchemeValidation:
    """Only http and https are allowed."""

    def test_https_is_allowed(self) -> None:
        _validate_provider_base_url("https://api.openai.com/v1")

    def test_http_is_allowed(self) -> None:
        _validate_provider_base_url("http://localhost:11434/v1")

    def test_ftp_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be an absolute http"):
            _validate_provider_base_url("ftp://example.com")

    def test_file_scheme_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be an absolute http"):
            _validate_provider_base_url("file:///etc/passwd")

    def test_data_scheme_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be an absolute http"):
            _validate_provider_base_url("data://text/plain;base64,SGVsbG8=")

    def test_empty_scheme_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be an absolute http"):
            _validate_provider_base_url("localhost:11434")

    def test_no_netloc_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be an absolute http"):
            _validate_provider_base_url("http://")


class TestPrivateIPValidation:
    """RFC 1918 private IPs must be rejected."""

    @pytest.mark.parametrize(
        "ip_address",
        [
            "http://10.0.0.1/v1",
            "http://10.255.255.255/v1",
            "http://172.16.0.1/v1",
            "http://172.31.255.255/v1",
            "http://192.168.0.1/v1",
            "http://192.168.255.255/v1",
        ],
    )
    def test_private_ip_rejected(self, ip_address: str) -> None:
        with pytest.raises(ValueError, match="private IP"):
            _validate_provider_base_url(ip_address)


class TestCloudMetadataValidation:
    """Link-local addresses including cloud metadata endpoint."""

    @pytest.mark.parametrize(
        "ip_address",
        [
            "http://169.254.169.254/latest/meta-data",
            "http://169.254.169.254/v1",
            "http://169.254.0.1",
        ],
    )
    def test_link_local_rejected(self, ip_address: str) -> None:
        with pytest.raises(ValueError, match="link-local|cloud metadata"):
            _validate_provider_base_url(ip_address)


class TestUnspecifiedValidation:
    """0.0.0.0 must be rejected."""

    def test_unspecified_rejected(self) -> None:
        with pytest.raises(ValueError, match="unspecified"):
            _validate_provider_base_url("http://0.0.0.0:8080")


class TestCGNATValidation:
    """CGNAT (100.64.0.0/10) is a reserved range."""

    @pytest.mark.parametrize(
        "ip_address",
        [
            "http://100.64.0.1",
            "http://100.127.255.255",
        ],
    )
    def test_cgnat_rejected(self, ip_address: str) -> None:
        with pytest.raises(ValueError, match="reserved IP"):
            _validate_provider_base_url(ip_address)


class TestLocalhostAllowed:
    """Localhost is explicitly allowed for local model servers."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:11434/v1",
            "http://127.0.0.1:8080",
            "http://127.255.255.255",
            "http://[::1]:11434/v1",
            "http://localhost:11434/v1",
        ],
    )
    def test_localhost_allowed(self, url: str) -> None:
        _validate_provider_base_url(url)


class TestHostnameAllowed:
    """Non-IP hostnames pass through (DNS rebinding is a separate concern)."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.openai.com/v1",
            "https://api.anthropic.com/v1",
            "https://llama.us.gaianet.network/v1",
            "http://ollama.local:11434/v1",
        ],
    )
    def test_hostname_allowed(self, url: str) -> None:
        _validate_provider_base_url(url)
