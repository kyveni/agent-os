"""CacheBreakMonitor._baselines eviction tests — 2-phase."""

from __future__ import annotations

from agentos.engine.cache_break_monitor import (
    _BASELINE_CACHE_MAX,
    CacheBreakMonitor,
)
from agentos.provider import ChatConfig


def _snapshot(monitor: CacheBreakMonitor) -> object:
    return monitor.record_prompt_state(
        messages=[],
        tools=None,
        config=ChatConfig(),
        model="m",
    )


class TestEvictStaleBaselines:
    """Unit: _evict_stale_baselines — Phase 2 (cap) eviction."""

    def test_evicts_when_over_max(self) -> None:
        monitor = CacheBreakMonitor()
        for i in range(_BASELINE_CACHE_MAX + 10):
            monitor._baselines[f"s-{i}"] = _snapshot(monitor)
        monitor._evict_stale_baselines()
        assert len(monitor._baselines) == _BASELINE_CACHE_MAX

    def test_empty_is_fine(self) -> None:
        monitor = CacheBreakMonitor()
        monitor._evict_stale_baselines()
        assert len(monitor._baselines) == 0

    def test_at_max_is_fine(self) -> None:
        monitor = CacheBreakMonitor()
        for i in range(_BASELINE_CACHE_MAX):
            monitor._baselines[f"s-{i}"] = _snapshot(monitor)
        monitor._evict_stale_baselines()
        assert len(monitor._baselines) == _BASELINE_CACHE_MAX

    def test_under_max_is_fine(self) -> None:
        monitor = CacheBreakMonitor()
        for i in range(10):
            monitor._baselines[f"s-{i}"] = _snapshot(monitor)
        monitor._evict_stale_baselines()
        assert len(monitor._baselines) == 10

    def test_removes_oldest_first(self) -> None:
        monitor = CacheBreakMonitor()
        for i in range(_BASELINE_CACHE_MAX + 20):
            monitor._baselines[f"s-{i}"] = _snapshot(monitor)
        monitor._evict_stale_baselines()
        assert "s-0" not in monitor._baselines
        assert "s-19" not in monitor._baselines
        last = f"s-{_BASELINE_CACHE_MAX + 19}"
        assert last in monitor._baselines

    def test_boundary_at_max(self) -> None:
        monitor = CacheBreakMonitor()
        for i in range(_BASELINE_CACHE_MAX):
            monitor._baselines[f"s-{i}"] = _snapshot(monitor)
        monitor._evict_stale_baselines()
        assert len(monitor._baselines) == _BASELINE_CACHE_MAX

    def test_boundary_one_over(self) -> None:
        monitor = CacheBreakMonitor()
        for i in range(_BASELINE_CACHE_MAX + 1):
            monitor._baselines[f"s-{i}"] = _snapshot(monitor)
        monitor._evict_stale_baselines()
        assert len(monitor._baselines) == _BASELINE_CACHE_MAX


class TestPurgeSession:
    """Unit: purge_session — targeted eviction."""

    def test_purge_removes_existing(self) -> None:
        monitor = CacheBreakMonitor()
        monitor._baselines["target"] = _snapshot(monitor)
        monitor.purge_session("target")
        assert "target" not in monitor._baselines

    def test_purge_removes_reset_pending(self) -> None:
        monitor = CacheBreakMonitor()
        monitor._baselines["s1"] = _snapshot(monitor)
        monitor._reset_pending.add("s1")
        monitor.purge_session("s1")
        assert "s1" not in monitor._reset_pending

    def test_purge_nonexistent_is_noop(self) -> None:
        monitor = CacheBreakMonitor()
        monitor._baselines["s1"] = _snapshot(monitor)
        monitor.purge_session("nonexistent")
        assert "s1" in monitor._baselines


class TestCheckResponseForCacheBreak:
    """Integration: eviction triggered through real path."""

    def test_eviction_triggered_on_insert(self) -> None:
        monitor = CacheBreakMonitor()
        s = _snapshot(monitor)
        for i in range(_BASELINE_CACHE_MAX + 5):
            monitor._baselines[f"s-{i}"] = s
        # Next call triggers eviction
        report = monitor.check_response_for_cache_break("trigger", s, 100)
        assert report.baseline_reset is False
        assert len(monitor._baselines) <= _BASELINE_CACHE_MAX

    def test_creates_new_baseline_on_first_call(self) -> None:
        monitor = CacheBreakMonitor()
        s = _snapshot(monitor)
        report = monitor.check_response_for_cache_break("first-key", s, 100)
        assert "first-key" in monitor._baselines
        assert report.reason == "baseline_initialized"

    def test_updates_baseline_on_second_call(self) -> None:
        monitor = CacheBreakMonitor()
        s = _snapshot(monitor)
        monitor.check_response_for_cache_break("sk", s, 100)
        monitor.check_response_for_cache_break("sk", s, 100)
        assert "sk" in monitor._baselines
        assert len(monitor._baselines) == 1

    def test_different_sessions_independent(self) -> None:
        monitor = CacheBreakMonitor()
        s = _snapshot(monitor)
        monitor.check_response_for_cache_break("a", s, 100)
        monitor.check_response_for_cache_break("b", s, 100)
        assert len(monitor._baselines) == 2

    def test_keeps_recent_sessions_after_eviction(self) -> None:
        monitor = CacheBreakMonitor()
        s = _snapshot(monitor)
        for i in range(_BASELINE_CACHE_MAX + 10):
            monitor._baselines[f"old-{i}"] = s
        monitor.check_response_for_cache_break("recent", s, 100)
        assert "recent" in monitor._baselines
        assert len(monitor._baselines) == _BASELINE_CACHE_MAX
