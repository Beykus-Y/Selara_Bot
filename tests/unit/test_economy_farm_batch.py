from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from selara.application.use_cases.economy.harvest_all_ready import execute as harvest_all_ready
from selara.application.use_cases.economy.plant_all_last_crop import execute as plant_all_last_crop
from selara.application.use_cases.economy.plant_crop import execute as plant_crop
from selara.domain.economy_entities import EconomyAccount, EconomyScope, FarmState, InventoryItem, PlotState


class FakeBatchRepo:
    def __init__(self) -> None:
        self.scope = EconomyScope(scope_id="chat:-1", scope_type="chat", chat_id=-1)
        self.account = EconomyAccount(
            id=1,
            scope_id=self.scope.scope_id,
            scope_type="chat",
            chat_id=-1,
            user_id=10,
            balance=1_000,
            tap_streak=0,
            last_tap_at=None,
            daily_streak=0,
            last_daily_claimed_at=None,
            free_lottery_claimed_on=None,
            paid_lottery_used_today=0,
            paid_lottery_used_on=None,
            sprinkler_level=0,
            tap_glove_level=0,
            storage_level=0,
        )
        self.farm = FarmState(account_id=1, farm_level=2, size_tier="small", negative_event_streak=0)
        self.plots: dict[int, PlotState] = {
            1: PlotState(id=1, account_id=1, plot_no=1, crop_code=None, planted_at=None, ready_at=None, yield_boost_pct=0, shield_active=False),
            2: PlotState(id=2, account_id=1, plot_no=2, crop_code=None, planted_at=None, ready_at=None, yield_boost_pct=0, shield_active=False),
            3: PlotState(id=3, account_id=1, plot_no=3, crop_code=None, planted_at=None, ready_at=None, yield_boost_pct=0, shield_active=False),
        }
        self.inventory: dict[str, int] = {}
        self.ledger: list[dict[str, object]] = []

    async def resolve_scope(self, *, mode: str, chat_id: int | None, user_id: int):
        return self.scope, None

    async def get_or_create_account(self, *, scope: EconomyScope, user_id: int):
        return self.account, self.farm

    async def get_plot(self, *, account_id: int, plot_no: int):
        return self.plots.get(plot_no)

    async def list_plots(self, *, account_id: int):
        return [self.plots[key] for key in sorted(self.plots)]

    async def upsert_plot(
        self,
        *,
        account_id: int,
        plot_no: int,
        crop_code: str | None,
        planted_at: datetime | None,
        ready_at: datetime | None,
        yield_boost_pct: int,
        shield_active: bool,
    ):
        plot = self.plots[plot_no]
        updated = replace(
            plot,
            crop_code=crop_code,
            planted_at=planted_at,
            ready_at=ready_at,
            yield_boost_pct=yield_boost_pct,
            shield_active=shield_active,
        )
        self.plots[plot_no] = updated
        return updated

    async def add_balance(self, *, account_id: int, delta: int):
        self.account = replace(self.account, balance=self.account.balance + delta)
        return self.account.balance

    async def add_ledger(self, **kwargs):
        self.ledger.append(kwargs)
        return None

    async def set_last_planted_crop_code(self, *, account_id: int, crop_code: str | None):
        self.farm = replace(self.farm, last_planted_crop_code=crop_code)

    async def set_negative_event_streak(self, *, account_id: int, value: int):
        self.farm = replace(self.farm, negative_event_streak=value)

    async def add_inventory_item(self, *, account_id: int, item_code: str, delta: int):
        self.inventory[item_code] = max(0, self.inventory.get(item_code, 0) + delta)
        return InventoryItem(account_id=account_id, item_code=item_code, quantity=self.inventory[item_code])


@pytest.mark.asyncio
async def test_plant_crop_updates_last_planted_crop_code() -> None:
    repo = FakeBatchRepo()
    now = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)

    result = await plant_crop(
        repo,
        economy_mode="local",
        chat_id=-1,
        user_id=10,
        crop_code="radish",
        plot_no=1,
        event_at=now,
    )

    assert result.accepted
    assert repo.farm.last_planted_crop_code == "radish"


@pytest.mark.asyncio
async def test_plant_all_last_crop_uses_saved_crop_code() -> None:
    repo = FakeBatchRepo()
    repo.farm = replace(repo.farm, last_planted_crop_code="radish")
    now = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)

    result = await plant_all_last_crop(
        repo,
        economy_mode="local",
        chat_id=-1,
        user_id=10,
        event_at=now,
    )

    assert result.accepted
    assert result.crop_code == "radish"
    assert result.planted_plots == (1, 2, 3)


@pytest.mark.asyncio
async def test_harvest_all_ready_collects_multiple_plots(monkeypatch) -> None:
    repo = FakeBatchRepo()
    now = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
    ready_at = now - timedelta(minutes=5)
    repo.plots[1] = replace(repo.plots[1], crop_code="radish", planted_at=ready_at - timedelta(minutes=30), ready_at=ready_at)
    repo.plots[2] = replace(repo.plots[2], crop_code="radish", planted_at=ready_at - timedelta(minutes=30), ready_at=ready_at)

    monkeypatch.setattr("selara.application.use_cases.economy.harvest.random.randint", lambda a, b: a)
    monkeypatch.setattr("selara.application.use_cases.economy.harvest.random.random", lambda: 0.99)

    result = await harvest_all_ready(
        repo,
        economy_mode="local",
        chat_id=-1,
        user_id=10,
        negative_event_chance_percent=0,
        negative_event_loss_percent=0,
        event_at=now,
    )

    assert result.accepted
    assert result.harvested_plots == (1, 2)
    assert result.total_amount > 0
    assert repo.inventory["crop:radish"] == result.total_amount
