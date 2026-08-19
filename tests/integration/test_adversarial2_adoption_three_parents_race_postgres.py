"""Adversarial concurrency test: family-tree "max 2 parents" race.

Hypothesis:
  `SqlAlchemyActivityRepository.validate_parent_link` (called from the
  family-request accept flow in
  src/selara/presentation/handlers/chat_assistant.py::family_request_callback,
  via `upsert_graph_relationship`) enforces "a child can have at most 2
  parents" by running:

      SELECT ... WHERE relation_type='parent' AND user_b=<child> FOR UPDATE

  and rejecting if 2+ rows already exist. `FOR UPDATE` only locks *existing*
  rows -- when a child currently has 0 parents, there is nothing to lock, so
  it provides no serialization there. If three different users concurrently
  send /adopt requests for the same target (each in an independent
  request/session, as real concurrent Telegram callback_query updates would
  be), all three can observe "0 existing parents" before any of them
  commits their INSERT, and all three succeed -- giving the child 3 parents
  instead of the intended maximum of 2. There's also no DB-level check
  constraint capping the number of 'parent' edges per user_b.

This test races three concurrent `validate_parent_link` + insert flows
(mirroring `upsert_graph_relationship`) targeting the same child with no
pre-existing parents.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import RelationshipGraphModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository


class _Rendezvous:
    def __init__(self, parties: int) -> None:
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

    async def validate_parent_link(self, *, chat_id: int, actor_user_id: int, target_user_id: int):
        result = await super().validate_parent_link(
            chat_id=chat_id, actor_user_id=actor_user_id, target_user_id=target_user_id
        )
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
async def test_concurrent_adoptions_can_exceed_two_parents():
    engine, session_factory = await _database()
    try:
        chat = ChatSnapshot(telegram_chat_id=990401, chat_type="group", title="Test chat")
        child = UserSnapshot(telegram_user_id=990501, username="child", first_name="C", last_name=None, is_bot=False)
        parents = [
            UserSnapshot(telegram_user_id=990502 + i, username=f"p{i}", first_name=f"P{i}", last_name=None, is_bot=False)
            for i in range(3)
        ]

        rendezvous = _Rendezvous(parties=3)

        async def adopt(parent: UserSnapshot):
            async with session_factory() as session:
                repo = _RaceRepository(session, rendezvous)
                try:
                    relation = await repo.upsert_graph_relationship(
                        chat=chat,
                        user_a=parent,
                        user_b=child,
                        relation_type="parent",
                        actor_user_id=parent.telegram_user_id,
                    )
                    await session.commit()
                    return relation
                except ValueError as exc:
                    await session.rollback()
                    return exc

        results = await asyncio.gather(*(adopt(p) for p in parents))
        successes = [r for r in results if not isinstance(r, ValueError)]

        async with session_factory() as verify_session:
            rows = (
                await verify_session.execute(
                    select(RelationshipGraphModel).where(
                        RelationshipGraphModel.chat_id == chat.telegram_chat_id,
                        RelationshipGraphModel.relation_type == "parent",
                        RelationshipGraphModel.user_b == child.telegram_user_id,
                    )
                )
            ).scalars().all()

        print(f"successes={len(successes)} parent_rows_in_db={len(rows)} results={results!r}")

        assert len(rows) <= 2, (
            f"expected at most 2 parents for the child, but {len(rows)} parent "
            f"edges were created via concurrent adoption requests -- "
            f"max-2-parents invariant bypassed via race"
        )
    finally:
        await engine.dispose()
