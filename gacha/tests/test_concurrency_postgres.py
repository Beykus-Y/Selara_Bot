from __future__ import annotations

import asyncio
import os
import random
from datetime import UTC, datetime

import pytest
from gacha_service.application.service import PAID_PULL_PRICE, GachaService
from gacha_service.infrastructure.models import (
    Base,
    CurrencyGrantModel,
    PlayerBannerWalletModel,
    PlayerModel,
    PullHistoryModel,
)
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
    pulled_at = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)

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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sell_pull_after_acquiring_advisory_lock() -> None:
    database_url = os.getenv("TEST_GACHA_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_GACHA_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    pulled_at = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    sold_at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    async with session_factory() as session:
        session.add(
            PlayerModel(
                user_id=2002,
                username="seller",
                adventure_rank=19,
                adventure_xp=1170,
                total_points=2016400,
                total_primogems=1462,
                next_pull_at=None,
            )
        )
        session.add(
            PlayerBannerWalletModel(
                user_id=2002,
                banner="genshin",
                currency_balance=1462,
            )
        )
        history_entry = PullHistoryModel(
            user_id=2002,
            banner="genshin",
            character_code="noelle",
            character_name="Ноэлль (С6) дубликат",
            rarity="epic",
            points=5400,
            primogems=50,
            adventure_xp=16,
            image_url="/images/genshin/noelle.jpg",
            source="free",
            base_currency_price=25,
            purchase_price=0,
            sale_price=0,
            sold_at=None,
            pulled_at=pulled_at,
        )
        session.add(history_entry)
        await session.commit()
        pull_id = int(history_entry.id)

    async with session_factory() as session:
        result = await GachaService(GachaRepository(session)).sell_pull(
            user_id=2002,
            pull_id=pull_id,
            now=sold_at,
        )

    assert result.sale_price == 75
    assert result.banner == "genshin"
    assert result.player.total_primogems == 1537
    assert result.sold_at == sold_at

    async with session_factory() as session:
        stored_entry = await session.get(PullHistoryModel, pull_id)
        stored_wallet = await session.get(
            PlayerBannerWalletModel,
            {"user_id": 2002, "banner": "genshin"},
        )

    assert stored_entry is not None
    assert stored_entry.sale_price == 75
    assert stored_entry.sold_at == sold_at
    assert stored_wallet is not None
    assert stored_wallet.currency_balance == 1537
    await engine.dispose()


class _PaidPullRaceRepository(GachaRepository):
    def __init__(self, session, rendezvous: _Rendezvous) -> None:
        super().__init__(session)
        self._rendezvous = rendezvous

    async def get_card_copies(self, *, user_id: int, banner: str, card_code: str) -> int:
        result = await super().get_card_copies(user_id=user_id, banner=banner, card_code=card_code)
        await self._rendezvous.wait()
        return result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_paid_pull_cannot_be_double_spent_under_concurrency() -> None:
    """Regression test for a gap flagged in docs/GACHA_MODERNIZATION_TODO.md
    Этап 0: only the free-pull race had a concurrency test before this. The
    advisory lock (prepare_player_for_pull) is acquired and the currency
    balance re-read *before* this rendezvous point, so only one of two
    concurrent paid pulls sharing the same balance should succeed."""
    database_url = os.getenv("TEST_GACHA_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_GACHA_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        session.add(
            PlayerModel(
                user_id=3003,
                username="buyer",
                adventure_rank=1,
                adventure_xp=0,
                total_points=0,
                total_primogems=PAID_PULL_PRICE,
                next_pull_at=None,
            )
        )
        session.add(
            PlayerBannerWalletModel(user_id=3003, banner="genshin", currency_balance=PAID_PULL_PRICE)
        )
        await session.commit()

    rendezvous = _Rendezvous()
    pulled_at = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)

    async def run_purchase(seed: int):
        async with session_factory() as session:
            service = GachaService(_PaidPullRaceRepository(session, rendezvous), rng=random.Random(seed))
            try:
                return await service.pull_purchase(user_id=3003, username="buyer", banner="genshin", now=pulled_at)
            except ValueError as exc:
                return exc

    results = await asyncio.gather(run_purchase(1), run_purchase(2))

    ok_results = [r for r in results if not isinstance(r, Exception)]
    failed_results = [r for r in results if isinstance(r, ValueError)]
    assert len(ok_results) == 1
    assert len(failed_results) == 1
    assert "Недостаточно" in str(failed_results[0])

    async with session_factory() as session:
        wallet = await session.get(PlayerBannerWalletModel, {"user_id": 3003, "banner": "genshin"})
    assert wallet is not None
    assert wallet.currency_balance >= 0
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_double_sell_of_same_pull_is_rejected_under_concurrency() -> None:
    """Two concurrent sell attempts for the same pull_id must not both
    succeed — protected by the per-user-banner advisory lock plus
    with_for_update() + sold_at check in GachaRepository.sell_pull."""
    database_url = os.getenv("TEST_GACHA_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_GACHA_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    pulled_at = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    async with session_factory() as session:
        session.add(
            PlayerModel(
                user_id=4004,
                username="seller",
                adventure_rank=1,
                adventure_xp=0,
                total_points=0,
                total_primogems=100,
                next_pull_at=None,
            )
        )
        session.add(PlayerBannerWalletModel(user_id=4004, banner="genshin", currency_balance=100))
        history_entry = PullHistoryModel(
            user_id=4004,
            banner="genshin",
            character_code="noelle",
            character_name="Ноэлль дубликат",
            rarity="epic",
            points=100,
            primogems=10,
            adventure_xp=1,
            image_url="/images/genshin/noelle.jpg",
            source="free",
            base_currency_price=25,
            purchase_price=0,
            sale_price=0,
            sold_at=None,
            pulled_at=pulled_at,
        )
        session.add(history_entry)
        await session.commit()
        pull_id = int(history_entry.id)

    async def run_sell(seed: int):
        _ = seed
        async with session_factory() as session:
            try:
                return await GachaService(GachaRepository(session)).sell_pull(
                    user_id=4004, pull_id=pull_id, now=pulled_at
                )
            except ValueError as exc:
                return exc

    results = await asyncio.gather(run_sell(1), run_sell(2))

    ok_results = [r for r in results if not isinstance(r, Exception)]
    failed_results = [r for r in results if isinstance(r, ValueError)]
    assert len(ok_results) == 1
    assert len(failed_results) == 1
    assert "уже продана" in str(failed_results[0])
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_currency_grant_idempotency_key_is_deduplicated_under_concurrency() -> None:
    """Two concurrent admin currency grants sharing the same idempotency_key
    must apply the amount only once — protects buy_currency_with_coins'
    retry-on-timeout path from double-crediting (see
    docs/GACHA_MODERNIZATION_TODO.md, Этап 0)."""
    database_url = os.getenv("TEST_GACHA_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_GACHA_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    async def run_grant():
        async with session_factory() as session:
            return await GachaService(GachaRepository(session)).grant_currency(
                user_id=5005,
                username="buyer",
                banner="genshin",
                amount=160,
                idempotency_key="shared-topup-key",
            )

    results = await asyncio.gather(run_grant(), run_grant())

    assert {result.player.total_primogems for result in results} == {160}

    async with session_factory() as session:
        grant_count = await session.scalar(select(func.count(CurrencyGrantModel.idempotency_key)))
        wallet = await session.get(PlayerBannerWalletModel, {"user_id": 5005, "banner": "genshin"})

    assert int(grant_count or 0) == 1
    assert wallet is not None
    assert wallet.currency_balance == 160
    await engine.dispose()
