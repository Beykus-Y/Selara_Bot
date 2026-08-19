"""Adversarial concurrency test: relationship/marriage proposal accept race.

Hypothesis:
  `SqlAlchemyActivityRepository._accept_marriage_proposal` (called from
  `respond_relationship_proposal` -> `respond_marriage_proposal`, which the
  `relationships.py` handler calls directly with no advisory lock or
  SELECT-FOR-UPDATE) enforces "at most one active marriage per user in a
  chat" purely via a plain SELECT-then-INSERT check
  (`get_active_marriage`). There is no DB-level uniqueness spanning a
  single user across different partners -- the unique index
  `uq_marriages_chat_pair` is keyed on (chat_id, user_low_id, user_high_id),
  i.e. per *pair*, not per user. So if user X has two independent pending
  marriage proposals in the same chat (one from Y, one from Z -- allowed,
  since `create_marriage_proposal` only blocks duplicate proposals for the
  *same* pair) and X accepts both concurrently, both concurrent
  transactions can pass the "not already married" check before either
  commits, producing two simultaneous active marriage rows for X in the
  same chat (bigamy).

This test races two concurrent `respond_marriage_proposal` calls (accepting
proposal A from Y, and proposal B from Z) for the same target user X in the
same chat, using a rendezvous to force both to observe the pre-write state.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import MarriageModel
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
    """Injects a rendezvous right after each 'is target already married?'
    read inside _accept_marriage_proposal, mirroring the TOCTOU window a
    real concurrent /marry accept in two different chat updates would hit
    (each update runs in its own DB session/transaction)."""

    def __init__(self, session, rendezvous: _Rendezvous) -> None:
        super().__init__(session)
        self._rendezvous = rendezvous

    async def get_active_marriage(self, *, user_id: int, chat_id: int | None = None):
        result = await super().get_active_marriage(user_id=user_id, chat_id=chat_id)
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
async def test_concurrent_marriage_accepts_cause_bigamy_in_same_chat():
    engine, session_factory = await _database()
    try:
        chat = ChatSnapshot(telegram_chat_id=990001, chat_type="group", title="Test chat")
        user_x = UserSnapshot(telegram_user_id=990101, username="x", first_name="X", last_name=None, is_bot=False)
        user_y = UserSnapshot(telegram_user_id=990102, username="y", first_name="Y", last_name=None, is_bot=False)
        user_z = UserSnapshot(telegram_user_id=990103, username="z", first_name="Z", last_name=None, is_bot=False)

        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=10)

        # Set up two independent pending marriage proposals targeting X:
        # Y -> X, and Z -> X. create_marriage_proposal only blocks a second
        # proposal for the *same pair*, so both are allowed to coexist.
        async with session_factory() as setup_session:
            setup_repo = SqlAlchemyActivityRepository(setup_session)
            proposal_a, err_a = await setup_repo.create_marriage_proposal(
                chat=chat, proposer=user_y, target=user_x, kind="marriage",
                expires_at=expires, event_at=now,
            )
            assert err_a is None, err_a
            proposal_b, err_b = await setup_repo.create_marriage_proposal(
                chat=chat, proposer=user_z, target=user_x, kind="marriage",
                expires_at=expires, event_at=now,
            )
            assert err_b is None, err_b
            await setup_session.commit()

        rendezvous = _Rendezvous(parties=2)

        async def accept(proposal_id: int):
            async with session_factory() as session:
                repo = _RaceRepository(session, rendezvous)
                proposal, marriage, error = await repo.respond_marriage_proposal(
                    proposal_id=proposal_id,
                    actor_user_id=user_x.telegram_user_id,
                    accept=True,
                    event_at=now,
                )
                await session.commit()
                return proposal, marriage, error

        results = await asyncio.gather(
            accept(proposal_a.id),
            accept(proposal_b.id),
        )

        errors = [error for _, _, error in results]
        successes = [r for r in results if r[2] is None]

        async with session_factory() as verify_session:
            rows = (
                await verify_session.execute(
                    select(MarriageModel).where(
                        MarriageModel.chat_id == chat.telegram_chat_id,
                        MarriageModel.is_active.is_(True),
                    )
                )
            ).scalars().all()

        active_marriages_for_x = [
            row for row in rows
            if user_x.telegram_user_id in (row.user_low_id, row.user_high_id)
        ]

        print(f"errors={errors!r} active_marriages_for_x={len(active_marriages_for_x)}")

        # Correct behavior would be: exactly one accept succeeds, the other
        # is rejected with "already married". If BOTH succeed, X ends up
        # bigamously married to both Y and Z in the same chat at once --
        # that's the bug this test proves.
        assert len(successes) == 1, (
            f"expected exactly one accept to succeed (no bigamy), got "
            f"{len(successes)} successes; errors={errors!r}; "
            f"active marriages for X in chat: {len(active_marriages_for_x)}"
        )
        assert len(active_marriages_for_x) == 1
    finally:
        await engine.dispose()
