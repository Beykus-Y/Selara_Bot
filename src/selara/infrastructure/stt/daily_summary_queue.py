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

A periodic scan that blindly re-discovered the same rows would also turn every
failing message into an unbounded retry loop: a failed attempt releases the DB
claim (`transcribed_at = NULL`), which makes the row a candidate again on the
very next pass. Before, that cost one retry per process restart; a scan every
minute would re-download the file from Telegram and re-bill the STT provider
forever for a deterministic failure (too large file, provider 4xx, permanent
over-budget). So every released attempt puts its message into an in-memory
retry cooldown: the recovery scan skips it until the cooldown expires, which
bounds the retry rate without losing the durable work state (a restart simply
re-arms everything once).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.daily_summary.transcription import (
    TranscriptionJob,
    build_job_from_raw_message,
    is_transcription_enabled,
)
from selara.core.config import Settings
from selara.infrastructure.db.repositories import STT_CLAIM_STALE_AFTER_SECONDS, SqlAlchemyActivityRepository
from selara.infrastructure.db.stt_budget_repository import SttBudgetRepository
from selara.infrastructure.llm.pricing import estimate_stt_cost_usd
from selara.infrastructure.stt.client import SttClient

logger = logging.getLogger(__name__)

_DEFAULT_MAX_QUEUE_SIZE = 1000
_DEFAULT_MAX_LOOKUP_ATTEMPTS = 5
_DEFAULT_LOOKUP_BACKOFF_SECONDS = 2.0
_DEFAULT_RECOVERY_SCAN_INTERVAL_SECONDS = 60.0
# Minimum spacing between the periodic scan and the next pass over the same
# message whose attempt failed and released its DB claim. Without it the scan
# retries a deterministically failing message every scan interval forever
# (re-downloading the file and re-billing the STT provider each time).
_DEFAULT_RETRY_COOLDOWN_SECONDS = 900.0
_MIN_RECOVERY_SCAN_INTERVAL_SECONDS = 1.0
_QUEUE_FULL_LOG_INTERVAL_SECONDS = 60.0
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
        retry_cooldown_seconds: float = _DEFAULT_RETRY_COOLDOWN_SECONDS,
    ) -> None:
        self._bot = bot
        self._stt_client = stt_client
        self._session_factory = session_factory
        self._settings = settings
        self._queue: asyncio.Queue[TranscriptionJob] = asyncio.Queue(maxsize=max_queue_size)
        self._max_lookup_attempts = max_lookup_attempts
        self._lookup_backoff_seconds = lookup_backoff_seconds
        self._recovery_scan_interval_seconds = (
            float(recovery_scan_interval_seconds)
            if recovery_scan_interval_seconds > 0
            else _MIN_RECOVERY_SCAN_INTERVAL_SECONDS
        )
        self._retry_cooldown_seconds = max(0.0, float(retry_cooldown_seconds))
        # (chat_id, telegram_message_id) of jobs currently queued or in flight --
        # keeps periodic recovery scans from piling duplicate jobs onto a backed-up
        # queue. A job dropped by a full queue is deliberately NOT marked here, so
        # the next scan gets another chance at it (issue #81).
        self._pending_keys: set[tuple[int, int]] = set()
        # (chat_id, telegram_message_id) -> monotonic deadline until which the
        # recovery scan must not re-enqueue that message after a released attempt.
        self._cooldown_until: dict[tuple[int, int], float] = {}
        self._queue_full_suppressed = 0
        self._queue_full_last_log_at = 0.0
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

    @staticmethod
    def _job_key(job: TranscriptionJob) -> tuple[int, int]:
        return (job.chat_id, job.telegram_message_id)

    def _is_cooling_down(self, key: tuple[int, int]) -> bool:
        deadline = self._cooldown_until.get(key)
        if deadline is None:
            return False
        if deadline <= time.monotonic():
            del self._cooldown_until[key]
            return False
        return True

    def _start_retry_cooldown(self, job: TranscriptionJob) -> None:
        """Back off from re-processing a message whose attempt just failed and
        released its DB claim. Bounds the retry rate of permanently failing
        messages to one attempt per cooldown instead of one per scan interval.
        """
        if self._retry_cooldown_seconds <= 0:
            return
        now = time.monotonic()
        # Opportunistically drop expired entries so the mapping cannot grow
        # without bound in a long-running process.
        for key in [key for key, deadline in self._cooldown_until.items() if deadline <= now]:
            del self._cooldown_until[key]
        self._cooldown_until[self._job_key(job)] = now + self._retry_cooldown_seconds

    def enqueue(self, job: TranscriptionJob) -> bool:
        """Queue a job unless it is already queued/in flight or cooling down.

        Returns True only when the job was actually put on the queue, so the
        recovery scan can report real insertions instead of attempts.
        """
        if self._closed:
            return False
        key = self._job_key(job)
        if key in self._pending_keys:
            return False  # already queued or being worked on -- a recovery scan must not duplicate it
        if self._is_cooling_down(key):
            # A previous attempt failed and released its claim; the periodic scan
            # must not immediately re-download/re-transcribe it again.
            return False
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            # Deliberately not marked as pending: the periodic recovery scan will
            # re-discover this message from the archive table once capacity frees
            # up, so a full queue is a delay, not a permanent loss.
            self._log_queue_full(job)
            return False
        self._pending_keys.add(key)
        return True

    def _log_queue_full(self, job: TranscriptionJob) -> None:
        """Rate-limit the queue-full warning: a full queue makes the scan repeat
        the same dropped jobs on every pass, which would otherwise be one warning
        line per candidate per minute, forever."""
        self._queue_full_suppressed += 1
        now = time.monotonic()
        if now - self._queue_full_last_log_at < _QUEUE_FULL_LOG_INTERVAL_SECONDS:
            return
        suppressed = max(0, self._queue_full_suppressed - 1)
        self._queue_full_suppressed = 0
        self._queue_full_last_log_at = now
        logger.warning(
            "daily summary STT queue full, leaving job chat_id=%s message_id=%s to the recovery scan "
            "(%s further drops suppressed in the last %ss)",
            job.chat_id,
            job.telegram_message_id,
            suppressed,
            int(_QUEUE_FULL_LOG_INTERVAL_SECONDS),
        )

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

        claim_at = datetime.now(timezone.utc)
        archive_row_id = await self._claim_with_retry(job, claim_at=claim_at)
        if archive_row_id is None:
            return

        async with self._session_factory() as session:
            token = await SttBudgetRepository(session).reserve(
                chat_id=job.chat_id, archive_row_id=archive_row_id, claim_at=claim_at,
                duration_seconds=job.duration_seconds,
                max_seconds=self._settings.daily_summary_max_transcription_seconds_per_chat_per_day,
            )
            await session.commit()
        if token is None:
            logger.info(
                "daily summary STT: chat_id=%s over the daily transcription budget, skipping message_id=%s",
                job.chat_id,
                job.telegram_message_id,
            )
            await self._release(job, archive_row_id, claim_at=claim_at)
            return

        try:
            # Finish network work before the durable claim/lease can be reclaimed.
            deadline = claim_at + timedelta(seconds=STT_CLAIM_STALE_AFTER_SECONDS - 5)
            remaining = max(0.0, (deadline - datetime.now(timezone.utc)).total_seconds())
            async with asyncio.timeout(remaining):
                file = await self._bot.get_file(job.file_id)
                downloaded = await self._bot.download_file(file.file_path)  # type: ignore[arg-type]
                raw = downloaded.read() if hasattr(downloaded, "read") else bytes(downloaded)
                text = await self._stt_client.transcribe_with_retry(raw, filename=job.filename)
        except asyncio.CancelledError:
            try:
                await self._release(job, archive_row_id, reservation_token=token)
            except Exception:
                # Cancellation must still stop the worker during a DB outage;
                # the durable lease/recovery policy covers uncertain cleanup.
                logger.exception("daily summary STT: cancellation cleanup failed token=%s", token)
            raise
        except Exception:
            logger.warning(
                "daily summary STT: download/transcription failed chat_id=%s message_id=%s",
                job.chat_id,
                job.telegram_message_id,
                exc_info=True,
            )
            await self._release(job, archive_row_id, reservation_token=token)
            return

        async with self._session_factory() as session:
            settled = await SttBudgetRepository(session).settle(
                token=token, transcript=text,
                model=self._stt_client.model,
                estimated_cost_usd=estimate_stt_cost_usd(audio_seconds=job.duration_seconds),
                audio_seconds=job.duration_seconds,
            )
            await session.commit()
        if not settled:
            logger.warning("daily summary STT: discarded expired/reclaimed result chat_id=%s message_id=%s",
                           job.chat_id, job.telegram_message_id)

    async def _claim_with_retry(self, job: TranscriptionJob, *, claim_at: datetime | None = None) -> int | None:
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
                    chat_id=job.chat_id, telegram_message_id=job.telegram_message_id, now=claim_at,
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

    async def _release(self, job: TranscriptionJob, archive_row_id: int, *,
                       reservation_token: str | None = None, claim_at: datetime | None = None) -> None:
        """Release the DB claim after a failed/skipped attempt and start the
        retry cooldown so the next recovery scan does not immediately repeat the
        same (possibly permanently failing) work."""
        async with self._session_factory() as session:
            if reservation_token is not None:
                await SttBudgetRepository(session).release(token=reservation_token)
            else:
                repo = SqlAlchemyActivityRepository(session)
                await repo.release_transcription_claim(archive_row_id=archive_row_id, claim_at=claim_at)
            await session.commit()
        self._start_retry_cooldown(job)

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
        `list_pending_voice_transcription_candidates`, already-known jobs and
        messages inside their retry cooldown are skipped by `enqueue`, and the DB
        claim makes any remaining race pay for at most one STT call."""
        try:
            since = datetime.now(timezone.utc) - timedelta(hours=_RECOVERY_LOOKBACK_HOURS)
            async with self._session_factory() as session:
                repo = SqlAlchemyActivityRepository(session)
                candidates = await repo.list_pending_voice_transcription_candidates(since=since)

            queued = 0
            skipped = 0
            unbuildable = 0
            for candidate in candidates:
                job = build_job_from_raw_message(
                    chat_id=candidate.chat_id,
                    telegram_message_id=candidate.telegram_message_id,
                    message_type=candidate.message_type,
                    raw_message_json=candidate.raw_message_json,
                )
                if job is None:
                    # The row has no usable file_id (e.g. a redacted/legacy
                    # snapshot) and can never become a job, yet it stays a
                    # candidate forever and occupies one of the LIMIT slots on
                    # every pass -- surface it instead of skipping silently.
                    unbuildable += 1
                    logger.warning(
                        "daily summary STT: recovery scan cannot build a job from archived message "
                        "chat_id=%s message_id=%s type=%s",
                        candidate.chat_id,
                        candidate.telegram_message_id,
                        candidate.message_type,
                    )
                    continue
                if self.enqueue(job):
                    queued += 1
                else:
                    skipped += 1
            if queued or skipped or unbuildable:
                logger.info(
                    "daily summary STT: recovery scan queued %s message(s), skipped %s "
                    "(queued/in flight or in retry cooldown), unbuildable %s",
                    queued,
                    skipped,
                    unbuildable,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("daily summary STT: recovery scan failed")
