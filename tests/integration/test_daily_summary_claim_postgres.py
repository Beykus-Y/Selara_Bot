"""Concurrency test for the daily summary "atomic claim" (docs/DAILY_SUMMARY_TODO.md).

Hypothesis this guards against: `claim_daily_summary_run` exists specifically so that
two scheduler ticks (or a scheduler tick racing a `/summary` call) can never both pay
for and send two daily summaries for the same chat/day. If the claim were implemented
as "SELECT existing row, then decide, then INSERT/UPDATE" (a classic check-then-act),
two concurrent callers could both observe "no live claim" and both proceed to run the
expensive LLM pipeline. The real implementation instead issues a single
`INSERT ... ON CONFLICT (...) DO UPDATE ... WHERE ...` statement, which Postgres
resolves atomically -- this test proves exactly one of two truly concurrent callers
gets the claim, and that a dead run (expired lease_until) can be reclaimed while a
live one cannot.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.domain.entities import ChatSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _chat() -> ChatSnapshot:
    return ChatSnapshot(telegram_chat_id=-100777, chat_type="supergroup", title="Test Chat")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_two_concurrent_claims_for_the_same_run_only_one_succeeds() -> None:
    engine, session_factory = await _database()
    try:
        now = datetime.now(timezone.utc)
        summary_date = now.date()
        window_from = now - timedelta(hours=24)

        async def _claim() -> bool:
            async with session_factory() as session:
                repo = SqlAlchemyActivityRepository(session)
                run = await repo.claim_daily_summary_run(
                    chat=_chat(),
                    summary_date=summary_date,
                    window_from=window_from,
                    window_to=now,
                    trigger="scheduled",
                    lease_seconds=1800,
                )
                await session.commit()
                return run is not None

        results = await asyncio.gather(_claim(), _claim())

        assert sorted(results) == [False, True]
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_claim_cannot_be_reclaimed_before_lease_expires() -> None:
    engine, session_factory = await _database()
    try:
        now = datetime.now(timezone.utc)
        summary_date = now.date()
        window_from = now - timedelta(hours=24)

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            first = await repo.claim_daily_summary_run(
                chat=_chat(),
                summary_date=summary_date,
                window_from=window_from,
                window_to=now,
                trigger="scheduled",
                lease_seconds=1800,
                now=now,
            )
            await session.commit()
        assert first is not None

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            second = await repo.claim_daily_summary_run(
                chat=_chat(),
                summary_date=summary_date,
                window_from=window_from,
                window_to=now,
                trigger="scheduled",
                lease_seconds=1800,
                now=now + timedelta(minutes=5),  # well within the 30-minute lease
            )
            await session.commit()

        assert second is None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dead_run_with_expired_lease_can_be_reclaimed() -> None:
    engine, session_factory = await _database()
    try:
        now = datetime.now(timezone.utc)
        summary_date = now.date()
        window_from = now - timedelta(hours=24)

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            first = await repo.claim_daily_summary_run(
                chat=_chat(),
                summary_date=summary_date,
                window_from=window_from,
                window_to=now,
                trigger="scheduled",
                lease_seconds=600,  # 10 minutes
                now=now,
            )
            await session.commit()
        assert first is not None

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            reclaimed = await repo.claim_daily_summary_run(
                chat=_chat(),
                summary_date=summary_date,
                window_from=window_from,
                window_to=now,
                trigger="scheduled",
                lease_seconds=600,
                now=now + timedelta(minutes=15),  # past the original lease
            )
            await session.commit()

        assert reclaimed is not None
        assert reclaimed.id == first.id
        assert reclaimed.status == "claimed"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_generated_run_is_never_reclaimed_for_regeneration() -> None:
    # a 'generated' run should only ever be resent, never regenerated from scratch --
    # claim_daily_summary_run must not touch it even long after its lease window.
    engine, session_factory = await _database()
    try:
        now = datetime.now(timezone.utc)
        summary_date = now.date()
        window_from = now - timedelta(hours=24)

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            first = await repo.claim_daily_summary_run(
                chat=_chat(),
                summary_date=summary_date,
                window_from=window_from,
                window_to=now,
                trigger="scheduled",
                lease_seconds=60,
                now=now,
            )
            assert first is not None
            await repo.finalize_daily_summary_run_generated(
                run_id=first.id,
                generated_text="Итоги дня: тестовая сводка",
                topics_json={"themes": []},
                pipeline_cost_usd=0.01,
                context_stt_cost_usd=0.0,
            )
            await session.commit()

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            reclaimed = await repo.claim_daily_summary_run(
                chat=_chat(),
                summary_date=summary_date,
                window_from=window_from,
                window_to=now,
                trigger="scheduled",
                lease_seconds=60,
                now=now + timedelta(hours=1),
            )
            await session.commit()

        assert reclaimed is None

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            stored = await repo.get_daily_summary_run(
                chat_id=_chat().telegram_chat_id,
                summary_date=summary_date,
                trigger="scheduled",
            )
        assert stored is not None
        assert stored.status == "generated"
        assert stored.generated_text == "Итоги дня: тестовая сводка"
    finally:
        await engine.dispose()
