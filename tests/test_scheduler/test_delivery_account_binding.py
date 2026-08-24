"""Cron delivery honours ``delivery.account_id`` (issue #359).

Before the fix, ``_post_to_channel`` resolved the adapter from the channel
name alone: a job pinned to a specific account on a multi-account channel
silently delivered from the wrong one. The schema even documented the gap
("Stored on the job but not yet honoured by channel delivery").

These tests pin the new behaviour: the account binding is threaded into
``ChannelManager.resolve_delivery_target`` and a bad binding fails loudly.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentos.channels.types import DeliveryTargetResolution
from agentos.scheduler.delivery import DeliveryChain
from agentos.scheduler.payloads import make_script_payload
from agentos.scheduler.types import CronJob, DeliveryConfig, DeliveryMode, SessionTarget


class _RecordingAdapter:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, message: Any) -> None:
        self.sent.append(message)


class _MultiAccountChannelManager:
    """Stands in for ChannelManager with two accounts on one channel type."""

    def __init__(self, adapters: dict[str, Any], channel_type: str = "telegram") -> None:
        self._adapters = adapters
        self._channel_type = channel_type
        self.resolution_calls: list[dict[str, str]] = []

    def get(self, name: str) -> Any:
        return self._adapters.get(name)

    def resolve_delivery_target(
        self,
        *,
        target: str,
        to: str = "",
        account_id: str = "",
        thread_id: str = "",
    ) -> DeliveryTargetResolution:
        self.resolution_calls.append(
            {"target": target, "to": to, "account_id": account_id, "thread_id": thread_id}
        )
        account = account_id.strip()
        if account:
            adapter = self._adapters.get(account)
            if adapter is None:
                return DeliveryTargetResolution(ok=False, reason="unsupported_account")
            return DeliveryTargetResolution(
                ok=True,
                adapter=adapter,
                adapter_name=account,
                channel_type=self._channel_type,
                to=to,
                account_id=account,
                thread_id=thread_id,
            )
        adapter = self._adapters.get(target)
        if adapter is None:
            return DeliveryTargetResolution(ok=False, reason="unsupported_target")
        return DeliveryTargetResolution(
            ok=True,
            adapter=adapter,
            adapter_name=target,
            channel_type=self._channel_type,
            to=to,
            account_id="",
            thread_id=thread_id,
        )


def _job(account_id: str = "") -> CronJob:
    return CronJob(
        id="job-1",
        name="watch-memory",
        handler_key="script_run",
        payload=make_script_payload("watch-memory.sh"),
        session_target=SessionTarget.ISOLATED,
        delivery=DeliveryConfig(
            mode=DeliveryMode.CHANNEL,
            channel_name="telegram",
            channel_id="1245463966",
            account_id=account_id,
        ),
    )


async def _deliver(chain: DeliveryChain, job: CronJob) -> str:
    report = await chain.deliver(
        job,
        result_text="3 alerts pending",
        success=True,
        summary="3 alerts pending",
        session_key="cron:job-1:run:deadbeef",
    )
    return report.channel_status + (f": {report.channel_detail}" if report.channel_detail else "")


@pytest.mark.asyncio
async def test_account_id_selects_the_bound_account_adapter() -> None:
    default_adapter = _RecordingAdapter()
    pinned_adapter = _RecordingAdapter()
    manager = _MultiAccountChannelManager(
        {"telegram": default_adapter, "telegram-alerts": pinned_adapter}
    )
    chain = DeliveryChain(channel_manager_ref=lambda: manager)

    result = await _deliver(chain, _job(account_id="telegram-alerts"))

    assert result == "delivered"
    assert manager.resolution_calls[0]["account_id"] == "telegram-alerts"
    assert pinned_adapter.sent, "the pinned account's adapter must send the message"
    assert not default_adapter.sent, "the default adapter must not be used"


@pytest.mark.asyncio
async def test_unknown_account_fails_loudly_instead_of_using_the_default() -> None:
    default_adapter = _RecordingAdapter()
    manager = _MultiAccountChannelManager({"telegram": default_adapter})
    chain = DeliveryChain(channel_manager_ref=lambda: manager)

    result = await _deliver(chain, _job(account_id="telegram-missing"))

    assert "failed" in result, result
    assert "unsupported_account" in result
    assert not default_adapter.sent, "a bad binding must never fall back to the default"


@pytest.mark.asyncio
async def test_empty_account_id_keeps_the_legacy_name_only_path() -> None:
    default_adapter = _RecordingAdapter()
    manager = _MultiAccountChannelManager({"telegram": default_adapter})
    chain = DeliveryChain(channel_manager_ref=lambda: manager)

    result = await _deliver(chain, _job())

    assert result == "delivered"
    assert manager.resolution_calls == [], "no account binding, no account resolution"
    assert default_adapter.sent
