from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.application.use_cases import gacha as gacha_use_cases


class _FakeEconomyRepo:
    def __init__(self, *, balance: int) -> None:
        self.account = SimpleNamespace(id=1, balance=balance)
        self.ledger_calls: list[dict[str, object]] = []

    async def resolve_scope(self, *, mode: str, chat_id: int | None, user_id: int):
        _ = (mode, chat_id, user_id)
        return SimpleNamespace(scope_id="global", scope_type="global", chat_id=None), None

    async def get_or_create_account(self, *, scope, user_id: int):
        _ = (scope, user_id)
        return self.account, SimpleNamespace()

    async def add_balance(self, *, account_id: int, delta: int) -> int:
        assert account_id == self.account.id
        new_balance = self.account.balance + delta
        if new_balance < 0:
            raise ValueError("Insufficient balance")
        self.account.balance = new_balance
        return new_balance

    async def add_ledger(self, **kwargs) -> None:
        self.ledger_calls.append(kwargs)


@pytest.mark.asyncio
async def test_buy_currency_with_coins_debits_balance_and_returns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeEconomyRepo(balance=5_000)
    monkeypatch.setattr(
        gacha_use_cases,
        "grant_currency",
        AsyncMock(
            return_value=SimpleNamespace(
                player=SimpleNamespace(total_primogems=180),
            )
        ),
    )

    result = await gacha_use_cases.buy_currency_with_coins(
        SimpleNamespace(),
        repo,
        economy_mode="global",
        chat_id=None,
        user_id=1,
        username="buyer",
        banner="genshin",
    )

    assert result.currency_amount == gacha_use_cases.GACHA_DEFAULT_CURRENCY_PURCHASE_AMOUNT
    assert result.coin_price == 1800
    assert result.new_coin_balance == 3200
    assert result.gacha_balance == 180
    assert repo.account.balance == 3200
    assert repo.ledger_calls[0]["reason"] == "gacha_currency_purchase"


@pytest.mark.asyncio
async def test_buy_currency_with_coins_refunds_balance_when_gacha_topup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeEconomyRepo(balance=2_000)
    monkeypatch.setattr(
        gacha_use_cases,
        "grant_currency",
        AsyncMock(side_effect=gacha_use_cases.GachaUseCaseError("gacha offline")),
    )

    with pytest.raises(gacha_use_cases.GachaUseCaseError, match="gacha offline"):
        await gacha_use_cases.buy_currency_with_coins(
            SimpleNamespace(),
            repo,
            economy_mode="global",
            chat_id=None,
            user_id=1,
            username="buyer",
            banner="hsr",
        )

    assert repo.account.balance == 2_000
    assert repo.ledger_calls == []


@pytest.mark.asyncio
async def test_buy_currency_with_coins_requires_enough_coins() -> None:
    repo = _FakeEconomyRepo(balance=1_799)

    with pytest.raises(gacha_use_cases.GachaUseCaseError, match="Недостаточно монет"):
        await gacha_use_cases.buy_currency_with_coins(
            SimpleNamespace(),
            repo,
            economy_mode="global",
            chat_id=None,
            user_id=1,
            username="buyer",
            banner="genshin",
        )
