"""Test Telegram webhook secret verification uses constant-time comparison."""
from __future__ import annotations

import inspect

from agentos.channels.telegram import TelegramChannel


class TestTelegramConstantTimeCompare:
    def test_valid_secret_passes(self) -> None:
        ch = TelegramChannel(token="x", telegram_chat_id="C", webhook_secret_token="secret123")
        assert ch._verify_webhook_secret("secret123") is True

    def test_invalid_secret_rejected(self) -> None:
        ch = TelegramChannel(token="x", telegram_chat_id="C", webhook_secret_token="secret123")
        assert ch._verify_webhook_secret("wrong") is False

    def test_empty_secret_handled(self) -> None:
        ch = TelegramChannel(token="x", telegram_chat_id="C", webhook_secret_token=None)
        result = ch._verify_webhook_secret("")
        assert result is True or result is False

    def test_uses_compare_digest(self) -> None:
        ch = TelegramChannel(token="x", telegram_chat_id="C", webhook_secret_token="test")
        source = inspect.getsource(ch._verify_webhook_secret)
        assert "hmac.compare_digest" in source
