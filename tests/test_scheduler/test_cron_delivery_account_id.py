"""Cron delivery must thread account_id to the channel adapter.

Bug: a cron job's delivery.account_id was stored and validated but silently
dropped before the actual channel send. On a multi-account channel, a job bound
to a specific account delivered from the wrong one — no error, just silent
misrouting.

Fix: _post_to_channel now accepts and uses account_id via
ChannelManager.resolve_delivery_target, and _deliver_channel +
_deliver_to_failure_destination thread it through.
"""

from __future__ import annotations

from typing import Any

from agentos.channels.types import DeliveryTargetResolution
from agentos.scheduler.delivery import DeliveryChain
from agentos.scheduler.types import (
    CronJob,
    DeliveryConfig,
    DeliveryMode,
    FailureDestination,
    SessionTarget,
)

# --- Helpers ----------------------------------------------------------------


class _RecordingAdapter:
    """Minimal adapter that records send calls."""

    def __init__(self, name: str = "telegram") -> None:
        self.name = name
        self.sent: list[Any] = []

    async def send(self, msg: Any) -> None:
        self.sent.append(msg)


class _RecordingChannelManager:
    """Channel manager that tracks resolve_delivery_target calls."""

    def __init__(self) -> None:
        self.adapters: dict[str, _RecordingAdapter] = {}
        self.resolve_calls: list[dict[str, Any]] = []

    def register(self, name: str) -> _RecordingAdapter:
        adapter = _RecordingAdapter(name=name)
        self.adapters[name] = adapter
        return adapter

    def get(self, name: str) -> _RecordingAdapter | None:
        return self.adapters.get(name)

    def resolve_delivery_target(
        self,
        *,
        target: str,
        to: str = "",
        account_id: str = "",
        thread_id: str = "",
    ) -> DeliveryTargetResolution:
        """Record the call and resolve to the named adapter if possible."""
        self.resolve_calls.append(
            {
                "target": target,
                "to": to,
                "account_id": account_id,
                "thread_id": thread_id,
            }
        )
        # If we have an account_id, require an exact adapter match.
        if account_id:
            if account_id not in self.adapters:
                return DeliveryTargetResolution(
                    ok=False, reason="unsupported_account"
                )
            adapter = self.adapters[account_id]
            return DeliveryTargetResolution(
                ok=True,
                adapter=adapter,
                adapter_name=account_id,
                channel_type=target.lower(),
                to=to,
                account_id=account_id,
                thread_id=thread_id,
            )
        # Without account_id, fall back to type lookup.
        adapter = self.adapters.get(target)
        if adapter is None:
            return DeliveryTargetResolution(ok=False, reason="unsupported_target")
        return DeliveryTargetResolution(
            ok=True,
            adapter=adapter,
            adapter_name=target,
            channel_type=target.lower(),
            to=to,
            account_id="",
            thread_id=thread_id,
        )


class _FakeSessionStorage:
    """Minimal session storage for _deliver_channel code path."""

    async def get_session(self, key: str) -> None:
        return None

    async def list_sessions(self, **kw: Any) -> list[Any]:
        return []


def _job_with_channel_delivery(
    *,
    account_id: str = "",
    channel_name: str = "telegram",
    channel_id: str = "12345",
) -> CronJob:
    return CronJob(
        id="job-acct",
        name="test-account-delivery",
        cron_expr="* * * * *",
        handler_key="agent_run",
        payload={"kind": "agent_turn", "task": "hello", "agent_id": "main"},
        session_target=SessionTarget.ISOLATED,
        delivery=DeliveryConfig(
            mode=DeliveryMode.CHANNEL,
            channel_name=channel_name,
            channel_id=channel_id,
            account_id=account_id,
        ),
    )


# --- Tests ------------------------------------------------------------------


async def test_post_to_channel_passes_account_id_to_resolve() -> None:
    """When account_id is set, _post_to_channel must call
    resolve_delivery_target with that account_id instead of
    falling back to cm.get(channel_name)."""
    cm = _RecordingChannelManager()
    # Register two accounts for "telegram" type:
    # "telegram" (default) and "telegram-business" (named account).
    default_adapter = cm.register("telegram")
    business_adapter = cm.register("telegram-business")

    chain = DeliveryChain(channel_manager_ref=lambda: cm)

    job = _job_with_channel_delivery(account_id="telegram-business")
    # Build a route envelope with the channel target so _deliver_channel
    # reaches _post_to_channel.
    from agentos.scheduler.routing import build_cron_route_envelope

    envelope = build_cron_route_envelope(
        job,
        session_key="agent:main",
        delivery=job.delivery,
    )

    result = await chain._deliver_channel(
        job, "test message", envelope, session_key="agent:main:isolated:job-acct"
    )

    assert result == "delivered"
    # resolve_delivery_target must have been called with account_id.
    assert len(cm.resolve_calls) >= 1
    resolve = cm.resolve_calls[0]
    assert resolve["account_id"] == "telegram-business"
    # The business adapter must have received the message, not the default.
    assert len(business_adapter.sent) == 1
    assert business_adapter.sent[0].content == "test message"
    # Default adapter must NOT have received it.
    assert len(default_adapter.sent) == 0


async def test_post_to_channel_without_account_id_uses_get() -> None:
    """When account_id is empty, _post_to_channel should fall back to the
    simple cm.get(channel_name) path without calling resolve_delivery_target."""
    cm = _RecordingChannelManager()
    default_adapter = cm.register("telegram")

    chain = DeliveryChain(channel_manager_ref=lambda: cm)

    job = _job_with_channel_delivery(account_id="")
    from agentos.scheduler.routing import build_cron_route_envelope

    envelope = build_cron_route_envelope(
        job,
        session_key="agent:main",
        delivery=job.delivery,
    )

    result = await chain._deliver_channel(
        job, "plain message", envelope, session_key="agent:main:isolated:job-acct"
    )

    assert result == "delivered"
    # Without account_id, resolve should NOT be called.
    assert len(cm.resolve_calls) == 0
    # The default adapter gets it via cm.get().
    assert len(default_adapter.sent) == 1
    assert default_adapter.sent[0].content == "plain message"


async def test_post_to_channel_falls_back_on_bad_account_id() -> None:
    """If resolve_delivery_target rejects an unknown account_id,
    _post_to_channel must fall back to cm.get(channel_name) rather than
    silently failing delivery."""
    cm = _RecordingChannelManager()
    default_adapter = cm.register("telegram")

    chain = DeliveryChain(channel_manager_ref=lambda: cm)

    # Request a non-existent account.
    job = _job_with_channel_delivery(account_id="no-such-account")
    from agentos.scheduler.routing import build_cron_route_envelope

    envelope = build_cron_route_envelope(
        job,
        session_key="agent:main",
        delivery=job.delivery,
    )

    result = await chain._deliver_channel(
        job, "fallback message", envelope, session_key="agent:main:isolated:job-acct"
    )

    # Delivery must still succeed via the fallback adapter.
    assert result == "delivered"
    assert len(default_adapter.sent) == 1
    assert default_adapter.sent[0].content == "fallback message"


async def test_failure_destination_threads_account_id() -> None:
    """FailureDestination.account_id must be threaded through to
    _post_to_channel for multi-account failure alerting."""
    cm = _RecordingChannelManager()
    cm.register("slack")
    ops_adapter = cm.register("slack-ops")

    chain = DeliveryChain(channel_manager_ref=lambda: cm)

    job = CronJob(
        id="job-fd-acct",
        name="test",
        cron_expr="* * * * *",
        handler_key="agent_run",
        payload={"kind": "agent_turn", "task": "x", "agent_id": "main"},
        session_target=SessionTarget.ISOLATED,
        delivery=DeliveryConfig(
            mode=DeliveryMode.NONE,
            failure_destination=FailureDestination(
                mode=DeliveryMode.CHANNEL,
                channel_name="slack",
                channel_id="C-ops",
                account_id="slack-ops",
            ),
        ),
    )

    status = await chain.dispatch_failure_alert(job, "heartbeat dead")

    assert status == "delivered"
    # resolve_delivery_target should have been called with account_id.
    assert len(cm.resolve_calls) >= 1
    assert cm.resolve_calls[0]["account_id"] == "slack-ops"
    # The ops adapter should have received the alert.
    assert len(ops_adapter.sent) == 1
    assert ops_adapter.sent[0].content == "heartbeat dead"


async def test_deliver_channel_prefers_reply_target_account_id() -> None:
    """When the route envelope's reply_target carries an account_id,
    _deliver_channel must prefer it over job.delivery.account_id."""
    cm = _RecordingChannelManager()
    cm.register("telegram")
    biz_adapter = cm.register("telegram-biz")

    job = _job_with_channel_delivery(account_id="telegram")
    # Simulate a reply_target with a different account_id.
    from agentos.scheduler.routing import build_cron_route_envelope

    envelope = build_cron_route_envelope(
        job,
        session_key="agent:main",
        delivery=DeliveryConfig(
            mode=DeliveryMode.CHANNEL,
            channel_name="telegram",
            channel_id="12345",
            account_id="telegram-biz",
        ),
    )

    chain = DeliveryChain(channel_manager_ref=lambda: cm)

    result = await chain._deliver_channel(
        job, "routed message", envelope, session_key="agent:main:isolated:job-acct"
    )

    assert result == "delivered"
    # The account_id from the delivery config used to build the envelope
    # should be threaded — in this case "telegram-biz".
    assert len(cm.resolve_calls) >= 1
    assert cm.resolve_calls[0]["account_id"] == "telegram-biz"
    assert len(biz_adapter.sent) == 1
