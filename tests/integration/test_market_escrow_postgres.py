from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.use_cases.economy.catalog import inventory_stack_limit
from selara.application.use_cases.economy.market_buy_listing import (
    execute as buy_listing,
)
from selara.application.use_cases.economy.market_cancel_listing import (
    execute as cancel_listing,
)
from selara.application.use_cases.economy.market_create_listing import (
    execute as create_listing,
)
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import EconomyAccountModel, EconomyInventoryModel, EconomyMarketListingModel
from selara.infrastructure.db.repositories import SqlAlchemyEconomyRepository

EVENT_AT = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
EXPIRES_AT = EVENT_AT + timedelta(hours=24)
AFTER_EXPIRY = EVENT_AT + timedelta(hours=25)


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


class _ListingRaceRepository(SqlAlchemyEconomyRepository):
    def __init__(self, session, rendezvous: _Rendezvous) -> None:
        super().__init__(session)
        self._rendezvous = rendezvous

    async def get_market_listing(self, *, listing_id: int):
        result = await super().get_market_listing(listing_id=listing_id)
        await self._rendezvous.wait()
        return result


class _BuyerInventoryRaceRepository(SqlAlchemyEconomyRepository):
    def __init__(self, session, rendezvous: _Rendezvous) -> None:
        super().__init__(session)
        self._rendezvous = rendezvous

    async def list_inventory(self, *, account_id: int):
        result = await super().list_inventory(account_id=account_id)
        await self._rendezvous.wait()
        return result


class _AccountRaceRepository(SqlAlchemyEconomyRepository):
    def __init__(self, session, rendezvous: _Rendezvous) -> None:
        super().__init__(session)
        self._rendezvous = rendezvous

    async def get_or_create_account(self, *, scope, user_id: int):
        # Hold each account lock first, then wait, so both trades own their first account before either asks for its second.
        result = await super().get_or_create_account(scope=scope, user_id=user_id)
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


async def _seed_listing(
    session_factory,
    *,
    seller_user_id: int,
    item_code: str = "crop:radish",
    quantity: int = 10,
    event_at: datetime = EVENT_AT,
) -> tuple[int, int]:
    """Give the seller stock and list it; returns (listing_id, seller_account_id)."""
    async with session_factory() as session:
        repo = SqlAlchemyEconomyRepository(session)
        scope, _ = await repo.resolve_scope(mode="global", chat_id=None, user_id=seller_user_id)
        seller, _ = await repo.get_or_create_account(scope=scope, user_id=seller_user_id)
        await repo.add_inventory_item(account_id=seller.id, item_code=item_code, delta=quantity)
        created = await create_listing(
            repo,
            economy_mode="global",
            chat_id=None,
            user_id=seller_user_id,
            item_code=item_code,
            quantity=quantity,
            unit_price=100,
            market_fee_percent=0,
            event_at=event_at,
        )
        assert created.listing is not None
        await session.commit()
        return created.listing.id, seller.id


async def _fund(session_factory, *, user_id: int, amount: int) -> int:
    async with session_factory() as session:
        repo = SqlAlchemyEconomyRepository(session)
        scope, _ = await repo.resolve_scope(mode="global", chat_id=None, user_id=user_id)
        account, _ = await repo.get_or_create_account(scope=scope, user_id=user_id)
        await repo.add_balance(account_id=account.id, delta=amount)
        await session.commit()
        return account.id


async def _stock(session_factory, *, account_id: int, item_code: str) -> int:
    async with session_factory() as session:
        quantity = await session.scalar(
            select(EconomyInventoryModel.quantity).where(
                EconomyInventoryModel.account_id == account_id,
                EconomyInventoryModel.item_code == item_code,
            )
        )
    return int(quantity or 0)


async def _slot_count(session_factory, *, account_id: int) -> int:
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count(EconomyInventoryModel.item_code)).where(
                EconomyInventoryModel.account_id == account_id,
                EconomyInventoryModel.quantity > 0,
            )
        )
    return int(count or 0)


async def _balance(session_factory, *, account_id: int) -> int:
    async with session_factory() as session:
        balance = await session.scalar(select(EconomyAccountModel.balance).where(EconomyAccountModel.id == account_id))
    return int(balance or 0)


async def _listing_state(session_factory, *, listing_id: int) -> tuple[str, int]:
    async with session_factory() as session:
        row = await session.get(EconomyMarketListingModel, listing_id)
    assert row is not None
    return row.status, int(row.qty_left)


async def _buy(session_factory, *, buyer_user_id: int, listing_id: int, quantity: int, event_at: datetime):
    async with session_factory() as session:
        result = await buy_listing(
            SqlAlchemyEconomyRepository(session),
            economy_mode="global",
            chat_id=None,
            buyer_user_id=buyer_user_id,
            listing_id=listing_id,
            quantity=quantity,
            seller_tax_percent=0,
            event_at=event_at,
        )
        await session.commit()
        return result


async def _cancel(session_factory, *, seller_user_id: int, listing_id: int, event_at: datetime):
    async with session_factory() as session:
        result = await cancel_listing(
            SqlAlchemyEconomyRepository(session),
            economy_mode="global",
            chat_id=None,
            seller_user_id=seller_user_id,
            listing_id=listing_id,
            event_at=event_at,
        )
        await session.commit()
        return result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_buying_an_expired_listing_returns_unsold_units_to_seller() -> None:
    engine, session_factory = await _database()
    listing_id, seller_account_id = await _seed_listing(session_factory, seller_user_id=40)

    result = await _buy(session_factory, buyer_user_id=41, listing_id=listing_id, quantity=1, event_at=AFTER_EXPIRY)

    assert not result.accepted
    assert await _listing_state(session_factory, listing_id=listing_id) == ("expired", 0)
    assert await _stock(session_factory, account_id=seller_account_id, item_code="crop:radish") == 10
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cancelling_an_expired_listing_returns_unsold_units_exactly_once() -> None:
    engine, session_factory = await _database()
    listing_id, seller_account_id = await _seed_listing(session_factory, seller_user_id=40)

    first = await _cancel(session_factory, seller_user_id=40, listing_id=listing_id, event_at=AFTER_EXPIRY)
    second = await _cancel(session_factory, seller_user_id=40, listing_id=listing_id, event_at=AFTER_EXPIRY)

    assert not first.accepted and not second.accepted
    assert await _listing_state(session_factory, listing_id=listing_id) == ("expired", 0)
    assert await _stock(session_factory, account_id=seller_account_id, item_code="crop:radish") == 10
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_partially_bought_listing_returns_only_the_remaining_units_after_expiry() -> None:
    engine, session_factory = await _database()
    listing_id, seller_account_id = await _seed_listing(session_factory, seller_user_id=40)
    buyer_account_id = await _fund(session_factory, user_id=41, amount=1000)

    bought = await _buy(
        session_factory,
        buyer_user_id=41,
        listing_id=listing_id,
        quantity=4,
        event_at=EVENT_AT + timedelta(hours=1),
    )
    cancelled = await _cancel(session_factory, seller_user_id=40, listing_id=listing_id, event_at=AFTER_EXPIRY)

    assert bought.accepted
    assert not cancelled.accepted
    assert await _listing_state(session_factory, listing_id=listing_id) == ("expired", 0)
    assert await _stock(session_factory, account_id=buyer_account_id, item_code="crop:radish") == 4
    assert await _stock(session_factory, account_id=seller_account_id, item_code="crop:radish") == 6
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_listing_is_still_buyable_one_second_before_expiry() -> None:
    engine, session_factory = await _database()
    listing_id, _ = await _seed_listing(session_factory, seller_user_id=40)
    buyer_account_id = await _fund(session_factory, user_id=41, amount=1000)

    result = await _buy(
        session_factory,
        buyer_user_id=41,
        listing_id=listing_id,
        quantity=1,
        event_at=EXPIRES_AT - timedelta(seconds=1),
    )

    assert result.accepted
    assert await _stock(session_factory, account_id=buyer_account_id, item_code="crop:radish") == 1
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_buy_at_exact_expiry_is_refused_and_returns_units_to_seller() -> None:
    engine, session_factory = await _database()
    listing_id, seller_account_id = await _seed_listing(session_factory, seller_user_id=40)

    result = await _buy(session_factory, buyer_user_id=41, listing_id=listing_id, quantity=1, event_at=EXPIRES_AT)

    assert not result.accepted
    assert await _listing_state(session_factory, listing_id=listing_id) == ("expired", 0)
    assert await _stock(session_factory, account_id=seller_account_id, item_code="crop:radish") == 10
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_buy_and_cancel_racing_after_expiry_return_units_exactly_once() -> None:
    engine, session_factory = await _database()
    listing_id, seller_account_id = await _seed_listing(session_factory, seller_user_id=40)
    rendezvous = _Rendezvous()

    async def run_buy():
        async with session_factory() as session:
            result = await buy_listing(
                _ListingRaceRepository(session, rendezvous),
                economy_mode="global",
                chat_id=None,
                buyer_user_id=41,
                listing_id=listing_id,
                quantity=1,
                seller_tax_percent=0,
                event_at=AFTER_EXPIRY,
            )
            await session.commit()
            return result

    async def run_cancel():
        async with session_factory() as session:
            result = await cancel_listing(
                _ListingRaceRepository(session, rendezvous),
                economy_mode="global",
                chat_id=None,
                seller_user_id=40,
                listing_id=listing_id,
                event_at=AFTER_EXPIRY,
            )
            await session.commit()
            return result

    results = await asyncio.gather(run_buy(), run_cancel())

    assert not any(result.accepted for result in results)
    assert await _listing_state(session_factory, listing_id=listing_id) == ("expired", 0)
    assert await _stock(session_factory, account_id=seller_account_id, item_code="crop:radish") == 10
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parallel_buys_of_different_listings_cannot_exceed_buyer_slot_cap() -> None:
    engine, session_factory = await _database()
    first_listing, _ = await _seed_listing(session_factory, seller_user_id=40, item_code="crop:radish")
    second_listing, _ = await _seed_listing(session_factory, seller_user_id=40, item_code="crop:wheat")

    async with session_factory() as session:
        repo = SqlAlchemyEconomyRepository(session)
        scope, _ = await repo.resolve_scope(mode="global", chat_id=None, user_id=41)
        buyer, _ = await repo.get_or_create_account(scope=scope, user_id=41)
        await repo.add_balance(account_id=buyer.id, delta=1000)
        slots = inventory_stack_limit(buyer.storage_level)
        # Leave exactly one free slot: both purchases are for item codes the buyer does not hold yet.
        for index in range(slots - 1):
            await repo.add_inventory_item(account_id=buyer.id, item_code=f"item:filler-{index}", delta=1)
        await session.commit()
        buyer_account_id = buyer.id

    rendezvous = _Rendezvous()

    async def run_buy(listing_id: int):
        async with session_factory() as session:
            result = await buy_listing(
                _BuyerInventoryRaceRepository(session, rendezvous),
                economy_mode="global",
                chat_id=None,
                buyer_user_id=41,
                listing_id=listing_id,
                quantity=1,
                seller_tax_percent=0,
                event_at=EVENT_AT + timedelta(hours=1),
            )
            await session.commit()
            return result

    results = await asyncio.gather(run_buy(first_listing), run_buy(second_listing))

    assert sum(result.accepted for result in results) == 1
    assert await _slot_count(session_factory, account_id=buyer_account_id) <= slots
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parallel_buys_of_different_listings_cannot_overspend_buyer_balance() -> None:
    engine, session_factory = await _database()
    first_listing, _ = await _seed_listing(session_factory, seller_user_id=40, item_code="crop:radish")
    second_listing, _ = await _seed_listing(session_factory, seller_user_id=40, item_code="crop:wheat")
    buyer_account_id = await _fund(session_factory, user_id=41, amount=150)
    rendezvous = _Rendezvous()

    async def run_buy(listing_id: int):
        async with session_factory() as session:
            result = await buy_listing(
                _BuyerInventoryRaceRepository(session, rendezvous),
                economy_mode="global",
                chat_id=None,
                buyer_user_id=41,
                listing_id=listing_id,
                quantity=1,
                seller_tax_percent=0,
                event_at=EVENT_AT + timedelta(hours=1),
            )
            await session.commit()
            return result

    # Each listing costs 100 and the buyer holds 150: the second purchase must be refused, not raise.
    results = await asyncio.gather(run_buy(first_listing), run_buy(second_listing))

    assert sum(result.accepted for result in results) == 1
    assert await _balance(session_factory, account_id=buyer_account_id) == 50
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sweep_returns_unsold_units_of_expired_listings_without_a_buy_or_cancel() -> None:
    from selara.infrastructure.db.market_expiry_sweeper import sweep_due_market_listings

    engine, session_factory = await _database()
    long_ago = datetime.now(timezone.utc) - timedelta(days=2)
    listing_id, seller_account_id = await _seed_listing(session_factory, seller_user_id=40, event_at=long_ago)

    first = await sweep_due_market_listings(session_factory)
    second = await sweep_due_market_listings(session_factory)

    assert first == 10
    assert second == 0
    assert await _listing_state(session_factory, listing_id=listing_id) == ("expired", 0)
    assert await _stock(session_factory, account_id=seller_account_id, item_code="crop:radish") == 10
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sweep_leaves_listings_that_have_not_expired_alone() -> None:
    from selara.infrastructure.db.market_expiry_sweeper import sweep_due_market_listings

    engine, session_factory = await _database()
    listing_id, seller_account_id = await _seed_listing(
        session_factory, seller_user_id=40, event_at=datetime.now(timezone.utc)
    )

    returned = await sweep_due_market_listings(session_factory)

    assert returned == 0
    assert await _listing_state(session_factory, listing_id=listing_id) == ("open", 10)
    assert await _stock(session_factory, account_id=seller_account_id, item_code="crop:radish") == 0
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sweep_repays_legacy_expired_rows_that_still_hold_escrow_exactly_once() -> None:
    from selara.infrastructure.db.market_expiry_sweeper import sweep_due_market_listings

    engine, session_factory = await _database()
    # Shape left behind by the pre-fix code: status expired, escrow still counted in qty_left.
    listing_id, seller_account_id = await _seed_listing(session_factory, seller_user_id=40, quantity=7)
    async with session_factory() as session:
        row = await session.get(EconomyMarketListingModel, listing_id)
        assert row is not None
        row.status = "expired"
        await session.commit()

    first = await sweep_due_market_listings(session_factory)
    second = await sweep_due_market_listings(session_factory)

    assert first == 7
    assert second == 0
    assert await _listing_state(session_factory, listing_id=listing_id) == ("expired", 0)
    assert await _stock(session_factory, account_id=seller_account_id, item_code="crop:radish") == 7
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_opposite_trades_between_two_users_do_not_deadlock() -> None:
    engine, session_factory = await _database()
    radish_listing, _ = await _seed_listing(session_factory, seller_user_id=40, item_code="crop:radish")
    wheat_listing, _ = await _seed_listing(session_factory, seller_user_id=41, item_code="crop:wheat")
    account_40 = await _fund(session_factory, user_id=40, amount=1000)
    account_41 = await _fund(session_factory, user_id=41, amount=1000)
    rendezvous = _Rendezvous()

    async def run_buy(buyer_user_id: int, listing_id: int):
        async with session_factory() as session:
            result = await buy_listing(
                _AccountRaceRepository(session, rendezvous),
                economy_mode="global",
                chat_id=None,
                buyer_user_id=buyer_user_id,
                listing_id=listing_id,
                quantity=1,
                seller_tax_percent=0,
                event_at=EVENT_AT + timedelta(hours=1),
            )
            await session.commit()
            return result

    # 41 buys radish from 40 while 40 buys wheat from 41. Each buy locks its buyer's account
    # before the seller's, so the two trades can end up waiting on each other.
    results = await asyncio.gather(run_buy(41, radish_listing), run_buy(40, wheat_listing))

    assert all(result.accepted for result in results)
    assert await _stock(session_factory, account_id=account_41, item_code="crop:radish") == 1
    assert await _stock(session_factory, account_id=account_40, item_code="crop:wheat") == 1
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sweep_never_returns_units_of_a_cancelled_listing_again() -> None:
    from selara.infrastructure.db.market_expiry_sweeper import sweep_due_market_listings

    engine, session_factory = await _database()
    # Cancelling keeps qty_left on the row, so the sweep must key on status, not on qty_left alone.
    created_at = datetime.now(timezone.utc) - timedelta(days=2)
    listing_id, seller_account_id = await _seed_listing(session_factory, seller_user_id=40, event_at=created_at)
    cancelled = await _cancel(
        session_factory,
        seller_user_id=40,
        listing_id=listing_id,
        event_at=created_at + timedelta(hours=1),
    )

    returned = await sweep_due_market_listings(session_factory)

    assert cancelled.accepted
    assert returned == 0
    status, _ = await _listing_state(session_factory, listing_id=listing_id)
    assert status == "cancelled"
    assert await _stock(session_factory, account_id=seller_account_id, item_code="crop:radish") == 10
    await engine.dispose()
