from __future__ import annotations

import asyncio
import os
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.use_cases.economy.buy_shop_item import build_shop_offers, execute as buy
from selara.application.use_cases.economy.catalog import inventory_stack_limit
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import EconomyAccountModel, EconomyInventoryModel, EconomyLedgerModel
from selara.infrastructure.db.repositories import SqlAlchemyEconomyRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("scenario", ["upgrade", "last_stack"])
@pytest.mark.parametrize("mode,chat_id", [("global", None), ("local", -100)])
async def test_shop_serializes_level_and_stack_capacity_before_charging(scenario, mode, chat_id):
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    current_day = date(2026, 2, 14)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            repo = SqlAlchemyEconomyRepository(session)
            scope, _ = await repo.resolve_scope(mode=mode, chat_id=chat_id, user_id=10)
            account, _ = await repo.get_or_create_account(scope=scope, user_id=10)
            account_id = account.id
            await repo.add_balance(account_id=account_id, delta=100000)
            offers = build_shop_offers(scope=scope, user_id=10, current_day=current_day, account=account)
            if scenario == "upgrade":
                offer = next(offer for offer in offers if offer.offer_code == "upgrade_tap_glove_1")
                requested = [offer, offer]
            else:
                requested = [offer for offer in offers if offer.category != "upgrades"][:2]
                for index in range(inventory_stack_limit(0) - 1):
                    await repo.add_inventory_item(account_id=account_id, item_code=f"item:existing_{index}", delta=1)
            await session.commit()

        class RaceRepository(SqlAlchemyEconomyRepository):
            async def add_balance(self, *, account_id, delta):
                # Let the second transaction try to enter the account operation
                # while the first is between its validations and the debit.
                await asyncio.sleep(0.05)
                return await super().add_balance(account_id=account_id, delta=delta)

        async def invoke(offer):
            async with session_factory() as session:
                result = await buy(RaceRepository(session), economy_mode=mode, chat_id=chat_id,
                                   user_id=10, offer_code=offer.offer_code, current_day=current_day)
                await session.commit()
                return result

        results = await asyncio.wait_for(asyncio.gather(*(invoke(offer) for offer in requested)), timeout=10)
        assert sum(result.accepted for result in results) == 1
        winner = next(result for result in results if result.accepted)
        async with session_factory() as session:
            stored = await session.get(EconomyAccountModel, account_id)
            ledger = (await session.execute(select(EconomyLedgerModel).where(
                EconomyLedgerModel.account_id == account_id,
            ))).scalars().all()
            inventory = (await session.execute(select(EconomyInventoryModel).where(
                EconomyInventoryModel.account_id == account_id,
            ))).scalars().all()
            assert stored.balance == 100000 - winner.offer.price
            # Funding changes balance directly; only the purchase writes ledger.
            assert len(ledger) == 1
            assert ledger[0].amount == winner.offer.price
            assert ledger[0].reason == ("shop_upgrade" if scenario == "upgrade" else "shop_buy")
            if scenario == "upgrade":
                assert stored.tap_glove_level == 1
            else:
                assert len(inventory) == inventory_stack_limit(0)
                assert next(row for row in inventory if row.item_code == winner.offer.item_code).quantity == winner.offer.quantity
    finally:
        await engine.dispose()
