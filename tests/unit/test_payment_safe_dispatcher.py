from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation.payment_safe_dispatcher import PaymentSafeDispatcher


def _plain_update(update_id: int):
    return SimpleNamespace(update_id=update_id, pre_checkout_query=None, message=SimpleNamespace(successful_payment=None))


@pytest.mark.asyncio
async def test_polling_handles_at_most_the_configured_number_of_updates_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a bound, a flood of slow updates runs all at once. The limit caps how many handlers run together;
    updates past the cap wait for a slot inside their own task."""
    dispatcher = PaymentSafeDispatcher()
    bot = SimpleNamespace(me=AsyncMock(return_value=SimpleNamespace(username="selara", id=1)), id=1)
    updates = [_plain_update(update_id) for update_id in range(10)]

    async def listen_updates(*_args, **_kwargs):
        for update in updates:
            yield update

    monkeypatch.setattr(dispatcher, "_listen_updates", listen_updates)
    running = 0
    peak = 0
    finished: list[int] = []

    async def process_update(*, bot, update, **_kwargs) -> bool:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        finished.append(update.update_id)
        return True

    monkeypatch.setattr(dispatcher, "_process_update", process_update)

    await dispatcher._polling(bot, tasks_concurrency_limit=3)
    for _ in range(500):
        if len(finished) == len(updates):
            break
        await asyncio.sleep(0.01)

    assert sorted(finished) == list(range(10))
    assert peak == 3


@pytest.mark.asyncio
async def test_pre_checkout_is_dispatched_while_every_slot_is_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The limit caps ordinary handlers only. With every slot held by slow updates, a pre-checkout query that
    arrives behind them is still dispatched at once, because Telegram allows ten seconds for its answer."""
    dispatcher = PaymentSafeDispatcher()
    bot = SimpleNamespace(me=AsyncMock(return_value=SimpleNamespace(username="selara", id=1)), id=1)
    pre_checkout = SimpleNamespace(update_id=99, pre_checkout_query=SimpleNamespace(id="q"), message=None)

    async def listen_updates(*_args, **_kwargs):
        for update_id in range(3):
            yield _plain_update(update_id)
        # Give the three slow handlers time to take their slots before the payment arrives.
        await asyncio.sleep(0.01)
        yield pre_checkout

    monkeypatch.setattr(dispatcher, "_listen_updates", listen_updates)
    release = asyncio.Event()
    ordinary_running = 0
    running_when_payment_dispatched: list[int] = []
    handled: list[int] = []

    async def process_update(*, bot, update, **_kwargs) -> bool:
        nonlocal ordinary_running
        if update.update_id == 99:
            running_when_payment_dispatched.append(ordinary_running)
        else:
            ordinary_running += 1
            await release.wait()
            ordinary_running -= 1
        handled.append(update.update_id)
        return True

    monkeypatch.setattr(dispatcher, "_process_update", process_update)

    await dispatcher._polling(bot, tasks_concurrency_limit=3)

    assert running_when_payment_dispatched == [3]
    assert handled == [99]
    release.set()
    for _ in range(500):
        if len(handled) == 4:
            break
        await asyncio.sleep(0.01)
    assert sorted(handled) == [0, 1, 2, 99]


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition was not reached in time"
        await asyncio.sleep(0.01)


def _bot():
    return SimpleNamespace(me=AsyncMock(return_value=SimpleNamespace(username="selara", id=1)), id=1)


@pytest.mark.asyncio
async def test_reader_stops_fetching_once_pending_updates_reach_the_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """With every handler stalled, admitted updates stop at four times the concurrency limit. The reader then waits
    instead of fetching more, so a stall cannot grow the in-memory queue without bound."""
    dispatcher = PaymentSafeDispatcher()
    read: list[int] = []
    handled: list[int] = []
    release = asyncio.Event()

    async def listen_updates(*_args, **_kwargs):
        for update_id in range(30):
            read.append(update_id)
            yield _plain_update(update_id)

    async def process_update(*, bot, update, **_kwargs) -> bool:
        await release.wait()
        handled.append(update.update_id)
        return True

    monkeypatch.setattr(dispatcher, "_listen_updates", listen_updates)
    monkeypatch.setattr(dispatcher, "_process_update", process_update)

    polling = asyncio.create_task(dispatcher._polling(_bot(), tasks_concurrency_limit=2))
    await _wait_until(lambda: len(read) == 9)
    await asyncio.sleep(0.02)

    # Eight admitted tasks (2 * 4). The reader has pulled a ninth update and is now blocked on the ceiling.
    assert len(dispatcher._handle_update_tasks) == 8
    assert len(read) == 9

    release.set()
    await asyncio.wait_for(polling, timeout=2)
    await _wait_until(lambda: len(handled) == 30)
    assert sorted(handled) == list(range(30))
    await _wait_until(lambda: dispatcher._handle_update_tasks == set())


@pytest.mark.asyncio
async def test_payment_read_while_pending_ceiling_is_full_is_dispatched_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A payment does not take a pending slot, so it is handled even when the ceiling is full and every ordinary
    handler is still stalled. Telegram's pre-checkout deadline does not wait for the queue to drain."""
    dispatcher = PaymentSafeDispatcher()
    pre_checkout = SimpleNamespace(update_id=99, pre_checkout_query=SimpleNamespace(id="q"), message=None)
    release = asyncio.Event()
    pending_when_payment: list[int] = []
    handled: list[int] = []

    async def listen_updates(*_args, **_kwargs):
        for update_id in range(4):
            yield _plain_update(update_id)
        # The reader has now admitted four ordinary updates (1 * 4), so the ceiling is exactly full.
        yield pre_checkout

    async def process_update(*, bot, update, **_kwargs) -> bool:
        if update.update_id == 99:
            pending_when_payment.append(len(dispatcher._handle_update_tasks))
        else:
            await release.wait()
        handled.append(update.update_id)
        return True

    monkeypatch.setattr(dispatcher, "_listen_updates", listen_updates)
    monkeypatch.setattr(dispatcher, "_process_update", process_update)

    await asyncio.wait_for(dispatcher._polling(_bot(), tasks_concurrency_limit=1), timeout=2)

    assert pending_when_payment == [4]
    assert handled[0] == 99
    release.set()
    await _wait_until(lambda: sorted(handled) == [0, 1, 2, 3, 99])


@pytest.mark.asyncio
async def test_failed_handler_releases_its_pending_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A handler that raises must free its pending slot, or the reader would stop for good after the ceiling."""
    dispatcher = PaymentSafeDispatcher()
    read: list[int] = []

    async def listen_updates(*_args, **_kwargs):
        for update_id in range(20):
            read.append(update_id)
            yield _plain_update(update_id)

    async def process_update(*, bot, update, **_kwargs) -> bool:
        raise RuntimeError("handler failed")

    monkeypatch.setattr(dispatcher, "_listen_updates", listen_updates)
    monkeypatch.setattr(dispatcher, "_process_update", process_update)

    await asyncio.wait_for(dispatcher._polling(_bot(), tasks_concurrency_limit=1), timeout=2)
    await _wait_until(lambda: dispatcher._handle_update_tasks == set())

    assert read == list(range(20))


@pytest.mark.asyncio
async def test_cancelling_polling_while_the_queue_is_full_stops_promptly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shutdown cancels the reader even when it is parked on a full queue. The stalled handlers do not hold it up."""
    dispatcher = PaymentSafeDispatcher()
    read: list[int] = []
    never = asyncio.Event()

    async def listen_updates(*_args, **_kwargs):
        update_id = 0
        while True:
            read.append(update_id)
            yield _plain_update(update_id)
            update_id += 1

    async def process_update(*, bot, update, **_kwargs) -> bool:
        await never.wait()
        return True

    monkeypatch.setattr(dispatcher, "_listen_updates", listen_updates)
    monkeypatch.setattr(dispatcher, "_process_update", process_update)

    polling = asyncio.create_task(dispatcher._polling(_bot(), tasks_concurrency_limit=1))
    await _wait_until(lambda: len(read) == 5)
    await asyncio.sleep(0.02)
    assert len(dispatcher._handle_update_tasks) == 4

    polling.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(polling, timeout=1)

    stalled = list(dispatcher._handle_update_tasks)
    for task in stalled:
        task.cancel()
    await asyncio.gather(*stalled, return_exceptions=True)
    assert len(read) == 5
