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
async def test_reader_keeps_reading_past_the_ceiling_and_drops_the_ordinary_updates_that_do_not_fit(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """With every handler stalled, the configured ceiling is admitted and the updates past it are dropped and counted. The
    reader still reads the whole batch, so memory stays bounded without holding up what comes next. The burst logs one
    warning, not one line per dropped update."""
    dispatcher = PaymentSafeDispatcher(max_pending_updates=8)
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

    await asyncio.wait_for(dispatcher._polling(_bot(), tasks_concurrency_limit=2), timeout=2)

    # Eight admitted tasks. The other 22 updates were read and dropped.
    assert read == list(range(30))
    assert len(dispatcher._handle_update_tasks) == 8
    assert dispatcher.dropped_updates == 22
    assert caplog.text.count("Dropped ") == 1

    release.set()
    await _wait_until(lambda: len(handled) == 8)
    await _wait_until(lambda: dispatcher._handle_update_tasks == set())
    assert sorted(handled) == list(range(8))


@pytest.mark.asyncio
async def test_pre_checkout_read_behind_an_ordinary_update_at_the_ceiling_is_dispatched_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the reader used to park on an ordinary update once the ceiling was full, so a pre-checkout query read
    after it waited for a stalled handler to finish. Telegram allows ten seconds for that answer."""
    dispatcher = PaymentSafeDispatcher(max_pending_updates=4)
    pre_checkout = SimpleNamespace(update_id=99, pre_checkout_query=SimpleNamespace(id="q"), message=None)
    release = asyncio.Event()
    pending_when_payment: list[int] = []
    handled: list[int] = []

    async def listen_updates(*_args, **_kwargs):
        for update_id in range(4):
            yield _plain_update(update_id)
        # The ceiling (4) is full. This ordinary update is the one a parked reader used to wait on.
        yield _plain_update(4)
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

    # The payment ran while all four admitted handlers were still stalled. The fifth ordinary update was dropped.
    assert pending_when_payment == [4]
    assert handled == [99]
    release.set()
    await _wait_until(lambda: sorted(handled) == [0, 1, 2, 3, 99])


@pytest.mark.asyncio
async def test_payment_read_while_pending_ceiling_is_full_is_dispatched_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A payment does not take a pending slot, so it is handled even when the ceiling is full and every ordinary
    handler is still stalled. Telegram's pre-checkout deadline does not wait for the queue to drain."""
    dispatcher = PaymentSafeDispatcher(max_pending_updates=4)
    pre_checkout = SimpleNamespace(update_id=99, pre_checkout_query=SimpleNamespace(id="q"), message=None)
    release = asyncio.Event()
    pending_when_payment: list[int] = []
    handled: list[int] = []

    async def listen_updates(*_args, **_kwargs):
        for update_id in range(4):
            yield _plain_update(update_id)
        # The reader has now admitted four ordinary updates, so the ceiling is exactly full.
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
    """A handler that raises must free its pending slot, or every later ordinary update would be dropped once the ceiling
    was reached."""
    dispatcher = PaymentSafeDispatcher(max_pending_updates=4)
    handled: list[int] = []

    async def listen_updates(*_args, **_kwargs):
        for update_id in range(4):
            yield _plain_update(update_id)
        # Let the four failing handlers finish first. The updates after them must be admitted, not dropped.
        await _wait_until(lambda: not dispatcher._handle_update_tasks)
        for update_id in range(4, 8):
            yield _plain_update(update_id)

    async def process_update(*, bot, update, **_kwargs) -> bool:
        if update.update_id < 4:
            raise RuntimeError("handler failed")
        handled.append(update.update_id)
        return True

    monkeypatch.setattr(dispatcher, "_listen_updates", listen_updates)
    monkeypatch.setattr(dispatcher, "_process_update", process_update)

    await asyncio.wait_for(dispatcher._polling(_bot(), tasks_concurrency_limit=1), timeout=2)
    await _wait_until(lambda: sorted(handled) == [4, 5, 6, 7])


@pytest.mark.asyncio
async def test_cancelling_polling_while_the_queue_is_full_stops_promptly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shutdown cancels the reader even while handlers are stalled and the ceiling is full. The stalled handlers do not
    hold it up."""
    dispatcher = PaymentSafeDispatcher(max_pending_updates=4)
    read: list[int] = []
    never = asyncio.Event()

    async def listen_updates(*_args, **_kwargs):
        update_id = 0
        while True:
            read.append(update_id)
            yield _plain_update(update_id)
            update_id += 1
            # The real listener awaits Telegram between batches. Without a suspension point here, this loop would never
            # give the cancellation a chance to arrive.
            await asyncio.sleep(0)

    async def process_update(*, bot, update, **_kwargs) -> bool:
        await never.wait()
        return True

    monkeypatch.setattr(dispatcher, "_listen_updates", listen_updates)
    monkeypatch.setattr(dispatcher, "_process_update", process_update)

    polling = asyncio.create_task(dispatcher._polling(_bot(), tasks_concurrency_limit=1))
    await _wait_until(lambda: len(read) >= 10)
    assert len(dispatcher._handle_update_tasks) == 4

    polling.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(polling, timeout=1)

    stalled = list(dispatcher._handle_update_tasks)
    for task in stalled:
        task.cancel()
    await asyncio.gather(*stalled, return_exceptions=True)


@pytest.mark.asyncio
async def test_the_pending_limit_is_absolute_and_payments_still_bypass_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ceiling is the configured number of pending ordinary updates. It does not scale with the concurrency limit: two
    handler slots with a ceiling of three admit three updates, not eight. A payment read at the full ceiling is still
    handled at once, and it does not count against the limit."""
    dispatcher = PaymentSafeDispatcher(max_pending_updates=3)
    pre_checkout = SimpleNamespace(update_id=99, pre_checkout_query=SimpleNamespace(id="q"), message=None)
    release = asyncio.Event()
    pending_when_payment: list[int] = []
    handled: list[int] = []

    async def listen_updates(*_args, **_kwargs):
        for update_id in range(10):
            yield _plain_update(update_id)
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

    await asyncio.wait_for(dispatcher._polling(_bot(), tasks_concurrency_limit=2), timeout=2)

    assert dispatcher.dropped_updates == 7
    assert pending_when_payment == [3]
    assert handled == [99]
    release.set()
    await _wait_until(lambda: sorted(handled) == [0, 1, 2, 99])


@pytest.mark.asyncio
async def test_the_pending_limit_must_be_at_least_one() -> None:
    with pytest.raises(ValueError):
        PaymentSafeDispatcher(max_pending_updates=0)
