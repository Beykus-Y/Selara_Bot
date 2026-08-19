"""Adversarial concurrency test: LLM admin-action rollback double-fire.

Hypothesis (finding #35 in docs/STT_LLM_AUDIT_TODO.md):
  `LlmRepository.mark_rolled_back` used to be a plain read-then-write
  (`session.get()` followed by mutating the row and flushing) with no row
  lock and no atomic compare-and-swap. Two concurrent rollback clicks on the
  same action could both read `rolled_back_at is None`, both proceed, and
  both fire the underlying side effect -- dangerous specifically for
  `unwarn`/`unpred`, which are relative (clamped-at-zero) subtractions
  rather than idempotent absolute-state-sets, so a double-fire can silently
  erase an unrelated legitimate warning.

  The fix makes `mark_rolled_back` a single atomic
  `UPDATE ... WHERE id = :id AND rolled_back_at IS NULL` -- Postgres
  serializes concurrent UPDATEs on the same row, so only one of two
  concurrent callers can ever observe `rowcount == 1`.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure.db.base import Base
from selara.infrastructure.db.llm_repository import LlmRepository
from selara.infrastructure.db.models import LlmAdminActionModel


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


class _LegacyReadThenWriteLlmRepository(LlmRepository):
    """Reproduces the pre-fix shape of mark_rolled_back verbatim (a plain
    session.get() read, a Python-level None check, then a mutate+flush with
    no WHERE guard) with a rendezvous forced into the exact gap between the
    read and the write, so the race window opens deterministically instead
    of depending on incidental timing. Used only to characterize the
    vulnerability class this test guards against -- production code no
    longer has this shape."""

    def __init__(self, session, rendezvous: "_Rendezvous") -> None:
        super().__init__(session)
        self._rendezvous = rendezvous

    async def mark_rolled_back(self, *, action_id: int, rolled_back_by_user_id: int) -> bool:
        row = await self._session.get(LlmAdminActionModel, action_id)
        if row is None or row.rolled_back_at is not None:
            return False
        await self._rendezvous.wait()
        row.rolled_back_at = datetime.now(timezone.utc)
        row.rolled_back_by_user_id = rolled_back_by_user_id
        await self._session.flush()
        return True


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url, poolclass=None)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory


async def _seed_action(
    session_factory,
    *,
    chat_id: int,
    admin_user_id: int,
    target_user_id: int,
    rollback_actor_ids: tuple[int, int],
) -> int:
    async with session_factory() as setup_session:
        from selara.infrastructure.db.models import ChatModel, UserModel

        setup_session.add(UserModel(telegram_user_id=admin_user_id, username="admin", is_bot=False))
        setup_session.add(UserModel(telegram_user_id=target_user_id, username="target", is_bot=False))
        setup_session.add(UserModel(telegram_user_id=rollback_actor_ids[0], username="rollback_a", is_bot=False))
        setup_session.add(UserModel(telegram_user_id=rollback_actor_ids[1], username="rollback_b", is_bot=False))
        setup_session.add(ChatModel(telegram_chat_id=chat_id, type="supergroup", title="Test chat"))
        await setup_session.commit()

        repo = LlmRepository(setup_session)
        action = await repo.add_admin_action(
            chat_id=chat_id,
            admin_user_id=admin_user_id,
            tool_name="warn_user",
            action_description="Варн target",
            undo_payload={"tool": "unwarn", "target_user_id": target_user_id, "chat_id": chat_id},
        )
        action_id = action.id
        await setup_session.commit()
        return action_id


@pytest.mark.asyncio
async def test_legacy_read_then_write_shape_double_fires_under_forced_race():
    """Characterizes the pre-fix vulnerability: with the read and the write
    forced apart by a rendezvous, both concurrent clicks pass the None
    check and both claim the rollback."""
    engine, session_factory = await _database()
    try:
        action_id = await _seed_action(
            session_factory,
            chat_id=991201, admin_user_id=991301, target_user_id=991302,
            rollback_actor_ids=(991401, 991402),
        )
        rendezvous = _Rendezvous(parties=2)

        async def click(rollback_by: int):
            async with session_factory() as session:
                repo = _LegacyReadThenWriteLlmRepository(session, rendezvous)
                claimed = await repo.mark_rolled_back(action_id=action_id, rolled_back_by_user_id=rollback_by)
                await session.commit()
                return claimed

        results = await asyncio.gather(click(991401), click(991402))
        claimed_count = sum(1 for r in results if r)

        assert claimed_count == 2, (
            f"expected the legacy read-then-write shape to double-fire under a forced race, got "
            f"claimed_count={claimed_count} (results={results!r})"
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_rollback_clicks_only_one_wins():
    engine, session_factory = await _database()
    try:
        action_id = await _seed_action(
            session_factory,
            chat_id=991202, admin_user_id=991303, target_user_id=991304,
            rollback_actor_ids=(991403, 991404),
        )
        rendezvous = _Rendezvous(parties=2)

        async def click(rollback_by: int):
            async with session_factory() as session:
                repo = LlmRepository(session)
                await rendezvous.wait()
                claimed = await repo.mark_rolled_back(action_id=action_id, rolled_back_by_user_id=rollback_by)
                await session.commit()
                return claimed

        results = await asyncio.gather(click(991403), click(991404))
        claimed_count = sum(1 for r in results if r)

        assert claimed_count == 1, (
            f"expected exactly one concurrent rollback click to win the claim, got {claimed_count} "
            f"(results={results!r}) -- rollback double-fire, would double-execute a non-idempotent undo"
        )
    finally:
        await engine.dispose()
