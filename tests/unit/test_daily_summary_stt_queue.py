"""In-memory unit tests for DailySummaryTranscriptionQueue's queue-full and
recovery-scan behavior (issue #81): a job dropped by a full asyncio.Queue must
stay eligible for re-discovery by the (now periodic) recovery scan, scans must
not pile duplicate jobs onto a backed-up queue, and the scan loop must keep
running on its interval. The DB-backed half of these guarantees is covered in
tests/integration/test_daily_summary_stt_queue_postgres.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.application.daily_summary.transcription import TranscriptionJob
from selara.infrastructure.stt import daily_summary_queue as dq
from selara.infrastructure.stt.daily_summary_queue import DailySummaryTranscriptionQueue

_CHAT_ID = -100123


class _FakeSession:
    """Minimal async session stand-in: the queue only commits releases."""

    def __init__(self) -> None:
        self.commits = 0

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def commit(self) -> None:
        self.commits += 1


def _candidate(*, telegram_message_id: int, raw_message_json: dict) -> SimpleNamespace:
    return SimpleNamespace(
        chat_id=_CHAT_ID,
        telegram_message_id=telegram_message_id,
        message_type="voice",
        raw_message_json=raw_message_json,
    )


def _job(*, telegram_message_id: int = 1) -> TranscriptionJob:
    return TranscriptionJob(
        chat_id=_CHAT_ID,
        telegram_message_id=telegram_message_id,
        file_id=f"file-{telegram_message_id}",
        filename="voice.ogg",
        message_type="voice",
        duration_seconds=10.0,
    )


def _queue(*, max_queue_size: int = 1000, recovery_scan_interval_seconds: float = 60.0) -> DailySummaryTranscriptionQueue:
    # bot/stt_client/session_factory are only touched once jobs are actually
    # processed or the recovery scan hits the DB -- not by these paths.
    return DailySummaryTranscriptionQueue(
        bot=SimpleNamespace(),
        stt_client=SimpleNamespace(),
        session_factory=SimpleNamespace(),
        settings=SimpleNamespace(
            daily_summary_stt_concurrency=1,
            daily_summary_max_transcription_seconds_per_chat_per_day=1800,
        ),
        max_queue_size=max_queue_size,
        recovery_scan_interval_seconds=recovery_scan_interval_seconds,
    )


async def test_enqueue_into_full_queue_does_not_mark_job_as_known() -> None:
    queue = _queue(max_queue_size=1)
    queue.enqueue(_job(telegram_message_id=1))
    queue.enqueue(_job(telegram_message_id=2))  # QueueFull -- the issue #81 drop path

    assert queue._queue.qsize() == 1
    assert queue._queue.get_nowait().telegram_message_id == 1
    # the dropped job must NOT be remembered as "already queued", or the
    # recovery scan would never re-discover it
    assert (_CHAT_ID, 2) not in queue._pending_keys


async def test_dropped_job_can_be_enqueued_again_once_capacity_frees() -> None:
    queue = _queue(max_queue_size=1)
    queue.enqueue(_job(telegram_message_id=1))
    queue.enqueue(_job(telegram_message_id=2))  # dropped, queue full

    queue._queue.get_nowait()  # capacity frees (worker picked the first job up)
    queue.enqueue(_job(telegram_message_id=2))  # as the recovery scan would

    assert queue._queue.qsize() == 1
    assert queue._queue.get_nowait().telegram_message_id == 2


async def test_enqueue_deduplicates_job_already_queued() -> None:
    queue = _queue()
    queue.enqueue(_job(telegram_message_id=7))
    queue.enqueue(_job(telegram_message_id=7))  # e.g. a second recovery scan pass

    assert queue._queue.qsize() == 1


async def test_worker_frees_pending_key_after_processing() -> None:
    queue = _queue()
    processed: list[TranscriptionJob] = []

    async def _fake_process(job: TranscriptionJob) -> None:
        processed.append(job)

    queue._process_job = _fake_process  # type: ignore[method-assign]
    await queue.start()
    try:
        queue.enqueue(_job(telegram_message_id=5))
        await asyncio.wait_for(queue._queue.join(), timeout=5.0)

        assert len(processed) == 1
        assert (_CHAT_ID, 5) not in queue._pending_keys

        # after processing, the same message may legitimately be enqueued again
        # (a recovery scan retry after a released claim) instead of being deduped
        queue.enqueue(_job(telegram_message_id=5))
        await asyncio.wait_for(queue._queue.join(), timeout=5.0)
        assert len(processed) == 2
    finally:
        await queue.close()


async def test_recovery_loop_rescans_on_interval() -> None:
    queue = _queue(recovery_scan_interval_seconds=0.01)
    queue._run_recovery_scan = AsyncMock()  # type: ignore[method-assign]
    loop = asyncio.create_task(queue._recovery_loop())
    try:
        await asyncio.sleep(0.08)
        assert queue._run_recovery_scan.await_count >= 2  # not a one-shot anymore
    finally:
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)


async def test_recovery_loop_keeps_running_when_a_scan_crashes() -> None:
    queue = _queue(recovery_scan_interval_seconds=0.01)
    calls = {"n": 0}

    async def _flaky_scan() -> None:
        calls["n"] += 1
        raise RuntimeError("scan blew up")

    queue._run_recovery_scan = _flaky_scan  # type: ignore[method-assign]
    loop = asyncio.create_task(queue._recovery_loop())
    try:
        await asyncio.sleep(0.08)
        assert calls["n"] >= 2  # one failed scan must not kill the discovery loop
        assert not loop.done()
    finally:
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)


async def test_close_without_start_is_safe() -> None:
    # close() before start() (no workers, no recovery task) -- relevant for the
    # restart paths this queue now supports
    queue = _queue()
    queue.enqueue(_job(telegram_message_id=1))
    await queue.close()
    assert queue._closed


@pytest.mark.parametrize("first,second", [(1, 1), (1, 2)])
async def test_pending_key_is_scoped_per_message(first: int, second: int) -> None:
    queue = _queue()
    queue.enqueue(_job(telegram_message_id=first))
    queue.enqueue(_job(telegram_message_id=second))

    assert queue._queue.qsize() == (1 if first == second else 2)


async def test_enqueue_reports_whether_the_job_was_actually_queued() -> None:
    queue = _queue(max_queue_size=1)

    assert queue.enqueue(_job(telegram_message_id=1)) is True
    assert queue.enqueue(_job(telegram_message_id=1)) is False  # dedup
    assert queue.enqueue(_job(telegram_message_id=2)) is False  # queue full


async def test_job_under_retry_cooldown_is_not_requeued_until_it_expires() -> None:
    # The core #81 follow-up guard: a released (failed) attempt must not be
    # retried on the very next scan, or a permanently failing message would be
    # re-downloaded/re-billed forever.
    queue = _queue()
    job = _job(telegram_message_id=9)
    key = (_CHAT_ID, 9)

    queue._start_retry_cooldown(job)

    assert key in queue._cooldown_until
    assert queue.enqueue(job) is False
    assert queue._queue.qsize() == 0

    # once the cooldown elapses the scan may retry it again
    queue._cooldown_until[key] = time.monotonic() - 1.0
    assert queue.enqueue(job) is True
    assert queue._queue.qsize() == 1


async def test_retry_cooldown_is_disabled_when_configured_to_zero() -> None:
    queue = _queue()
    queue._retry_cooldown_seconds = 0.0
    job = _job(telegram_message_id=11)

    queue._start_retry_cooldown(job)

    assert queue._cooldown_until == {}
    assert queue.enqueue(job) is True


async def test_release_starts_the_retry_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = _queue()
    queue._session_factory = _FakeSession  # type: ignore[assignment]
    release = AsyncMock()
    monkeypatch.setattr(
        dq,
        "SqlAlchemyActivityRepository",
        lambda session: SimpleNamespace(release_transcription_claim=release),
    )
    job = _job(telegram_message_id=12)

    await queue._release(job, 42)

    release.assert_awaited_once_with(archive_row_id=42)
    assert (_CHAT_ID, 12) in queue._cooldown_until
    assert queue.enqueue(job) is False


async def test_recovery_scan_skips_cooling_down_candidates_and_counts_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = _queue()
    queue._session_factory = _FakeSession  # type: ignore[assignment]
    candidates = [
        _candidate(telegram_message_id=21, raw_message_json={"voice": {"file_id": "f21", "duration": 5}}),
        _candidate(telegram_message_id=22, raw_message_json={"voice": {"file_id": "f22", "duration": 5}}),
    ]
    monkeypatch.setattr(
        dq,
        "SqlAlchemyActivityRepository",
        lambda session: SimpleNamespace(
            list_pending_voice_transcription_candidates=AsyncMock(return_value=candidates)
        ),
    )
    # message 21 already failed and is cooling down; 22 must still be queued
    queue._start_retry_cooldown(_job(telegram_message_id=21))

    await queue._run_recovery_scan()

    assert queue._queue.qsize() == 1
    assert queue._queue.get_nowait().telegram_message_id == 22


async def test_recovery_scan_warns_about_unbuildable_candidates(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    queue = _queue()
    queue._session_factory = _FakeSession  # type: ignore[assignment]
    candidates = [_candidate(telegram_message_id=31, raw_message_json={"voice": {}})]
    monkeypatch.setattr(
        dq,
        "SqlAlchemyActivityRepository",
        lambda session: SimpleNamespace(
            list_pending_voice_transcription_candidates=AsyncMock(return_value=candidates)
        ),
    )

    with caplog.at_level(logging.WARNING, logger=dq.logger.name):
        await queue._run_recovery_scan()

    assert queue._queue.qsize() == 0
    assert any("cannot build a job" in record.getMessage() for record in caplog.records)


async def test_queue_full_warnings_are_rate_limited(caplog: pytest.LogCaptureFixture) -> None:
    queue = _queue(max_queue_size=1)
    queue.enqueue(_job(telegram_message_id=1))

    with caplog.at_level(logging.WARNING, logger=dq.logger.name):
        for message_id in range(2, 40):
            queue.enqueue(_job(telegram_message_id=message_id))

    warnings = [record for record in caplog.records if "queue full" in record.getMessage()]
    assert len(warnings) == 1  # 38 drops, one warning line
    assert queue._queue_full_suppressed == 37  # the rest is folded into the next window


def test_recovery_interval_has_a_floor_for_non_positive_values() -> None:
    # asyncio.sleep(<=0) returns immediately; a misconfigured interval must not
    # turn the scan loop into a tight LIMIT-500 DB busy loop.
    assert _queue(recovery_scan_interval_seconds=0.0)._recovery_scan_interval_seconds >= 1.0
    assert _queue(recovery_scan_interval_seconds=-5.0)._recovery_scan_interval_seconds >= 1.0
    # a deliberately small positive interval stays as configured (tests rely on it)
    assert _queue(recovery_scan_interval_seconds=0.01)._recovery_scan_interval_seconds == 0.01
