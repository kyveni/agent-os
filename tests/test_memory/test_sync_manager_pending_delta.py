"""Test that pending session delta is consumed after search-time sync with no session indexer."""
from __future__ import annotations

from agentos.memory.sync_manager import MemorySyncManager, SessionDeltaTracker


class TestPendingConsumptionWithoutSessionIndexer:
    async def test_consumed_after_search_sync(self) -> None:
        manager = MemorySyncManager.__new__(MemorySyncManager)
        manager._workspace = None  # type: ignore[assignment]
        manager._session_indexer = None
        manager._delta = SessionDeltaTracker()
        manager._delta.record(byte_count=10)
        assert manager._delta.has_pending()
        manager._delta.reset()
        assert not manager._delta.has_pending()

    async def test_no_pending_delta_takes_fast_path(self) -> None:
        manager = MemorySyncManager.__new__(MemorySyncManager)
        manager._workspace = None  # type: ignore[assignment]
        manager._session_indexer = None
        manager._delta = SessionDeltaTracker()
        manager._dirty = False
        assert not manager._delta.has_pending()
