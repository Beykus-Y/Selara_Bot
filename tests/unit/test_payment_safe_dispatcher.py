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
