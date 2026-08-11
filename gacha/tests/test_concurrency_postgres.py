from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime, timezone

import pytest
from gacha_service.application.service import GachaService
from gacha_service.infrastructure.models import Base, PullHistoryModel
from gacha_service.infrastructure.repository import GachaRepository
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class _Rendezvous:
    def __init__(self) -> None:
        self._arrived = 0
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()

    async def wait(self) -> None:
        async with self._lock:
            self._arrived += 1
            if self._arrived >= 2:
                self._ready.set()
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=0.4)
        except TimeoutError:
            return


class _PullRaceRepository(GachaRepository):
    def __init__(self, session, rendezvous: _Rendezvous) -> None:
        super().__init__(session)
        self._rendezvous = rendezvous

    async def get_banner_cooldown(self, *, user_id: int, banner: str):
        result = await super().get_banner_cooldown(user_id=user_id, banner=banner)
        await self._rendezvous.wait()
        return result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_free_pull_has_single_winner_under_concurrency() -> None:
    database_url = os.getenv("TEST_GACHA_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_GACHA_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    rendezvous = _Rendezvous()
    pulled_at = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)

    async def run_pull(seed: int):
        async with session_factory() as session:
            return await GachaService(
                _PullRaceRepository(session, rendezvous),
                default_cooldown_seconds=3600,
                rng=random.Random(seed),
            ).pull(user_id=1001, username="user", banner="genshin", now=pulled_at)

    results = await asyncio.gather(run_pull(1), run_pull(2))

    async with session_factory() as session:
        history_count = await session.scalar(select(func.count(PullHistoryModel.id)))

    assert sorted(result.status for result in results) == ["cooldown", "ok"]
    assert int(history_count or 0) == 1
    await engine.dispose()
