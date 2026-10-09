from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import EditMessageText

from selara.application.use_cases.gacha import GachaUseCaseError
from selara.infrastructure.http.gacha_client import GachaClientError
from selara.presentation.handlers import text_commands

_CHAT_SETTINGS = SimpleNamespace(economy_mode="global", gacha_enabled=True)


class _DummyCallbackMessage:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(type="group", id=-100123, title="Test chat")
        self.message_id = 700
        self.edit_text_calls: list[tuple[str, dict[str, object]]] = []
        self.edit_reply_markup_calls: list[dict[str, object]] = []
        self.answer_calls: list[tuple[str, dict[str, object]]] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answer_calls.append((text, kwargs))

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
    bot = AsyncMock()

    activity_repo = SimpleNamespace(is_subscription_exempt=AsyncMock(return_value=False))
    await text_commands.gacha_callback(query, bot=bot, settings=SimpleNamespace(), economy_repo=object(), activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS)

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
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    bot = AsyncMock()
    activity_repo = SimpleNamespace(
        is_subscription_exempt=AsyncMock(return_value=False),
        is_gacha_animation_enabled=AsyncMock(return_value=False),
    )

    await text_commands.gacha_callback(query, bot=bot, settings=settings, economy_repo=economy_repo, activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS)

    purchase_mock.assert_awaited_once()
    deliver_mock.assert_awaited_once()
    assert build_info_mock.await_args_list == [
        ((settings, economy_repo, activity_repo), {"user_id": 1, "economy_mode": "global", "chat_id": -100123, "use_custom_emojis": True, "session_factory": None, "event": query}),
        ((settings, economy_repo, activity_repo), {"user_id": 1, "economy_mode": "global", "chat_id": -100123, "use_custom_emojis": False, "session_factory": None, "event": query}),
    ]
    assert query.message.edit_text_calls[0][0] == "<b>Гача инфо</b>"
    assert query.answers[-1] == (None, False)


@pytest.mark.asyncio
async def test_gacha_buy_callback_sends_animation_when_enabled_and_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Paid pulls (callback 'buy') get the same animated reveal as free
    pulls (docs/GACHA_MODERNIZATION_TODO.md, Этап 3)."""
    query = _DummyQuery(data="gacha:buy:genshin:u1", user_id=1)
    settings = SimpleNamespace()
    economy_repo = object()
    purchase_mock = AsyncMock(
        return_value=SimpleNamespace(
            message="paid pull",
            card=SimpleNamespace(code="amber", name="Эмбер", rarity="common", rarity_label="⬜", image_url="http://example.com/card.jpg"),
            sell_offer=None,
            pull_id=10,
        )
    )
    monkeypatch.setattr(text_commands, "purchase_gacha_pull", purchase_mock)
    monkeypatch.setattr(text_commands, "_deliver_gacha_pull_response", AsyncMock())
    monkeypatch.setattr(text_commands, "_build_gacha_info_view", AsyncMock(return_value=("<b>Гача инфо</b>", None)))
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "is_gacha_animation_cache_ready", AsyncMock(return_value=True))
    resolved = SimpleNamespace(payload="fresh-payload", needs_caching=True, cache_version="v1")
    monkeypatch.setattr(text_commands, "resolve_gacha_reel_animation", AsyncMock(return_value=resolved))
    cache_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "cache_reel_variant_after_send", cache_mock)
    monkeypatch.setattr(text_commands.asyncio, "sleep", AsyncMock())

    sent_message = SimpleNamespace(message_id=555, animation=SimpleNamespace(file_id="paid-file-id"))
    bot = AsyncMock()
    bot.send_animation = AsyncMock(return_value=sent_message)
    bot.delete_message = AsyncMock()
    activity_repo = SimpleNamespace(
        is_subscription_exempt=AsyncMock(return_value=False),
        is_gacha_animation_enabled=AsyncMock(return_value=True),
    )

    await text_commands.gacha_callback(query, bot=bot, settings=settings, economy_repo=economy_repo, activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS)

    bot.send_animation.assert_awaited_once()
    assert bot.send_animation.await_args.kwargs["chat_id"] == query.message.chat.id
    cache_mock.assert_awaited_once_with(
        activity_repo=activity_repo, banner="genshin", card_code="amber", telegram_file_id="paid-file-id", cache_version="v1"
    )
    bot.delete_message.assert_awaited_once_with(chat_id=query.message.chat.id, message_id=555)
    purchase_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_gacha_buy_callback_delivers_classic_result_without_retry_alert_when_cache_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _DummyQuery(data="gacha:buy:genshin:u1", user_id=1)
    settings = SimpleNamespace()
    economy_repo = object()
    purchase_mock = AsyncMock(
        return_value=SimpleNamespace(
            message="paid pull",
            card=SimpleNamespace(code="amber", name="Эмбер", rarity="common", rarity_label="⬜", image_url="http://example.com/card.jpg"),
            sell_offer=None,
            pull_id=10,
        )
    )
    monkeypatch.setattr(text_commands, "purchase_gacha_pull", purchase_mock)
    monkeypatch.setattr(text_commands, "_deliver_gacha_pull_response", AsyncMock())
    monkeypatch.setattr(text_commands, "_build_gacha_info_view", AsyncMock(return_value=("<b>Гача инфо</b>", None)))
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "is_gacha_animation_cache_ready", AsyncMock(return_value=False))
    resolve_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "resolve_gacha_reel_animation", resolve_mock)
    deliver_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "_deliver_gacha_pull_response", deliver_mock)

    bot = AsyncMock()
    activity_repo = SimpleNamespace(
        is_subscription_exempt=AsyncMock(return_value=False),
        is_gacha_animation_enabled=AsyncMock(return_value=True),
    )

    await text_commands.gacha_callback(query, bot=bot, settings=settings, economy_repo=economy_repo, activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS)

    resolve_mock.assert_not_awaited()
    bot.send_animation.assert_not_awaited()
    purchase_mock.assert_awaited_once()  # the real pull result must still go through
    deliver_mock.assert_awaited_once()
    # The pull is already charged, so no "try again later" alert may be shown; the callback is answered once.
    assert query.answers == [(None, False)]


@pytest.mark.asyncio
async def test_gacha_callback_unsubscribed_user_gets_alert_and_tappable_subscribe_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _DummyQuery(data="gacha:buy:genshin:u1", user_id=1)
    purchase_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "purchase_gacha_pull", purchase_mock)
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=False))
    activity_repo = SimpleNamespace(is_subscription_exempt=AsyncMock(return_value=False))

    await text_commands.gacha_callback(
        query,
        bot=AsyncMock(),
        settings=SimpleNamespace(),
        economy_repo=object(),
        activity_repo=activity_repo,
        chat_settings=_CHAT_SETTINGS,
    )

    purchase_mock.assert_not_awaited()
    assert query.answers == [("Для гачи нужно подписаться на канал @SelaraBot_Chanel", True)]
    assert query.message.answer_calls == [
        (
            text_commands._GACHA_SUBSCRIPTION_PROMPT_HTML,
            {"parse_mode": "HTML", "disable_web_page_preview": True},
        )
    ]
    assert 'href="https://t.me/SelaraBot_Chanel"' in text_commands._GACHA_SUBSCRIPTION_PROMPT_HTML


@pytest.mark.asyncio
async def test_gacha_buy_callback_still_delivers_result_when_readiness_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same pre-deploy audit finding as the text-command path (2026-08-19):
    is_gacha_animation_cache_ready must not be able to swallow the already-
    paid-for pull result if it raises."""
    query = _DummyQuery(data="gacha:buy:genshin:u1", user_id=1)
    settings = SimpleNamespace()
    economy_repo = object()
    purchase_mock = AsyncMock(
        return_value=SimpleNamespace(
            message="paid pull",
            card=SimpleNamespace(code="amber", name="Эмбер", rarity="common", rarity_label="⬜", image_url="http://example.com/card.jpg"),
            sell_offer=None,
            pull_id=10,
        )
    )
    monkeypatch.setattr(text_commands, "purchase_gacha_pull", purchase_mock)
    deliver_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "_deliver_gacha_pull_response", deliver_mock)
    monkeypatch.setattr(text_commands, "_build_gacha_info_view", AsyncMock(return_value=("<b>Гача инфо</b>", None)))
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(
        text_commands, "is_gacha_animation_cache_ready", AsyncMock(side_effect=RuntimeError("gacha service down"))
    )
    resolve_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "resolve_gacha_reel_animation", resolve_mock)

    bot = AsyncMock()
    activity_repo = SimpleNamespace(
        is_subscription_exempt=AsyncMock(return_value=False),
        is_gacha_animation_enabled=AsyncMock(return_value=True),
    )

    await text_commands.gacha_callback(query, bot=bot, settings=settings, economy_repo=economy_repo, activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS)

    resolve_mock.assert_not_awaited()
    bot.send_animation.assert_not_awaited()
    deliver_mock.assert_awaited_once()  # the real, already-paid-for result must still go through


@pytest.mark.asyncio
async def test_gacha_sell_callback_removes_markup_and_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    query = _DummyQuery(data="gacha:sell:genshin:42:u1", user_id=1)
    sell_mock = AsyncMock(return_value=SimpleNamespace(message="Продажа: +54 примогемов. Баланс: 120."))
    monkeypatch.setattr(text_commands, "sell_gacha_pull", sell_mock)
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    bot = AsyncMock()
    activity_repo = SimpleNamespace(is_subscription_exempt=AsyncMock(return_value=False))

    await text_commands.gacha_callback(query, bot=bot, settings=SimpleNamespace(), economy_repo=object(), activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS)

    sell_mock.assert_awaited_once()
    assert query.message.edit_reply_markup_calls == [{"reply_markup": None}]
    assert query.answers[-1] == ("Продажа: +54 примогемов. Баланс: 120.", False)


@pytest.mark.asyncio
async def test_gacha_sell_timeout_sends_operational_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    query = _DummyQuery(data="gacha:sell:genshin:42:u1", user_id=1)
    timeout = text_commands.GachaUseCaseError("service timed out", is_timeout=True)
    monkeypatch.setattr(text_commands, "sell_gacha_pull", AsyncMock(side_effect=timeout))
    monkeypatch.setattr(text_commands, "_require_channel_subscription_callback", AsyncMock(return_value=True))
    alert = AsyncMock()
    monkeypatch.setattr(text_commands, "notify_operational_error", alert)

    await text_commands.gacha_callback(
        query,
        bot=object(),
        settings=SimpleNamespace(),
        economy_repo=object(),
        activity_repo=SimpleNamespace(),
        chat_settings=_CHAT_SETTINGS,
    )

    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_gacha_info_hides_unexpected_exception_and_reports_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_commands, "_load_gacha_coin_balance", AsyncMock(return_value=None))
    monkeypatch.setattr(text_commands, "_render_gacha_info_section", lambda **_kwargs: "profile ok")
    monkeypatch.setattr(text_commands, "get_gacha_profile", AsyncMock(side_effect=[SimpleNamespace(), RuntimeError("INTERNAL_SECRET")]))
    alert = AsyncMock()
    monkeypatch.setattr(text_commands, "notify_operational_error", alert)
    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=False))

    text, _markup = await text_commands._build_gacha_info_view(
        SimpleNamespace(),
        object(),
        activity_repo,
        user_id=1,
        economy_mode="global",
        chat_id=None,
        session_factory=object(),
        event=SimpleNamespace(),
    )

    failures = []
    if "INTERNAL_SECRET" in text:
        failures.append("internal exception text reached the user")
    if "Не удалось загрузить данные гачи." not in text:
        failures.append("neutral gacha error was not shown")
    if alert.await_count != 1:
        failures.append("unexpected exception did not produce exactly one operational alert")
    assert not failures, "; ".join(failures)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["connect", "server", "malformed_json", "business"])
async def test_gacha_profile_reports_operational_http_failures_but_not_business_4xx(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = httpx.Request("GET", "https://gacha.example/profile")
    if failure == "connect":
        root_cause = httpx.ConnectError("connection refused", request=request)
    elif failure == "malformed_json":
        root_cause = json.JSONDecodeError("invalid JSON", "{", 0)
    else:
        status = 500 if failure == "server" else 400
        response = httpx.Response(status, request=request)
        root_cause = httpx.HTTPStatusError("HTTP error", request=request, response=response)
    client_error = GachaClientError("gacha request failed", is_operational=failure != "business")
    client_error.__cause__ = root_cause
    use_case_error = GachaUseCaseError(
        "gacha request failed", is_operational=client_error.is_operational
    )
    use_case_error.__cause__ = client_error
    monkeypatch.setattr(text_commands, "get_gacha_profile", AsyncMock(side_effect=use_case_error))
    alert = AsyncMock()
    monkeypatch.setattr(text_commands, "notify_operational_error", alert)
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private", id=1),
        from_user=SimpleNamespace(id=1),
        answer=AsyncMock(),
    )

    await text_commands._send_gacha_profile(
        message,
        SimpleNamespace(),
        banner="genshin",
        session_factory=object(),
    )

    if failure == "business":
        alert.assert_not_awaited()
    else:
        alert.assert_awaited_once()


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
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    bot = AsyncMock()
    activity_repo = SimpleNamespace(is_subscription_exempt=AsyncMock(return_value=False))

    await text_commands.gacha_callback(query, bot=bot, settings=settings, economy_repo=economy_repo, activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS)

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
    assert build_info_mock.await_args_list == [
        ((settings, economy_repo, activity_repo), {"user_id": 1, "economy_mode": "global", "chat_id": -100123, "use_custom_emojis": True, "session_factory": None, "event": query}),
        ((settings, economy_repo, activity_repo), {"user_id": 1, "economy_mode": "global", "chat_id": -100123, "use_custom_emojis": False, "session_factory": None, "event": query}),
    ]
    assert query.answers[-1] == ("Обмен: -1600 монет, +160 звездного нефрита. Баланс монет: 1200.", False)


@pytest.mark.asyncio
async def test_gacha_animation_toggle_callback_flips_state_and_refreshes_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Этап 2 of docs/GACHA_MODERNIZATION_TODO.md: a per-user opt-out toggle
    for the (future) animated pull mode, surfaced as a button in 'гача
    инфо'. Pressing it flips the stored preference and re-renders the info
    message, same pattern as buy/currency."""
    query = _DummyQuery(data="gacha:animtoggle:u1", user_id=1)
    settings = SimpleNamespace()
    economy_repo = object()
    activity_repo = SimpleNamespace(
        is_subscription_exempt=AsyncMock(return_value=False),
        is_gacha_animation_enabled=AsyncMock(return_value=True),
        set_gacha_animation_enabled=AsyncMock(),
    )
    build_info_mock = AsyncMock(return_value=("<b>Гача инфо</b>", None))
    monkeypatch.setattr(text_commands, "_build_gacha_info_view", build_info_mock)
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    bot = AsyncMock()

    await text_commands.gacha_callback(
        query, bot=bot, settings=settings, economy_repo=economy_repo, activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS
    )

    activity_repo.set_gacha_animation_enabled.assert_awaited_once_with(user_id=1, enabled=False)
    assert build_info_mock.await_count == 2
    assert query.message.edit_text_calls[0][0] == "<b>Гача инфо</b>"
    assert query.answers[-1][0] is not None and query.answers[-1][1] is False


@pytest.mark.asyncio
async def test_gacha_callback_rejects_second_press_while_same_message_is_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_query = _DummyQuery(data="gacha:buy:genshin:u1", user_id=1)
    second_query = _DummyQuery(data="gacha:buy:genshin:u1", user_id=1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def purchase(*args, **kwargs):
        _ = (args, kwargs)
        started.set()
        await release.wait()
        return SimpleNamespace(
            message="paid pull",
            card=SimpleNamespace(name="Эмбер", image_url="http://example.com/card.jpg"),
            sell_offer=None,
            pull_id=10,
        )

    monkeypatch.setattr(text_commands, "purchase_gacha_pull", purchase)
    monkeypatch.setattr(text_commands, "_deliver_gacha_pull_response", AsyncMock())
    monkeypatch.setattr(text_commands, "_build_gacha_info_view", AsyncMock(return_value=("info", None)))
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    activity_repo = SimpleNamespace(
        is_subscription_exempt=AsyncMock(return_value=False),
        is_gacha_animation_enabled=AsyncMock(return_value=False),
    )

    first_task = asyncio.create_task(
        text_commands.gacha_callback(
            first_query,
            bot=AsyncMock(),
            settings=SimpleNamespace(),
            economy_repo=object(),
            activity_repo=activity_repo,
            chat_settings=_CHAT_SETTINGS,
        )
    )
    await started.wait()
    await text_commands.gacha_callback(
        second_query,
        bot=AsyncMock(),
        settings=SimpleNamespace(),
        economy_repo=object(),
        activity_repo=activity_repo,
        chat_settings=_CHAT_SETTINGS,
    )
    release.set()
    await first_task

    assert second_query.answers == [("Запрос уже обрабатывается.", True)]


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
        rarity_counts=[
            SimpleNamespace(
                rarity="legendary",
                rarity_label="🟨 Легендарная",
                summary_label="Легендарных карт",
                count=10,
            ),
            SimpleNamespace(
                rarity="epic",
                rarity_label="🟪 Эпическая",
                summary_label="Эпических карт",
                count=7,
            ),
        ],
        recent_pulls=[],
    )
    monkeypatch.setattr(text_commands, "get_gacha_profile", AsyncMock(return_value=profile))
    monkeypatch.setattr(
        text_commands,
        "_load_gacha_custom_emoji_catalog",
        lambda: {
            "event_pull": text_commands._GachaCustomEmoji(custom_emoji_id="event-id", fallback="🎴"),
            "primogem": text_commands._GachaCustomEmoji(custom_emoji_id="primogem-id", fallback="💠"),
        },
    )

    activity_repo = SimpleNamespace(is_gacha_animation_enabled=AsyncMock(return_value=True))

    text, markup = await text_commands._build_gacha_info_view(
        SimpleNamespace(),
        _DummyEconomyRepo(),
        activity_repo,
        user_id=1,
        economy_mode="global",
        chat_id=None,
    )

    assert "Монеты бота" in text
    assert "🪙 Монеты бота: <b>200 942</b>" in text
    assert "💱 Курс: <b>1</b> валюта = <b>10</b> монет" in text
    assert "«гача генш» или «гача хср» в чате" in text
    assert "«моя гача генш» или «моя гача хср»" in text
    assert "⬜ обычная, 🟦 редкая, 🟪 эпическая, 🟨 легендарная, 🟥 мифическая" in text
    assert "/help → 🎮 Игры и развлечения → 🎴 Гача Genshin и HSR" in text
    assert '<tg-emoji emoji-id="primogem-id">💠</tg-emoji> Примогемы' in text
    assert "📊 В коллекции: 🟨 <b>10</b> | 🟪 <b>7</b>" in text
    assert markup is not None
    # Pull and currency purchase are on separate rows, one button per row.
    assert [len(row) for row in markup.inline_keyboard] == [1, 1, 1, 1, 1]
    assert markup.inline_keyboard[0][0].icon_custom_emoji_id == "event-id"
    assert markup.inline_keyboard[0][0].text == "Крутка • Геншин (160 валюты)"
    assert markup.inline_keyboard[1][0].icon_custom_emoji_id == "primogem-id"
    assert markup.inline_keyboard[1][0].text == "+160 примогемов за 1600 монет"
    assert markup.inline_keyboard[2][0].icon_custom_emoji_id is None
    assert markup.inline_keyboard[3][0].icon_custom_emoji_id is None
    assert "Вкл" in markup.inline_keyboard[4][0].text
    assert markup.inline_keyboard[4][0].callback_data == "gacha:animtoggle:u1"


@pytest.mark.asyncio
async def test_gacha_sell_callback_rejects_foreign_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    query = _DummyQuery(
        data=text_commands._gacha_sell_callback_data(banner="genshin", pull_id=42, owner_user_id=99),
        user_id=1,
    )
    sell_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "sell_gacha_pull", sell_mock)
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    activity_repo = SimpleNamespace(is_subscription_exempt=AsyncMock(return_value=False))

    await text_commands.gacha_callback(
        query, bot=AsyncMock(), settings=SimpleNamespace(), economy_repo=object(),
        activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS,
    )

    sell_mock.assert_not_awaited()
    assert query.message.edit_reply_markup_calls == []
    assert query.answers == [("Эта кнопка не для вас.", True)]


@pytest.mark.asyncio
async def test_gacha_currency_callback_rejects_foreign_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    query = _DummyQuery(
        data=text_commands._gacha_currency_buy_callback_data(
            banner="genshin", amount=160, owner_user_id=99
        ),
        user_id=1,
    )
    buy_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "buy_gacha_currency_with_coins", buy_mock)
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    activity_repo = SimpleNamespace(is_subscription_exempt=AsyncMock(return_value=False))

    await text_commands.gacha_callback(
        query, bot=AsyncMock(), settings=SimpleNamespace(), economy_repo=object(),
        activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS,
    )

    buy_mock.assert_not_awaited()
    assert query.answers == [("Эта кнопка не для вас.", True)]


@pytest.mark.asyncio
async def test_gacha_sell_callback_rejects_second_press_while_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    data = text_commands._gacha_sell_callback_data(banner="genshin", pull_id=42, owner_user_id=1)
    first_query = _DummyQuery(data=data, user_id=1)
    second_query = _DummyQuery(data=data, user_id=1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def sell(*args, **kwargs):
        _ = (args, kwargs)
        started.set()
        await release.wait()
        return SimpleNamespace(message="Продажа: +54 примогемов. Баланс: 120.")

    sell_mock = AsyncMock(side_effect=sell)
    monkeypatch.setattr(text_commands, "sell_gacha_pull", sell_mock)
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    activity_repo = SimpleNamespace(is_subscription_exempt=AsyncMock(return_value=False))

    first_task = asyncio.create_task(
        text_commands.gacha_callback(
            first_query, bot=AsyncMock(), settings=SimpleNamespace(), economy_repo=object(),
            activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS,
        )
    )
    await started.wait()
    await text_commands.gacha_callback(
        second_query, bot=AsyncMock(), settings=SimpleNamespace(), economy_repo=object(),
        activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS,
    )
    release.set()
    await first_task

    # One sale only: the second tap is refused before it reaches the gacha service.
    sell_mock.assert_awaited_once()
    assert second_query.answers == [("Запрос уже обрабатывается.", True)]


@pytest.mark.asyncio
async def test_gacha_sell_callback_shows_already_sold_error_without_touching_markup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _DummyQuery(data="gacha:sell:genshin:42:u1", user_id=1)
    sell_mock = AsyncMock(side_effect=GachaUseCaseError("Эта копия уже продана."))
    monkeypatch.setattr(text_commands, "sell_gacha_pull", sell_mock)
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    activity_repo = SimpleNamespace(is_subscription_exempt=AsyncMock(return_value=False))

    await text_commands.gacha_callback(
        query, bot=AsyncMock(), settings=SimpleNamespace(), economy_repo=object(),
        activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS,
    )

    assert query.answers == [("Эта копия уже продана.", True)]
    assert query.message.edit_reply_markup_calls == []


def _profile_with_balance(total_primogems: int) -> SimpleNamespace:
    return SimpleNamespace(
        player=SimpleNamespace(
            adventure_rank=1,
            xp_into_rank=0,
            xp_for_next_rank=300,
            total_points=10,
            total_primogems=total_primogems,
        ),
        unique_cards=0,
        total_copies=0,
        rarity_counts=[],
        recent_pulls=[],
    )


def test_gacha_sell_button_shows_sale_price_before_tap() -> None:
    response = SimpleNamespace(pull_id=5, sell_offer=SimpleNamespace(sale_price=30))
    markup = text_commands._build_gacha_pull_markup(response=response, banner="hsr", owner_user_id=1)
    no_offer = text_commands._build_gacha_pull_markup(
        response=SimpleNamespace(pull_id=5, sell_offer=None), banner="hsr", owner_user_id=1
    )

    assert markup is not None
    assert markup.inline_keyboard[0][0].text == "Продать за 30 валюты"
    assert no_offer is None


def test_gacha_subscription_prompt_cooldown_starts_only_after_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_commands, "_gacha_subscription_prompt_sent_at", {})

    assert text_commands._gacha_subscription_prompt_is_due(chat_id=-100, user_id=1)
    # A failed send never marks the prompt, so the next tap still gets the link.
    assert text_commands._gacha_subscription_prompt_is_due(chat_id=-100, user_id=1)

    text_commands._mark_gacha_subscription_prompt_sent(chat_id=-100, user_id=1)
    assert not text_commands._gacha_subscription_prompt_is_due(chat_id=-100, user_id=1)
    assert text_commands._gacha_subscription_prompt_is_due(chat_id=-100, user_id=2)


def test_gacha_subscription_prompt_marking_prunes_expired_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    expired_at = text_commands.time.monotonic() - text_commands._GACHA_SUBSCRIPTION_PROMPT_COOLDOWN - 1
    sent_at = {(-200, 2): expired_at}
    monkeypatch.setattr(text_commands, "_gacha_subscription_prompt_sent_at", sent_at)

    text_commands._mark_gacha_subscription_prompt_sent(chat_id=-100, user_id=1)

    assert (-200, 2) not in sent_at
    assert (-100, 1) in sent_at


def test_gacha_currency_button_shows_cost_in_coins() -> None:
    assert text_commands._gacha_currency_button_label("genshin") == "+160 примогемов за 1600 монет"
    assert text_commands._gacha_currency_button_label("hsr") == "+160 нефрита за 1600 монет"


def test_gacha_info_section_hints_top_up_when_balance_below_pull_price() -> None:
    low = text_commands._render_gacha_info_section(
        banner="genshin", response=_profile_with_balance(50), use_custom_emojis=False
    )
    enough = text_commands._render_gacha_info_section(
        banner="genshin", response=_profile_with_balance(160), use_custom_emojis=False
    )

    assert "Платная крутка за 160 валюты, у вас <b>50</b>." in low
    assert "Бесплатная крутка от валюты не зависит" in low
    assert "Платная крутка" not in enough


def test_gacha_info_section_shows_first_pull_hint_only_for_empty_collection() -> None:
    empty = text_commands._render_gacha_info_section(
        banner="genshin", response=_profile_with_balance(500), use_custom_emojis=False
    )
    filled_profile = _profile_with_balance(500)
    filled_profile.unique_cards = 1
    filled = text_commands._render_gacha_info_section(
        banner="genshin", response=filled_profile, use_custom_emojis=False
    )
    hsr_empty = text_commands._render_gacha_info_section(
        banner="hsr", response=_profile_with_balance(500), use_custom_emojis=False
    )

    assert "Коллекция пока пуста: начните с бесплатной крутки командой «гача генш»" in empty
    assert "Коллекция пока пуста" not in filled
    assert "командой «гача хср»" in hsr_empty


@pytest.mark.asyncio
async def test_gacha_subscription_prompt_link_is_not_repeated_on_every_tap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_commands, "_gacha_subscription_prompt_sent_at", {})
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=False))
    activity_repo = SimpleNamespace(is_subscription_exempt=AsyncMock(return_value=False))
    first = _DummyQuery(data="gacha:buy:genshin:u1", user_id=1)
    second = _DummyQuery(data="gacha:buy:genshin:u1", user_id=1)

    for query in (first, second):
        await text_commands.gacha_callback(
            query,
            bot=AsyncMock(),
            settings=SimpleNamespace(),
            economy_repo=object(),
            activity_repo=activity_repo,
            chat_settings=_CHAT_SETTINGS,
        )

    # The alert is shown on every tap, the chat link only once per chat and user.
    assert first.answers == [("Для гачи нужно подписаться на канал @SelaraBot_Chanel", True)]
    assert second.answers == [("Для гачи нужно подписаться на канал @SelaraBot_Chanel", True)]
    assert len(first.message.answer_calls) == 1
    assert second.message.answer_calls == []


@pytest.mark.asyncio
async def test_run_gacha_message_edit_retries_after_flood_control(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyCallbackMessage()
    method = EditMessageText(chat_id=message.chat.id, message_id=message.message_id, text="updated")
    operation = AsyncMock(
        side_effect=[
            TelegramRetryAfter(method=method, message="retry", retry_after=3),
            None,
        ]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(text_commands.asyncio, "sleep", sleep)

    await text_commands._run_gacha_message_edit(message, operation)

    assert operation.await_count == 2
    sleep.assert_awaited_once_with(3)


@pytest.mark.asyncio
async def test_run_gacha_message_edit_ignores_message_not_modified() -> None:
    message = _DummyCallbackMessage()
    method = EditMessageText(chat_id=message.chat.id, message_id=message.message_id, text="same")
    operation = AsyncMock(
        side_effect=TelegramBadRequest(method=method, message="message is not modified")
    )

    await text_commands._run_gacha_message_edit(message, operation)

    operation.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_gacha_message_edit_serializes_edits_for_same_message() -> None:
    message = _DummyCallbackMessage()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    active = 0
    max_active = 0

    async def operation() -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if not first_started.is_set():
            first_started.set()
            await release_first.wait()
        active -= 1

    first_task = asyncio.create_task(text_commands._run_gacha_message_edit(message, operation))
    await first_started.wait()
    second_task = asyncio.create_task(text_commands._run_gacha_message_edit(message, operation))
    await asyncio.sleep(0)
    release_first.set()
    await asyncio.gather(first_task, second_task)

    assert max_active == 1


@pytest.mark.asyncio
async def test_gacha_currency_callback_insufficient_coins_alerts_and_keeps_info_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GUX-13: a refused exchange (not enough coins) must explain the shortfall in an alert
    and leave the info message untouched, so no half-updated balance is shown."""
    query = _DummyQuery(data="gacha:currency:hsr:160:u1", user_id=1)
    buy_currency_mock = AsyncMock(
        side_effect=GachaUseCaseError("Недостаточно монет. Нужно 1600, у вас 20.")
    )
    build_info_mock = AsyncMock(return_value=("<b>Гача инфо</b>", None))
    monkeypatch.setattr(text_commands, "buy_gacha_currency_with_coins", buy_currency_mock)
    monkeypatch.setattr(text_commands, "_build_gacha_info_view", build_info_mock)
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    alert = AsyncMock()
    monkeypatch.setattr(text_commands, "notify_operational_error", alert)
    activity_repo = SimpleNamespace(is_subscription_exempt=AsyncMock(return_value=False))

    await text_commands.gacha_callback(
        query, bot=AsyncMock(), settings=SimpleNamespace(), economy_repo=object(),
        activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS,
    )

    assert query.answers == [("Недостаточно монет. Нужно 1600, у вас 20.", True)]
    assert query.message.edit_text_calls == []
    build_info_mock.assert_not_awaited()
    alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_gacha_buy_callback_timeout_alerts_operator_and_skips_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GUX-13: a timed-out paid pull is reported to the operator and the user gets an alert;
    no result is delivered and the info message is not re-rendered as if the pull succeeded."""
    query = _DummyQuery(data="gacha:buy:genshin:u1", user_id=1)
    timeout = GachaUseCaseError("service timed out", is_timeout=True)
    monkeypatch.setattr(text_commands, "purchase_gacha_pull", AsyncMock(side_effect=timeout))
    deliver_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "_deliver_gacha_pull_response", deliver_mock)
    build_info_mock = AsyncMock(return_value=("<b>Гача инфо</b>", None))
    monkeypatch.setattr(text_commands, "_build_gacha_info_view", build_info_mock)
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    alert = AsyncMock()
    monkeypatch.setattr(text_commands, "notify_operational_error", alert)
    activity_repo = SimpleNamespace(is_subscription_exempt=AsyncMock(return_value=False))

    await text_commands.gacha_callback(
        query, bot=AsyncMock(), settings=SimpleNamespace(), economy_repo=object(),
        activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS,
    )

    alert.assert_awaited_once()
    assert query.answers == [("service timed out", True)]
    deliver_mock.assert_not_awaited()
    build_info_mock.assert_not_awaited()
    assert query.message.edit_text_calls == []


@pytest.mark.asyncio
async def test_gacha_buy_callback_business_refusal_is_alert_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """GUX-13: a business refusal (for example, not enough currency) is shown to the user as an
    alert and is not reported as an operational incident."""
    query = _DummyQuery(data="gacha:buy:hsr:u1", user_id=1)
    monkeypatch.setattr(
        text_commands,
        "purchase_gacha_pull",
        AsyncMock(side_effect=GachaUseCaseError("Недостаточно валюты для крутки.")),
    )
    deliver_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "_deliver_gacha_pull_response", deliver_mock)
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    alert = AsyncMock()
    monkeypatch.setattr(text_commands, "notify_operational_error", alert)
    activity_repo = SimpleNamespace(is_subscription_exempt=AsyncMock(return_value=False))

    await text_commands.gacha_callback(
        query, bot=AsyncMock(), settings=SimpleNamespace(), economy_repo=object(),
        activity_repo=activity_repo, chat_settings=_CHAT_SETTINGS,
    )

    assert query.answers == [("Недостаточно валюты для крутки.", True)]
    alert.assert_not_awaited()
    deliver_mock.assert_not_awaited()
    assert query.message.edit_text_calls == []


@pytest.mark.asyncio
async def test_gacha_callback_disabled_chat_never_charges(monkeypatch: pytest.MonkeyPatch) -> None:
    """GUX-13: a stale purchase button in a chat where gacha was switched off must not reach
    the gacha service at all."""
    query = _DummyQuery(data="gacha:buy:genshin:u1", user_id=1)
    purchase_mock = AsyncMock()
    monkeypatch.setattr(text_commands, "purchase_gacha_pull", purchase_mock)
    monkeypatch.setattr(text_commands, "_is_subscribed_to_channel", AsyncMock(return_value=True))
    disabled_settings = SimpleNamespace(economy_mode="global", gacha_enabled=False)
    activity_repo = SimpleNamespace(is_subscription_exempt=AsyncMock(return_value=False))

    await text_commands.gacha_callback(
        query, bot=AsyncMock(), settings=SimpleNamespace(), economy_repo=object(),
        activity_repo=activity_repo, chat_settings=disabled_settings,
    )

    purchase_mock.assert_not_awaited()
    assert query.answers == [(None, False)]
