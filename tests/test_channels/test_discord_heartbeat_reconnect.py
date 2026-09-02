"""Discord heartbeat timeout must not cancel its own reconnect."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentos.channels.discord import DiscordChannel, DiscordChannelConfig


@pytest.mark.asyncio
async def test_heartbeat_timeout_allows_reconnect_to_complete() -> None:
    channel = DiscordChannel(DiscordChannelConfig(token="token"))
    channel._connected = True
    channel._state.last_heartbeat_ack = False
    channel._state.heartbeat_interval_ms = 10000
    channel._state.sequence = 42
    channel._state.session_id = "sess-1"
    do_reconnect = AsyncMock()
    channel._do_reconnect = do_reconnect
    await channel._heartbeat_loop()
    do_reconnect.assert_awaited_once()
    assert channel._heartbeat_task is None


@pytest.mark.asyncio
async def test_external_cancel_still_works() -> None:
    channel = DiscordChannel(DiscordChannelConfig(token="token"))
    channel._connected = True
    channel._state.heartbeat_interval_ms = 300_000
    channel._ws_send = AsyncMock(return_value=None)
    task = asyncio.create_task(channel._heartbeat_loop())
    channel._heartbeat_task = task
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_self_cancel_guard_does_not_raise() -> None:
    channel = DiscordChannel(DiscordChannelConfig(token="token"))
    channel._connected = True
    channel._state.last_heartbeat_ack = False
    channel._state.heartbeat_interval_ms = 10000
    channel._state.session_id = "sess-1"
    channel._state.sequence = 1
    channel._state.resume_url = "wss://example.com"

    async def run():
        t = asyncio.current_task()
        channel._heartbeat_task = t
        channel._ws_send = AsyncMock(return_value=None)
        with patch.object(channel, "_close_ws", AsyncMock()):
            with patch.object(channel, "_connect_ws",
                              AsyncMock(return_value=MagicMock())):
                with patch.object(channel, "_ws_recv",
                                  AsyncMock(return_value={
                                      "op": 0,
                                      "d": {"heartbeat_interval": 41250}})):
                    with patch.object(channel, "_identify", AsyncMock()):
                        await channel._do_reconnect()
    await run()
    # _do_reconnect spawns a new heartbeat loop at the end
    assert channel._heartbeat_task is not None
    assert not channel._heartbeat_task.done()
