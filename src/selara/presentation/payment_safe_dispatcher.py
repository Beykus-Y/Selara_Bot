from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.dispatcher.dispatcher import DEFAULT_BACKOFF_CONFIG
from aiogram.types import Update

logger = logging.getLogger(__name__)

# Ordinary updates admitted at once (running or waiting for a concurrency slot), as a multiple of the concurrency
# limit. Past that ceiling a new ordinary update is dropped and logged, so a stalled handler cannot grow the in-memory
# queue without bound. Payment updates are never counted against it.
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
    concurrency limit. The reader never waits for one of them: aiogram confirms an
    update only when the reader asks for the next one, so a reader parked on an
    ordinary update would also leave the pre-checkout query behind it unread. This
    override follows aiogram 3's dispatcher polling hook so a long AI handler cannot
    delay the ten-second pre-checkout deadline, while a failed Stars DB write cannot
    be acked before the payment transaction commits.
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
        pending_ceiling = (
            tasks_concurrency_limit * PENDING_UPDATES_PER_SLOT
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

                # Every task in this set is an admitted ordinary update that has not finished yet, and payments never
                # enter it. At the ceiling the update is dropped rather than waited for, so the next read is not held up.
                pending = len(self._handle_update_tasks)
                if pending_ceiling is not None and pending >= pending_ceiling:
                    logger.warning(
                        "Dropped update id=%s: %d ordinary updates already pending (ceiling %d)",
                        update.update_id, pending, pending_ceiling,
                    )
                    continue
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
        finally:
            logger.info("Payment-safe polling stopped for bot @%s id=%s", user.username, bot.id)
