from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation.handlers import text_commands

_CHAT_SETTINGS = SimpleNamespace(economy_mode="global")


class _DummyCallbackMessage:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(type="group", id=-100123, title="Test chat")
        self.edit_text_calls: list[tuple[str, dict[str, object]]] = []
        self.edit_reply_markup_calls: list[dict[str, object]] = []

    async def edit_text(self, text: str, **kwargs) -> None:
        self.edit_text_calls.append((text, kwargs))

    async def edit_reply_markup(self, **kwargs) -> None:
        self.edit_reply_markup_calls.append(kwargs)


class _DummyQuery:
    def __init__(self, *, data: str, user_id: int) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id, username="actor", first_name="Actor", last_name=None, is_bot=False)
        self.message = _DummyCallbackMessage()
        self.chat_instance = "chat-instance"
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


class _DummyEconomyRepo:
    async def resolve_scope(self, *, mode: str, chat_id: int | None, user_id: int):
        _ = (mode, chat_id, user_id)
        return SimpleNamespace(scope_id="global", scope_type="global", chat_id=None), None

    async def get_or_create_account(self, *, scope, user_id: int):
        _ = (scope, user_id)
        return SimpleNamespace(id=1, balance=200_942), SimpleNamespace()


@pytest.mark.asyncio
async def test_gacha_callback_rejects_foreign_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    query = _DummyQuery(data="gacha:buy:genshin:u99", user_id=1)
    purchase_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "purchase_gacha_pull", purchase_mock)

    await text_commands.gacha_callback(query, settings=SimpleNamespace(), economy_repo=object(), chat_settings=_CHAT_SETTINGS)

    purchase_mock.assert_not_awaited()
    assert query.answers == [("Эта кнопка не для вас.", True)]


@pytest.mark.asyncio
async def test_gacha_buy_callback_refreshes_info_message(monkeypatch: pytest.MonkeyPatch) -> None:
    query = _DummyQuery(data="gacha:buy:genshin:u1", user_id=1)
    settings = SimpleNamespace()
    economy_repo = object()
    purchase_mock = AsyncMock(
        return_value=SimpleNamespace(
            message="paid pull",
            card=SimpleNamespace(name="Эмбер", image_url="http://example.com/card.jpg"),
            sell_offer=None,
            pull_id=10,
        )
    )
    deliver_mock = AsyncMock()
    build_info_mock = AsyncMock(return_value=("<b>Гача инфо</b>", None))
    monkeypatch.setattr(text_commands, "purchase_gacha_pull", purchase_mock)
    monkeypatch.setattr(text_commands, "_deliver_gacha_pull_response", deliver_mock)
    monkeypatch.setattr(text_commands, "_build_gacha_info_view", build_info_mock)

    await text_commands.gacha_callback(query, settings=settings, economy_repo=economy_repo, chat_settings=_CHAT_SETTINGS)

    purchase_mock.assert_awaited_once()
    deliver_mock.assert_awaited_once()
    build_info_mock.assert_awaited_once_with(
        settings,
        economy_repo,
        user_id=1,
        economy_mode="global",
        chat_id=-100123,
    )
    assert query.message.edit_text_calls[0][0] == "<b>Гача инфо</b>"
    assert query.answers[-1] == (None, False)


@pytest.mark.asyncio
async def test_gacha_sell_callback_removes_markup_and_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    query = _DummyQuery(data="gacha:sell:genshin:42:u1", user_id=1)
    sell_mock = AsyncMock(return_value=SimpleNamespace(message="Продажа: +54 примогемов. Баланс: 120."))
    monkeypatch.setattr(text_commands, "sell_gacha_pull", sell_mock)

    await text_commands.gacha_callback(query, settings=SimpleNamespace(), economy_repo=object(), chat_settings=_CHAT_SETTINGS)

    sell_mock.assert_awaited_once()
    assert query.message.edit_reply_markup_calls == [{"reply_markup": None}]
    assert query.answers[-1] == ("Продажа: +54 примогемов. Баланс: 120.", False)


@pytest.mark.asyncio
async def test_gacha_currency_callback_buys_currency_and_refreshes_info(monkeypatch: pytest.MonkeyPatch) -> None:
    query = _DummyQuery(data="gacha:currency:hsr:160:u1", user_id=1)
    settings = SimpleNamespace()
    economy_repo = object()
    buy_currency_mock = AsyncMock(
        return_value=SimpleNamespace(
            message="Обмен: -1600 монет, +160 звездного нефрита. Баланс монет: 1200.",
        )
    )
    build_info_mock = AsyncMock(return_value=("<b>Гача инфо</b>", None))
    monkeypatch.setattr(text_commands, "buy_gacha_currency_with_coins", buy_currency_mock)
    monkeypatch.setattr(text_commands, "_build_gacha_info_view", build_info_mock)

    await text_commands.gacha_callback(query, settings=settings, economy_repo=economy_repo, chat_settings=_CHAT_SETTINGS)

    buy_currency_mock.assert_awaited_once_with(
        settings,
        economy_repo,
        economy_mode="global",
        chat_id=-100123,
        user_id=1,
        username="actor",
        banner="hsr",
        currency_amount=160,
    )
    build_info_mock.assert_awaited_once_with(
        settings,
        economy_repo,
        user_id=1,
        economy_mode="global",
        chat_id=-100123,
    )
    assert query.answers[-1] == ("Обмен: -1600 монет, +160 звездного нефрита. Баланс монет: 1200.", False)


@pytest.mark.asyncio
async def test_build_gacha_info_view_shows_coin_balance_and_currency_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = SimpleNamespace(
        player=SimpleNamespace(
            adventure_rank=1,
            xp_into_rank=0,
            xp_for_next_rank=300,
            total_points=10,
            total_primogems=180,
        ),
        unique_cards=1,
        total_copies=1,
        recent_pulls=[],
    )
    monkeypatch.setattr(text_commands, "get_gacha_profile", AsyncMock(return_value=profile))

    text, markup = await text_commands._build_gacha_info_view(
        SimpleNamespace(),
        _DummyEconomyRepo(),
        user_id=1,
        economy_mode="global",
        chat_id=None,
    )

    assert "Монеты бота" in text
    assert "200942" in text
    assert "1</code> валюты = <code>10" in text
    assert markup is not None
    assert len(markup.inline_keyboard) == 2
    assert len(markup.inline_keyboard[0]) == 2
