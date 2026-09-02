"""Test that channel verification message uses agentos (not uv run agentos).

Issue #835: the printed Verify: message must work on pipx/pip installs
that don't have uv on PATH.
"""

from __future__ import annotations

from agentos.cli.channels_cmd import _print_channel_verification_next_step


def test_channel_verify_msg_uses_agentos_not_uv_run(capsys) -> None:
    """The verify message should use 'agentos' directly."""
    _print_channel_verification_next_step("test")
    captured = capsys.readouterr()
    assert "uv run agentos" not in captured.out, (
        f"Verify message uses uv run, output: {captured.out!r}"
    )
    assert "agentos channels status" in captured.out, (
        f"Verify message missing 'agentos channels status', got: {captured.out!r}"
    )
