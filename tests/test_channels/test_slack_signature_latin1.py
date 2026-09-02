"""Test Slack signature verification with latin-1 body decoding."""
from __future__ import annotations

import hashlib
import hmac

from agentos.channels.slack import SlackChannel


def _sign(channel: SlackChannel, body: bytes, timestamp: str) -> str:
    sig_basestring = f"v0:{timestamp}:{body.decode('latin-1')}".encode()
    return "v0=" + hmac.HMAC(
        channel.signing_secret.encode(), sig_basestring, hashlib.sha256
    ).hexdigest()


class TestVerifySignatureCorrectness:
    def test_valid_signature_passes(self) -> None:
        ch = SlackChannel(token="x", slack_channel_id="C", signing_secret="secret")
        body = b"hello\x80world"
        ts = "1700000000"
        sig = _sign(ch, body, ts)
        assert ch._verify_signature(body, ts, sig)

    def test_body_with_high_ascii(self) -> None:
        ch = SlackChannel(token="x", slack_channel_id="C", signing_secret="secret")
        body = bytes(range(128, 256))
        ts = "1700000000"
        sig = _sign(ch, body, ts)
        assert ch._verify_signature(body, ts, sig)

    def test_no_signing_secret_returns_false(self) -> None:
        ch = SlackChannel(token="x", slack_channel_id="C", signing_secret=None)
        assert not ch._verify_signature(b"body", "0", "sig")
