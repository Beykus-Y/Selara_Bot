"""Adversarial concurrency test: karma vote daily-limit race.

Hypothesis:
  `vote_karma.execute` (src/selara/application/use_cases/vote_karma.py)
  enforces the per-chat daily vote limit with a classic check-then-act:
  it calls `repo.count_votes_by_voter_since(...)` (a plain SELECT count),
  compares against `daily_limit`, and only afterwards calls
  `repo.record_vote(...)` (a plain INSERT). There is no DB-level unique
  constraint or advisory lock keyed on (chat, voter, day) serializing this,
  unlike the economy tap/lottery paths which take a Postgres advisory
  transaction lock on the account before checking cooldowns (see
  test_adversarial_lottery_tap_race_postgres.py). Two concurrent votes from
  the same voter, arriving before either commits, should both read the same
  "0 votes used" count and both be accepted even though daily_limit is 1 --
  letting a user exceed their daily karma-vote allowance via concurrency.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.use_cases.vote_karma import execute as vote_karma
from selara.domain.entities import UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository


class _Rendezvous:
    def __init__(self, parties: int = 2) -> None:
        self._parties = parties
        self._arrived = 0
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()

    async def wait(self) -> None:
        async with self._lock:
            self._arrived += 1
            if self._arrived >= self._parties:
                self._ready.set()
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=0.4)
        except TimeoutError:
            return


class _RaceRepository(SqlAlchemyActivityRepository):
    def __init__(self, session, rendezvous: _Rendezvous) -> None:
        super().__init__(session)
        self._rendezvous = rendezvous

    async def count_votes_by_voter_since(self, *, chat_id: int, voter_user_id: int, since):
        result = await super().count_votes_by_voter_since(chat_id=chat_id, voter_user_id=voter_user_id, since=since)
        await self._rendezvous.wait()
        return result


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url, poolclass=None)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory


@pytest.mark.asyncio
async def test_concurrent_votes_exceed_daily_limit():
    engine, session_factory = await _database()
    try:
        chat_id = 990201
        voter = UserSnapshot(telegram_user_id=990301, username="voter", first_name="V", last_name=None, is_bot=False)
        target_a = UserSnapshot(telegram_user_id=990302, username="ta", first_name="A", last_name=None, is_bot=False)
        target_b = UserSnapshot(telegram_user_id=990303, username="tb", first_name="B", last_name=None, is_bot=False)

        now = datetime.now(timezone.utc)
        rendezvous = _Rendezvous(parties=2)

        async def vote(target: UserSnapshot):
            async with session_factory() as session:
                repo = _RaceRepository(session, rendezvous)
                result = await vote_karma(
                    repo,
                    chat_id=chat_id,
                    chat_type="group",
                    chat_title="Test chat",
                    voter=voter,
                    target=target,
                    vote_value=1,
                    event_at=now,
                    daily_limit=1,
                    days_for_7d=7,
                )
                await session.commit()
                return result

        results = await asyncio.gather(vote(target_a), vote(target_b))
        accepted_count = sum(1 for r in results if r.accepted)

        print(f"accepted_count={accepted_count} results={results!r}")

        assert accepted_count == 1, (
            f"daily_limit=1 but {accepted_count} concurrent votes were accepted "
            f"for the same voter in the same chat -- daily vote-limit bypass via race"
        )
    finally:
        await engine.dispose()
