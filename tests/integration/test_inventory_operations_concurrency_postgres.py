from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.use_cases.economy.catalog import RECIPES
from selara.application.use_cases.economy.craft import execute as craft
from selara.application.use_cases.economy.market_create_listing import execute as sell
from selara.application.use_cases.economy.use_item import execute as use_item
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import EconomyAccountModel, EconomyInventoryModel
from selara.infrastructure.db.repositories import SqlAlchemyEconomyRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("scenario", ["craft", "use", "craft_vs_market", "rollback"])
@pytest.mark.parametrize("mode,chat_id", [("global", None), ("local", -100)])
async def test_inventory_operation_is_atomic(scenario, mode, chat_id):
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 3, 10, tzinfo=timezone.utc)
    recipe = RECIPES["pizza"]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            repo = SqlAlchemyEconomyRepository(session)
            scope, _ = await repo.resolve_scope(mode=mode, chat_id=chat_id, user_id=10)
            account, _ = await repo.get_or_create_account(scope=scope, user_id=10)
            account_id = account.id
            initial = dict(recipe.ingredients) if scenario != "use" else {"item:growth_gel": 1}
            for code, quantity in initial.items():
                await repo.add_inventory_item(account_id=account_id, item_code=code, delta=quantity)
            await session.commit()

        class RaceRepository(SqlAlchemyEconomyRepository):
            async def add_inventory_item(self, *, account_id, item_code, delta):
                # Hold the logical operation between validation and mutation.
                await asyncio.sleep(0.05)
                if scenario == "rollback" and item_code == recipe.result_item_code:
                    raise RuntimeError("injected output failure")
                return await super().add_inventory_item(account_id=account_id, item_code=item_code, delta=delta)

        async def invoke(action):
            async with sessions() as session:
                async with session.begin():
                    repo = RaceRepository(session)
                    kwargs = dict(economy_mode=mode, chat_id=chat_id, user_id=10)
                    if action == "use":
                        return await use_item(repo, item_code="growth_gel", plot_no=None, event_at=now, **kwargs)
                    if action == "sell":
                        code, quantity = recipe.ingredients[0]
                        return await sell(repo, item_code=code, quantity=quantity, unit_price=10,
                                          market_fee_percent=0, event_at=now, **kwargs)
                    return await craft(repo, recipe_code=recipe.code, **kwargs)

        if scenario == "rollback":
            with pytest.raises(RuntimeError, match="injected output failure"):
                await invoke("craft")
        else:
            actions = ["use", "use"] if scenario == "use" else ["craft", "sell" if scenario == "craft_vs_market" else "craft"]
            results = await asyncio.wait_for(asyncio.gather(*(invoke(action) for action in actions)), timeout=10)
            assert sum(result.accepted for result in results) == 1

        async with sessions() as session:
            rows = (await session.execute(select(EconomyInventoryModel).where(
                EconomyInventoryModel.account_id == account_id,
            ))).scalars().all()
            stored = {row.item_code: row.quantity for row in rows if row.quantity > 0}
            account = await session.get(EconomyAccountModel, account_id)
            if scenario == "rollback":
                assert stored == initial
            elif scenario == "use":
                assert stored == {}
                assert account.growth_boost_pct == 40
            elif scenario == "craft":
                assert stored == {recipe.result_item_code: recipe.result_quantity}
            elif results[0].accepted:
                assert stored == {recipe.result_item_code: recipe.result_quantity}
            else:
                assert stored == {recipe.ingredients[1][0]: recipe.ingredients[1][1]}
    finally:
        await engine.dispose()
