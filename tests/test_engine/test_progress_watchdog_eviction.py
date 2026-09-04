"""ProgressWatchdog repeat cache eviction tests — upgraded w/ 2-phase + boundary."""

from __future__ import annotations

from agentos.engine.progress_watchdog import (
    _MAX_REPEAT_TRACKED_KEYS,
    ProgressObservation,
    ProgressWatchdog,
    ToolCallSignature,
)


class TestEvictStaleRepeatCache:
    """Unit tests for _evict_stale_repeat_cache — Phase 1 + 2."""

    def test_evicts_when_over_max(self) -> None:
        watchdog = ProgressWatchdog()
        for i in range(_MAX_REPEAT_TRACKED_KEYS + 10):
            watchdog._repeat_counts[(f"t-{i}", f"h-{i}")] = 1
            watchdog._repeat_results[(f"t-{i}", f"h-{i}")] = f"r-{i}"
        watchdog._evict_stale_repeat_cache()
        assert len(watchdog._repeat_counts) == _MAX_REPEAT_TRACKED_KEYS
        assert len(watchdog._repeat_results) == _MAX_REPEAT_TRACKED_KEYS

    def test_empty_is_fine(self) -> None:
        watchdog = ProgressWatchdog()
        watchdog._evict_stale_repeat_cache()
        assert len(watchdog._repeat_counts) == 0
        assert len(watchdog._repeat_results) == 0

    def test_at_max_is_fine(self) -> None:
        watchdog = ProgressWatchdog()
        for i in range(_MAX_REPEAT_TRACKED_KEYS):
            watchdog._repeat_counts[(f"t-{i}", f"h-{i}")] = 1
            watchdog._repeat_results[(f"t-{i}", f"h-{i}")] = f"r-{i}"
        watchdog._evict_stale_repeat_cache()
        assert len(watchdog._repeat_counts) == _MAX_REPEAT_TRACKED_KEYS
        assert len(watchdog._repeat_results) == _MAX_REPEAT_TRACKED_KEYS

    def test_under_max_is_fine(self) -> None:
        watchdog = ProgressWatchdog()
        for i in range(10):
            watchdog._repeat_counts[(f"t-{i}", f"h-{i}")] = 1
            watchdog._repeat_results[(f"t-{i}", f"h-{i}")] = f"r-{i}"
        watchdog._evict_stale_repeat_cache()
        assert len(watchdog._repeat_counts) == 10

    def test_removes_oldest_first(self) -> None:
        watchdog = ProgressWatchdog()
        for i in range(_MAX_REPEAT_TRACKED_KEYS + 5):
            watchdog._repeat_counts[(f"t-{i}", f"h-{i}")] = 1
            watchdog._repeat_results[(f"t-{i}", f"h-{i}")] = f"r-{i}"
        watchdog._evict_stale_repeat_cache()
        # Oldest 5 evicted
        assert ("t-0", "h-0") not in watchdog._repeat_counts
        assert ("t-4", "h-4") not in watchdog._repeat_counts
        # Newest preserved
        newest_key = (f"t-{_MAX_REPEAT_TRACKED_KEYS + 4}", f"h-{_MAX_REPEAT_TRACKED_KEYS + 4}")
        assert newest_key in watchdog._repeat_counts

    def test_boundary_at_max(self) -> None:
        watchdog = ProgressWatchdog()
        for i in range(_MAX_REPEAT_TRACKED_KEYS):
            watchdog._repeat_counts[(f"t-{i}", f"h-{i}")] = 1
            watchdog._repeat_results[(f"t-{i}", f"h-{i}")] = f"r-{i}"
        watchdog._evict_stale_repeat_cache()
        # All preserved at boundary
        assert len(watchdog._repeat_counts) == _MAX_REPEAT_TRACKED_KEYS

    def test_boundary_one_over(self) -> None:
        watchdog = ProgressWatchdog()
        for i in range(_MAX_REPEAT_TRACKED_KEYS + 1):
            watchdog._repeat_counts[(f"t-{i}", f"h-{i}")] = 1
            watchdog._repeat_results[(f"t-{i}", f"h-{i}")] = f"r-{i}"
        watchdog._evict_stale_repeat_cache()
        # One evicted, now at max
        assert len(watchdog._repeat_counts) == _MAX_REPEAT_TRACKED_KEYS

    def test_symmetry_both_dicts(self) -> None:
        watchdog = ProgressWatchdog()
        for i in range(_MAX_REPEAT_TRACKED_KEYS + 50):
            watchdog._repeat_counts[(f"t-{i}", f"h-{i}")] = 1
            watchdog._repeat_results[(f"t-{i}", f"h-{i}")] = f"r-{i}"
        watchdog._evict_stale_repeat_cache()
        assert len(watchdog._repeat_counts) == len(watchdog._repeat_results)
        assert set(watchdog._repeat_counts) == set(watchdog._repeat_results)


class TestRecordRepeatedToolCalls:
    """Integration through the real _record_repeated_tool_calls path."""

    def _observe(self, tool_name: str, args_hash: str, result_hash: str) -> ProgressObservation:
        sig = ToolCallSignature(
            tool_name=tool_name,
            arguments_hash=args_hash,
            result_hash=result_hash,
        )
        return ProgressObservation(
            tool_calls=(sig,),
            iteration=1,
            provider_call_count=1,
        )

    def test_tracks_repeated_calls(self) -> None:
        watchdog = ProgressWatchdog(repeated_tool_call_threshold=3)
        obs = self._observe("read_file", "abc", "def")
        for _ in range(3):
            watchdog._record_repeated_tool_calls(obs)
        assert watchdog._repeat_counts[("read_file", "abc")] >= 3

    def test_tracks_new_signatures(self) -> None:
        watchdog = ProgressWatchdog()
        obs_a = self._observe("read_file", "abc", "def")
        obs_b = self._observe("write_file", "xyz", "uvw")
        watchdog._record_repeated_tool_calls(obs_a)
        watchdog._record_repeated_tool_calls(obs_b)
        assert ("read_file", "abc") in watchdog._repeat_counts
        assert ("write_file", "xyz") in watchdog._repeat_counts

    def test_different_result_is_new_entry(self) -> None:
        watchdog = ProgressWatchdog()
        obs1 = self._observe("read_file", "abc", "result1")
        obs2 = self._observe("read_file", "abc", "result2")
        watchdog._record_repeated_tool_calls(obs1)
        watchdog._record_repeated_tool_calls(obs2)
        assert watchdog._repeat_counts[("read_file", "abc")] == 1

    def test_eviction_triggered_through_observe(self) -> None:
        watchdog = ProgressWatchdog()
        for i in range(_MAX_REPEAT_TRACKED_KEYS + 5):
            obs = self._observe(f"tool-{i}", f"hash-{i}", f"result-{i}")
            watchdog._record_repeated_tool_calls(obs)
        assert len(watchdog._repeat_counts) <= _MAX_REPEAT_TRACKED_KEYS
