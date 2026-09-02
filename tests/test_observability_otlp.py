from __future__ import annotations

from typing import Any

import httpx
import pytest
from pytest import MonkeyPatch

from agentos.observability.otlp import (
    OtlpTraceSink,
    _iso_to_unix_nano,
    _to_hex16,
    _to_hex32,
)
from agentos.observability.trace import TraceContext, TraceEvent


@pytest.fixture
def _mock_httpx(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    """Replace httpx.AsyncClient with a mock that records requests."""
    captured: dict[str, list[dict[str, Any]]] = {"requests": []}

    class _MockClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _MockClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, json: Any = None, headers: Any = None) -> Any:
            captured["requests"].append({"url": url, "json": json, "headers": headers})

            class _MockResp:
                status_code = 200

                def raise_for_status(self) -> None:
                    pass

            return _MockResp()

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)
    return captured


def test_hex_conversion_helpers() -> None:
    assert len(_to_hex32("4bf92f3577b34da6a3ce929d0e0e4736")) == 32
    assert len(_to_hex32("custom-arbitrary-trace-id-12345")) == 32
    assert len(_to_hex16("00f067aa0ba902b7")) == 16
    assert len(_to_hex16("arbitrary-run-id-987")) == 16


def test_iso_to_unix_nano() -> None:
    nano = _iso_to_unix_nano("2026-08-22T06:00:00Z")
    assert isinstance(nano, int)
    assert nano > 0


def test_build_export_payload() -> None:
    sink = OtlpTraceSink(
        endpoint="http://localhost:4318",
        service_name="agentos-test",
        service_version="1.0.0",
    )

    ctx = TraceContext.new(
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        session_key="sess-test",
        turn_id="turn-42",
        run_id="run-100",
        parent_run_id="parent-050",
        agent_id="main-agent",
    )
    event = TraceEvent(
        kind="llm_call",
        context=ctx,
        attrs={"model": "deepseek-chat", "temperature": 0.7, "stream": True},
    )

    payload = sink.build_export_payload([event])

    assert "resourceSpans" in payload
    resource_spans = payload["resourceSpans"]
    assert len(resource_spans) == 1

    scope_spans = resource_spans[0]["scopeSpans"]
    assert len(scope_spans) == 1

    spans = scope_spans[0]["spans"]
    assert len(spans) == 1

    span = spans[0]
    assert span["traceId"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert len(span["spanId"]) == 16
    assert span["name"] == "agentos.llm_call"

    attr_dict = {a["key"]: a["value"] for a in span["attributes"]}
    assert attr_dict["agentos.kind"]["stringValue"] == "llm_call"
    assert attr_dict["agentos.agent_id"]["stringValue"] == "main-agent"
    assert attr_dict["attr.model"]["stringValue"] == "deepseek-chat"
    assert attr_dict["attr.stream"]["boolValue"] is True


@pytest.mark.asyncio
async def test_otlp_flush_network_call(
    _mock_httpx: dict[str, list[dict[str, Any]]],
) -> None:
    captured = _mock_httpx

    sink = OtlpTraceSink(endpoint="http://collector.internal:4318/v1/traces")
    ctx = TraceContext.new(trace_id="test-trace-1")
    event = TraceEvent(kind="turn_start", context=ctx)

    sink.write(event)
    success = await sink.flush()

    assert success is True
    assert len(captured["requests"]) == 1
    assert captured["requests"][0]["url"] == "http://collector.internal:4318/v1/traces"
    assert "resourceSpans" in captured["requests"][0]["json"]


def test_otlp_queue_capacity_bounds() -> None:
    sink = OtlpTraceSink(max_queue_size=5)
    ctx = TraceContext.new(trace_id="bound-test")

    # Write 10 events into queue with max_queue_size=5
    for i in range(10):
        sink.write(TraceEvent(kind=f"event_{i}", context=ctx))

    assert len(sink._queue) == 5
    # The 5 remaining should be the newest events (5..9)
    kinds = [e.kind for e in sink._queue]
    assert kinds == ["event_5", "event_6", "event_7", "event_8", "event_9"]


def test_trace_sink_registration_and_fanout(tmp_path: Any) -> None:
    from agentos.observability.trace import (
        MemoryTraceSink,
        clear_trace_sinks,
        get_trace_sinks,
        register_trace_sink,
        unregister_trace_sink,
        write_trace_event,
    )

    clear_trace_sinks()
    mem_sink = MemoryTraceSink()
    try:
        register_trace_sink(mem_sink)
        assert mem_sink in get_trace_sinks()

        ctx = TraceContext.new(trace_id="fanout-trace")
        event = TraceEvent(kind="custom_action", context=ctx)

        path = write_trace_event(event, log_dir=tmp_path)
        assert path.exists()
        assert len(mem_sink.events) == 1
        assert mem_sink.events[0].kind == "custom_action"

        unregister_trace_sink(mem_sink)
        assert mem_sink not in get_trace_sinks()
    finally:
        clear_trace_sinks()


@pytest.mark.asyncio
async def test_boot_build_services_otlp_lifecycle() -> None:
    from agentos.gateway.boot import build_services
    from agentos.gateway.config import GatewayConfig
    from agentos.observability.trace import clear_trace_sinks, get_trace_sinks

    clear_trace_sinks()
    cfg = GatewayConfig.model_validate(
        {
            "observability": {
                "otlp_enabled": True,
                "otlp_endpoint": "http://collector:4318",
                "otlp_service_name": "agentos-prod",
            }
        }
    )

    try:
        svc = await build_services(config=cfg)
        assert svc.otlp_trace_sink is not None
        assert svc.otlp_trace_sink in get_trace_sinks()
        assert svc.otlp_trace_sink.service_name == "agentos-prod"

        await svc.close()
        assert svc.otlp_trace_sink is None
        assert len(get_trace_sinks()) == 0
    finally:
        clear_trace_sinks()


@pytest.mark.asyncio
async def test_otlp_interval_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    import httpx

    flushed_events: list[dict[str, Any]] = []

    class _MockClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _MockClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, json: Any = None, headers: Any = None) -> Any:
            flushed_events.append(json)

            class _MockResp:
                status_code = 200

                def raise_for_status(self) -> None:
                    pass

            return _MockResp()

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)

    sink = OtlpTraceSink(
        endpoint="http://collector.internal:4318",
        flush_interval_s=0.05,
    )
    sink.start()
    assert sink._flush_task is not None

    ctx = TraceContext.new(trace_id="test-interval-flush")
    sink.write(TraceEvent(kind="timed_event", context=ctx))

    # Wait for periodic flush task to trigger
    for _ in range(20):
        if flushed_events:
            break
        await asyncio.sleep(0.02)

    assert len(flushed_events) == 1
    assert len(sink._queue) == 0

    await sink.close()
    assert sink._flush_task is None


def test_otlp_multithreaded_writes() -> None:
    import threading

    sink = OtlpTraceSink(max_queue_size=100)
    ctx = TraceContext.new(trace_id="thread-test")

    def _worker(thread_idx: int) -> None:
        for i in range(20):
            sink.write(TraceEvent(kind=f"t{thread_idx}_e{i}", context=ctx))

    threads = [threading.Thread(target=_worker, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(sink._queue) == 100


@pytest.mark.asyncio
async def test_otlp_flush_lock_serializes_concurrent_flush(_mock_httpx: Any) -> None:
    """Verify that flush() serialises concurrent callers via _flush_lock.

    Concurrent flushes from _periodic_flush and batch-triggered tasks
    should not interleave: only one HTTP export in-flight at a time.
    """
    import asyncio

    flush_count = 0
    original_lock = asyncio.Lock()

    sink = OtlpTraceSink(endpoint="http://collector.internal:4318/v1/traces")
    # Replace _flush_lock with one we can observe
    sink._flush_lock = original_lock

    ctx = TraceContext.new(trace_id="concurrent-flush-test")

    # Write enough events to trigger batch flushes from both paths
    for _ in range(30):
        sink.write(TraceEvent(kind="event", context=ctx))

    # Launch concurrent flushes
    async def _do_flush() -> bool:
        nonlocal flush_count
        result = await sink.flush()
        flush_count += 1
        return result

    results = await asyncio.gather(_do_flush(), _do_flush(), _do_flush())

    assert all(results)  # All flushes succeeded
    assert flush_count == 3  # All three completed
    # Only one HTTP request should have been made since flushes are
    # serialised — the first one drains the queue, subsequent ones
    # find it empty and return True immediately.
    assert len(_mock_httpx["requests"]) == 1


@pytest.mark.asyncio
async def test_otlp_flush_lock_concurrent_with_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    """Concurrent flush calls still serialise correctly when one fails."""
    call_count = 0

    async def _failing_post(url: str, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.HTTPError("Simulated network failure")

        class _MockResp:
            status_code = 200

            def raise_for_status(self) -> None:
                pass

        return _MockResp()

    class _FailingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FailingClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> Any:
            return await _failing_post(url, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _FailingClient)

    sink = OtlpTraceSink(endpoint="http://collector.internal:4318/v1/traces")
    ctx = TraceContext.new(trace_id="concurrent-fail-test")
    for _ in range(30):
        sink.write(TraceEvent(kind="event", context=ctx))

    # First flush: fails (call_count=1), events re-queued
    result1 = await sink.flush()
    assert result1 is False, "First flush should fail"

    # Second flush (call_count=2): succeeds
    result2 = await sink.flush()
    assert result2 is True, "Second flush should succeed after re-queue"

    assert call_count == 2, "Should have made exactly 2 HTTP calls"
    # After second flush succeeds, queue should be empty
    assert len(sink._queue) == 0
