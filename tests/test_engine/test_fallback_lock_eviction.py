"""Fallback lock cache eviction tests — upgraded 2-phase."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentos.engine.runtime import (
    _FALLBACK_LOCK_MAX_ENTRIES,
    TurnRunner,
    _evict_fallback_lock,
    _purge_fallback_locks,
)
from agentos.engine.types import DoneEvent
from agentos.tools.types import ToolContext


class TestEvictFallbackLock:
    """Unit: _evict_fallback_lock — Phase 1 (TTL) + Phase 2 (cap)."""

    def test_evicts_existing_key(self) -> None:
        locks = {"s1": (asyncio.Lock(), 100.0), "s2": (asyncio.Lock(), 100.0)}
        _evict_fallback_lock(locks, "s1", now=200.0)
        assert "s1" not in locks
        assert "s2" in locks

    def test_evict_nonexistent_key_is_noop(self) -> None:
        locks = {"s1": (asyncio.Lock(), 100.0)}
        _evict_fallback_lock(locks, "s2", now=200.0)
        assert "s1" in locks

    def test_purges_stale_when_over_max(self) -> None:
        locks = {}
        for i in range(_FALLBACK_LOCK_MAX_ENTRIES + 1):
            locks[f"s{i}"] = (asyncio.Lock(), 100.0)
        locks["fresh"] = (asyncio.Lock(), 99999.0)
        _evict_fallback_lock(locks, "s0", now=9999.0)
        assert "fresh" in locks
        assert len(locks) <= 2

    def test_max_not_reached_no_purge(self) -> None:
        locks = {"s1": (asyncio.Lock(), 100.0)}
        _evict_fallback_lock(locks, "s1", now=200.0)
        assert len(locks) == 0

    def test_boundary_at_max(self) -> None:
        locks = {f"s{i}": (asyncio.Lock(), 100.0) for i in range(_FALLBACK_LOCK_MAX_ENTRIES)}
        key = "s0"
        _evict_fallback_lock(locks, key, now=9999.0)
        assert len(locks) == _FALLBACK_LOCK_MAX_ENTRIES - 1

    def test_boundary_over_max_triggers_purge(self) -> None:
        locks = {f"s{i}": (asyncio.Lock(), 100.0) for i in range(_FALLBACK_LOCK_MAX_ENTRIES + 2)}
        key = "s0"
        _evict_fallback_lock(locks, key, now=9999.0)
        assert len(locks) <= 1

    def test_custom_now(self) -> None:
        locks = {"s1": (asyncio.Lock(), 100.0)}
        _evict_fallback_lock(locks, "s1", now=50.0)
        assert "s1" not in locks

    def test_ttl_not_triggered_below_cap(self) -> None:
        locks = {"s1": (asyncio.Lock(), 1.0)}
        _evict_fallback_lock(locks, "s1", now=99999.0)
        assert len(locks) == 0

    def test_purges_only_stale_not_fresh(self) -> None:
        locks = {f"s{i}": (asyncio.Lock(), 0.0) for i in range(_FALLBACK_LOCK_MAX_ENTRIES)}
        locks["fresh"] = (asyncio.Lock(), 99999.0)
        key = "s0"
        _evict_fallback_lock(locks, key, now=50000.0)
        assert "fresh" in locks


class TestPurgeFallbackLocks:
    """Evict-on-write: full purge path."""

    def test_purge_removes_all(self) -> None:
        locks = {"s1": (asyncio.Lock(), 1.0), "s2": (asyncio.Lock(), 2.0)}
        count = _purge_fallback_locks(locks)
        assert count == 2
        assert len(locks) == 0

    def test_purge_empty(self) -> None:
        count = _purge_fallback_locks({})
        assert count == 0


class TestTurnRunnerFallbackLockLifecycle:
    """Integration: TurnRunner creates + evicts fallback locks."""

    @pytest.fixture
    def runner(self) -> TurnRunner:
        provider = MagicMock()
        provider.provider_name = "stub"
        async def _chat(*a, **kw): yield DoneEvent()
        provider.chat = _chat
        selector = MagicMock()
        selector.resolve.return_value = provider
        selector.clone.return_value = selector
        selector.current_config = MagicMock(model="m")
        session_manager = MagicMock()
        session_manager.get = AsyncMock(return_value=None)
        session_manager.append_message = AsyncMock(return_value=None)
        session_manager.update = AsyncMock(return_value=None)
        session_manager.get_compaction_summary = AsyncMock(return_value=None)
        return TurnRunner(
            provider_selector=selector,
            session_manager=session_manager,
            session_lock_provider=None,
        )

    @pytest.mark.asyncio
    async def test_fallback_lock_attr_created(self, runner: TurnRunner) -> None:
        assert runner._per_session_lock_dict is not None
        assert len(runner._per_session_lock_dict) == 0

    @pytest.mark.asyncio
    async def test_run_creates_and_evicts(self, runner: TurnRunner) -> None:
        sk = "agent:main:test-evict"
        from agentos.tools.types import ToolContext
        tc = ToolContext(session_key=sk)
        async for _ in runner.run(message="hello", session_key=sk, tool_context=tc):
            assert sk in runner._per_session_lock_dict
        assert sk not in runner._per_session_lock_dict

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent(
        self, runner: TurnRunner
    ) -> None:
        keys = ["agent:main:a", "agent:main:b"]
        for sk in keys:
            ctx = ToolContext(session_key=sk)
            async for _ in runner.run(
                message="hi", session_key=sk, tool_context=ctx
            ):
                pass
        assert "agent:main:a" not in runner._per_session_lock_dict
        assert "agent:main:b" not in runner._per_session_lock_dict

    @pytest.mark.asyncio
    async def test_concurrent_50_sessions(
        self, runner: TurnRunner
    ) -> None:
        async def _run(sk: str) -> None:
            ctx = ToolContext(session_key=sk)
            async for _ in runner.run(
                message="x", session_key=sk, tool_context=ctx
            ):
                pass
        keys = [f"agent:main:c-{i:03d}" for i in range(50)]
        await asyncio.gather(*(_run(k) for k in keys))
        remaining = [k for k in keys if k in (runner._per_session_lock_dict or {})]
        assert remaining == [], f"Leaked: {remaining}"

    @pytest.mark.asyncio
    async def test_purge_after_run(
        self, runner: TurnRunner
    ) -> None:
        ctx = ToolContext(session_key="agent:main:p")
        async for _ in runner.run(
            message="x", session_key="agent:main:p", tool_context=ctx
        ):
            pass
        if runner._per_session_lock_dict is not None:
            _purge_fallback_locks(runner._per_session_lock_dict)
        assert runner._per_session_lock_dict is not None and len(runner._per_session_lock_dict) == 0

    @pytest.mark.asyncio
    async def test_reentry_path_does_not_leak(self, runner: TurnRunner) -> None:
        """Caller-holds-lock path (re-entry) should also evict."""
        from agentos.tools.types import ToolContext
        sk = "agent:main:reentry"
        tc = ToolContext(session_key=sk)
        lock = runner.get_session_lock(sk)
        async with lock:
            async for _ in runner.run(message="x", session_key=sk, tool_context=tc):
                pass
        assert sk not in runner._per_session_lock_dict
