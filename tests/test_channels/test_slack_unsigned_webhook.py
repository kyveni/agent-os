"""Slack webhook handler must fail closed when signing_secret is not configured.

Issue #674: if signing_secret is None, the webhook handler should reject
event_callback payloads instead of processing them without authentication.
"""

from __future__ import annotations

import json

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from agentos.channels.slack import SlackChannel


@pytest.fixture
def unsigned_slack() -> SlackChannel:
    """A SlackChannel with signing_secret=None (misconfigured)."""
    channel = SlackChannel(
        token="xoxb-test-token",
        slack_channel_id="C001",
        signing_secret=None,
    )
    return channel


@pytest.fixture
def app(unsigned_slack: SlackChannel) -> Starlette:
    routes = [Route("/slack/events", endpoint=unsigned_slack._handle_webhook, methods=["POST"])]
    return Starlette(routes=routes)


@pytest.fixture
def client(app: Starlette) -> TestClient:
    return TestClient(app)


class TestUnsignedWebhook:
    """All tests verify that unauthenticated payloads are rejected."""

    def test_event_callback_rejected_when_no_signing_secret(self, client: TestClient) -> None:
        """event_callback must not be processed when signing_secret is None."""
        payload = {
            "type": "event_callback",
            "event": {"type": "message", "text": "pwned"},
            "team_id": "T001",
        }
        resp = client.post("/slack/events", json=payload)
        assert resp.status_code == 503, (
            f"Expected 503, got {resp.status_code}: {resp.text}"
        )

    def test_url_verification_rejected_when_no_signing_secret(self, client: TestClient) -> None:
        """Even url_verification must be rejected when signing_secret is None."""
        payload = {"type": "url_verification", "challenge": "challenge-token"}
        resp = client.post("/slack/events", json=payload)
        assert resp.status_code == 503, (
            f"Expected 503, got {resp.status_code}: {resp.text}"
        )

    def test_interactive_payload_rejected_when_no_signing_secret(self, client: TestClient) -> None:
        """Interactive form payloads must be rejected."""
        resp = client.post(
            "/slack/events",
            data={"payload": json.dumps({"type": "block_actions"})},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code != 200, "Unsigned webhook accepted interactive payload"

    def test_slash_command_rejected_when_no_signing_secret(self, client: TestClient) -> None:
        """Slash command payloads must be rejected."""
        resp = client.post(
            "/slack/events",
            data={"command": "/test", "text": "hello"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code != 200, "Unsigned webhook accepted slash command"


class TestSignedWebhook:
    """When signing_secret is configured, webhook must accept valid payloads."""

    @pytest.fixture
    def signed_slack(self) -> SlackChannel:
        return SlackChannel(
            token="xoxb-test-token",
            slack_channel_id="C001",
            signing_secret="valid-secret",
        )

    @pytest.fixture
    def signed_app(self, signed_slack: SlackChannel) -> Starlette:
        routes = [
            Route("/slack/events", endpoint=signed_slack._handle_webhook, methods=["POST"])
        ]
        return Starlette(routes=routes)

    @pytest.fixture
    def signed_client(self, signed_app: Starlette) -> TestClient:
        return TestClient(signed_app)

    def test_url_verification_passes_with_signing_secret(
        self, signed_client: TestClient, signed_slack: SlackChannel
    ) -> None:
        import hashlib
        import hmac
        import time

        timestamp = str(int(time.time()))
        body = json.dumps({"type": "url_verification", "challenge": "challenge-token"})
        sig_basestring = f"v0:{timestamp}:{body}"
        sig = "v0=" + hmac.HMAC(
            "valid-secret".encode(),
            sig_basestring.encode(),
            hashlib.sha256,
        ).hexdigest()
        resp = signed_client.post(
            "/slack/events",
            content=body,
            headers={
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": sig,
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        assert resp.json()["challenge"] == "challenge-token"
