"""IntentApprovalCache._entries eviction tests — 2-phase (TTL + cap)."""

from __future__ import annotations

from agentos.application.intent_cache import (
    _INTENT_CACHE_MAX,
    IntentApprovalCache,
)


class TestEvictStaleEntries:
    """Unit: _evict_stale_entries — Phase 1 (TTL) + Phase 2 (cap)."""

    def test_evicts_expired_entries(self) -> None:
        cache = IntentApprovalCache()
        cache._entries[("delete", "/a")] = (100.0, "once")
        cache._entries[("delete", "/b")] = (200.0, "once")
        cache._evict_stale_entries(now=150.0)
        assert ("delete", "/a") not in cache._entries
        assert ("delete", "/b") in cache._entries

    def test_evicts_when_over_max(self) -> None:
        cache = IntentApprovalCache()
        for i in range(_INTENT_CACHE_MAX + 10):
            cache._entries[("k", f"t-{i}")] = (99999.0, "once")
        cache._evict_stale_entries(now=50000.0)
        assert len(cache._entries) == _INTENT_CACHE_MAX

    def test_empty_is_fine(self) -> None:
        cache = IntentApprovalCache()
        cache._evict_stale_entries(now=100.0)
        assert len(cache._entries) == 0

    def test_at_max_is_fine(self) -> None:
        cache = IntentApprovalCache()
        for i in range(_INTENT_CACHE_MAX):
            cache._entries[("k", f"t-{i}")] = (99999.0, "once")
        cache._evict_stale_entries(now=50000.0)
        assert len(cache._entries) == _INTENT_CACHE_MAX

    def test_under_max_is_fine(self) -> None:
        cache = IntentApprovalCache()
        for i in range(10):
            cache._entries[("k", f"t-{i}")] = (99999.0, "once")
        cache._evict_stale_entries(now=50000.0)
        assert len(cache._entries) == 10

    def test_removes_oldest_first(self) -> None:
        cache = IntentApprovalCache()
        for i in range(_INTENT_CACHE_MAX + 20):
            cache._entries[("k", f"t-{i}")] = (99999.0, "once")
        cache._evict_stale_entries(now=50000.0)
        assert ("k", "t-0") not in cache._entries
        assert ("k", "t-19") not in cache._entries
        assert ("k", f"t-{_INTENT_CACHE_MAX + 19}") in cache._entries

    def test_boundary_at_max(self) -> None:
        cache = IntentApprovalCache()
        for i in range(_INTENT_CACHE_MAX):
            cache._entries[("k", f"t-{i}")] = (99999.0, "once")
        cache._evict_stale_entries(now=50000.0)
        assert len(cache._entries) == _INTENT_CACHE_MAX

    def test_boundary_one_over(self) -> None:
        cache = IntentApprovalCache()
        for i in range(_INTENT_CACHE_MAX + 1):
            cache._entries[("k", f"t-{i}")] = (99999.0, "once")
        cache._evict_stale_entries(now=50000.0)
        assert len(cache._entries) == _INTENT_CACHE_MAX


class TestPurgeSession:
    """Unit: purge_session — targeted eviction."""

    def test_purge_removes_existing(self) -> None:
        cache = IntentApprovalCache()
        cache._entries[("delete", "/a")] = (99999.0, "once")
        cache.purge_session(("delete", "/a"))
        assert ("delete", "/a") not in cache._entries

    def test_purge_nonexistent_is_noop(self) -> None:
        cache = IntentApprovalCache()
        cache._entries[("delete", "/a")] = (99999.0, "once")
        cache.purge_session(("delete", "/b"))
        assert ("delete", "/a") in cache._entries


class TestRecord:
    """Integration: record triggers eviction on write."""

    def test_record_triggers_eviction(self) -> None:
        cache = IntentApprovalCache()
        for i in range(_INTENT_CACHE_MAX + 5):
            cache._entries[("k", f"t-{i}")] = (99999.0, "once")
        cache.record("rm /trigger")
        assert len(cache._entries) <= _INTENT_CACHE_MAX

    def test_record_multiple_targets(self) -> None:
        cache = IntentApprovalCache()
        for i in range(_INTENT_CACHE_MAX + 5):
            cache._entries[("k", f"t-{i}")] = (99999.0, "once")
        cache.record("rm /a /b /c")
        assert len(cache._entries) <= _INTENT_CACHE_MAX

    def test_record_always_same_eviction(self) -> None:
        cache = IntentApprovalCache()
        for i in range(_INTENT_CACHE_MAX + 5):
            cache._entries[("k", f"t-{i}")] = (99999.0, "once")
        cache.record_always("rm /target")
        assert len(cache._entries) <= _INTENT_CACHE_MAX

    def test_empty_command_noop(self) -> None:
        cache = IntentApprovalCache()
        result = cache.record("")
        assert result == []

    def test_recent_entries_survive_eviction(self) -> None:
        cache = IntentApprovalCache()
        for i in range(_INTENT_CACHE_MAX + 10):
            cache._entries[("k", f"t-{i}")] = (99999.0, "once")
        cache.record("rm /fresh-target")
        # Windows normalizes /fresh-target to D:\fresh-target
        assert any(k[0] == "delete" for k in cache._entries)
