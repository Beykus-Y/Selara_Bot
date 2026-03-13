from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.achievements import (
    AchievementAwardService,
    AchievementCatalogService,
    AchievementConditionEvaluator,
    AchievementOrchestrator,
)
from selara.infrastructure.db.activity_batching import ActivityBatchFlushResult, ActivityBatchMessage
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

logger = logging.getLogger(__name__)


class ActivityBatcher:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        catalog: AchievementCatalogService,
        flush_seconds: int,
        max_events: int,
        live_event_publisher: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._catalog = catalog
        self._flush_seconds = max(1, int(flush_seconds))
        self._max_events = max(1, int(max_events))
        self._live_event_publisher = live_event_publisher
        self._pending: deque[ActivityBatchMessage] = deque()
        self._lock = asyncio.Lock()
        self._wake_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._closed = False
        self._task = asyncio.create_task(self._run(), name="activity-batcher")

    async def enqueue_message(
        self,
        *,
        chat_id: int,
        chat_type: str,
        chat_title: str | None,
        user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        is_bot: bool,
        event_at: datetime,
        telegram_message_id: int | None = None,
    ) -> None:
        if self._closed:
            raise RuntimeError("ActivityBatcher is closed.")

        async with self._lock:
            self._pending.append(
                ActivityBatchMessage(
                    chat_id=chat_id,
                    chat_type=chat_type,
                    chat_title=chat_title,
                    user_id=user_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    is_bot=is_bot,
                    event_at=event_at,
                    telegram_message_id=telegram_message_id,
                )
            )
            should_wake = len(self._pending) >= self._max_events

        if should_wake:
            self._wake_event.set()

    async def close(self) -> None:
        if self._task is None:
            self._closed = True
            return

        self._closed = True
        self._wake_event.set()
        task = self._task
        self._task = None
        await task

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=self._flush_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake_event.clear()

            batch = await self._drain_pending()
            if not batch:
                if self._closed:
                    break
                continue

            flushed = await self._flush_batch(batch)
            if not flushed:
                await asyncio.sleep(self._flush_seconds)

            if self._closed and not await self._has_pending():
                break

    async def _drain_pending(self) -> list[ActivityBatchMessage]:
        async with self._lock:
            if not self._pending:
                return []
            batch = list(self._pending)
            self._pending.clear()
            return batch

    async def _has_pending(self) -> bool:
        async with self._lock:
            return bool(self._pending)

    async def _requeue_front(self, batch: Sequence[ActivityBatchMessage]) -> None:
        async with self._lock:
            restored = deque(batch)
            restored.extend(self._pending)
            self._pending = restored
            if self._pending:
                self._wake_event.set()

    async def _flush_batch(self, batch: Sequence[ActivityBatchMessage]) -> bool:
        try:
            async with self._session_factory() as session:
                repo = SqlAlchemyActivityRepository(session)
                result = await repo.flush_activity_batch(batch)
                if result.latest_event_at_by_pair:
                    await self._process_achievements(session=session, repo=repo, result=result)
                await session.commit()
        except Exception:
            logger.exception("Failed to flush activity batch", extra={"batch_size": len(batch)})
            await self._requeue_front(batch)
            return False

        await self._publish_live_events(result)
        return True

    async def _process_achievements(
        self,
        *,
        session: AsyncSession,
        repo: SqlAlchemyActivityRepository,
        result: ActivityBatchFlushResult,
    ) -> None:
        orchestrator = AchievementOrchestrator(
            catalog=self._catalog,
            evaluator=AchievementConditionEvaluator(),
            award_service=AchievementAwardService(session, self._catalog),
            repo=repo,
        )
        for (chat_id, user_id), event_at in sorted(
            result.latest_event_at_by_pair.items(),
            key=lambda item: (item[1], item[0][0], item[0][1]),
        ):
            await orchestrator.process_message(
                chat_id=chat_id,
                user_id=user_id,
                event_at=event_at,
            )

    async def _publish_live_events(self, result: ActivityBatchFlushResult) -> None:
        if self._live_event_publisher is None or not result.impacted_chat_ids:
            return

        for chat_id in sorted(result.impacted_chat_ids):
            try:
                await self._live_event_publisher(
                    event_type="chat_activity",
                    scope="chat",
                    chat_id=chat_id,
                )
            except Exception:
                logger.exception("Failed to publish batched chat activity live event", extra={"chat_id": chat_id})
