from __future__ import annotations

import asyncio
import importlib
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.use_cases.economy.catalog import get_crop, get_plot_slots
from selara.application.use_cases.economy.harvest import execute as harvest
from selara.application.use_cases.economy.harvest_all_ready import execute as harvest_all
from selara.application.use_cases.economy.plant_crop import execute as plant
from selara.application.use_cases.economy.plant_all_last_crop import execute as plant_all
from selara.application.use_cases.economy.use_item import execute as use_item
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import (
    EconomyAccountModel, EconomyFarmModel, EconomyInventoryModel, EconomyLedgerModel, EconomyPlotModel,
)
from selara.infrastructure.db.repositories import SqlAlchemyEconomyRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("scenario", ["harvest", "harvest_all", "plant", "plant_all", "plant_vs_harvest", "use_vs_harvest"])
@pytest.mark.parametrize("mode,chat_id", [("global", None), ("local", -100)])
async def test_farm_mutations_serialize_whole_operation(monkeypatch, scenario, mode, chat_id):
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    harvest_module = importlib.import_module("selara.application.use_cases.economy.harvest")
    monkeypatch.setattr(harvest_module.random, "randint", lambda low, high: low)
    monkeypatch.setattr(harvest_module.random, "random", lambda: 0.99)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 3, 10, tzinfo=timezone.utc)
    crop = get_crop("radish")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            repo = SqlAlchemyEconomyRepository(session)
            scope, _ = await repo.resolve_scope(mode=mode, chat_id=chat_id, user_id=10)
            account, farm = await repo.get_or_create_account(scope=scope, user_id=10)
            account_id = account.id
            slots = get_plot_slots(farm.farm_level)
            await repo.add_balance(account_id=account_id, delta=100000)
            if "harvest" in scenario:
                for plot_no in range(1, 3 if scenario == "harvest_all" else 2):
                    await repo.upsert_plot(account_id=account_id, plot_no=plot_no, crop_code=crop.code,
                                           planted_at=now - timedelta(hours=2),
                                           ready_at=now + timedelta(minutes=30) if scenario == "use_vs_harvest" else now - timedelta(minutes=1),
                                           yield_boost_pct=0, shield_active=False)
                await repo.set_negative_event_streak(account_id=account_id, value=2)
            if scenario == "use_vs_harvest":
                await repo.add_inventory_item(account_id=account_id, item_code="item:fertilizer_rich", delta=1)
            await session.commit()

        class RaceRepository(SqlAlchemyEconomyRepository):
            async def upsert_plot(self, **kwargs):
                # Yield between the validated snapshot and the plot mutation.
                await asyncio.sleep(0.05)
                return await super().upsert_plot(**kwargs)

        async def invoke(action):
            async with sessions() as session:
                async with session.begin():
                    repo = RaceRepository(session)
                    kwargs = dict(economy_mode=mode, chat_id=chat_id, user_id=10, event_at=now)
                    if action == "plant":
                        return await plant(repo, crop_code=crop.code, plot_no=1, **kwargs)
                    if action == "plant_all":
                        return await plant_all(repo, crop_code=crop.code, **kwargs)
                    if action == "use":
                        return await use_item(repo, item_code="fertilizer_rich", plot_no=1, **kwargs)
                    if scenario == "use_vs_harvest":
                        kwargs["event_at"] = now + timedelta(hours=1)
                    kwargs.update(negative_event_chance_percent=0, negative_event_loss_percent=0)
                    if action == "harvest_all":
                        return await harvest_all(repo, **kwargs)
                    return await harvest(repo, plot_no=1, **kwargs)

        actions = {
            "harvest": ("harvest", "harvest"),
            "harvest_all": ("harvest_all", "harvest"),
            "plant": ("plant", "plant"),
            "plant_all": ("plant_all", "plant"),
            "plant_vs_harvest": ("plant", "harvest"),
            "use_vs_harvest": ("use", "harvest"),
        }[scenario]
        results = await asyncio.wait_for(asyncio.gather(*(invoke(action) for action in actions)), timeout=10)

        async with sessions() as session:
            account = await session.get(EconomyAccountModel, account_id)
            farm = await session.get(EconomyFarmModel, account_id)
            plots = (await session.execute(select(EconomyPlotModel).where(
                EconomyPlotModel.account_id == account_id,
            ))).scalars().all()
            ledger = (await session.execute(select(EconomyLedgerModel).where(
                EconomyLedgerModel.account_id == account_id,
            ))).scalars().all()
            inventory = (await session.execute(select(EconomyInventoryModel).where(
                EconomyInventoryModel.account_id == account_id,
            ))).scalars().all()
            quantities = {item.item_code: item.quantity for item in inventory}
            active = [plot for plot in plots if plot.crop_code is not None]
            harvest_entries = [entry for entry in ledger if entry.reason == "harvest"]
            plant_entries = [entry for entry in ledger if entry.reason == "plant_seed"]
            assert account.balance == 100000 - sum(entry.amount for entry in plant_entries)
            assert quantities.get("crop:radish", 0) == sum(entry.amount for entry in harvest_entries)
            if scenario in ("plant", "plant_all"):
                count = 1 if scenario == "plant" else slots
                assert len(active) == len(plant_entries) == count
                assert all(entry.amount == crop.seed_cost for entry in plant_entries)
                if scenario == "plant":
                    assert sum(result.accepted for result in results) == 1
            else:
                assert len(harvest_entries) == (2 if scenario == "harvest_all" else 1)
                assert farm.negative_event_streak == 0
                if scenario == "plant_vs_harvest":
                    assert len(active) == len(plant_entries) == int(results[0].accepted)
                    if active:
                        assert active[0].planted_at == now
                        assert active[0].ready_at > now
                else:
                    assert active == []
                if scenario == "harvest":
                    assert sum(result.accepted for result in results) == 1
                if scenario == "use_vs_harvest":
                    used = results[0].accepted
                    assert quantities.get("item:fertilizer_rich", 0) == (0 if used else 1)
                    expected = max(1, int(round(crop.min_yield * (1.25 if used else 1))))
                    assert results[1].amount == expected
    finally:
        await engine.dispose()
