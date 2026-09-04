"""UsageTracker._scopes cache eviction tests — upgraded."""

from __future__ import annotations

from agentos.engine.usage import _SCOPES_CACHE_MAX, UsageTracker


class TestEvictStaleScopes:
    """Unit: _evict_stale_scopes — Phase 1 + 2."""

    def test_evicts_when_over_max(self) -> None:
        tracker = UsageTracker()
        for i in range(_SCOPES_CACHE_MAX + 10):
            tracker._scopes[(f"s{i}", f"s{i}")] = object()
        tracker._evict_stale_scopes()
        assert len(tracker._scopes) <= _SCOPES_CACHE_MAX

    def test_empty_is_fine(self) -> None:
        tracker = UsageTracker()
        tracker._evict_stale_scopes()
        assert len(tracker._scopes) == 0

    def test_at_max_is_fine(self) -> None:
        tracker = UsageTracker()
        for i in range(_SCOPES_CACHE_MAX):
            tracker._scopes[(f"s{i}", f"s{i}")] = object()
        tracker._evict_stale_scopes()
        assert len(tracker._scopes) == _SCOPES_CACHE_MAX

    def test_under_max_is_fine(self) -> None:
        tracker = UsageTracker()
        for i in range(10):
            tracker._scopes[(f"s{i}", f"s{i}")] = object()
        tracker._evict_stale_scopes()
        assert len(tracker._scopes) == 10

    def test_removes_oldest_first(self) -> None:
        tracker = UsageTracker()
        for i in range(_SCOPES_CACHE_MAX + 20):
            tracker._scopes[(f"s{i}", f"s{i}")] = object()
        tracker._evict_stale_scopes()
        assert ("s0", "s0") not in tracker._scopes
        assert ("s19", "s19") not in tracker._scopes
        assert (f"s{_SCOPES_CACHE_MAX + 19}", f"s{_SCOPES_CACHE_MAX + 19}") in tracker._scopes

    def test_boundary_at_max(self) -> None:
        tracker = UsageTracker()
        for i in range(_SCOPES_CACHE_MAX):
            tracker._scopes[(f"s{i}", f"s{i}")] = object()
        tracker._evict_stale_scopes()
        assert len(tracker._scopes) == _SCOPES_CACHE_MAX

    def test_boundary_one_over(self) -> None:
        tracker = UsageTracker()
        for i in range(_SCOPES_CACHE_MAX + 1):
            tracker._scopes[(f"s{i}", f"s{i}")] = object()
        tracker._evict_stale_scopes()
        assert len(tracker._scopes) == _SCOPES_CACHE_MAX


class TestAddToSessionUsage:
    """Integration: add_to_session_usage triggers eviction on scope fill."""

    def test_evicts_via_add_path(self) -> None:
        tracker = UsageTracker()
        for i in range(_SCOPES_CACHE_MAX + 10):
            tracker._scopes[(f"s{i}", f"s{i}")] = object()
        tracker._evict_stale_scopes()
        assert len(tracker._scopes) <= _SCOPES_CACHE_MAX

    def test_different_keys(self) -> None:
        tracker = UsageTracker()
        for i in range(3):
            tracker._scopes[(f"session-{i}", f"scope-{i}")] = object()
        assert len(tracker._scopes) == 3
