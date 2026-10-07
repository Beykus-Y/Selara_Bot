from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.achievements import (
    AchievementAwardService,
    AchievementCatalogService,
    AchievementConditionEvaluator,
    AchievementOrchestrator,
)
from selara.infrastructure.db.activity_batching import (
    ActivityBatchFlushResult,
    ActivityBatchMessage,
)
from selara.infrastructure.db.activity_inbox import (
    claim_activity_inbox_batch,
    delete_activity_inbox_events,
    stage_activity_inbox_events,
)
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

logger = logging.getLogger(__name__)


class ActivityBatcher:
    """Aggregates tracked group messages into activity counters and the message archive.

    `enqueue_message` writes each event to `activity_event_inbox` and returns only after that commit, so a
    crash cannot lose it. A background task applies inbox rows in batches. Each batch is aggregated and its
    rows are deleted in one transaction: a failed or interrupted batch is retried whole, and an applied row
    is never applied again.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        catalog: AchievementCatalogService,
        flush_seconds: int,
        max_events: int,
        max_inflight_writes: int = 8,
        live_event_publisher: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._catalog = catalog
        self._flush_seconds = max(1, int(flush_seconds))
        self._max_events = max(1, int(max_events))
        self._write_slots = asyncio.Semaphore(max(1, int(max_inflight_writes)))
        self._live_event_publisher = live_event_publisher
        self._unflushed_hint = 0
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
        count_as_activity: bool = True,
        snapshot_kind: str | None = None,
        snapshot_at: datetime | None = None,
        sent_at: datetime | None = None,
        edited_at: datetime | None = None,
        message_type: str | None = None,
        text: str | None = None,
        caption: str | None = None,
        raw_message_json: dict[str, object] | None = None,
        snapshot_hash: str | None = None,
        reply_to_telegram_message_id: int | None = None,
    ) -> None:
        if self._closed:
            raise RuntimeError("ActivityBatcher is closed.")

        event = ActivityBatchMessage(
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
            count_as_activity=count_as_activity,
            snapshot_kind=snapshot_kind,
            snapshot_at=snapshot_at,
            sent_at=sent_at,
            edited_at=edited_at,
            message_type=message_type,
            text=text,
            caption=caption,
            raw_message_json=raw_message_json,
            snapshot_hash=snapshot_hash,
            reply_to_telegram_message_id=reply_to_telegram_message_id,
        )
        async with self._write_slots:
            if self._closed:
                raise RuntimeError("ActivityBatcher is closed.")
            async with self._session_factory() as session:
                stage_activity_inbox_events(session, [event])
                await session.commit()

        self._unflushed_hint += 1
        if self._unflushed_hint >= self._max_events:
            self._wake_event.set()

    async def close(self) -> None:
        # Stops accepting events, applies what is in the inbox and returns. If the database is failing, the
        # remaining rows stay in the inbox and the next start applies them.
        self._closed = True
        self._wake_event.set()
        task = self._task
        self._task = None
        if task is not None:
            await task

    async def _run(self) -> None:
        retry = False
        while True:
            if not retry:
                await self._wait_for_work()
            self._wake_event.clear()
            retry = not await self._drain_inbox()
            if self._closed:
                break
            if retry:
                # The failed rows are still in the inbox. A fixed backoff keeps enqueue wake-ups from turning
                # one failing flush into a retry per message.
                await asyncio.sleep(self._flush_seconds)

    async def _wait_for_work(self) -> None:
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=self._flush_seconds)
        except asyncio.TimeoutError:
            pass

    async def _drain_inbox(self) -> bool:
        """Apply inbox batches until the inbox is empty. Returns False if a batch failed."""
        while True:
            applied = await self._flush_next_batch()
            if applied is None:
                return False
            self._unflushed_hint = max(0, self._unflushed_hint - applied)
            if applied < self._max_events:
                return True

    async def _flush_next_batch(self) -> int | None:
        try:
            applied, result = await self._apply_next_batch()
        except Exception:
            logger.exception("Failed to flush activity inbox batch", extra={"batch_limit": self._max_events})
            return None

        await self._publish_live_events(result)
        return applied

    async def _apply_next_batch(self) -> tuple[int, ActivityBatchFlushResult]:
        # Aggregates, achievements and inbox deletes share one transaction: they all commit or none do.
        async with self._session_factory() as session:
            claimed = await claim_activity_inbox_batch(session, limit=self._max_events)
            if not claimed:
                return 0, ActivityBatchFlushResult()

            repo = SqlAlchemyActivityRepository(session)
            result = await repo.flush_activity_batch([event for _, event in claimed])
            if result.latest_event_at_by_pair:
                await self._process_achievements(session=session, repo=repo, result=result)
            await delete_activity_inbox_events(session, [row_id for row_id, _ in claimed])
            await session.commit()
        return len(claimed), result

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
