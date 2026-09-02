"""Test background_process output capping."""
from __future__ import annotations

from agentos.tools.builtin.shell import _BgSession


def test_output_truncated_field_default() -> None:
    s = _BgSession(session_id="t", command="echo hi", process=None)  # type: ignore[arg-type]
    assert not s.output_truncated
