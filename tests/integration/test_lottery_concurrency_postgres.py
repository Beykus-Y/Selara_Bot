from __future__ import annotations

import asyncio
import importlib
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.use_cases.economy.draw_lottery import execute as draw
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import EconomyAccountModel, EconomyInventoryModel, EconomyLedgerModel
from selara.infrastructure.db.repositories import SqlAlchemyEconomyRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("ticket", ["free", "item", "paid"])
@pytest.mark.parametrize("reward", ["coins", "item"])
@pytest.mark.parametrize("mode,chat_id", [("global", None), ("local", -100)])
async def test_lottery_admission_matches_exactly_the_consumed_tickets(monkeypatch, ticket, reward, mode, chat_id):
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    module = importlib.import_module("selara.application.use_cases.economy.draw_lottery")
    monkeypatch.setattr(module.random, "random", lambda: 0.0 if reward == "coins" else 0.80)
    monkeypatch.setattr(module.random, "randint", lambda low, high: low)
    monkeypatch.setattr(module.random, "choice", lambda items: items[0])
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 3, 10, tzinfo=timezone.utc)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            repo = SqlAlchemyEconomyRepository(session)
            scope, _ = await repo.resolve_scope(mode=mode, chat_id=chat_id, user_id=10)
            account, _ = await repo.get_or_create_account(scope=scope, user_id=10)
            account_id = account.id
            await repo.add_balance(account_id=account_id, delta=100000)
            # Yesterday's used count must reset, but today's admission must not race.
            stored = await session.get(EconomyAccountModel, account_id)
            stored.paid_lottery_used_on = (now - timedelta(days=1)).date()
            stored.paid_lottery_used_today = 100
            if ticket == "item":
                await repo.add_inventory_item(account_id=account_id, item_code="item:lottery_ticket", delta=1)
            await session.commit()

        class RaceRepository(SqlAlchemyEconomyRepository):
            async def add_balance(self, **kwargs):
                await asyncio.sleep(0.05)
                return await super().add_balance(**kwargs)

            async def add_inventory_item(self, **kwargs):
                await asyncio.sleep(0.05)
                return await super().add_inventory_item(**kwargs)

        async def invoke():
            async with sessions() as session:
                async with session.begin():
                    return await draw(RaceRepository(session), economy_mode=mode, chat_id=chat_id,
                                      user_id=10, ticket_type=ticket, lottery_ticket_price=150,
                                      lottery_paid_daily_limit=3, event_at=now)

        results = await asyncio.wait_for(asyncio.gather(*(invoke() for _ in range(6))), timeout=15)
        count = 3 if ticket == "paid" else 1
        assert sum(result.accepted for result in results) == count
        async with sessions() as session:
            stored = await session.get(EconomyAccountModel, account_id)
            ledger = (await session.execute(select(EconomyLedgerModel).where(
                EconomyLedgerModel.account_id == account_id,
            ))).scalars().all()
            inventory = (await session.execute(select(EconomyInventoryModel).where(
                EconomyInventoryModel.account_id == account_id,
            ))).scalars().all()
            quantities = {item.item_code: item.quantity for item in inventory}
            expected_coins = count * 80 if reward == "coins" else 0
            expected_charge = count * 150 if ticket == "paid" else 0
            assert stored.balance == 100000 - expected_charge + expected_coins
            charges = [entry for entry in ledger if entry.reason == "lottery_ticket_paid"]
            awards = [entry for entry in ledger if entry.reason == "lottery_coins"]
            assert len(charges) == (count if ticket == "paid" else 0)
            assert len(awards) == (count if reward == "coins" else 0)
            assert sum(entry.amount for entry in charges) == expected_charge
            assert sum(entry.amount for entry in awards) == expected_coins
            if ticket == "free":
                assert stored.free_lottery_claimed_on == now.date()
            if ticket == "item":
                assert quantities["item:lottery_ticket"] == 0
            if ticket == "paid":
                assert stored.paid_lottery_used_today == count
                assert stored.paid_lottery_used_on == now.date()
            assert quantities.get("item:fertilizer_fast", 0) == (count if reward == "item" else 0)
    finally:
        await engine.dispose()
