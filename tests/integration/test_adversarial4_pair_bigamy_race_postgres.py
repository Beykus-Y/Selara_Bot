"""Adversarial concurrency test: /pair proposal accept race ("pair-bigamy").

Hypothesis:
  Round 3 of this review found and fixed marriage-bigamy in
  `SqlAlchemyActivityRepository._accept_marriage_proposal` by adding a
  pg_advisory_xact_lock on both participants before the "not already
  married" check (commit 5f21bc1). That fix touched
  `_accept_marriage_proposal` only. Its sibling function,
  `_accept_pair_proposal` (also in src/selara/infrastructure/db/repositories.py,
  reached via the exact same `respond_relationship_proposal` dispatch used by
  the `rel:accept:<id>` callback in relationships.py), has the IDENTICAL
  check-then-act shape and was NOT touched by the fix:

      proposer_pair = await self.get_active_pair(user_id=proposer_id, ...)
      if proposer_pair is not None: return None, "..."
      target_pair = await self.get_active_pair(user_id=target_id, ...)
      if target_pair is not None: return None, "..."
      # ... INSERT PairModel ...

  `create_marriage_proposal` only blocks a duplicate *pending proposal for
  the same pair* -- it does not stop user X from holding two independent
  pending "pair" proposals in the same chat (one from Y, one from Z). If X
  accepts both concurrently (e.g. taps both inline "Accept" buttons in quick
  succession, or two bot instances/webhook retries process both updates at
  once), both transactions can pass the "not already paired" check for X
  before either commits, producing two simultaneous active `PairModel` rows
  for X in the same chat -- the same class of bug as the marriage-bigamy fix
  addressed, just for /pair instead of /marry, and left unfixed.

This mirrors test_adversarial2_marriage_bigamy_race_postgres.py exactly,
substituting kind="pair" and hooking `get_active_pair` instead of
`get_active_marriage`.
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
from selara.infrastructure.db.models import PairModel
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
    """Injects a rendezvous right after each 'is X already paired?' read
    inside _accept_pair_proposal, mirroring the TOCTOU window a real
    concurrent /pair accept in two different chat updates would hit (each
    update runs in its own DB session/transaction)."""

    def __init__(self, session, rendezvous: _Rendezvous) -> None:
        super().__init__(session)
        self._rendezvous = rendezvous

    async def get_active_pair(self, *, user_id: int, chat_id: int | None = None):
        result = await super().get_active_pair(user_id=user_id, chat_id=chat_id)
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
async def test_concurrent_pair_accepts_cause_pair_bigamy_in_same_chat():
    engine, session_factory = await _database()
    try:
        chat = ChatSnapshot(telegram_chat_id=990201, chat_type="group", title="Test chat")
        user_x = UserSnapshot(telegram_user_id=990401, username="x", first_name="X", last_name=None, is_bot=False)
        user_y = UserSnapshot(telegram_user_id=990402, username="y", first_name="Y", last_name=None, is_bot=False)
        user_z = UserSnapshot(telegram_user_id=990403, username="z", first_name="Z", last_name=None, is_bot=False)

        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=10)

        # Two independent pending "pair" proposals targeting X: Y -> X and
        # Z -> X. create_marriage_proposal (used for both kinds) only blocks
        # a second proposal for the *same pair*, so both are allowed to
        # coexist.
        async with session_factory() as setup_session:
            setup_repo = SqlAlchemyActivityRepository(setup_session)
            proposal_a, err_a = await setup_repo.create_marriage_proposal(
                chat=chat, proposer=user_y, target=user_x, kind="pair",
                expires_at=expires, event_at=now,
            )
            assert err_a is None, err_a
            proposal_b, err_b = await setup_repo.create_marriage_proposal(
                chat=chat, proposer=user_z, target=user_x, kind="pair",
                expires_at=expires, event_at=now,
            )
            assert err_b is None, err_b
            await setup_session.commit()

        rendezvous = _Rendezvous(parties=2)

        async def accept(proposal_id: int):
            async with session_factory() as session:
                repo = _RaceRepository(session, rendezvous)
                proposal, relationship, error = await repo.respond_relationship_proposal(
                    proposal_id=proposal_id,
                    actor_user_id=user_x.telegram_user_id,
                    accept=True,
                    event_at=now,
                )
                await session.commit()
                return proposal, relationship, error

        results = await asyncio.gather(
            accept(proposal_a.id),
            accept(proposal_b.id),
        )

        errors = [error for _, _, error in results]
        successes = [r for r in results if r[2] is None]

        async with session_factory() as verify_session:
            rows = (
                await verify_session.execute(
                    select(PairModel).where(PairModel.chat_id == chat.telegram_chat_id)
                )
            ).scalars().all()

        active_pairs_for_x = [
            row for row in rows
            if user_x.telegram_user_id in (row.user_low_id, row.user_high_id)
        ]

        print(f"errors={errors!r} active_pairs_for_x={len(active_pairs_for_x)}")

        # Correct behavior: exactly one accept succeeds, the other is
        # rejected with "already in a relationship". If BOTH succeed, X ends
        # up simultaneously paired with both Y and Z in the same chat --
        # that's the bug this test proves (sibling of the marriage-bigamy
        # bug fixed in commit 5f21bc1, but for /pair, left unfixed).
        assert len(successes) == 1, (
            f"expected exactly one /pair accept to succeed (no pair-bigamy), got "
            f"{len(successes)} successes; errors={errors!r}; "
            f"active pairs for X in chat: {len(active_pairs_for_x)}"
        )
        assert len(active_pairs_for_x) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_non_racing_pair_accepts_in_same_chat_both_succeed():
    """Companion legitimate-use test: the advisory lock added to
    _accept_pair_proposal must only serialize genuinely conflicting accepts
    (same participant), not block unrelated pairs from forming concurrently
    in the same chat, and a user with no existing marriage/pair must still
    be able to accept a pending pair proposal."""
    engine, session_factory = await _database()
    try:
        chat = ChatSnapshot(telegram_chat_id=990202, chat_type="group", title="Test chat")
        user_a = UserSnapshot(telegram_user_id=990501, username="a", first_name="A", last_name=None, is_bot=False)
        user_b = UserSnapshot(telegram_user_id=990502, username="b", first_name="B", last_name=None, is_bot=False)
        user_c = UserSnapshot(telegram_user_id=990503, username="c", first_name="C", last_name=None, is_bot=False)
        user_d = UserSnapshot(telegram_user_id=990504, username="d", first_name="D", last_name=None, is_bot=False)

        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=10)

        # Two entirely independent pairs proposing in the same chat: A -> B
        # and C -> D. No participant overlaps, so both should succeed even
        # when accepted concurrently.
        async with session_factory() as setup_session:
            setup_repo = SqlAlchemyActivityRepository(setup_session)
            proposal_ab, err_ab = await setup_repo.create_marriage_proposal(
                chat=chat, proposer=user_a, target=user_b, kind="pair",
                expires_at=expires, event_at=now,
            )
            assert err_ab is None, err_ab
            proposal_cd, err_cd = await setup_repo.create_marriage_proposal(
                chat=chat, proposer=user_c, target=user_d, kind="pair",
                expires_at=expires, event_at=now,
            )
            assert err_cd is None, err_cd
            await setup_session.commit()

        async def accept(proposal_id: int, actor_user_id: int):
            async with session_factory() as session:
                repo = SqlAlchemyActivityRepository(session)
                proposal, relationship, error = await repo.respond_relationship_proposal(
                    proposal_id=proposal_id,
                    actor_user_id=actor_user_id,
                    accept=True,
                    event_at=now,
                )
                await session.commit()
                return proposal, relationship, error

        results = await asyncio.gather(
            accept(proposal_ab.id, user_b.telegram_user_id),
            accept(proposal_cd.id, user_d.telegram_user_id),
        )

        errors = [error for _, _, error in results]
        assert errors == [None, None], f"expected both non-conflicting pairs to succeed, got errors={errors!r}"

        async with session_factory() as verify_session:
            rows = (
                await verify_session.execute(
                    select(PairModel).where(PairModel.chat_id == chat.telegram_chat_id)
                )
            ).scalars().all()

        assert len(rows) == 2, f"expected both independent pairs to be recorded, got {len(rows)}"
    finally:
        await engine.dispose()
