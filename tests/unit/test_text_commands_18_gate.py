from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from selara.core.chat_settings import ChatSettings
from selara.presentation.handlers.text_commands import _send_social_action


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


def _chat_settings(*, actions_18_enabled: bool) -> ChatSettings:
    return replace(_BASE_CHAT_SETTINGS, actions_18_enabled=actions_18_enabled)


class _FakeActivityRepo:
    async def get_chat_display_name(self, *, chat_id: int, user_id: int) -> None:
        return None


class _DummyMessage:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(type="group", id=-100123)
        self.from_user = SimpleNamespace(id=1, username="actor", first_name="Actor", last_name=None, is_bot=False)
        self.reply_to_message = SimpleNamespace(
            from_user=SimpleNamespace(id=2, username="target", first_name="Target", last_name=None, is_bot=False)
        )
        self.answers: list[tuple[str, dict[str, object]]] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append((text, kwargs))


@pytest.mark.asyncio
@pytest.mark.parametrize("action_key", ["fuck", "seduce", "makeout", "night"])
async def test_send_social_action_blocks_explicit_actions_when_18_disabled(action_key: str) -> None:
    message = _DummyMessage()

    await _send_social_action(
        message,
        _FakeActivityRepo(),
        _chat_settings(actions_18_enabled=False),
        action_key=action_key,
    )

    assert len(message.answers) == 1
    assert message.answers[0][0].startswith("18+ действия отключены")
    assert message.answers[0][1]["parse_mode"] == "HTML"


@pytest.mark.asyncio
@pytest.mark.parametrize("action_key", ["fuck", "seduce", "makeout", "night"])
async def test_send_social_action_allows_explicit_actions_when_18_enabled(action_key: str) -> None:
    message = _DummyMessage()

    await _send_social_action(
        message,
        _FakeActivityRepo(),
        _chat_settings(actions_18_enabled=True),
        action_key=action_key,
    )

    assert len(message.answers) == 1
    assert "18+ действия отключены" not in message.answers[0][0]
    assert message.answers[0][1]["parse_mode"] == "HTML"
