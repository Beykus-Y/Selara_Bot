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
    """Without a bound, a flood of slow updates piles up in memory as tasks.
    The limit makes the polling loop wait for a free slot before it takes the next update."""
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
