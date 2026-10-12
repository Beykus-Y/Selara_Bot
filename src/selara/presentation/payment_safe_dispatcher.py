from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.dispatcher.dispatcher import DEFAULT_BACKOFF_CONFIG
from aiogram.types import Update

logger = logging.getLogger(__name__)

# Ordinary updates admitted at once (running or waiting for a concurrency slot), as a multiple of the concurrency
# limit. Once reached, the reader stops fetching until one of them finishes, so a stalled handler cannot grow the
# in-memory queue without bound. Payment updates are never counted against it.
PENDING_UPDATES_PER_SLOT = 4


def _requires_durable_processing(update: Update) -> bool:
    if update.pre_checkout_query is not None:
        return True
    message = update.message
    return message is not None and message.successful_payment is not None


class PaymentSafeDispatcher(Dispatcher):
    """Keep Telegram payment updates unacknowledged until their handlers finish.

    Other updates retain aiogram's normal concurrent handling, with the number of
    admitted-but-unfinished updates capped at PENDING_UPDATES_PER_SLOT times the
    concurrency limit. This override follows aiogram 3's dispatcher polling hook
    so a long AI handler cannot delay the ten-second pre-checkout deadline, while
    a failed Stars DB write cannot be acked before the payment transaction commits.
    """

    async def _polling(
        self,
        bot: Bot,
        polling_timeout: int = 30,
        handle_as_tasks: bool = True,
        backoff_config=DEFAULT_BACKOFF_CONFIG,
        allowed_updates: list[str] | None = None,
        tasks_concurrency_limit: int | None = None,
        **kwargs: Any,
    ) -> None:
        user = await bot.me()
        logger.info("Run payment-safe polling for bot @%s id=%s", user.username, bot.id)
        semaphore = (
            asyncio.Semaphore(tasks_concurrency_limit)
            if tasks_concurrency_limit is not None and handle_as_tasks
            else None
        )

        # Unlike the concurrency slot, this one is taken by the reader itself. That is the backpressure: at the ceiling
        # the reader stops fetching updates instead of creating tasks without limit.
        pending_slots = (
            asyncio.Semaphore(tasks_concurrency_limit * PENDING_UPDATES_PER_SLOT)
            if tasks_concurrency_limit is not None and handle_as_tasks
            else None
        )

        async def process_with_semaphore(coro) -> bool:
            assert semaphore is not None
            async with semaphore:
                return await coro

        try:
            async for update in self._listen_updates(
                bot,
                polling_timeout=polling_timeout,
                backoff_config=backoff_config,
                allowed_updates=allowed_updates,
            ):
                if not handle_as_tasks or _requires_durable_processing(update):
                    await self._process_update(bot=bot, update=update, **kwargs)
                    continue

                # Payment updates return above, so they never take a pending slot. An ordinary update does wait here
                # when the ceiling is full, and the reader fetches nothing further until that wait ends.
                if pending_slots is not None:
                    await pending_slots.acquire()
                handler = self._process_update(bot=bot, update=update, **kwargs)
                # The concurrency slot is taken inside the task. Waiting for it here would stop the loop from reading
                # the updates behind this one, including a pre-checkout query that Telegram expects an answer to
                # within ten seconds.
                if semaphore is not None:
                    task = asyncio.create_task(process_with_semaphore(handler))
                else:
                    task = asyncio.create_task(handler)
                self._handle_update_tasks.add(task)
                task.add_done_callback(self._handle_update_tasks.discard)
                if pending_slots is not None:
                    # Runs on success, failure and cancellation, so a task can never leak its pending slot.
                    task.add_done_callback(lambda _task: pending_slots.release())
        finally:
            logger.info("Payment-safe polling stopped for bot @%s id=%s", user.username, bot.id)
