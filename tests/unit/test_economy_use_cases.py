from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from selara.application.use_cases.economy.claim_daily import execute as claim_daily
from selara.application.use_cases.economy.get_dashboard import execute as get_dashboard
from selara.application.use_cases.economy.growth import get_profile as get_growth_profile
from selara.application.use_cases.economy.growth import perform_action as perform_growth_action
from selara.application.use_cases.economy.tap import execute as tap
from selara.application.use_cases.economy.use_item import execute as use_item
from selara.domain.economy_entities import EconomyAccount, EconomyScope, FarmState, InventoryItem, PlotState


class FakeEconomyRepo:
    def __init__(self) -> None:
        self.scope = EconomyScope(scope_id="global", scope_type="global", chat_id=None)
        self.account = EconomyAccount(
            id=1,
            scope_id="global",
            scope_type="global",
            chat_id=None,
            user_id=10,
            balance=0,
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
        self.farm = FarmState(account_id=1, farm_level=1, size_tier="small", negative_event_streak=0)
        self.inventory: dict[str, int] = {}
        self.plots: list[PlotState] = []

    async def resolve_scope(self, *, mode: str, chat_id: int | None, user_id: int):
        return self.scope, None

    async def get_or_create_account(self, *, scope: EconomyScope, user_id: int):
        return self.account, self.farm

    async def add_balance(self, *, account_id: int, delta: int) -> int:
        self.account = replace(self.account, balance=self.account.balance + delta)
        return self.account.balance

    async def update_tap_state(self, *, account_id: int, tap_streak: int, last_tap_at: datetime) -> None:
        self.account = replace(self.account, tap_streak=tap_streak, last_tap_at=last_tap_at)

    async def add_ledger(self, **kwargs) -> None:
        return None

    async def update_daily_state(self, *, account_id: int, daily_streak: int, last_daily_claimed_at: datetime) -> None:
        self.account = replace(self.account, daily_streak=daily_streak, last_daily_claimed_at=last_daily_claimed_at)

    async def update_growth_state(
        self,
        *,
        account_id: int,
        growth_size_mm: int,
        growth_stress_pct: int,
        growth_actions: int,
        last_growth_at: datetime | None,
        growth_boost_pct: int,
        growth_cooldown_discount_seconds: int,
    ) -> None:
        self.account = replace(
            self.account,
            growth_size_mm=growth_size_mm,
            growth_stress_pct=growth_stress_pct,
            growth_actions=growth_actions,
            last_growth_at=last_growth_at,
            growth_boost_pct=growth_boost_pct,
            growth_cooldown_discount_seconds=growth_cooldown_discount_seconds,
        )

    async def add_inventory_item(self, *, account_id: int, item_code: str, delta: int) -> InventoryItem:
        self.inventory[item_code] = max(0, self.inventory.get(item_code, 0) + delta)
        return InventoryItem(account_id=account_id, item_code=item_code, quantity=self.inventory[item_code])

    async def get_inventory_item(self, *, account_id: int, item_code: str) -> InventoryItem | None:
        qty = self.inventory.get(item_code, 0)
        if qty <= 0:
            return None
        return InventoryItem(account_id=account_id, item_code=item_code, quantity=qty)

    async def list_inventory(self, *, account_id: int):
        return [
            InventoryItem(account_id=account_id, item_code=item_code, quantity=quantity)
            for item_code, quantity in self.inventory.items()
            if quantity > 0
        ]

    async def list_plots(self, *, account_id: int):
        _ = account_id
        return list(self.plots)


@pytest.mark.asyncio
async def test_tap_accepts_and_updates_balance() -> None:
    repo = FakeEconomyRepo()
    now = datetime(2026, 2, 14, 12, 0, tzinfo=timezone.utc)

    result = await tap(
        repo,
        economy_mode="global",
        chat_id=1,
        user_id=10,
        tap_cooldown_seconds=45,
        event_at=now,
    )

    assert result.accepted
    assert result.new_balance is not None
    assert result.new_balance > 0
    assert repo.account.tap_streak == 1


@pytest.mark.asyncio
async def test_tap_respects_cooldown() -> None:
    repo = FakeEconomyRepo()
    now = datetime(2026, 2, 14, 12, 0, tzinfo=timezone.utc)

    first = await tap(
        repo,
        economy_mode="global",
        chat_id=1,
        user_id=10,
        tap_cooldown_seconds=45,
        event_at=now,
    )
    assert first.accepted

    second = await tap(
        repo,
        economy_mode="global",
        chat_id=1,
        user_id=10,
        tap_cooldown_seconds=45,
        event_at=now + timedelta(seconds=10),
    )
    assert not second.accepted
    assert second.reason is not None


@pytest.mark.asyncio
async def test_daily_resets_streak_after_gap() -> None:
    repo = FakeEconomyRepo()
    old_claim = datetime(2026, 2, 10, 0, 0, tzinfo=timezone.utc)
    repo.account = replace(repo.account, daily_streak=5, last_daily_claimed_at=old_claim)

    now = old_claim + timedelta(hours=40)
    result = await claim_daily(
        repo,
        economy_mode="global",
        chat_id=1,
        user_id=10,
        daily_base_reward=120,
        daily_streak_cap=7,
        event_at=now,
    )

    assert result.accepted
    assert result.streak == 1


@pytest.mark.asyncio
async def test_daily_grants_ticket_on_cap() -> None:
    repo = FakeEconomyRepo()
    old_claim = datetime(2026, 2, 13, 0, 0, tzinfo=timezone.utc)
    repo.account = replace(repo.account, daily_streak=6, last_daily_claimed_at=old_claim)

    now = old_claim + timedelta(hours=24, minutes=1)
    result = await claim_daily(
        repo,
        economy_mode="global",
        chat_id=1,
        user_id=10,
        daily_base_reward=120,
        daily_streak_cap=7,
        event_at=now,
    )

    assert result.accepted
    assert result.streak == 7
    assert result.granted_lottery_ticket
    assert repo.inventory.get("item:lottery_ticket", 0) == 1


@pytest.mark.asyncio
async def test_growth_action_and_cooldown() -> None:
    repo = FakeEconomyRepo()
    now = datetime(2026, 2, 14, 12, 0, tzinfo=timezone.utc)

    first = await perform_growth_action(
        repo,
        economy_mode="global",
        chat_id=1,
        user_id=10,
        event_at=now,
    )
    assert first.accepted
    assert first.new_balance is not None

    second = await perform_growth_action(
        repo,
        economy_mode="global",
        chat_id=1,
        user_id=10,
        event_at=now + timedelta(minutes=5),
    )
    assert not second.accepted
    assert second.reason is not None


@pytest.mark.asyncio
async def test_growth_profile_after_action() -> None:
    repo = FakeEconomyRepo()
    now = datetime(2026, 2, 14, 12, 0, tzinfo=timezone.utc)

    _ = await perform_growth_action(
        repo,
        economy_mode="global",
        chat_id=1,
        user_id=10,
        event_at=now,
    )
    profile = await get_growth_profile(
        repo,
        economy_mode="global",
        chat_id=1,
        user_id=10,
        event_at=now + timedelta(minutes=1),
    )
    assert profile.accepted
    assert profile.actions == 1
    assert profile.next_available_at is not None


@pytest.mark.asyncio
async def test_growth_action_size_is_clamped_to_zero(monkeypatch) -> None:
    repo = FakeEconomyRepo()
    now = datetime(2026, 2, 14, 12, 0, tzinfo=timezone.utc)
    repo.account = replace(repo.account, growth_size_mm=0, growth_stress_pct=100)

    monkeypatch.setattr("selara.application.use_cases.economy.growth.random.random", lambda: 0.0)
    monkeypatch.setattr("selara.application.use_cases.economy.growth.random.randint", lambda a, b: b)

    result = await perform_growth_action(
        repo,
        economy_mode="global",
        chat_id=1,
        user_id=10,
        event_at=now,
    )

    assert result.accepted
    assert result.fumble
    assert result.new_size_mm == 0
    assert repo.account.growth_size_mm == 0


@pytest.mark.asyncio
async def test_use_item_stimulant_shot_applies_boost_and_stress() -> None:
    repo = FakeEconomyRepo()
    repo.account = replace(repo.account, growth_boost_pct=20, growth_stress_pct=15)
    repo.inventory["item:stimulant_shot"] = 1

    result = await use_item(
        repo,
        economy_mode="global",
        chat_id=1,
        user_id=10,
        item_code="item:stimulant_shot",
        plot_no=None,
    )

    assert result.accepted
    assert repo.account.growth_boost_pct == 90
    assert repo.account.growth_stress_pct == 25
    assert repo.inventory.get("item:stimulant_shot", 0) == 0


@pytest.mark.asyncio
async def test_get_dashboard_sorts_plots_and_inventory() -> None:
    repo = FakeEconomyRepo()
    repo.plots = [
        PlotState(id=2, account_id=1, plot_no=2, crop_code=None, planted_at=None, ready_at=None, yield_boost_pct=0, shield_active=False),
        PlotState(id=1, account_id=1, plot_no=1, crop_code="radish", planted_at=None, ready_at=None, yield_boost_pct=0, shield_active=False),
    ]
    repo.inventory["item:zeta"] = 1
    repo.inventory["crop:alpha"] = 2

    dashboard, error = await get_dashboard(
        repo,
        economy_mode="global",
        chat_id=None,
        user_id=10,
    )

    assert error is None
    assert dashboard is not None
    assert [plot.plot_no for plot in dashboard.plots] == [1, 2]
    assert [item.item_code for item in dashboard.inventory] == ["crop:alpha", "item:zeta"]
