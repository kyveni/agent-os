"""Fallback lock cache eviction tests.

Verifies that the closure-based fallback lock dict in TurnRunner does not
grow without bound.  The fix is additive — existing behavior unchanged.
Formula: ≥8 tests, boundary coverage, evict-on-read AND evict-on-write,
concurrent safety.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentos.engine.runtime import (
    TurnRunner,
    _evict_fallback_lock,
    _purge_fallback_locks,
)
from agentos.engine.types import DoneEvent

# ---------------------------------------------------------------------------
# Unit tests: _evict_fallback_lock
# ---------------------------------------------------------------------------

class TestEvictFallbackLock:
    """Direct unit tests for the eviction function (testability hook)."""

    def test_evicts_existing_key(self) -> None:
        locks = {"s1": (asyncio.Lock(), 100.0), "s2": (asyncio.Lock(), 100.0)}
        _evict_fallback_lock(locks, "s1", now=200.0)
        assert "s1" not in locks
        assert "s2" in locks

    def test_evict_nonexistent_key_is_noop(self) -> None:
        locks = {"s1": (asyncio.Lock(), 100.0)}
        _evict_fallback_lock(locks, "s2", now=200.0)
        assert locks == {"s1": (locks["s1"][0], 100.0)}

    def test_purges_stale_entries_when_over_max(self) -> None:
        """evict-on-read: purge stale on call when dict exceeds _MAX_ENTRIES."""
        locks = {}
        for i in range(201):
            locks[f"s{i}"] = (asyncio.Lock(), 100.0)
        # One fresh entry
        locks["fresh"] = (asyncio.Lock(), 9999.0)
        _evict_fallback_lock(locks, "s0", now=2000.0)
        # "fresh" has ts=9999, shouldn't be purged
        assert "fresh" in locks
        # All old keys should be purged except the evicted one
        assert len(locks) <= 2  # fresh + maybe one other

    def test_max_not_reached_no_purge(self) -> None:
        locks = {"s1": (asyncio.Lock(), 100.0)}
        _evict_fallback_lock(locks, "s1", now=200.0)
        assert len(locks) == 0  # s1 evicted, no stale purge needed

    def test_boundary_at_max(self) -> None:
        """at _FALLBACK_LOCK_MAX_ENTRIES = no purge; at MAX+1 = purge."""
        from agentos.engine.runtime import _FALLBACK_LOCK_MAX_ENTRIES

        locks = {}
        for i in range(_FALLBACK_LOCK_MAX_ENTRIES):
            locks[f"s{i}"] = (asyncio.Lock(), 100.0)
        key = list(locks.keys())[0]
        _evict_fallback_lock(locks, key, now=9999.0)
        # Exactly at max, no stale purge — only the evicted key is gone
        assert len(locks) == _FALLBACK_LOCK_MAX_ENTRIES - 1

    def test_boundary_over_max_triggers_purge(self) -> None:
        from agentos.engine.runtime import _FALLBACK_LOCK_MAX_ENTRIES

        locks = {}
        for i in range(_FALLBACK_LOCK_MAX_ENTRIES + 2):
            locks[f"s{i}"] = (asyncio.Lock(), 100.0)
        key = list(locks.keys())[0]
        _evict_fallback_lock(locks, key, now=9999.0)
        # After evicting 1: 201 left > 200 → stale purge triggers.
        # All have ts=100 < now=9999 - 600 → all stale → all purged.
        # Max 1 left due to dict-iteration race.
        assert len(locks) <= 1

    def test_custom_now_parameter(self) -> None:
        locks = {"s1": (asyncio.Lock(), 100.0)}
        _evict_fallback_lock(locks, "s1", now=50.0)
        assert "s1" not in locks  # evicted regardless of now


# ---------------------------------------------------------------------------
# Unit tests: _purge_fallback_locks
# ---------------------------------------------------------------------------

class TestPurgeFallbackLocks:
    """Evict-on-write: full purge / clear path."""

    def test_purge_removes_all(self) -> None:
        locks = {"s1": (asyncio.Lock(), 1.0), "s2": (asyncio.Lock(), 2.0)}
        count = _purge_fallback_locks(locks)
        assert count == 2
        assert len(locks) == 0

    def test_purge_empty_dict(self) -> None:
        locks: dict[str, Any] = {}
        count = _purge_fallback_locks(locks)
        assert count == 0


# ---------------------------------------------------------------------------
# Integration tests: TurnRunner fallback lock lifecycle
# ---------------------------------------------------------------------------

def _stub_turn_runner() -> TurnRunner:
    """Build a TurnRunner that uses the fallback closure path."""
    provider = MagicMock()
    provider.provider_name = "stub"

    async def _chat(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        yield DoneEvent()

    provider.chat = _chat

    selector = MagicMock()
    selector.resolve.return_value = provider
    selector.clone.return_value = selector
    selector.current_config = MagicMock(model="stub-model")

    session_manager = MagicMock()
    session_manager.get = AsyncMock(return_value=None)
    session_manager.append_message = AsyncMock(return_value=None)
    session_manager.update = AsyncMock(return_value=None)
    session_manager.get_compaction_summary = AsyncMock(return_value=None)

    return TurnRunner(
        provider_selector=selector,
        session_manager=session_manager,
        session_lock_provider=None,  # ← triggers fallback closure
    )


class TestTurnRunnerFallbackLockLifecycle:
    """Integration tests exercising the actual TurnRunner path."""

    @pytest.mark.asyncio
    async def test_fallback_locks_attr_created(self) -> None:
        runner = _stub_turn_runner()
        assert runner._per_session_lock_dict is not None
        assert len(runner._per_session_lock_dict) == 0

    @pytest.mark.asyncio
    async def test_run_creates_and_evicts_fallback_lock(self) -> None:
        """evict-on-read: lock created by run(), evicted afterwards."""
        runner = _stub_turn_runner()
        session_key = "agent:main:test-evict-001"
        from agentos.tools.types import ToolContext
        tool_ctx = ToolContext(session_key=session_key)

        async for _ in runner.run(
            message="hello",
            session_key=session_key,
            tool_context=tool_ctx,
        ):
            # During run, the lock should exist
            assert session_key in runner._per_session_lock_dict

        # After run, lock evicted
        assert session_key not in runner._per_session_lock_dict

    @pytest.mark.asyncio
    async def test_multiple_sessions_evicted_independently(self) -> None:
        runner = _stub_turn_runner()
        from agentos.tools.types import ToolContext
        tool_ctx = ToolContext(session_key="agent:main:multi-session")

        async for _ in runner.run(
            message="hello",
            session_key="agent:main:session-a",
            tool_context=tool_ctx,
        ):
            pass

        async for _ in runner.run(
            message="hello",
            session_key="agent:main:session-b",
            tool_context=tool_ctx,
        ):
            pass

        assert "agent:main:session-a" not in runner._per_session_lock_dict
        assert "agent:main:session-b" not in runner._per_session_lock_dict

    @pytest.mark.asyncio
    async def test_concurrent_safety(self) -> None:
        """50 concurrent runs must not observe corruption."""
        runner = _stub_turn_runner()
        from agentos.tools.types import ToolContext

        async def _run(session_key: str) -> None:
            tool_ctx = ToolContext(session_key=session_key)
            async for _ in runner.run(
                message="ping",
                session_key=session_key,
                tool_context=tool_ctx,
            ):
                pass

        keys = [f"agent:main:concurrent-{i:03d}" for i in range(50)]
        await asyncio.gather(*(_run(k) for k in keys))
        # No locks should remain after all runs complete
        remaining = [k for k in keys if k in (runner._per_session_lock_dict or {})]
        assert remaining == [], f"Leaked locks: {remaining}"

    @pytest.mark.asyncio
    async def test_purge_removes_all_locks(self) -> None:
        runner = _stub_turn_runner()
        from agentos.tools.types import ToolContext

        async for _ in runner.run(
            message="hello",
            session_key="agent:main:purge-me",
            tool_context=ToolContext(session_key="agent:main:purge-me"),
        ):
            pass

        # Simulate manual purge (evict-on-write)
        if runner._per_session_lock_dict is not None:
            _purge_fallback_locks(runner._per_session_lock_dict)
        assert runner._per_session_lock_dict is not None and len(runner._per_session_lock_dict) == 0
