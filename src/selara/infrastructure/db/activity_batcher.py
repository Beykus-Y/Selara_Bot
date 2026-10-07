from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

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
    ActivityInboxRow,
    claim_activity_inbox_batch,
    claim_activity_inbox_row,
    delete_activity_inbox_events,
    read_activity_inbox_backlog,
    record_activity_inbox_failure,
    stage_activity_inbox_events,
)
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

logger = logging.getLogger(__name__)

# A row moves to the dead-letter table after this many failed applies.
DEFAULT_MAX_ROW_ATTEMPTS = 5
# The backlog is logged at most this often. It is also logged whenever a row is parked.
_BACKLOG_LOG_INTERVAL_SECONDS = 300
# A backlog whose oldest row is older than this is logged as a warning rather than info.
_BACKLOG_ALERT_AGE_SECONDS = 600


class _BatchFailed(Exception):
    """A batch transaction failed. Its rows are still in the inbox, so they are applied one at a time."""

    def __init__(self, rows: Sequence[ActivityInboxRow]) -> None:
        super().__init__(f"Activity inbox batch of {len(rows)} rows failed")
        self.rows = list(rows)


@dataclass(frozen=True, slots=True)
class _FlushStep:
    # Rows taken from the inbox.
    claimed: int = 0
    # Rows applied, parked, or left to another flusher. Each one has left this flush step.
    handled: int = 0
    # Rows whose failed attempt was recorded.
    failed: int = 0
    # The database could not take the step, so the rows wait for a backoff.
    transient: bool = False


def _seconds_since(moment: datetime | None) -> float | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        # SQLite returns naive timestamps. The inbox stores UTC, so read them as UTC.
        moment = moment.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - moment).total_seconds()


class ActivityBatcher:
    """Aggregates tracked group messages into activity counters and the message archive.

    `enqueue_message` writes each event to `activity_event_inbox` and returns only after that commit, so a
    crash cannot lose it. A background task applies inbox rows in batches. Each batch is aggregated and its
    rows are deleted in one transaction. If a batch fails, its rows are applied one at a time, so one bad row
    cannot hold back the others. A row that fails `max_row_attempts` times moves to the dead-letter table.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        catalog: AchievementCatalogService,
        flush_seconds: int,
        max_events: int,
        max_inflight_writes: int = 8,
        max_row_attempts: int = DEFAULT_MAX_ROW_ATTEMPTS,
        live_event_publisher: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._catalog = catalog
        self._flush_seconds = max(1, int(flush_seconds))
        self._max_events = max(1, int(max_events))
        self._max_row_attempts = max(1, int(max_row_attempts))
        self._write_slots = asyncio.Semaphore(max(1, int(max_inflight_writes)))
        self._live_event_publisher = live_event_publisher
        self._unflushed_hint = 0
        self._last_backlog_log = float("-inf")
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
        session: AsyncSession | None = None,
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
        if session is not None:
            # Joins the caller's transaction, so it commits with the request and takes no second pooled connection.
            # A request still running when close() returns commits its row afterwards, and the next start applies it.
            stage_activity_inbox_events(session, [event])
        else:
            async with self._write_slots:
                if self._closed:
                    raise RuntimeError("ActivityBatcher is closed.")
                async with self._session_factory() as own_session:
                    stage_activity_inbox_events(own_session, [event])
                    await own_session.commit()

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
            await self._log_backlog_if_due()
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
        """Apply inbox rows until the inbox is empty. Returns False if the database failed and the rows must wait."""
        while True:
            step = await self._flush_next_batch()
            self._unflushed_hint = max(0, self._unflushed_hint - step.handled)
            if step.transient:
                return False
            if step.claimed == 0:
                # Rows from rolled-back request transactions never reach the inbox, so the hint would otherwise
                # keep growing and wake the flusher on every enqueue.
                self._unflushed_hint = 0
                return True
            if step.failed or step.claimed < self._max_events:
                # A row that failed is retried on the next flush interval, not in a tight loop.
                return True

    async def _flush_next_batch(self) -> _FlushStep:
        try:
            rows, result = await self._apply_next_batch()
        except _BatchFailed as failure:
            logger.warning(
                "Activity inbox batch failed; applying its rows one at a time",
                exc_info=failure.__cause__,
                extra={"batch_limit": self._max_events, "batch_size": len(failure.rows)},
            )
            return await self._apply_rows_one_at_a_time(failure.rows)
        except Exception:
            logger.exception("Failed to flush activity inbox batch", extra={"batch_limit": self._max_events})
            return _FlushStep(transient=True)

        await self._publish_live_events(result)
        return _FlushStep(claimed=len(rows), handled=len(rows))

    async def _apply_next_batch(self) -> tuple[list[ActivityInboxRow], ActivityBatchFlushResult]:
        # Aggregates, achievements and inbox deletes share one transaction: they all commit or none do.
        async with self._session_factory() as session:
            rows = await claim_activity_inbox_batch(session, limit=self._max_events)
            if not rows:
                return [], ActivityBatchFlushResult()
            try:
                result = await self._apply_rows(session, rows)
            except Exception as exc:
                raise _BatchFailed(rows) from exc
        return rows, result

    async def _apply_rows(self, session: AsyncSession, rows: Sequence[ActivityInboxRow]) -> ActivityBatchFlushResult:
        events = [row.to_event() for row in rows]
        repo = SqlAlchemyActivityRepository(session)
        result = await repo.flush_activity_batch(events)
        if result.latest_event_at_by_pair:
            await self._process_achievements(session=session, repo=repo, result=result)
        await delete_activity_inbox_events(session, [row.id for row in rows])
        await session.commit()
        return result

    async def _apply_rows_one_at_a_time(self, rows: Sequence[ActivityInboxRow]) -> _FlushStep:
        handled = 0
        failed = 0
        transient = False
        impacted_chat_ids: set[int] = set()
        for row in rows:
            try:
                result = await self._apply_one_row(row.id)
            except Exception as exc:
                try:
                    counted = await self._record_row_failure(row, exc)
                except Exception:
                    # The failure cannot be recorded either, so the database is the problem, not this row.
                    logger.exception("Failed to record an activity inbox row failure", extra={"inbox_row_id": row.id})
                    transient = True
                    break
                if counted:
                    failed += 1
                handled += 1
                continue
            handled += 1
            if result is not None:
                impacted_chat_ids.update(result.impacted_chat_ids)
        await self._publish_live_events(ActivityBatchFlushResult(impacted_chat_ids=impacted_chat_ids))
        return _FlushStep(claimed=len(rows), handled=handled, failed=failed, transient=transient)

    async def _apply_one_row(self, row_id: int) -> ActivityBatchFlushResult | None:
        async with self._session_factory() as session:
            row = await claim_activity_inbox_row(session, row_id=row_id)
            if row is None:
                # Already applied, parked, or held by another flusher.
                return None
            return await self._apply_rows(session, [row])

    async def _record_row_failure(self, row: ActivityInboxRow, exc: Exception) -> bool:
        """Count one failed apply of a row. Returns False if the row is no longer ours to count."""
        async with self._session_factory() as session:
            outcome = await record_activity_inbox_failure(
                session,
                row_id=row.id,
                max_attempts=self._max_row_attempts,
                failed_at=datetime.now(timezone.utc),
            )
            await session.commit()
        if outcome is None:
            return False

        extra = {"inbox_row_id": row.id, "inbox_chat_id": row.chat_id, "inbox_attempts": outcome.attempts}
        if outcome.parked:
            logger.error(
                "Activity inbox row failed every attempt and moved to the dead-letter table",
                exc_info=exc,
                extra=extra,
            )
            await self._log_backlog()
        else:
            logger.warning("Activity inbox row failed to apply and will be retried", exc_info=exc, extra=extra)
        return True

    async def _log_backlog_if_due(self) -> None:
        if time.monotonic() - self._last_backlog_log >= _BACKLOG_LOG_INTERVAL_SECONDS:
            await self._log_backlog()

    async def _log_backlog(self) -> None:
        self._last_backlog_log = time.monotonic()
        try:
            async with self._session_factory() as session:
                backlog = await read_activity_inbox_backlog(session)
        except Exception:
            logger.exception("Failed to read the activity inbox backlog")
            return
        if backlog.pending == 0 and backlog.dead_letters == 0:
            return

        oldest_age = _seconds_since(backlog.oldest_created_at)
        alert = oldest_age is not None and oldest_age >= _BACKLOG_ALERT_AGE_SECONDS
        logger.log(
            logging.WARNING if alert else logging.INFO,
            "Activity inbox backlog",
            extra={
                "inbox_pending": backlog.pending,
                "inbox_oldest_age_seconds": None if oldest_age is None else round(oldest_age, 1),
                "inbox_dead_letters": backlog.dead_letters,
            },
        )

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
