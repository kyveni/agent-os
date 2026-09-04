"""Snapshot cache eviction tests — upgraded 2-phase."""

from __future__ import annotations

from types import SimpleNamespace

from agentos.bootstrap_types import BootstrapFileReport
from agentos.engine.runtime import (
    _SNAPSHOT_CACHE_MAX_ENTRIES,
    BootstrapSnapshot,
    TurnRunner,
    _evict_stale_snapshot_entries,
)


class TestEvictStaleSnapshotEntries:
    """Unit: _evict_stale_snapshot_entries — Phase 1 (TTL) + Phase 2 (cap)."""

    def test_evicts_when_over_max(self) -> None:
        snaps = {f"k{i}": object() for i in range(_SNAPSHOT_CACHE_MAX_ENTRIES + 10)}
        count = _evict_stale_snapshot_entries(snaps)
        assert count == 10
        assert len(snaps) == _SNAPSHOT_CACHE_MAX_ENTRIES

    def test_empty_is_noop(self) -> None:
        count = _evict_stale_snapshot_entries({})
        assert count == 0

    def test_at_max_is_fine(self) -> None:
        snaps = {f"k{i}": object() for i in range(_SNAPSHOT_CACHE_MAX_ENTRIES)}
        count = _evict_stale_snapshot_entries(snaps)
        assert count == 0
        assert len(snaps) == _SNAPSHOT_CACHE_MAX_ENTRIES

    def test_under_max_is_fine(self) -> None:
        snaps = {"a": object(), "b": object()}
        count = _evict_stale_snapshot_entries(snaps)
        assert count == 0
        assert len(snaps) == 2

    def test_removes_oldest_first(self) -> None:
        snaps = {f"k{i}": object() for i in range(_SNAPSHOT_CACHE_MAX_ENTRIES + 20)}
        _evict_stale_snapshot_entries(snaps)
        assert "k0" not in snaps
        assert "k19" not in snaps
        assert f"k{_SNAPSHOT_CACHE_MAX_ENTRIES + 19}" in snaps

    def test_boundary_at_max(self) -> None:
        snaps = {f"k{i}": object() for i in range(_SNAPSHOT_CACHE_MAX_ENTRIES)}
        _evict_stale_snapshot_entries(snaps)
        assert len(snaps) == _SNAPSHOT_CACHE_MAX_ENTRIES

    def test_boundary_one_over(self) -> None:
        snaps = {f"k{i}": object() for i in range(_SNAPSHOT_CACHE_MAX_ENTRIES + 1)}
        _evict_stale_snapshot_entries(snaps)
        assert len(snaps) == _SNAPSHOT_CACHE_MAX_ENTRIES

    def test_returns_eviction_count(self) -> None:
        snaps = {f"k{i}": object() for i in range(_SNAPSHOT_CACHE_MAX_ENTRIES + 50)}
        count = _evict_stale_snapshot_entries(snaps)
        assert count == 50


class TestSnapshotCacheIntegration:
    """Integration: TurnRunner methods trigger eviction."""

    def test_refresh_triggers_eviction(self) -> None:
        runner = TurnRunner(provider_selector=None)
        for i in range(_SNAPSHOT_CACHE_MAX_ENTRIES + 10):
            runner._memory_snapshots[(f"a-{i}", f"s-{i}")] = SimpleNamespace(
                memory_md="", daily_notes={},
            )
        runner.refresh_memory_snapshot("a-0")
        assert len(runner._memory_snapshots) <= _SNAPSHOT_CACHE_MAX_ENTRIES

    def test_bootstrap_write_triggers_eviction(self) -> None:
        runner = TurnRunner(provider_selector=None)
        report = [BootstrapFileReport(filename="USER.md", raw_chars=4, injected_chars=4)]
        bootstrap = BootstrapSnapshot(workspace_files={"USER.md": "x"}, report=report)
        for i in range(_SNAPSHOT_CACHE_MAX_ENTRIES + 10):
            runner._bootstrap_snapshots[(f"a-{i}", f"s-{i}", "full")] = bootstrap
        runner._handle_bootstrap_source_write("a-0", "USER.md")
        assert len(runner._bootstrap_snapshots) <= _SNAPSHOT_CACHE_MAX_ENTRIES

    def test_mixed_snapshots_independent(self) -> None:
        runner = TurnRunner(provider_selector=None)
        report = [BootstrapFileReport(filename="USER.md", raw_chars=4, injected_chars=4)]
        bootstrap = BootstrapSnapshot(workspace_files={"USER.md": "x"}, report=report)
        for i in range(_SNAPSHOT_CACHE_MAX_ENTRIES + 5):
            runner._memory_snapshots[(f"a-{i}", f"s-{i}")] = SimpleNamespace(
                memory_md="", daily_notes={},
            )
            runner._bootstrap_snapshots[(f"a-{i}", f"s-{i}", "full")] = bootstrap
        runner.refresh_memory_snapshot("a-0")
        assert len(runner._memory_snapshots) <= _SNAPSHOT_CACHE_MAX_ENTRIES
        assert len(runner._bootstrap_snapshots) == _SNAPSHOT_CACHE_MAX_ENTRIES + 5
