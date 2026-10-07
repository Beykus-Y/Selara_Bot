from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from selara.application.use_cases.economy.craft import execute as craft
from selara.application.use_cases.economy.use_item import execute as use_item
from selara.domain.economy_entities import EconomyAccount, EconomyScope, InventoryItem


class InventoryRepository:
    """No implicit account lock: use cases must protect the whole operation."""

    def __init__(self, mode, chat_id):
        scope_id = "global" if mode == "global" else f"chat:{chat_id}"
        self.scope = EconomyScope(scope_id, "global" if mode == "global" else "chat", chat_id)
        self.account = EconomyAccount(
            id=1, scope_id=scope_id, scope_type=self.scope.scope_type, chat_id=chat_id,
            user_id=10, balance=0, tap_streak=0, last_tap_at=None, daily_streak=0,
            last_daily_claimed_at=None, free_lottery_claimed_on=None, paid_lottery_used_today=0,
            paid_lottery_used_on=None, sprinkler_level=0, tap_glove_level=0, storage_level=0,
        )
        self.inventory = {}
        self.lock = asyncio.Lock()
        self.owner = None

    async def resolve_scope(self, **kwargs):
        return self.scope, None

    async def lock_resources(self, *keys):
        assert keys == (f"economy:account:{self.scope.scope_id}:10",)
        await self.lock.acquire()
        self.owner = asyncio.current_task()

    async def get_or_create_account(self, **kwargs):
        assert self.owner is asyncio.current_task(), "account snapshot must be loaded after lock"
        return self.account, None

    async def list_inventory(self, *, account_id):
        result = [InventoryItem(account_id, code, qty) for code, qty in self.inventory.items() if qty > 0]
        await asyncio.sleep(0)
        return result

    async def get_inventory_item(self, *, account_id, item_code):
        quantity = self.inventory.get(item_code, 0)
        await asyncio.sleep(0)
        return InventoryItem(account_id, item_code, quantity) if quantity > 0 else None

    async def add_inventory_item(self, *, account_id, item_code, delta):
        self.inventory[item_code] = self.inventory.get(item_code, 0) + delta
        assert self.inventory[item_code] >= 0

    async def update_growth_state(self, *, account_id, **values):
        self.account = replace(self.account, **values)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["craft", "use"])
@pytest.mark.parametrize("mode,chat_id", [("global", None), ("local", -100)])
async def test_last_inventory_units_have_one_logical_consumer(action, mode, chat_id):
    repo = InventoryRepository(mode, chat_id)
    repo.inventory = {"crop:wheat": 3, "crop:tomato": 2} if action == "craft" else {"item:growth_gel": 1}

    async def invoke():
        try:
            kwargs = dict(economy_mode=mode, chat_id=chat_id, user_id=10)
            if action == "craft":
                return await craft(repo, recipe_code="pizza", **kwargs)
            return await use_item(repo, item_code="growth_gel", plot_no=None,
                                  event_at=datetime(2026, 8, 3, tzinfo=timezone.utc), **kwargs)
        finally:
            # Production releases its transaction lock at commit/rollback.
            if repo.owner is asyncio.current_task():
                repo.owner = None
                repo.lock.release()

    results = await asyncio.wait_for(asyncio.gather(invoke(), invoke()), timeout=2)
    assert sum(result.accepted for result in results) == 1
    if action == "craft":
        assert repo.inventory == {"crop:wheat": 0, "crop:tomato": 0, "item:pizza": 1}
    else:
        assert repo.inventory == {"item:growth_gel": 0}
        assert repo.account.growth_boost_pct == 40
