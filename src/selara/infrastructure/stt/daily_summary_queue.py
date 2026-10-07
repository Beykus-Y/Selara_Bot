"""Background transcription queue for the daily summary feature's voice/video_note
support (docs/DAILY_SUMMARY_TODO.md). Completely separate from the instant
transcribe-and-reply feature in `presentation/handlers/voice.py` -- this queue never
touches that code path, has its own STT calls, and a failure here never affects it.

Why a queue at all, and not just transcribing inside the message handler: the
handler fires before the message is archived (`ActivityTrackerMiddleware` runs the
handler first, then hands the message to `ActivityBatcher`, which writes it to the
`messages` table on its own flush schedule -- there is no archive row yet at the
point a voice/video_note handler runs). So a job here is enqueued by
(chat_id, telegram_message_id, file_id), and the worker retries with backoff until
the archive row shows up (or gives up after a bounded number of attempts).

The in-memory queue is only a fast path, not the source of truth: the archive
table itself (voice/video_note rows with `transcript IS NULL`, guarded by the
`transcribed_at` claim marker) is the durable pending-jobs state. A recovery
scan runs periodically -- not just once at startup -- so a job lost to a full
queue (or to a process restart) is re-discovered from the DB within one scan
interval; repeated discovery is safe because two workers can never both win
`claim_message_for_transcription` for the same message.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.daily_summary.transcription import (
    TranscriptionJob,
    build_job_from_raw_message,
    is_transcription_enabled,
    is_within_transcription_budget,
)
from selara.core.config import Settings
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.infrastructure.llm.pricing import estimate_stt_cost_usd
from selara.infrastructure.stt.client import SttClient, SttClientError

logger = logging.getLogger(__name__)

_DEFAULT_MAX_QUEUE_SIZE = 1000
_DEFAULT_MAX_LOOKUP_ATTEMPTS = 5
_DEFAULT_LOOKUP_BACKOFF_SECONDS = 2.0
_DEFAULT_RECOVERY_SCAN_INTERVAL_SECONDS = 60.0
_RECOVERY_LOOKBACK_HOURS = 26  # a bit over the 24h analysis window, in case of clock/scan skew


class DailySummaryTranscriptionQueue:
    def __init__(
        self,
        *,
        bot: Bot,
        stt_client: SttClient,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        max_queue_size: int = _DEFAULT_MAX_QUEUE_SIZE,
        max_lookup_attempts: int = _DEFAULT_MAX_LOOKUP_ATTEMPTS,
        lookup_backoff_seconds: float = _DEFAULT_LOOKUP_BACKOFF_SECONDS,
        recovery_scan_interval_seconds: float = _DEFAULT_RECOVERY_SCAN_INTERVAL_SECONDS,
    ) -> None:
        self._bot = bot
        self._stt_client = stt_client
        self._session_factory = session_factory
        self._settings = settings
        self._queue: asyncio.Queue[TranscriptionJob] = asyncio.Queue(maxsize=max_queue_size)
        self._max_lookup_attempts = max_lookup_attempts
        self._lookup_backoff_seconds = lookup_backoff_seconds
        self._recovery_scan_interval_seconds = recovery_scan_interval_seconds
        # (chat_id, telegram_message_id) of jobs currently queued or in flight --
        # keeps periodic recovery scans from piling duplicate jobs onto a backed-up
        # queue. A job dropped by a full queue is deliberately NOT marked here, so
        # the next scan gets another chance at it (issue #81).
        self._pending_keys: set[tuple[int, int]] = set()
        self._workers: list[asyncio.Task[None]] = []
        self._recovery_task: asyncio.Task[None] | None = None
        self._closed = False

    async def start(self) -> None:
        if self._workers:
            return
        self._closed = False
        concurrency = max(1, int(self._settings.daily_summary_stt_concurrency))
        self._workers = [
            asyncio.create_task(self._worker_loop(), name=f"daily-summary-stt-worker-{i}") for i in range(concurrency)
        ]
        self._recovery_task = asyncio.create_task(self._recovery_loop(), name="daily-summary-stt-recovery")

    async def close(self) -> None:
        self._closed = True
        tasks = [task for task in (*self._workers, self._recovery_task) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers = []
        self._recovery_task = None

    def enqueue(self, job: TranscriptionJob) -> None:
        if self._closed:
            return
        key = (job.chat_id, job.telegram_message_id)
        if key in self._pending_keys:
            return  # already queued or being worked on -- a recovery scan must not duplicate it
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            # Deliberately not marked as pending: the periodic recovery scan will
            # re-discover this message from the archive table once capacity frees
            # up, so a full queue is a delay, not a permanent loss.
            logger.warning(
                "daily summary STT queue full, leaving job chat_id=%s message_id=%s to the recovery scan",
                job.chat_id,
                job.telegram_message_id,
            )
            return
        self._pending_keys.add(key)

    async def _worker_loop(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._process_job(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "daily summary STT job crashed chat_id=%s message_id=%s -- queue keeps running",
                    job.chat_id,
                    job.telegram_message_id,
                )
            finally:
                # Forget the message either way, so a later recovery scan may retry
                # it (e.g. after a failed/skipped attempt releases its DB claim).
                self._pending_keys.discard((job.chat_id, job.telegram_message_id))
                self._queue.task_done()

    async def _process_job(self, job: TranscriptionJob) -> None:
        async with self._session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            chat_settings = await repo.get_chat_settings(chat_id=job.chat_id)
        if chat_settings is None or not is_transcription_enabled(chat_settings, message_type=job.message_type):
            return  # toggle turned off (or chat gone) before we got to it -- spend nothing

        archive_row_id = await self._claim_with_retry(job)
        if archive_row_id is None:
            return

        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            seconds_used_today = await repo.sum_transcription_seconds_in_window(
                chat_id=job.chat_id, window_from=now - timedelta(hours=24), window_to=now
            )
        if not is_within_transcription_budget(
            seconds_used_today=seconds_used_today,
            job_duration_seconds=job.duration_seconds,
            max_seconds_per_day=self._settings.daily_summary_max_transcription_seconds_per_chat_per_day,
        ):
            logger.info(
                "daily summary STT: chat_id=%s over the daily transcription budget, skipping message_id=%s",
                job.chat_id,
                job.telegram_message_id,
            )
            await self._release(archive_row_id)
            return

        try:
            file = await self._bot.get_file(job.file_id)
            downloaded = await self._bot.download_file(file.file_path)  # type: ignore[arg-type]
            raw = downloaded.read() if hasattr(downloaded, "read") else bytes(downloaded)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "daily summary STT: failed to download file chat_id=%s message_id=%s",
                job.chat_id,
                job.telegram_message_id,
                exc_info=True,
            )
            await self._release(archive_row_id)
            return

        try:
            text = await self._stt_client.transcribe_with_retry(raw, filename=job.filename)
        except SttClientError:
            logger.warning(
                "daily summary STT: transcription failed chat_id=%s message_id=%s",
                job.chat_id,
                job.telegram_message_id,
                exc_info=True,
            )
            await self._release(archive_row_id)
            return

        completed_at = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            await repo.finalize_message_transcript(
                archive_row_id=archive_row_id, transcript=text, transcribed_at=completed_at
            )
            await repo.record_llm_usage(
                message_archive_id=archive_row_id,
                chat_id=job.chat_id,
                feature="daily_summary",
                stage="stt",
                model=self._stt_client.model,
                estimated_cost_usd=estimate_stt_cost_usd(audio_seconds=job.duration_seconds),
                audio_seconds=job.duration_seconds,
            )
            await session.commit()

    async def _claim_with_retry(self, job: TranscriptionJob) -> int | None:
        """Bounded retry/backoff waiting for the archive row to show up.

        A `None` result from `claim_message_for_transcription` means either "the
        row doesn't exist yet" (worth retrying) or "it's already claimed/done"
        (retrying is pointless but harmless) -- these aren't distinguished here on
        purpose: either way, giving up after a fixed number of attempts is the
        correct behavior, and not distinguishing them keeps this simple.
        """
        for attempt in range(self._max_lookup_attempts):
            async with self._session_factory() as session:
                repo = SqlAlchemyActivityRepository(session)
                claimed = await repo.claim_message_for_transcription(
                    chat_id=job.chat_id, telegram_message_id=job.telegram_message_id
                )
                await session.commit()
            if claimed is not None:
                return claimed
            if attempt < self._max_lookup_attempts - 1:
                await asyncio.sleep(self._lookup_backoff_seconds * (attempt + 1))

        logger.info(
            "daily summary STT: gave up waiting for archive row chat_id=%s message_id=%s after %s attempts",
            job.chat_id,
            job.telegram_message_id,
            self._max_lookup_attempts,
        )
        return None

    async def _release(self, archive_row_id: int) -> None:
        async with self._session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            await repo.release_transcription_claim(archive_row_id=archive_row_id)
            await session.commit()

    async def _recovery_loop(self) -> None:
        """Run the recovery scan periodically, forever (until cancelled by close()).

        One scan at startup alone is not enough (issue #81): a job dropped by a
        full in-memory queue, or a message archived seconds after the startup scan,
        would otherwise sit untranscribed until the next process restart -- and fall
        out of the recovery lookback entirely if that restart is late. The archive
        table is the durable work state; this loop is what keeps re-reading it.
        """
        while True:
            try:
                await self._run_recovery_scan()
            except asyncio.CancelledError:
                raise
            except Exception:
                # _run_recovery_scan already guards its body, but the loop itself
                # must never die -- a dead scan means silently lost jobs again.
                logger.exception("daily summary STT: recovery scan loop failed")
            await asyncio.sleep(self._recovery_scan_interval_seconds)

    async def _run_recovery_scan(self) -> None:
        """One pass: re-queue voice/video_note messages that were archived but
        never got a transcript -- a live job's in-memory `asyncio.Queue` does not
        survive a process restart, and a full one drops jobs outright. Safe to run
        at any frequency: candidates with a live claim are excluded by
        `list_pending_voice_transcription_candidates`, already-known jobs are
        deduplicated by `enqueue`, and the DB claim makes any remaining race pay
        for at most one STT call."""
        try:
            since = datetime.now(timezone.utc) - timedelta(hours=_RECOVERY_LOOKBACK_HOURS)
            async with self._session_factory() as session:
                repo = SqlAlchemyActivityRepository(session)
                candidates = await repo.list_pending_voice_transcription_candidates(since=since)

            requeued = 0
            for candidate in candidates:
                job = build_job_from_raw_message(
                    chat_id=candidate.chat_id,
                    telegram_message_id=candidate.telegram_message_id,
                    message_type=candidate.message_type,
                    raw_message_json=candidate.raw_message_json,
                )
                if job is not None:
                    self.enqueue(job)
                    requeued += 1
            if requeued:
                logger.info("daily summary STT: recovery scan re-queued %s message(s)", requeued)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("daily summary STT: recovery scan failed")
