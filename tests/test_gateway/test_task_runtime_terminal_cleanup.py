"""Tests for the TaskRuntime terminal-state dict leak fix.

Verifies that, at terminal state, the four short-lived tracking dicts
(``_tasks``, ``_running_by_session``, ``_pending_by_session``,
``_last_envelope_by_session``) drop the task / session_key.

Also verifies that ``_session_locks`` and ``_session_execution_locks``
are cleaned (same pattern as gh-966 for SessionWriteLock) to prevent
unbounded memory growth on long-running gateways, while still being
re-created via ``setdefault`` when the next task for the same session
arrives. Covers exception-path cleanup and a 10 000-task
tracemalloc-bounded soak.
"""

from __future__ import annotations

import asyncio
import gc
import tracemalloc
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentos.gateway import task_runtime
from agentos.gateway.routing import RouteEnvelope, SourceKind
from agentos.gateway.task_runtime import TaskRuntime
from agentos.session.models import AgentTaskRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_envelope(session_key: str = "agent-1::sess-1") -> RouteEnvelope:
    return RouteEnvelope(
        source_kind=SourceKind.WEB,
        source_name="test",
        agent_id="agent-1",
        session_key=session_key,
        input_provenance={"kind": "test"},
    )


def _make_storage() -> Any:
    """Minimal storage mock."""
    storage = MagicMock()
    task_db: dict[str, AgentTaskRecord] = {}

    async def create(record: AgentTaskRecord) -> None:
        task_db[record.task_id] = record

    async def update(task_id: str, **kwargs: Any) -> None:
        rec = task_db.get(task_id)
        if rec is None:
            return
        for k, v in kwargs.items():
            if hasattr(rec, k):
                object.__setattr__(rec, k, v)

    async def get(task_id: str) -> AgentTaskRecord | None:
        return task_db.get(task_id)

    async def list_tasks(**_: Any) -> list[AgentTaskRecord]:
        return list(task_db.values())

    storage.create_agent_task = create
    storage.update_agent_task = update
    storage.get_agent_task = get
    storage.list_agent_tasks = list_tasks
    return storage


def _make_runtime(
    turn_handler: Callable[..., Awaitable[Any]] | None = None,
    max_concurrency: int = 4,
    max_pending_per_session: int | None = 64,
) -> TaskRuntime:
    async def _default_handler(_run: Any) -> None:
        pass

    return TaskRuntime(
        storage=_make_storage(),
        turn_handler=turn_handler or _default_handler,
        max_concurrency=max_concurrency,
        max_pending_per_session=max_pending_per_session,
    )


# ---------------------------------------------------------------------------
# terminal_clears_all_dicts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_clears_all_dicts() -> None:
    """After a task succeeds, all tracking dicts including session locks must not contain its key.

    ``_session_locks`` and ``_session_execution_locks`` are cleaned because
    ``execution_lock`` serialises per-session ``_execute`` — no concurrent
    writer means no split-brain.
    """
    rt = _make_runtime()
    env = _make_envelope("agent-1::sess-a")
    handle = await rt.enqueue(env, "hello")
    await rt.wait(handle.task_id, timeout=2.0)

    sk = env.session_key
    assert handle.task_id not in rt._tasks
    assert sk not in rt._running_by_session
    assert sk not in rt._pending_by_session
    assert sk not in rt._last_envelope_by_session
    # session locks are evicted after _execute finishes
    assert sk not in rt._session_locks
    assert sk not in rt._session_execution_locks


# ---------------------------------------------------------------------------
# cancel_clears_dicts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_clears_dicts() -> None:
    """After a task is cancelled, all tracking dicts and session locks must not contain its key."""
    started = asyncio.Event()
    blocker = asyncio.Event()

    async def _blocking_handler(_run: Any) -> None:
        started.set()
        await blocker.wait()  # blocks until test cancels

    rt = _make_runtime(turn_handler=_blocking_handler)
    env = _make_envelope("agent-1::sess-b")
    handle = await rt.enqueue(env, "hello")

    # Wait for the handler to actually start, then cancel.
    await asyncio.wait_for(started.wait(), timeout=2.0)
    await rt.cancel(task_id=handle.task_id)
    await rt.wait(handle.task_id, timeout=2.0)

    sk = env.session_key
    assert handle.task_id not in rt._tasks
    assert sk not in rt._running_by_session
    assert sk not in rt._pending_by_session
    assert sk not in rt._last_envelope_by_session
    assert sk not in rt._session_locks
    assert sk not in rt._session_execution_locks


# ---------------------------------------------------------------------------
# session_lock_kept_during_pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_lock_kept_during_pending() -> None:
    """Session locks exist while tasks execute and are evicted after all complete."""
    first_release = asyncio.Event()
    second_release = asyncio.Event()
    call_count = 0

    async def _blocking_handler(_run: Any) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await first_release.wait()
        else:
            await second_release.wait()

    rt = _make_runtime(turn_handler=_blocking_handler, max_concurrency=1)
    env = _make_envelope("agent-1::sess-c")

    handle1 = await rt.enqueue(env, "first")
    await asyncio.sleep(0.02)
    handle2 = await rt.enqueue(env, "second")
    sk = env.session_key

    # First is executing -> lock exists
    assert sk in rt._session_locks

    # Release both
    first_release.set()
    second_release.set()
    await rt.wait(handle1.task_id, timeout=2.0)
    await rt.wait(handle2.task_id, timeout=2.0)

    # Both done -> locks evicted
    assert sk not in rt._session_locks
    assert sk not in rt._session_execution_locks


# ---------------------------------------------------------------------------
# exception path cleans up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exception_path_clears_dicts() -> None:
    """Even when the turn handler raises, cleanup must run for all tracking dicts
    including session locks.
    """

    async def _failing_handler(_run: Any) -> None:
        raise RuntimeError("deliberate failure")

    rt = _make_runtime(turn_handler=_failing_handler)
    env = _make_envelope("agent-1::sess-d")
    handle = await rt.enqueue(env, "hello")
    await rt.wait(handle.task_id, timeout=2.0)

    sk = env.session_key
    assert handle.task_id not in rt._tasks
    assert sk not in rt._running_by_session
    assert sk not in rt._pending_by_session
    assert sk not in rt._last_envelope_by_session
    assert sk not in rt._session_locks
    assert sk not in rt._session_execution_locks


# ---------------------------------------------------------------------------
# no_leak_under_load (tracemalloc quantitative)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_leak_under_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """10 000 tasks, each <=50 ms; dict sizes after GC must be within ±2 of baseline."""
    num_tasks = 10_000
    session_count = 50  # rotate sessions to mimic real load
    monkeypatch.setattr(task_runtime, "_emit_metric", lambda *_args, **_kwargs: None)

    async def _instant_handler(_run: Any) -> None:
        pass  # returns immediately — well under 50 ms

    rt = _make_runtime(
        turn_handler=_instant_handler,
        max_concurrency=32,
        max_pending_per_session=None,
    )

    # --- baseline snapshot (before any tasks) ---
    gc.collect()
    tracemalloc.start()
    snap_before = tracemalloc.take_snapshot()

    baseline_tasks = len(rt._tasks)
    baseline_pending = len(rt._pending_by_session)
    baseline_running = len(rt._running_by_session)
    baseline_envelope = len(rt._last_envelope_by_session)

    # --- run 10 000 tasks ---
    handles = []
    for i in range(num_tasks):
        sk = f"agent-1::sess-load-{i % session_count}"
        env = _make_envelope(sk)
        h = await rt.enqueue(env, f"msg-{i}")
        handles.append(h)

    # Wait for all to complete.
    await asyncio.gather(*(rt.wait(h.task_id, timeout=30.0) for h in handles))

    # --- post-GC snapshot ---
    gc.collect()
    snap_after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    after_tasks = len(rt._tasks)
    after_locks = len(rt._session_locks)
    after_execution_locks = len(rt._session_execution_locks)
    after_pending = len(rt._pending_by_session)
    after_running = len(rt._running_by_session)
    after_envelope = len(rt._last_envelope_by_session)

    tolerance = 2
    assert abs(after_tasks - baseline_tasks) <= tolerance, (
        f"_tasks leaked: baseline={baseline_tasks}, after={after_tasks}"
    )
    # Session locks are evicted after each _execute finishes; all tasks have
    # completed so the dicts must be empty.
    assert after_locks <= tolerance, (
        f"_session_locks leaked: after={after_locks}"
    )
    assert after_execution_locks <= tolerance, (
        f"_session_execution_locks leaked: after={after_execution_locks}"
    )
    assert abs(after_pending - baseline_pending) <= tolerance, (
        f"_pending_by_session leaked: baseline={baseline_pending}, after={after_pending}"
    )
    assert abs(after_running - baseline_running) <= tolerance, (
        f"_running_by_session leaked: baseline={baseline_running}, after={after_running}"
    )
    assert abs(after_envelope - baseline_envelope) <= tolerance, (
        f"_last_envelope_by_session leaked: baseline={baseline_envelope}, after={after_envelope}"
    )

    # Confirm memory allocation delta is reasonable (no catastrophic growth).
    # Informational only — the dict-size assertions above are authoritative.
    # 10 000 asyncio tasks create significant transient allocation for
    # Task/Future/Event objects; allow up to 200 MB of incidental growth.
    top_stats = snap_after.compare_to(snap_before, "lineno")
    total_added = sum(s.size_diff for s in top_stats if s.size_diff > 0)
    assert total_added < 200 * 1024 * 1024, f"Unexpected memory growth: {total_added / 1024:.1f} KB"


# ---------------------------------------------------------------------------
# execution_locks_evicted_on_exit (gh-1040 regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execution_locks_evicted_on_exit() -> None:
    """Both _session_locks and _session_execution_locks must be evicted after a
    task's _execute completes, even on early return (cancelled) or exception.
    """
    rt = _make_runtime()
    env = _make_envelope("agent-1::sess-gh1040")
    handle = await rt.enqueue(env, "hello")
    await rt.wait(handle.task_id, timeout=2.0)

    sk = env.session_key
    assert sk not in rt._session_locks
    assert sk not in rt._session_execution_locks


@pytest.mark.asyncio
async def test_execution_locks_evicted_after_cancel() -> None:
    """Ensure cancellation path also evicts locks."""
    started = asyncio.Event()
    blocker = asyncio.Event()

    async def _blocking_handler(_run: Any) -> None:
        started.set()
        await blocker.wait()

    rt = _make_runtime(turn_handler=_blocking_handler)
    env = _make_envelope("agent-1::sess-gh1040b")
    handle = await rt.enqueue(env, "hello")
    await asyncio.wait_for(started.wait(), timeout=2.0)
    await rt.cancel(task_id=handle.task_id)
    await rt.wait(handle.task_id, timeout=2.0)

    sk = env.session_key
    assert sk not in rt._session_locks
    assert sk not in rt._session_execution_locks
