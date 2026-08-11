from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.use_cases.economy.claim_daily import execute as claim_daily
from selara.application.use_cases.economy.market_buy_listing import (
    execute as buy_listing,
)
from selara.application.use_cases.economy.market_create_listing import (
    execute as create_listing,
)
from selara.application.use_cases.economy.transfer_coins import (
    execute as transfer_coins,
)
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import (
    EconomyLedgerModel,
    EconomyMarketListingModel,
    EconomyMarketTradeModel,
)
from selara.infrastructure.db.repositories import SqlAlchemyEconomyRepository


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
            # With a correct DB lock the second transaction cannot reach this
            # hook until the first transaction commits.
            return


class _DailyRaceRepository(SqlAlchemyEconomyRepository):
    def __init__(self, session, rendezvous: _Rendezvous) -> None:
        super().__init__(session)
        self._rendezvous = rendezvous

    async def add_balance(self, *, account_id: int, delta: int) -> int:
        await self._rendezvous.wait()
        return await super().add_balance(account_id=account_id, delta=delta)


class _TransferRaceRepository(SqlAlchemyEconomyRepository):
    def __init__(self, session, rendezvous: _Rendezvous) -> None:
        super().__init__(session)
        self._rendezvous = rendezvous

    async def get_transfer_daily(self, *, account_id: int, limit_date):
        result = await super().get_transfer_daily(account_id=account_id, limit_date=limit_date)
        await self._rendezvous.wait()
        return result


class _MarketRaceRepository(SqlAlchemyEconomyRepository):
    def __init__(self, session, rendezvous: _Rendezvous) -> None:
        super().__init__(session)
        self._rendezvous = rendezvous

    async def get_market_listing(self, *, listing_id: int):
        result = await super().get_market_listing(listing_id=listing_id)
        await self._rendezvous.wait()
        return result


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_daily_claim_is_single_winner_under_concurrency() -> None:
    engine, session_factory = await _database()
    event_at = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        await SqlAlchemyEconomyRepository(session).get_or_create_account(
            scope=(await SqlAlchemyEconomyRepository(session).resolve_scope(mode="global", chat_id=None, user_id=10))[0],
            user_id=10,
        )
        await session.commit()

    rendezvous = _Rendezvous()

    async def run_claim():
        async with session_factory() as session:
            result = await claim_daily(
                _DailyRaceRepository(session, rendezvous),
                economy_mode="global",
                chat_id=None,
                user_id=10,
                daily_base_reward=120,
                daily_streak_cap=7,
                event_at=event_at,
            )
            await session.commit()
            return result

    results = await asyncio.gather(run_claim(), run_claim())

    async with session_factory() as session:
        ledger_count = await session.scalar(
            select(func.count(EconomyLedgerModel.id)).where(EconomyLedgerModel.reason == "daily")
        )
        scope, _ = await SqlAlchemyEconomyRepository(session).resolve_scope(mode="global", chat_id=None, user_id=10)
        account = await SqlAlchemyEconomyRepository(session).get_account(scope=scope, user_id=10)

    assert sum(result.accepted for result in results) == 1
    assert int(ledger_count or 0) == 1
    assert account is not None and account.balance == 120
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_transfer_cannot_spend_the_same_balance_twice() -> None:
    engine, session_factory = await _database()
    event_at = datetime(2026, 8, 3, 11, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyEconomyRepository(session)
        scope, _ = await repo.resolve_scope(mode="global", chat_id=None, user_id=20)
        sender, _ = await repo.get_or_create_account(scope=scope, user_id=20)
        await repo.get_or_create_account(scope=scope, user_id=21)
        await repo.get_or_create_account(scope=scope, user_id=22)
        await repo.add_balance(account_id=sender.id, delta=100)
        await session.commit()

    rendezvous = _Rendezvous()

    async def run_transfer(receiver_user_id: int):
        async with session_factory() as session:
            result = await transfer_coins(
                _TransferRaceRepository(session, rendezvous),
                economy_mode="global",
                chat_id=None,
                sender_user_id=20,
                receiver_user_id=receiver_user_id,
                amount=100,
                transfer_daily_limit=5000,
                transfer_tax_percent=0,
                event_at=event_at,
            )
            await session.commit()
            return result

    results = await asyncio.gather(run_transfer(21), run_transfer(22))

    async with session_factory() as session:
        repo = SqlAlchemyEconomyRepository(session)
        scope, _ = await repo.resolve_scope(mode="global", chat_id=None, user_id=20)
        accounts = [await repo.get_account(scope=scope, user_id=user_id) for user_id in (20, 21, 22)]

    assert sum(result.accepted for result in results) == 1
    assert accounts[0] is not None and accounts[0].balance == 0
    assert sum(account.balance for account in accounts if account is not None) == 100
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_last_market_item_has_only_one_buyer_under_concurrency() -> None:
    engine, session_factory = await _database()
    event_at = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyEconomyRepository(session)
        scope, _ = await repo.resolve_scope(mode="global", chat_id=None, user_id=30)
        seller, _ = await repo.get_or_create_account(scope=scope, user_id=30)
        buyer_one, _ = await repo.get_or_create_account(scope=scope, user_id=31)
        buyer_two, _ = await repo.get_or_create_account(scope=scope, user_id=32)
        await repo.add_inventory_item(account_id=seller.id, item_code="crop:radish", delta=1)
        await repo.add_balance(account_id=buyer_one.id, delta=100)
        await repo.add_balance(account_id=buyer_two.id, delta=100)
        created = await create_listing(
            repo,
            economy_mode="global",
            chat_id=None,
            user_id=30,
            item_code="crop:radish",
            quantity=1,
            unit_price=100,
            market_fee_percent=0,
            event_at=event_at,
        )
        assert created.listing is not None
        listing_id = created.listing.id
        await session.commit()

    rendezvous = _Rendezvous()

    async def run_buy(buyer_user_id: int):
        async with session_factory() as session:
            result = await buy_listing(
                _MarketRaceRepository(session, rendezvous),
                economy_mode="global",
                chat_id=None,
                buyer_user_id=buyer_user_id,
                listing_id=listing_id,
                quantity=1,
                seller_tax_percent=0,
                event_at=event_at,
            )
            await session.commit()
            return result

    results = await asyncio.gather(run_buy(31), run_buy(32))

    async with session_factory() as session:
        listing = await session.get(EconomyMarketListingModel, listing_id)
        trade_count = await session.scalar(
            select(func.count(EconomyMarketTradeModel.id)).where(EconomyMarketTradeModel.listing_id == listing_id)
        )

    assert sum(result.accepted for result in results) == 1
    assert listing is not None and listing.qty_left == 0 and listing.status == "closed"
    assert int(trade_count or 0) == 1
    await engine.dispose()
