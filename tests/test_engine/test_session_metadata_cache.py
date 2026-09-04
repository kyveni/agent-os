"""UsageTracker._session_metadata cache eviction tests — upgraded 2-phase."""

from __future__ import annotations

from agentos.engine.usage import (
    _SESSION_METADATA_CACHE_MAX,
    UsageTracker,
)


class TestEvictStaleSessionMetadata:
    """Unit: _evict_stale_session_metadata — Phase 1 (TTL) + Phase 2 (cap)."""

    def test_evicts_when_over_max(self) -> None:
        tracker = UsageTracker()
        for i in range(_SESSION_METADATA_CACHE_MAX + 10):
            tracker._session_metadata[f"session-{i}"] = ("main", "")
        tracker._evict_stale_session_metadata()
        assert len(tracker._session_metadata) <= _SESSION_METADATA_CACHE_MAX

    def test_empty_is_fine(self) -> None:
        tracker = UsageTracker()
        tracker._evict_stale_session_metadata()
        assert len(tracker._session_metadata) == 0

    def test_at_max_is_fine(self) -> None:
        tracker = UsageTracker()
        for i in range(_SESSION_METADATA_CACHE_MAX):
            tracker._session_metadata[f"session-{i}"] = ("main", "")
        tracker._evict_stale_session_metadata()
        assert len(tracker._session_metadata) == _SESSION_METADATA_CACHE_MAX

    def test_under_max_is_fine(self) -> None:
        tracker = UsageTracker()
        for i in range(10):
            tracker._session_metadata[f"session-{i}"] = ("main", "")
        tracker._evict_stale_session_metadata()
        assert len(tracker._session_metadata) == 10

    def test_removes_oldest_first(self) -> None:
        tracker = UsageTracker()
        for i in range(_SESSION_METADATA_CACHE_MAX + 20):
            tracker._session_metadata[f"session-{i}"] = ("main", "")
        tracker._evict_stale_session_metadata()
        assert "session-0" not in tracker._session_metadata
        assert "session-19" not in tracker._session_metadata
        last = f"session-{_SESSION_METADATA_CACHE_MAX + 19}"
        assert last in tracker._session_metadata

    def test_boundary_at_max(self) -> None:
        tracker = UsageTracker()
        for i in range(_SESSION_METADATA_CACHE_MAX):
            tracker._session_metadata[f"s-{i}"] = ("main", "")
        tracker._evict_stale_session_metadata()
        assert len(tracker._session_metadata) == _SESSION_METADATA_CACHE_MAX

    def test_boundary_one_over(self) -> None:
        tracker = UsageTracker()
        for i in range(_SESSION_METADATA_CACHE_MAX + 1):
            tracker._session_metadata[f"s-{i}"] = ("main", "")
        tracker._evict_stale_session_metadata()
        assert len(tracker._session_metadata) == _SESSION_METADATA_CACHE_MAX


class TestGetSessionScope:
    """Integration: get_session_scope triggers eviction."""

    def test_creates_entry_on_miss(self) -> None:
        tracker = UsageTracker()
        result = tracker.get_session_scope("agent:test:session-a")
        assert result == ("test", "session-a")
        assert "agent:test:session-a" in tracker._session_metadata

    def test_returns_cached_on_hit(self) -> None:
        tracker = UsageTracker()
        first = tracker.get_session_scope("agent:test:123")
        second = tracker.get_session_scope("agent:test:123")
        assert first == second
        assert len(tracker._session_metadata) == 1

    def test_eviction_triggered_on_miss(self) -> None:
        """After filling past max, next miss triggers eviction."""
        tracker = UsageTracker()
        # Bypass: direct fill bypasses eviction check
        for i in range(_SESSION_METADATA_CACHE_MAX + 5):
            tracker._session_metadata[f"session-{i}"] = ("a", "")
        # Next get should trigger evict
        tracker.get_session_scope("agent:trigger:eviction")
        assert len(tracker._session_metadata) <= _SESSION_METADATA_CACHE_MAX

    def test_different_sessions_different_keys(self) -> None:
        tracker = UsageTracker()
        result_a = tracker.get_session_scope("agent:alpha:s1")
        result_b = tracker.get_session_scope("agent:beta:s2")
        assert result_a != result_b
        assert len(tracker._session_metadata) == 2
