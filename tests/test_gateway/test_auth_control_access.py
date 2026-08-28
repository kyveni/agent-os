from __future__ import annotations

from agentos.gateway.access import ConnectionSurface
from agentos.gateway.auth import resolve_auth, token_matches
from agentos.gateway.config import AuthConfig, GatewayConfig


def test_open_auth_loopback_admits_control_without_credentials() -> None:
    access = resolve_auth(
        GatewayConfig(debug=False, host="127.0.0.1"),
        {},
        "control",
        peer_ip="127.0.0.1",
    )

    assert access is not None
    assert access.surface is ConnectionSurface.CONTROL
    assert access.admitted is True
    assert access.credential_verified is False


def test_open_auth_public_listener_fails_even_for_loopback_peer() -> None:
    access = resolve_auth(
        GatewayConfig(debug=False, host="0.0.0.0"),
        {},
        "control",
        peer_ip="127.0.0.1",
    )

    assert access is None


def test_token_auth_admits_complete_control_surface() -> None:
    access = resolve_auth(
        GatewayConfig(
            host="0.0.0.0",
            auth=AuthConfig(mode="token", token="secret"),
        ),
        {"token": "secret"},
        "control",
        peer_ip="203.0.113.7",
    )

    assert access is not None
    assert access.surface is ConnectionSurface.CONTROL
    assert access.admitted is True
    assert access.credential_verified is True


def test_token_auth_without_configured_token_fails_closed() -> None:
    config = GatewayConfig(auth=AuthConfig(mode="token", token=None))

    assert resolve_auth(config, {}, "control", peer_ip="127.0.0.1") is None


class TestTokenMatches:
    """Constant-time token comparison helper (#498)."""

    def test_matching_tokens_pass(self) -> None:
        assert token_matches("secret", "secret") is True

    def test_mismatched_tokens_fail(self) -> None:
        assert token_matches("secret", "secreX") is False
        assert token_matches("secre", "secret") is False

    def test_missing_configured_token_fails_closed(self) -> None:
        assert token_matches("secret", None) is False
        assert token_matches("secret", "") is False

    def test_missing_or_empty_provided_token_fails(self) -> None:
        assert token_matches(None, "secret") is False
        assert token_matches("", "secret") is False

    def test_non_string_provided_token_fails(self) -> None:
        assert token_matches(123, "secret") is False
        assert token_matches(b"secret", "secret") is False
        assert token_matches(["secret"], "secret") is False

    def test_utf8_tokens_compare_by_bytes(self) -> None:
        assert token_matches("tökën", "tökën") is True
        assert token_matches("tökën", "töken") is False
