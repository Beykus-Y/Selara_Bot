from __future__ import annotations

from datetime import datetime, timezone
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
                player=SimpleNamespace(total_primogems=160),
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
    assert result.coin_price == 1600
    assert result.new_coin_balance == 3400
    assert result.gacha_balance == 160
    assert repo.account.balance == 3400
    assert repo.ledger_calls[0]["reason"] == "gacha_currency_purchase"


@pytest.mark.asyncio
async def test_buy_currency_with_coins_retries_once_on_timeout_and_keeps_coins_debited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ambiguous timeout on the currency-grant call must not be treated as
    a hard failure outright — retrying once with the same idempotency_key is
    safe (the server dedupes), so if the retry succeeds the coins must stay
    debited and no compensation should run (see
    docs/GACHA_MODERNIZATION_TODO.md, Этап 0)."""
    repo = _FakeEconomyRepo(balance=5_000)
    grant_mock = AsyncMock(
        side_effect=[
            gacha_use_cases.GachaUseCaseError("Гача-сервер не ответил вовремя.", is_timeout=True),
            SimpleNamespace(player=SimpleNamespace(total_primogems=160)),
        ]
    )
    monkeypatch.setattr(gacha_use_cases, "grant_currency", grant_mock)

    result = await gacha_use_cases.buy_currency_with_coins(
        SimpleNamespace(),
        repo,
        economy_mode="global",
        chat_id=None,
        user_id=1,
        username="buyer",
        banner="genshin",
    )

    assert result.gacha_balance == 160
    assert repo.account.balance == 3_400
    assert grant_mock.await_count == 2
    first_key = grant_mock.await_args_list[0].kwargs["idempotency_key"]
    second_key = grant_mock.await_args_list[1].kwargs["idempotency_key"]
    assert first_key == second_key
    assert first_key


@pytest.mark.asyncio
async def test_buy_currency_with_coins_does_not_refund_when_retry_also_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two consecutive ambiguous timeouts on the same idempotency_key mean we
    genuinely don't know whether the grant landed server-side. Refunding here
    would risk crediting free currency, so the balance must be left untouched
    (not refunded) and a distinct error surfaced for manual reconciliation."""
    repo = _FakeEconomyRepo(balance=5_000)
    grant_mock = AsyncMock(
        side_effect=gacha_use_cases.GachaUseCaseError("Гача-сервер не ответил вовремя.", is_timeout=True)
    )
    monkeypatch.setattr(gacha_use_cases, "grant_currency", grant_mock)

    with pytest.raises(gacha_use_cases.GachaUseCaseError, match="администратором"):
        await gacha_use_cases.buy_currency_with_coins(
            SimpleNamespace(),
            repo,
            economy_mode="global",
            chat_id=None,
            user_id=1,
            username="buyer",
            banner="genshin",
        )

    assert repo.account.balance == 3_400
    assert grant_mock.await_count == 2
    assert repo.ledger_calls == []


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
async def test_pull_card_recovers_lost_response_after_ambiguous_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the gacha service actually wrote the pull but the HTTP response was
    lost to a timeout, pull_card must recover the real result from /history
    instead of telling the user the pull failed (see
    docs/GACHA_MODERNIZATION_TODO.md, Этап 0)."""

    fixed_now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(gacha_use_cases, "_utcnow", lambda: fixed_now)

    recovered_entry = SimpleNamespace(
        pull_id=42,
        pulled_at="2026-08-19T12:00:05+00:00",
        card_name="Фурина",
        rarity="mythic",
        rarity_label="🟪 Мифическая",
        points=999,
        primogems=10,
        adventure_xp_gained=50,
        image_url="https://example.test/furina.png",
    )
    fake_client = SimpleNamespace(
        pull=AsyncMock(
            side_effect=gacha_use_cases.GachaClientError(
                "Гача-сервер не ответил вовремя.", is_timeout=True
            )
        ),
        get_history=AsyncMock(return_value=SimpleNamespace(entries=[recovered_entry])),
        get_profile=AsyncMock(
            return_value=SimpleNamespace(
                player=gacha_use_cases.GachaPlayerPayload(
                    user_id=1,
                    adventure_rank=5,
                    adventure_xp=100,
                    xp_into_rank=10,
                    xp_for_next_rank=200,
                    total_points=1000,
                    total_primogems=500,
                )
            )
        ),
    )
    monkeypatch.setattr(gacha_use_cases, "_build_client", lambda settings, *, banner: fake_client)

    response = await gacha_use_cases.pull_card(
        SimpleNamespace(), user_id=1, username="u", banner="genshin"
    )

    assert response.pull_id == 42
    assert response.card is not None
    assert response.card.name == "Фурина"
    assert response.card.primogems == 10
    assert response.player.total_primogems == 500
    assert response.sell_offer is None
    fake_client.get_history.assert_awaited_once_with(user_id=1, banner="genshin", limit=1)


@pytest.mark.asyncio
async def test_pull_card_does_not_recover_stale_history_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A history entry older than the request must not be mistaken for the
    lost pull — otherwise a genuinely failed pull would be reported as
    successful using an unrelated earlier result."""

    fixed_now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(gacha_use_cases, "_utcnow", lambda: fixed_now)

    stale_entry = SimpleNamespace(
        pull_id=41,
        pulled_at="2026-08-19T11:59:00+00:00",
        card_name="Эмбер",
        rarity="common",
        rarity_label="⬜ Обычная",
        points=1,
        primogems=1,
        adventure_xp_gained=1,
        image_url="https://example.test/amber.png",
    )
    fake_client = SimpleNamespace(
        pull=AsyncMock(
            side_effect=gacha_use_cases.GachaClientError(
                "Гача-сервер не ответил вовремя.", is_timeout=True
            )
        ),
        get_history=AsyncMock(return_value=SimpleNamespace(entries=[stale_entry])),
        get_profile=AsyncMock(),
    )
    monkeypatch.setattr(gacha_use_cases, "_build_client", lambda settings, *, banner: fake_client)

    with pytest.raises(gacha_use_cases.GachaUseCaseError, match="не ответил вовремя"):
        await gacha_use_cases.pull_card(SimpleNamespace(), user_id=1, username="u", banner="genshin")

    fake_client.get_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_banner_cards_returns_client_response(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = SimpleNamespace(
        get_banner_cards=AsyncMock(
            return_value=(SimpleNamespace(status="ok", banner="genshin", cards=[]), '"etag-1"')
        )
    )
    monkeypatch.setattr(gacha_use_cases, "_build_client", lambda settings, *, banner: fake_client)

    result, etag = await gacha_use_cases.get_banner_cards(SimpleNamespace(), banner="genshin")

    assert result.banner == "genshin"
    assert etag == '"etag-1"'
    fake_client.get_banner_cards.assert_awaited_once_with(banner="genshin", if_none_match=None)


@pytest.mark.asyncio
async def test_purchase_pull_recovers_lost_response_after_ambiguous_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same ambiguous-timeout recovery as pull_card, but for the paid pull
    path (purchase_pull) — a lost response after a real, already-paid pull
    must not be reported as a failure either."""

    fixed_now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(gacha_use_cases, "_utcnow", lambda: fixed_now)

    recovered_entry = SimpleNamespace(
        pull_id=77,
        pulled_at="2026-08-19T12:00:03+00:00",
        card_name="Нилу",
        rarity="legendary",
        rarity_label="🟨 Легендарная",
        points=500,
        primogems=5,
        adventure_xp_gained=20,
        image_url="https://example.test/nilou.png",
    )
    fake_client = SimpleNamespace(
        purchase_pull=AsyncMock(
            side_effect=gacha_use_cases.GachaClientError(
                "Гача-сервер не ответил вовремя.", is_timeout=True
            )
        ),
        get_history=AsyncMock(return_value=SimpleNamespace(entries=[recovered_entry])),
        get_profile=AsyncMock(
            return_value=SimpleNamespace(
                player=gacha_use_cases.GachaPlayerPayload(
                    user_id=1,
                    adventure_rank=5,
                    adventure_xp=100,
                    xp_into_rank=10,
                    xp_for_next_rank=200,
                    total_points=1000,
                    total_primogems=300,
                )
            )
        ),
    )
    monkeypatch.setattr(gacha_use_cases, "_build_client", lambda settings, *, banner: fake_client)

    response = await gacha_use_cases.purchase_pull(
        SimpleNamespace(), user_id=1, username="u", banner="genshin"
    )

    assert response.pull_id == 77
    assert response.card is not None
    assert response.card.name == "Нилу"
    assert response.player.total_primogems == 300
    assert response.sell_offer is None
    fake_client.get_history.assert_awaited_once_with(user_id=1, banner="genshin", limit=1)


@pytest.mark.asyncio
async def test_purchase_pull_does_not_recover_stale_history_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(gacha_use_cases, "_utcnow", lambda: fixed_now)

    stale_entry = SimpleNamespace(
        pull_id=76,
        pulled_at="2026-08-19T11:59:00+00:00",
        card_name="Эмбер",
        rarity="common",
        rarity_label="⬜ Обычная",
        points=1,
        primogems=1,
        adventure_xp_gained=1,
        image_url="https://example.test/amber.png",
    )
    fake_client = SimpleNamespace(
        purchase_pull=AsyncMock(
            side_effect=gacha_use_cases.GachaClientError(
                "Гача-сервер не ответил вовремя.", is_timeout=True
            )
        ),
        get_history=AsyncMock(return_value=SimpleNamespace(entries=[stale_entry])),
        get_profile=AsyncMock(),
    )
    monkeypatch.setattr(gacha_use_cases, "_build_client", lambda settings, *, banner: fake_client)

    with pytest.raises(gacha_use_cases.GachaUseCaseError, match="не ответил вовремя"):
        await gacha_use_cases.purchase_pull(SimpleNamespace(), user_id=1, username="u", banner="genshin")

    fake_client.get_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_purchase_pull_does_not_attempt_recovery_on_non_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = SimpleNamespace(
        purchase_pull=AsyncMock(side_effect=gacha_use_cases.GachaClientError("Недостаточно валюты.")),
        get_history=AsyncMock(),
    )
    monkeypatch.setattr(gacha_use_cases, "_build_client", lambda settings, *, banner: fake_client)

    with pytest.raises(gacha_use_cases.GachaUseCaseError, match="Недостаточно валюты"):
        await gacha_use_cases.purchase_pull(SimpleNamespace(), user_id=1, username="u", banner="genshin")

    fake_client.get_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_pull_card_does_not_attempt_recovery_on_non_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-timeout failure (e.g. validation error, 5xx) is not ambiguous —
    recovery must not run at all, since the pull is known not to exist."""

    fake_client = SimpleNamespace(
        pull=AsyncMock(side_effect=gacha_use_cases.GachaClientError("Некорректный баннер.")),
        get_history=AsyncMock(),
    )
    monkeypatch.setattr(gacha_use_cases, "_build_client", lambda settings, *, banner: fake_client)

    with pytest.raises(gacha_use_cases.GachaUseCaseError, match="Некорректный баннер"):
        await gacha_use_cases.pull_card(SimpleNamespace(), user_id=1, username="u", banner="genshin")

    fake_client.get_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_buy_currency_with_coins_requires_enough_coins() -> None:
    repo = _FakeEconomyRepo(balance=1_599)

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
