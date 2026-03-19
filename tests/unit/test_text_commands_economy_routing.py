from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
    smart_triggers_enabled=True,
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
    custom_rp_enabled=True,
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
    def __init__(self, text: str) -> None:
        self.text = text
        self.chat = SimpleNamespace(type="group", id=-100123, title="Test chat")
        self.from_user = SimpleNamespace(id=1, username="actor", first_name="Actor", last_name=None, is_bot=False)
        self.reply_to_message = None
        self.answers: list[tuple[str, dict[str, object]]] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append((text, kwargs))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent_name", "handler_attr"),
    [
        ("farm", "economy_farm_command"),
        ("shop", "economy_shop_command"),
        ("inventory", "economy_inventory_command"),
        ("craft", "economy_craft_command"),
        ("lottery", "economy_lottery_command"),
        ("market", "economy_market_command"),
        ("pay", "economy_pay_command"),
        ("growth", "economy_growth_command"),
        ("growth_action", "economy_growth_command"),
    ],
)
async def test_text_commands_handler_passes_bot_to_economy_routes(
    monkeypatch: pytest.MonkeyPatch,
    intent_name: str,
    handler_attr: str,
) -> None:
    message = _DummyMessage(text=f"{intent_name} сыр")
    activity_repo = SimpleNamespace(
        get_chat_alias_mode=AsyncMock(return_value="both"),
        list_chat_aliases=AsyncMock(return_value=[]),
    )
    economy_repo = object()
    bot = object()
    session_factory = object()
    settings = SimpleNamespace(supported_chat_types={"group", "supergroup"})
    target_handler = AsyncMock()

    monkeypatch.setattr(
        text_commands,
        "resolve_text_command",
        lambda *_args, **_kwargs: CommandIntent(name=intent_name, args={"raw_args": "сыр"}),
    )
    monkeypatch.setattr(text_commands, "_enforce_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "_handle_command_rank_phrase", AsyncMock(return_value=False))
    monkeypatch.setattr(text_commands, handler_attr, target_handler)
    if intent_name == "growth_action":
        monkeypatch.setattr(
            text_commands,
            "resolve_text_command",
            lambda *_args, **_kwargs: CommandIntent(name=intent_name),
        )

    await text_commands.text_commands_handler(
        message,
        activity_repo=activity_repo,
        economy_repo=economy_repo,
        bot=bot,
        settings=settings,
        chat_settings=replace(_BASE_CHAT_SETTINGS, custom_rp_enabled=False, smart_triggers_enabled=False),
        session_factory=session_factory,
    )

    target_handler.assert_awaited_once()
    assert target_handler.await_args.kwargs["bot"] is bot


@pytest.mark.asyncio
async def test_text_commands_handler_silently_skips_disabled_standard_trigger_for_alias_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _DummyMessage(text="кто я")
    activity_repo = SimpleNamespace(
        get_chat_alias_mode=AsyncMock(return_value="aliases_if_exists"),
        list_chat_aliases=AsyncMock(
            return_value=[
                SimpleNamespace(
                    alias_text_norm="мой профиль",
                    command_key="me",
                    source_trigger_norm="кто я",
                )
            ]
        ),
    )
    quiet_answer = AsyncMock()

    monkeypatch.setattr(text_commands, "_answer_quiet", quiet_answer)
    monkeypatch.setattr(text_commands, "_handle_command_rank_phrase", AsyncMock(return_value=False))

    await text_commands.text_commands_handler(
        message,
        activity_repo=activity_repo,
        economy_repo=object(),
        bot=object(),
        settings=SimpleNamespace(supported_chat_types={"group", "supergroup"}),
        chat_settings=replace(_BASE_CHAT_SETTINGS, custom_rp_enabled=False, smart_triggers_enabled=False),
        session_factory=object(),
    )

    quiet_answer.assert_not_awaited()
    assert message.answers == []


@pytest.mark.asyncio
async def test_text_commands_handler_routes_chat_gate_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _DummyMessage(text="+антирейд 5")
    activity_repo = SimpleNamespace(
        get_chat_alias_mode=AsyncMock(return_value="both"),
        list_chat_aliases=AsyncMock(return_value=[]),
    )
    gate_handler = AsyncMock()
    bot = object()

    monkeypatch.setattr(
        text_commands,
        "resolve_text_command",
        lambda *_args, **_kwargs: CommandIntent(name="antiraid_on", args={"raw_args": "5"}),
    )
    monkeypatch.setattr(text_commands, "_enforce_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "_handle_command_rank_phrase", AsyncMock(return_value=False))
    monkeypatch.setattr(text_commands, "manage_chat_gate_command", gate_handler)

    await text_commands.text_commands_handler(
        message,
        activity_repo=activity_repo,
        economy_repo=object(),
        bot=bot,
        settings=SimpleNamespace(supported_chat_types={"group", "supergroup"}),
        chat_settings=replace(_BASE_CHAT_SETTINGS, custom_rp_enabled=False, smart_triggers_enabled=False),
        session_factory=object(),
    )

    gate_handler.assert_awaited_once()
    assert gate_handler.await_args.kwargs["bot"] is bot
    assert gate_handler.await_args.kwargs["command_key"] == "antiraid_on"
    assert gate_handler.await_args.kwargs["raw_args"] == "5"
