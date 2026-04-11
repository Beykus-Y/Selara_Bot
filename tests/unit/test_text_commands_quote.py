from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import BufferedInputFile

from selara.application.dto import CommandIntent
from selara.core.chat_settings import ChatSettings
from selara.presentation.handlers import text_commands


_BASE_CHAT_SETTINGS = ChatSettings(
    top_limit_default=10,
    top_limit_max=50,
    vote_daily_limit=20,
    leaderboard_hybrid_karma_weight=0.7,
    leaderboard_hybrid_activity_weight=0.3,
    leaderboard_7d_days=7,
    leaderboard_week_start_weekday=0,
    leaderboard_week_start_hour=0,
    mafia_night_seconds=90,
    mafia_day_seconds=120,
    mafia_vote_seconds=60,
    mafia_reveal_eliminated_role=True,
    text_commands_enabled=True,
    text_commands_locale="ru",
    actions_18_enabled=True,
    smart_triggers_enabled=False,
    welcome_enabled=True,
    welcome_text="Привет, {user}! Добро пожаловать в {chat}.",
    welcome_button_text="",
    welcome_button_url="",
    goodbye_enabled=False,
    goodbye_text="Пока, {user}.",
    welcome_cleanup_service_messages=True,
    entry_captcha_enabled=False,
    entry_captcha_timeout_seconds=180,
    entry_captcha_kick_on_fail=True,
    custom_rp_enabled=False,
    family_tree_enabled=True,
    titles_enabled=True,
    title_price=50000,
    craft_enabled=True,
    auctions_enabled=True,
    auction_duration_minutes=10,
    auction_min_increment=100,
    economy_enabled=True,
    economy_mode="global",
    economy_tap_cooldown_seconds=45,
    economy_daily_base_reward=120,
    economy_daily_streak_cap=7,
    economy_lottery_ticket_price=150,
    economy_lottery_paid_daily_limit=10,
    economy_transfer_daily_limit=5000,
    economy_transfer_tax_percent=5,
    economy_market_fee_percent=2,
    economy_negative_event_chance_percent=22,
    economy_negative_event_loss_percent=30,
)


class _DummyMessage:
    def __init__(self, *, text: str = "цитировать", reply_to_message=None) -> None:
        self.text = text
        self.chat = SimpleNamespace(type="group", id=-100123, title="Test chat")
        self.from_user = SimpleNamespace(id=1, username="actor", first_name="Actor", last_name=None, is_bot=False)
        self.reply_to_message = reply_to_message
        self.photo_calls: list[tuple[object, dict[str, object]]] = []
        self.text_calls: list[tuple[str, dict[str, object]]] = []

    async def answer_photo(self, photo, **kwargs) -> None:
        self.photo_calls.append((photo, kwargs))

    async def answer(self, text: str, **kwargs) -> None:
        self.text_calls.append((text, kwargs))


@pytest.mark.asyncio
async def test_send_quote_card_requires_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyMessage(reply_to_message=None)
    quiet_answer = AsyncMock()

    monkeypatch.setattr(text_commands, "_answer_quiet", quiet_answer)

    await text_commands._send_quote_card(
        message,
        bot=SimpleNamespace(),
        settings=SimpleNamespace(bot_timezone="UTC"),
    )

    quiet_answer.assert_awaited_once()
    assert "цитировать" in quiet_answer.await_args.args[1]
    assert message.photo_calls == []


@pytest.mark.asyncio
async def test_send_quote_card_renders_reply_message_to_photo(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = SimpleNamespace(
        text="в камине в 6 утра..",
        caption=None,
        date=datetime(2026, 4, 11, 15, 29, tzinfo=timezone.utc),
        from_user=SimpleNamespace(
            id=77,
            username="celestiana",
            first_name="Celestiana",
            last_name=None,
            is_bot=False,
        ),
    )
    message = _DummyMessage(reply_to_message=reply)

    monkeypatch.setattr(text_commands, "_download_telegram_avatar", AsyncMock(return_value=b"avatar"))
    monkeypatch.setattr(text_commands, "build_quote_card", lambda **_kwargs: b"rendered-image")

    await text_commands._send_quote_card(
        message,
        bot=SimpleNamespace(),
        settings=SimpleNamespace(bot_timezone="UTC"),
    )

    assert message.text_calls == []
    assert len(message.photo_calls) == 1
    photo, kwargs = message.photo_calls[0]
    assert isinstance(photo, BufferedInputFile)
    assert photo.filename == "quote.png"
    assert kwargs["disable_notification"] is True


@pytest.mark.asyncio
async def test_text_commands_handler_routes_quote_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyMessage()
    activity_repo = SimpleNamespace(
        get_chat_alias_mode=AsyncMock(return_value="both"),
        list_chat_aliases=AsyncMock(return_value=[]),
    )
    quote_handler = AsyncMock()

    monkeypatch.setattr(
        text_commands,
        "resolve_text_command",
        lambda *_args, **_kwargs: CommandIntent(name="quote"),
    )
    monkeypatch.setattr(text_commands, "_enforce_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "_handle_command_rank_phrase", AsyncMock(return_value=False))
    monkeypatch.setattr(text_commands, "_send_quote_card", quote_handler)

    await text_commands.text_commands_handler(
        message,
        activity_repo=activity_repo,
        economy_repo=object(),
        bot=object(),
        settings=SimpleNamespace(bot_timezone="UTC", supported_chat_types={"group", "supergroup"}),
        chat_settings=replace(_BASE_CHAT_SETTINGS),
        session_factory=object(),
    )

    quote_handler.assert_awaited_once()
    assert quote_handler.await_args.args[0] is message
