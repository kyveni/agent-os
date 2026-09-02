"""Tests for bounded background process stdout (#803).

_BgSession.output_lines must not grow unbounded.  _read_bg_output should
cap retained output at _BG_OUTPUT_MAX_CHARS, emit a truncation marker,
continue draining the pipe, and set output_truncated.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agentos.tools.builtin import shell
from agentos.tools.types import CallerKind, ToolContext


class _FakeStreamReader:
    """Simulate a subprocess stdout stream that yields N chunks."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def read(self, n: int = 4096) -> bytes | None:
        if not self._chunks:
            return None
        return self._chunks.pop(0)


def _make_session(output_lines: list[str] | None = None) -> shell._BgSession:
    proc = MagicMock(spec=shell.asyncio.subprocess.Process)
    proc.returncode = 0
    session = shell._BgSession(
        session_id="test01",
        command="echo hello",
        process=proc,
    )
    if output_lines is not None:
        session.output_lines = list(output_lines)
    return session


class TestReadBgOutputCap:
    """_read_bg_output should cap retained lines at _BG_OUTPUT_MAX_CHARS."""

    @pytest.mark.asyncio
    async def test_small_output_passes_through(self):
        """Output under the cap is fully retained."""
        chunk = b"hello world\n" * 10  # ~120 bytes
        proc = MagicMock(spec=shell.asyncio.subprocess.Process)
        proc.stdout = _FakeStreamReader([chunk])
        session = shell._BgSession(
            session_id="t-small",
            command="echo small",
            process=proc,
        )
        await shell._read_bg_output(session)
        assert len(session.output_lines) == 1
        assert not session.output_truncated
        assert session.output_lines[0] == "hello world\n" * 10

    @pytest.mark.asyncio
    async def test_large_output_is_capped(self):
        """Output exceeding the cap is truncated with a marker."""
        # Generate enough data to exceed the cap (100_000 chars)
        line = b"A" * 500  # 500 bytes per chunk
        many_chunks = [line] * 300  # 150_000 bytes total
        proc = MagicMock(spec=shell.asyncio.subprocess.Process)
        proc.stdout = _FakeStreamReader(many_chunks)
        session = shell._BgSession(
            session_id="t-large",
            command="yes",
            process=proc,
        )
        await shell._read_bg_output(session)
        # Should have some retained lines plus one truncation marker
        assert session.output_truncated
        joined = "".join(session.output_lines)
        assert "[output truncated after" in joined
        # The retained portion should be at or near the cap
        marker_idx = joined.index("[output truncated")
        retained_chars = marker_idx
        assert retained_chars <= shell._BG_OUTPUT_MAX_CHARS + 500  # allow slight overshoot

    @pytest.mark.asyncio
    async def test_truncation_marker_appears_once(self):
        """Only one truncation marker is emitted regardless of overshoot."""
        line = b"X" * 8000
        many_chunks = [line] * 50  # 400_000 bytes
        proc = MagicMock(spec=shell.asyncio.subprocess.Process)
        proc.stdout = _FakeStreamReader(many_chunks)
        session = shell._BgSession(
            session_id="t-marker",
            command="yes",
            process=proc,
        )
        await shell._read_bg_output(session)
        assert session.output_truncated
        marker_count = sum(
            1 for line in session.output_lines if "[output truncated after" in line
        )
        assert marker_count == 1, f"Expected exactly 1 truncation marker, got {marker_count}"

    @pytest.mark.asyncio
    async def test_pipe_is_drained_after_cap(self):
        """The reader continues draining the pipe after the cap so the child
        process does not block on a full pipe buffer."""
        chunk = b"Z" * (shell._BG_OUTPUT_MAX_CHARS + 10_000)
        proc = MagicMock(spec=shell.asyncio.subprocess.Process)
        # First chunk exceeds cap, second chunk is the sentinel
        stream = _FakeStreamReader([chunk, b"END_MARKER"])
        proc.stdout = stream
        session = shell._BgSession(
            session_id="t-drain",
            command="dd",
            process=proc,
        )
        # Should not raise: pipe is fully drained
        await shell._read_bg_output(session)
        assert session.output_truncated
        # The partial content before cap + marker are retained
        joined = "".join(session.output_lines)
        assert "[output truncated after" in joined
        assert joined.endswith("\n")
        # The END_MARKER sentinel from the second chunk is NOT
        # retained (we stopped appending after the cap).
        assert "END_MARKER" not in joined


class TestBgSessionPayload:
    """_bg_session_payload should include output_truncated."""

    def test_output_truncated_default_false(self):
        session = _make_session()
        payload = shell._bg_session_payload(session)
        assert payload["output_truncated"] is False

    def test_output_truncated_true(self):
        session = _make_session()
        session.output_truncated = True
        payload = shell._bg_session_payload(session)
        assert payload["output_truncated"] is True


class TestProcessActionLogOutputTruncated:
    """process(action='log') response should include output_truncated field."""

    @pytest.mark.asyncio
    async def test_response_includes_output_truncated(self):
        session = _make_session()
        session.output_truncated = True
        ctx = ToolContext(
            agent_id="test-agent",
            session_key="test:user",
            caller_kind=CallerKind.CLI,
        )
        with (
            patch.dict(shell._bg_sessions, {"test01": session}),
            patch(
                "agentos.tools.builtin.shell.current_tool_context"
            ) as mock_ctx,
        ):
            mock_ctx.get.return_value = ctx
            result = await shell.process(
                action="log",
                session_id="test01",
                offset=0,
                limit=100,
            )
            data = json.loads(result)
            assert data["output_truncated"] is True
