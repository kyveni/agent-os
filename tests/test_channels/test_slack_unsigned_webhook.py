"""Slack webhook handler must fail closed when signing_secret is not configured.

Issue #674: if signing_secret is None, the webhook handler should reject
event_callback payloads instead of processing them without authentication.

Upstream/main already implements this (via _unsigned_url_verification_challenge):
- url_verification is allowed (echo challenge, no side effects)
- Everything else gets 401
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
    """All tests verify behavior when no signing_secret is configured."""

    def test_event_callback_rejected_when_no_signing_secret(self, client: TestClient) -> None:
        """event_callback must be rejected (401) when signing_secret is None."""
        payload = {
            "type": "event_callback",
            "event": {"type": "message", "text": "pwned"},
            "team_id": "T001",
        }
        resp = client.post("/slack/events", json=payload)
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )

    def test_url_verification_succeeds_when_no_signing_secret(self, client: TestClient) -> None:
        """url_verification should pass even without signing_secret (safe, no side effects)."""
        challenge_token = "challenge-token-abc123"
        payload = {"type": "url_verification", "challenge": challenge_token}
        resp = client.post("/slack/events", json=payload)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data.get("challenge") == challenge_token, (
            f"Expected challenge={challenge_token!r}, got {data}"
        )

    def test_interactive_payload_rejected_when_no_signing_secret(self, client: TestClient) -> None:
        """Interactive form payloads must be rejected (non-200)."""
        resp = client.post(
            "/slack/events",
            data={"payload": json.dumps({"type": "block_actions"})},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code != 200, "Unsigned webhook accepted interactive payload"

    def test_slash_command_rejected_when_no_signing_secret(self, client: TestClient) -> None:
        """Slash command payloads must be rejected (non-200)."""
        resp = client.post(
            "/slack/events",
            data={"command": "/test", "text": "hello"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code != 200, "Unsigned webhook accepted slash command"
