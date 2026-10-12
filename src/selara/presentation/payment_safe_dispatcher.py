from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.dispatcher.dispatcher import DEFAULT_BACKOFF_CONFIG
from aiogram.types import Update

logger = logging.getLogger(__name__)

# Default ceiling on ordinary updates admitted at once (running or waiting for a concurrency slot). Past it a new
# ordinary update is dropped and counted, so a stalled handler cannot grow the in-memory queue without bound. An update
# costs a few KB, so the default is roughly 10-20 MB. Payment updates are never counted against it.
DEFAULT_MAX_PENDING_UPDATES = 2000
# A burst of drops logs one warning per interval, not one line per update.
_DROP_LOG_INTERVAL_SECONDS = 10.0


def _requires_durable_processing(update: Update) -> bool:
    if update.pre_checkout_query is not None:
        return True
    message = update.message
    return message is not None and message.successful_payment is not None


class PaymentSafeDispatcher(Dispatcher):
    """Keep Telegram payment updates unacknowledged until their handlers finish.

    Other updates retain aiogram's normal concurrent handling, with the number of
    admitted-but-unfinished updates capped at max_pending_updates. Past that cap a
    new ordinary update is dropped and counted, not waited for. aiogram confirms an
    update as soon as the reader asks for the next one, so a reader parked on an
    ordinary update would also leave a pre-checkout query behind it unread. This
    override follows aiogram 3's dispatcher polling hook so a long AI handler cannot
    delay the ten-second pre-checkout deadline, while a failed Stars DB write cannot
    be acked before the payment transaction commits.
    """

    def __init__(self, *, max_pending_updates: int = DEFAULT_MAX_PENDING_UPDATES, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if max_pending_updates < 1:
            raise ValueError("max_pending_updates must be at least 1")
        self._max_pending_updates = max_pending_updates
        # Ordinary updates dropped since start, and since the last warning line.
        self.dropped_updates = 0
        self._dropped_since_log = 0
        self._last_drop_log = float("-inf")

    def _record_dropped_update(self, update_id: int, pending: int) -> None:
        self.dropped_updates += 1
        self._dropped_since_log += 1
        now = time.monotonic()
        if now - self._last_drop_log < _DROP_LOG_INTERVAL_SECONDS:
            return
        logger.warning(
            "Dropped %d ordinary updates since the last warning (%d dropped in total); latest id=%s, %d pending, ceiling %d",
            self._dropped_since_log, self.dropped_updates, update_id, pending, self._max_pending_updates,
        )
        self._dropped_since_log = 0
        self._last_drop_log = now

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
        pending_ceiling = self._max_pending_updates if handle_as_tasks else None

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
                    self._record_dropped_update(update.update_id, pending)
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
            logger.info(
                "Payment-safe polling stopped for bot @%s id=%s; %d ordinary updates dropped in total",
                user.username, bot.id, self.dropped_updates,
            )
