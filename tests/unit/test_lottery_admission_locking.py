import asyncio
import importlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from selara.application.use_cases.economy.draw_lottery import execute as draw
from selara.domain.economy_entities import EconomyScope, InventoryItem


class AdmissionRepository:
    def __init__(self):
        self.scope = EconomyScope("global", "global", None)
        self.account = SimpleNamespace(id=1, balance=100000, free_lottery_claimed_on=None,
                                       paid_lottery_used_today=0, paid_lottery_used_on=None)
        self.inventory = {"item:lottery_ticket": 1}
        self.ledger = []
        self.lock = asyncio.Lock()
        self.owner = None

    async def resolve_scope(self, **kwargs):
        return self.scope, None

    async def lock_resources(self, *keys):
        assert keys == ("economy:account:global:10",)
        await self.lock.acquire()
        self.owner = asyncio.current_task()

    async def get_or_create_account(self, **kwargs):
        assert self.owner is asyncio.current_task(), "admission snapshot must be loaded after lock"
        await asyncio.sleep(0)
        return self.account, None

    async def mark_free_lottery_claimed(self, *, account_id, claimed_on):
        self.account.free_lottery_claimed_on = claimed_on

    async def get_inventory_item(self, *, account_id, item_code):
        quantity = self.inventory.get(item_code, 0)
        return InventoryItem(account_id, item_code, quantity) if quantity > 0 else None

    async def add_inventory_item(self, *, account_id, item_code, delta):
        await asyncio.sleep(0)
        self.inventory[item_code] = self.inventory.get(item_code, 0) + delta
        assert self.inventory[item_code] >= 0

    async def add_balance(self, *, account_id, delta):
        await asyncio.sleep(0)
        self.account.balance += delta
        return self.account.balance

    async def increment_paid_lottery_used(self, *, account_id, used_on):
        self.account.paid_lottery_used_today += 1
        self.account.paid_lottery_used_on = used_on
        return self.account.paid_lottery_used_today

    async def add_ledger(self, **entry):
        self.ledger.append(entry)


@pytest.mark.asyncio
@pytest.mark.parametrize("ticket", ["free", "item", "paid"])
@pytest.mark.parametrize("reward", ["coins", "item"])
async def test_parallel_admissions_never_exceed_ticket_or_daily_allowance(monkeypatch, ticket, reward):
    module = importlib.import_module("selara.application.use_cases.economy.draw_lottery")
    monkeypatch.setattr(module.random, "random", lambda: 0.0 if reward == "coins" else 0.80)
    monkeypatch.setattr(module.random, "randint", lambda low, high: low)
    monkeypatch.setattr(module.random, "choice", lambda items: items[0])
    repo = AdmissionRepository()

    async def invoke():
        try:
            return await draw(repo, economy_mode="global", chat_id=None, user_id=10,
                              ticket_type=ticket, lottery_ticket_price=150, lottery_paid_daily_limit=3,
                              event_at=datetime(2026, 8, 3, tzinfo=timezone.utc))
        finally:
            if repo.owner is asyncio.current_task():
                repo.owner = None
                repo.lock.release()

    results = await asyncio.wait_for(asyncio.gather(*(invoke() for _ in range(6))), timeout=2)
    count = 3 if ticket == "paid" else 1
    assert sum(result.accepted for result in results) == count
    assert repo.account.balance == 100000 - (150 * count if ticket == "paid" else 0) + (80 * count if reward == "coins" else 0)
    assert sum(entry["reason"] == "lottery_ticket_paid" for entry in repo.ledger) == (count if ticket == "paid" else 0)
    assert sum(entry["reason"] == "lottery_coins" for entry in repo.ledger) == (count if reward == "coins" else 0)
    assert repo.inventory.get("item:fertilizer_fast", 0) == (count if reward == "item" else 0)
    if ticket == "item":
        assert repo.inventory["item:lottery_ticket"] == 0
