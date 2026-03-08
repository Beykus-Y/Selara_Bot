from __future__ import annotations

from dataclasses import replace

import pytest

from selara.core.chat_settings import ChatSettings
from selara.presentation.handlers.economy import is_growth_action_allowed


def _chat_settings(*, actions_18_enabled: bool) -> ChatSettings:
    return ChatSettings(
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
        actions_18_enabled=actions_18_enabled,
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


class _FakeEconomyRepo:
    def __init__(self, context_chat_id: int | None) -> None:
        self._context_chat_id = context_chat_id

    async def get_private_chat_context(self, *, user_id: int) -> int | None:
        return self._context_chat_id


class _FakeActivityRepo:
    def __init__(self, chat_settings_map: dict[int, ChatSettings]) -> None:
        self._chat_settings_map = chat_settings_map

    async def get_chat_settings(self, *, chat_id: int) -> ChatSettings | None:
        return self._chat_settings_map.get(chat_id)


@pytest.mark.asyncio
async def test_growth_action_gate_group_uses_current_chat_settings() -> None:
    allowed = await is_growth_action_allowed(
        economy_mode="global",
        chat_id=-1001,
        user_id=10,
        chat_settings=_chat_settings(actions_18_enabled=False),
        activity_repo=_FakeActivityRepo({}),
        economy_repo=_FakeEconomyRepo(None),
        settings=object(),
    )
    assert allowed is False


@pytest.mark.asyncio
async def test_growth_action_gate_private_global_uses_private_chat_defaults() -> None:
    allowed = await is_growth_action_allowed(
        economy_mode="global",
        chat_id=None,
        user_id=10,
        chat_settings=_chat_settings(actions_18_enabled=True),
        activity_repo=_FakeActivityRepo({}),
        economy_repo=_FakeEconomyRepo(None),
        settings=object(),
    )
    assert allowed is True


@pytest.mark.asyncio
async def test_growth_action_gate_private_local_uses_selected_group_setting() -> None:
    base = _chat_settings(actions_18_enabled=True)
    group_settings = replace(base, actions_18_enabled=False)
    allowed = await is_growth_action_allowed(
        economy_mode="local",
        chat_id=None,
        user_id=10,
        chat_settings=base,
        activity_repo=_FakeActivityRepo({-100777: group_settings}),
        economy_repo=_FakeEconomyRepo(-100777),
        settings=object(),
    )
    assert allowed is False
