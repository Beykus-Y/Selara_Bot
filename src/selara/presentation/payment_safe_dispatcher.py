from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.dispatcher.dispatcher import DEFAULT_BACKOFF_CONFIG
from aiogram.types import Update

logger = logging.getLogger(__name__)


def _requires_durable_processing(update: Update) -> bool:
    if update.pre_checkout_query is not None:
        return True
    message = update.message
    return message is not None and message.successful_payment is not None


class PaymentSafeDispatcher(Dispatcher):
    """Keep Telegram payment updates unacknowledged until their handlers finish.

    Other updates retain aiogram's normal concurrent handling. This override
    follows aiogram 3's dispatcher polling hook so a long AI handler cannot
    delay the ten-second pre-checkout deadline, while a failed Stars DB write
    cannot be acked before the payment transaction commits.
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

        async def process_with_semaphore(coro) -> bool:
            assert semaphore is not None
            try:
                return await coro
            finally:
                semaphore.release()

        try:
            async for update in self._listen_updates(
                bot,
                polling_timeout=polling_timeout,
                backoff_config=backoff_config,
                allowed_updates=allowed_updates,
            ):
                handler = self._process_update(bot=bot, update=update, **kwargs)
                if not handle_as_tasks or _requires_durable_processing(update):
                    await handler
                    continue

                if semaphore is not None:
                    await semaphore.acquire()
                    task = asyncio.create_task(process_with_semaphore(handler))
                else:
                    task = asyncio.create_task(handler)
                self._handle_update_tasks.add(task)
                task.add_done_callback(self._handle_update_tasks.discard)
        finally:
            logger.info("Payment-safe polling stopped for bot @%s id=%s", user.username, bot.id)
