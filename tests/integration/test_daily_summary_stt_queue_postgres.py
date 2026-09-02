"""Integration tests for the daily summary voice/video_note transcription queue
(docs/DAILY_SUMMARY_TODO.md, infrastructure/stt/daily_summary_queue.py).

Covers, against a real Postgres: the archive-row race (job enqueued before the
message is archived), bounded give-up when the row never appears, successful
transcription + cost accounting, toggle-turned-off-before-processing, per-chat
transcription budget enforcement, download/STT failure release-and-move-on, atomic
dedup between a "live" claim and a "recovery scan" claim for the same message, the
recovery scan itself finding and re-queuing pending candidates, worker pool sizing,
and one bad job not taking down the whole worker loop.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.daily_summary.transcription import TranscriptionJob
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import LlmUsageLogModel, MessageArchiveModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.infrastructure.stt.client import SttClientError
from selara.infrastructure.stt.daily_summary_queue import DailySummaryTranscriptionQueue

_CHAT_ID = -100999
_USER_ID = 2001
_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_chat(session_factory, *, include_voice: bool = True, include_video_notes: bool = False) -> None:
    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")
        await repo._upsert_chat(chat)
        await repo._upsert_user(
            UserSnapshot(telegram_user_id=_USER_ID, username="vasya", first_name="Vasya", last_name=None, is_bot=False)
        )
        await repo.upsert_chat_settings(
            chat=chat,
            values={
                "save_message": True,
                "daily_summary_include_voice": include_voice,
                "daily_summary_include_video_notes": include_video_notes,
            },
        )
        await session.commit()


async def _insert_archive_row(
    session_factory,
    *,
    telegram_message_id: int,
    message_type: str = "voice",
    file_id: str = "file-1",
    duration: int = 10,
    snapshot_at: datetime | None = None,
) -> None:
    async with session_factory() as session:
        session.add(
            MessageArchiveModel(
                chat_id=_CHAT_ID,
                user_id=_USER_ID,
                telegram_message_id=telegram_message_id,
                snapshot_kind="created",
                snapshot_at=snapshot_at or _NOW,
                sent_at=snapshot_at or _NOW,
                message_type=message_type,
                text=None,
                raw_message_json={message_type: {"file_id": file_id, "duration": duration}},
                snapshot_hash=f"hash-{telegram_message_id}",
            )
        )
        await session.commit()


def _job(*, telegram_message_id: int = 1, duration_seconds: float = 10.0, message_type: str = "voice") -> TranscriptionJob:
    return TranscriptionJob(
        chat_id=_CHAT_ID,
        telegram_message_id=telegram_message_id,
        file_id="file-1",
        filename="voice.ogg" if message_type == "voice" else "video_note.mp4",
        message_type=message_type,
        duration_seconds=duration_seconds,
    )


def _fake_bot(*, raw_audio: bytes = b"audio-bytes") -> SimpleNamespace:
    return SimpleNamespace(
        get_file=AsyncMock(return_value=SimpleNamespace(file_path="path.ogg")),
        download_file=AsyncMock(return_value=SimpleNamespace(read=lambda: raw_audio)),
    )


def _fake_stt_client(*, text: str = "привет из голосового", error: Exception | None = None) -> SimpleNamespace:
    async def _transcribe(raw, *, filename):
        if error is not None:
            raise error
        return text

    return SimpleNamespace(transcribe_with_retry=AsyncMock(side_effect=_transcribe), model="whisper-test")


def _settings(*, concurrency: int = 2, max_seconds_per_day: int = 1800) -> SimpleNamespace:
    return SimpleNamespace(
        daily_summary_stt_concurrency=concurrency,
        daily_summary_max_transcription_seconds_per_chat_per_day=max_seconds_per_day,
    )


async def _get_archive_row(session_factory, *, telegram_message_id: int) -> MessageArchiveModel:
    async with session_factory() as session:
        row = (
            await session.execute(
                select(MessageArchiveModel).where(
                    MessageArchiveModel.chat_id == _CHAT_ID,
                    MessageArchiveModel.telegram_message_id == telegram_message_id,
                )
            )
        ).scalar_one()
        return row


async def _count_stt_usage_rows(session_factory) -> int:
    async with session_factory() as session:
        rows = (await session.execute(select(LlmUsageLogModel).where(LlmUsageLogModel.stage == "stt"))).scalars().all()
        return len(rows)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_retries_until_archive_row_appears_then_succeeds() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory)
        queue = DailySummaryTranscriptionQueue(
            bot=_fake_bot(), stt_client=_fake_stt_client(), session_factory=session_factory,
            settings=_settings(), max_lookup_attempts=6, lookup_backoff_seconds=0.05,
        )
        job = _job(telegram_message_id=1)

        async def _insert_late():
            await asyncio.sleep(0.15)
            await _insert_archive_row(session_factory, telegram_message_id=1)

        claim_task = asyncio.create_task(queue._claim_with_retry(job))
        insert_task = asyncio.create_task(_insert_late())
        archive_row_id, _ = await asyncio.gather(claim_task, insert_task)

        assert archive_row_id is not None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_gives_up_after_bounded_attempts_if_row_never_appears() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory)
        queue = DailySummaryTranscriptionQueue(
            bot=_fake_bot(), stt_client=_fake_stt_client(), session_factory=session_factory,
            settings=_settings(), max_lookup_attempts=3, lookup_backoff_seconds=0.02,
        )
        job = _job(telegram_message_id=999)  # never archived

        result = await queue._claim_with_retry(job)

        assert result is None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_process_job_transcribes_and_records_usage() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory)
        await _insert_archive_row(session_factory, telegram_message_id=1, duration=15)
        bot = _fake_bot()
        stt_client = _fake_stt_client(text="итоговый текст")
        queue = DailySummaryTranscriptionQueue(
            bot=bot, stt_client=stt_client, session_factory=session_factory, settings=_settings(),
        )

        await queue._process_job(_job(telegram_message_id=1, duration_seconds=15.0))

        row = await _get_archive_row(session_factory, telegram_message_id=1)
        assert row.transcript == "итоговый текст"
        assert row.transcribed_at is not None

        async with session_factory() as session:
            usage_rows = (
                await session.execute(select(LlmUsageLogModel).where(LlmUsageLogModel.message_archive_id == row.id))
            ).scalars().all()
        assert len(usage_rows) == 1
        assert usage_rows[0].stage == "stt"
        assert usage_rows[0].audio_seconds == 15.0
        assert usage_rows[0].model == "whisper-test"
        stt_client.transcribe_with_retry.assert_awaited_once()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_process_job_skips_when_toggle_disabled_before_processing() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, include_voice=False)
        await _insert_archive_row(session_factory, telegram_message_id=1)
        stt_client = _fake_stt_client()
        queue = DailySummaryTranscriptionQueue(
            bot=_fake_bot(), stt_client=stt_client, session_factory=session_factory, settings=_settings(),
        )

        await queue._process_job(_job(telegram_message_id=1))

        stt_client.transcribe_with_retry.assert_not_awaited()
        row = await _get_archive_row(session_factory, telegram_message_id=1)
        assert row.transcript is None
        assert row.transcribed_at is None  # never even claimed
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_process_job_releases_claim_when_over_daily_budget() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory)
        await _insert_archive_row(session_factory, telegram_message_id=1, duration=100)
        # pre-fill the daily budget close to the cap
        async with session_factory() as session:
            session.add(
                LlmUsageLogModel(
                    chat_id=_CHAT_ID, feature="daily_summary", stage="stt", model="whisper-test",
                    audio_seconds=1750, estimated_cost_usd=0.1, created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        stt_client = _fake_stt_client()
        queue = DailySummaryTranscriptionQueue(
            bot=_fake_bot(), stt_client=stt_client, session_factory=session_factory,
            settings=_settings(max_seconds_per_day=1800),
        )

        await queue._process_job(_job(telegram_message_id=1, duration_seconds=100.0))

        stt_client.transcribe_with_retry.assert_not_awaited()
        row = await _get_archive_row(session_factory, telegram_message_id=1)
        assert row.transcript is None
        assert row.transcribed_at is None  # claim released -- can be retried once budget resets
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_process_job_releases_claim_on_download_failure() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory)
        await _insert_archive_row(session_factory, telegram_message_id=1)
        bot = SimpleNamespace(get_file=AsyncMock(side_effect=RuntimeError("network down")))
        stt_client = _fake_stt_client()
        queue = DailySummaryTranscriptionQueue(bot=bot, stt_client=stt_client, session_factory=session_factory, settings=_settings())

        await queue._process_job(_job(telegram_message_id=1))

        stt_client.transcribe_with_retry.assert_not_awaited()
        row = await _get_archive_row(session_factory, telegram_message_id=1)
        assert row.transcript is None
        assert row.transcribed_at is None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_process_job_releases_claim_on_stt_failure_without_crashing() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory)
        await _insert_archive_row(session_factory, telegram_message_id=1)
        stt_client = _fake_stt_client(error=SttClientError("provider down"))
        queue = DailySummaryTranscriptionQueue(bot=_fake_bot(), stt_client=stt_client, session_factory=session_factory, settings=_settings())

        await queue._process_job(_job(telegram_message_id=1))  # must not raise

        row = await _get_archive_row(session_factory, telegram_message_id=1)
        assert row.transcript is None
        assert row.transcribed_at is None
        assert await _count_stt_usage_rows(session_factory) == 0
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_claims_for_the_same_message_only_one_wins() -> None:
    # simulates a live job and a recovery-scan job racing for the same message
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory)
        await _insert_archive_row(session_factory, telegram_message_id=1)
        queue = DailySummaryTranscriptionQueue(bot=_fake_bot(), stt_client=_fake_stt_client(), session_factory=session_factory, settings=_settings())
        job = _job(telegram_message_id=1)

        results = await asyncio.gather(queue._claim_with_retry(job), queue._claim_with_retry(job))

        winners = [r for r in results if r is not None]
        assert len(winners) == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_recovery_scan_finds_and_processes_pending_candidate_exactly_once() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, include_voice=True)
        await _insert_archive_row(session_factory, telegram_message_id=1, file_id="recovered-file", duration=8)
        stt_client = _fake_stt_client(text="восстановленный текст")
        queue = DailySummaryTranscriptionQueue(bot=_fake_bot(), stt_client=stt_client, session_factory=session_factory, settings=_settings())

        await queue._run_recovery_scan()
        assert queue._queue.qsize() == 1

        recovered_job = await queue._queue.get()
        await queue._process_job(recovered_job)

        row = await _get_archive_row(session_factory, telegram_message_id=1)
        assert row.transcript == "восстановленный текст"
        assert await _count_stt_usage_rows(session_factory) == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stale_claim_from_a_crashed_worker_can_be_reclaimed() -> None:
    # Simulates: claim -> transcribed_at set -> process hard-crashes (kill -9) before
    # finalize/release ever runs. Without a lease/staleness cutoff this message would
    # be stuck forever (transcript=NULL, transcribed_at not NULL, recovery scan
    # thinks it's already "claimed"). A claim older than STT_CLAIM_STALE_AFTER_SECONDS
    # must be reclaimable, both by a direct claim attempt and by the recovery scan.
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, include_voice=True)
        await _insert_archive_row(session_factory, telegram_message_id=1, duration=8)

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            dead_claim_at = datetime.now(timezone.utc) - timedelta(seconds=900)  # older than the 600s default lease
            claimed = await repo.claim_message_for_transcription(
                chat_id=_CHAT_ID, telegram_message_id=1, now=dead_claim_at
            )
            await session.commit()
        assert claimed is not None

        row_before = await _get_archive_row(session_factory, telegram_message_id=1)
        assert row_before.transcript is None
        assert row_before.transcribed_at is not None  # looks "claimed" from a naive read

        # 1) a fresh claim attempt (as the live path would do after a restart) must
        #    win, since the existing claim is stale.
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            reclaimed = await repo.claim_message_for_transcription(chat_id=_CHAT_ID, telegram_message_id=1)
            await session.commit()
        assert reclaimed is not None
        assert reclaimed == row_before.id

        # reset back to the dead-claim state to test the recovery-scan path too
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            await repo.release_transcription_claim(archive_row_id=row_before.id)
            await repo.claim_message_for_transcription(chat_id=_CHAT_ID, telegram_message_id=1, now=dead_claim_at)
            await session.commit()

        # 2) the recovery scan itself must surface it as a candidate again.
        stt_client = _fake_stt_client(text="восстановлено после краша")
        queue = DailySummaryTranscriptionQueue(bot=_fake_bot(), stt_client=stt_client, session_factory=session_factory, settings=_settings())
        await queue._run_recovery_scan()
        assert queue._queue.qsize() == 1

        job = await queue._queue.get()
        await queue._process_job(job)

        row_after = await _get_archive_row(session_factory, telegram_message_id=1)
        assert row_after.transcript == "восстановлено после краша"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_claim_within_lease_is_not_stolen_by_a_second_claim_attempt() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory)
        await _insert_archive_row(session_factory, telegram_message_id=1)
        queue = DailySummaryTranscriptionQueue(bot=_fake_bot(), stt_client=_fake_stt_client(), session_factory=session_factory, settings=_settings())
        job = _job(telegram_message_id=1)

        first = await queue._claim_with_retry(job)
        assert first is not None

        # a second attempt right away (well within the 600s lease) must not steal it
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            second = await repo.claim_message_for_transcription(chat_id=_CHAT_ID, telegram_message_id=1)
        assert second is None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_recovery_scan_does_not_requeue_already_claimed_message() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, include_voice=True)
        await _insert_archive_row(session_factory, telegram_message_id=1)
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            claimed = await repo.claim_message_for_transcription(chat_id=_CHAT_ID, telegram_message_id=1)
            await session.commit()
        assert claimed is not None

        queue = DailySummaryTranscriptionQueue(bot=_fake_bot(), stt_client=_fake_stt_client(), session_factory=session_factory, settings=_settings())
        await queue._run_recovery_scan()

        assert queue._queue.qsize() == 0
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_pool_size_matches_configured_concurrency() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory)
        queue = DailySummaryTranscriptionQueue(
            bot=_fake_bot(), stt_client=_fake_stt_client(), session_factory=session_factory, settings=_settings(concurrency=3),
        )
        await queue.start()
        try:
            assert len(queue._workers) == 3
            assert all(not w.done() for w in queue._workers)
        finally:
            await queue.close()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_one_crashing_job_does_not_stop_the_worker_from_processing_the_next() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory)
        await _insert_archive_row(session_factory, telegram_message_id=2, file_id="file-2", duration=5)
        stt_client = _fake_stt_client(text="второе сообщение обработано")

        queue = DailySummaryTranscriptionQueue(
            bot=_fake_bot(), stt_client=stt_client, session_factory=session_factory, settings=_settings(concurrency=1),
        )

        # message_id=1 was never archived -> _claim_with_retry exhausts attempts and
        # returns None (handled gracefully); message_id=2 exists and must still be
        # processed by the same worker afterward.
        queue._max_lookup_attempts = 1
        queue._lookup_backoff_seconds = 0.01
        await queue.start()
        try:
            queue.enqueue(_job(telegram_message_id=1))
            queue.enqueue(_job(telegram_message_id=2, duration_seconds=5.0))
            await asyncio.wait_for(queue._queue.join(), timeout=5.0)
        finally:
            await queue.close()

        row2 = await _get_archive_row(session_factory, telegram_message_id=2)
        assert row2.transcript == "второе сообщение обработано"
    finally:
        await engine.dispose()
