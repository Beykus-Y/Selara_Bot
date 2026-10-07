from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.use_cases.economy.auction_bid import execute as auction_bid
from selara.application.use_cases.economy.market_buy_listing import execute as buy_listing
from selara.application.use_cases.economy.market_create_listing import execute as create_listing
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.repositories import SqlAlchemyEconomyRepository

EVENT_AT = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
CHAT_ID = -100555
SELLER_USER_ID = 42
ITEM_CODE = "crop:radish"


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
            await asyncio.wait_for(self._ready.wait(), timeout=1.0)
        except TimeoutError:
            # With a correct lock order the other transaction cannot reach this hook
            # until this one commits, so stop waiting and let it run.
            return


class _BidRepository(SqlAlchemyEconomyRepository):
    def __init__(self, session, rendezvous: _Rendezvous) -> None:
        super().__init__(session)
        self._rendezvous = rendezvous

    async def get_or_create_account(self, *, scope, user_id: int):
        # Pause after each account the bid takes, so the trade can hold its first account
        # before the bid asks for the second.
        result = await super().get_or_create_account(scope=scope, user_id=user_id)
        await self._rendezvous.wait()
        return result


class _TradeRepository(SqlAlchemyEconomyRepository):
    def __init__(self, session, rendezvous: _Rendezvous) -> None:
        super().__init__(session)
        self._rendezvous = rendezvous

    async def lock_resources(self, *resource_keys: str) -> None:
        # Take the first lock in sorted order, pause so the bid can take its first account,
        # then take the rest. The sort matches the one in SqlAlchemyEconomyRepository.
        ordered = sorted(set(resource_keys))
        if len(ordered) < 2:
            await super().lock_resources(*ordered)
            return
        await super().lock_resources(ordered[0])
        await self._rendezvous.wait()
        await super().lock_resources(*ordered[1:])


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _user(user_id: int) -> UserSnapshot:
    return UserSnapshot(telegram_user_id=user_id, username=None, first_name=None, last_name=None, is_bot=False)


async def _seed_chat_and_users(session_factory) -> None:
    async with session_factory() as session:
        repo = SqlAlchemyEconomyRepository(session)
        chat = ChatSnapshot(telegram_chat_id=CHAT_ID, chat_type="supergroup", title="Auction chat")
        for user_id in (SELLER_USER_ID, 40, 41):
            await repo.ensure_chat_and_user(chat=chat, user=_user(user_id))
        await session.commit()


async def _seed_auction(session_factory, *, leader_user_id: int) -> int:
    async with session_factory() as session:
        repo = SqlAlchemyEconomyRepository(session)
        scope, _ = await repo.resolve_scope(mode="global", chat_id=None, user_id=SELLER_USER_ID)
        auction = await repo.create_chat_auction(
            chat_id=CHAT_ID,
            scope=scope,
            seller_user_id=SELLER_USER_ID,
            item_code=ITEM_CODE,
            quantity=1,
            start_price=50,
            min_increment=10,
            ends_at=EVENT_AT + timedelta(days=2),
            message_id=None,
        )
        await repo.update_chat_auction_bid(
            auction_id=auction.id,
            current_bid=100,
            highest_bid_user_id=leader_user_id,
        )
        await session.commit()
        return auction.id


async def _seed_listing(session_factory, *, seller_user_id: int) -> int:
    async with session_factory() as session:
        repo = SqlAlchemyEconomyRepository(session)
        scope, _ = await repo.resolve_scope(mode="global", chat_id=None, user_id=seller_user_id)
        seller, _ = await repo.get_or_create_account(scope=scope, user_id=seller_user_id)
        await repo.add_inventory_item(account_id=seller.id, item_code=ITEM_CODE, delta=10)
        created = await create_listing(
            repo,
            economy_mode="global",
            chat_id=None,
            user_id=seller_user_id,
            item_code=ITEM_CODE,
            quantity=10,
            unit_price=100,
            market_fee_percent=0,
            event_at=EVENT_AT,
        )
        assert created.listing is not None
        await session.commit()
        return created.listing.id


async def _fund(session_factory, *, user_id: int, amount: int) -> None:
    async with session_factory() as session:
        repo = SqlAlchemyEconomyRepository(session)
        scope, _ = await repo.resolve_scope(mode="global", chat_id=None, user_id=user_id)
        account, _ = await repo.get_or_create_account(scope=scope, user_id=user_id)
        await repo.add_balance(account_id=account.id, delta=amount)
        await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("bidder_user_id,leader_user_id", [(41, 40), (40, 41)])
async def test_auction_bid_and_trade_between_the_same_two_users_do_not_deadlock(
    bidder_user_id: int,
    leader_user_id: int,
) -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat_and_users(session_factory)
        auction_id = await _seed_auction(session_factory, leader_user_id=leader_user_id)
        # The bidder sells to the current leader, so the bid and the trade touch the same two accounts.
        listing_id = await _seed_listing(session_factory, seller_user_id=bidder_user_id)
        await _fund(session_factory, user_id=bidder_user_id, amount=1000)
        await _fund(session_factory, user_id=leader_user_id, amount=1000)
        rendezvous = _Rendezvous()

        async def run_bid():
            async with session_factory() as session:
                result = await auction_bid(
                    _BidRepository(session, rendezvous),
                    auction_id=auction_id,
                    bidder_user_id=bidder_user_id,
                    bid_amount=200,
                    event_at=EVENT_AT + timedelta(hours=1),
                )
                await session.commit()
                return result

        async def run_trade():
            async with session_factory() as session:
                result = await buy_listing(
                    _TradeRepository(session, rendezvous),
                    economy_mode="global",
                    chat_id=None,
                    buyer_user_id=leader_user_id,
                    listing_id=listing_id,
                    quantity=1,
                    seller_tax_percent=0,
                    event_at=EVENT_AT + timedelta(hours=1),
                )
                await session.commit()
                return result

        bid, trade = await asyncio.wait_for(asyncio.gather(run_bid(), run_trade()), timeout=20)

        assert bid.accepted
        assert trade.accepted
        assert bid.auction is not None
        assert bid.auction.highest_bid_user_id == bidder_user_id
    finally:
        await engine.dispose()
