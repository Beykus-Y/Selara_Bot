"""Adversarial tests: rate limiting / cost DoS on the LLM admin-assistant path
(`?`/`??` prefix, llm_admin.py).

Unlike the fully-open voice/STT path, this one IS gated by has_permission(...,
permission="moderate_users") - so the attacker population is smaller (must be at
least junior_admin). But within that population there is still no cooldown at all:
- No per-admin/per-chat throttle on invoking `?`/`??`.
- Each single invocation can trigger up to _MAX_TOOL_ROUNDS=8 round-trips to the paid
  chat completion API before giving up (tool-calling loop in _handle).
- Every invocation additionally fires a "DM summary" chat_simple call
  (_send_dm_summary) and, when in ??-context mode, a context-compression summarize()
  call once the threshold is hit - so a single user message can fan out into up to
  ~10 billed LLM calls, repeatable with no cooldown.

This test proves the amplification factor mechanically (a maximally adversarial LLM
response that always requests another tool round drives chat_with_tools to the full
_MAX_TOOL_ROUNDS ceiling for one single Telegram message) and confirms there is no
cooldown gate preventing an admin from immediately repeating this.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message

from selara.core.chat_settings import ChatSettings
import selara.presentation.handlers.llm_admin as llm_admin_module
from selara.presentation.handlers.llm_admin import _handle


def _chat_settings() -> ChatSettings:
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
        actions_18_enabled=True,
        smart_triggers_enabled=True,
        welcome_enabled=True,
        welcome_text="Привет, {user}!",
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
        llm_enabled=True,
        llm_context_threshold=9999,
    )


def _admin_message(text: str) -> AsyncMock:
    message = AsyncMock(spec=Message)
    message.chat = SimpleNamespace(id=-100999, type="group", title="Chat")
    message.from_user = SimpleNamespace(
        id=111, username="admin", first_name="Admin", last_name=None, is_bot=False
    )
    message.text = text
    thinking_msg = AsyncMock()
    thinking_msg.edit_text = AsyncMock()
    message.reply = AsyncMock(return_value=thinking_msg)
    return message


@pytest.mark.asyncio
async def test_single_admin_message_can_drive_max_tool_rounds_billed_calls():
    """An adversarial (or simply chatty) LLM response that keeps requesting another
    tool call drives the loop to _MAX_TOOL_ROUNDS billed chat_with_tools calls for a
    SINGLE incoming Telegram message - proving the amplification factor is real and
    unbounded by anything except the hardcoded round cap (no cost/time budget check)."""
    call_count = 0

    async def fake_chat_with_tools(*, messages, tools):
        nonlocal call_count
        call_count += 1
        # Always return a tool_call for a harmless, always-available read tool so the
        # loop keeps going without hitting any of the moderation-target permission
        # gates (isolating the round-amplification behavior from permission checks).
        tool_call = SimpleNamespace(
            id=f"call-{call_count}",
            function=SimpleNamespace(name="get_chat_stats", arguments="{}"),
        )
        message = SimpleNamespace(
            content=None,
            tool_calls=[tool_call],
            model_dump=lambda exclude_none=True: {
                "role": "assistant",
                "tool_calls": [{"id": tool_call.id, "type": "function",
                                 "function": {"name": "get_chat_stats", "arguments": "{}"}}],
            },
        )
        choice = SimpleNamespace(finish_reason="tool_calls", message=message)
        return SimpleNamespace(choices=[choice])

    llm_client = AsyncMock()
    llm_client.chat_with_tools = fake_chat_with_tools
    llm_client.chat_simple = AsyncMock(return_value="summary")

    bot = AsyncMock()
    activity_repo = MagicMock()
    activity_repo._session = MagicMock()
    activity_repo._session.execute = AsyncMock(
        return_value=MagicMock(scalar_one=MagicMock(return_value=0))
    )
    db_session = MagicMock()

    message = _admin_message("? посчитай статистику")

    with patch.object(llm_admin_module, "has_permission", new=AsyncMock(return_value=(True, None, None))), \
         patch.object(llm_admin_module, "LlmRepository") as mock_repo_cls, \
         patch.object(llm_admin_module, "load_context", new=AsyncMock(return_value=SimpleNamespace(messages=[]))), \
         patch.object(llm_admin_module, "save_interaction", new=AsyncMock()), \
         patch.object(llm_admin_module, "maybe_compress", new=AsyncMock()):
        repo_mock = MagicMock()
        repo_mock.get_last_user_message_at = AsyncMock(return_value=None)
        mock_repo_cls.return_value = repo_mock

        await _handle(
            message, bot, activity_repo, _chat_settings(), llm_client, db_session, with_context=False
        )

    # _MAX_TOOL_ROUNDS = 8 in llm_admin.py - one Telegram message => up to 8 billed
    # chat completion calls, plus one more for the DM summary. No budget/cost check
    # short-circuits this early within a single invocation (cross-invocation
    # repeats are now throttled by the #3 cooldown fix -- see next test).
    assert call_count == llm_admin_module._MAX_TOOL_ROUNDS


@pytest.mark.asyncio
async def test_llm_cooldown_throttles_immediate_repeat_invocation_by_same_admin():
    """#3 fix: repeated `?`-prefixed messages sent back-to-back by the same admin
    in the same chat are now throttled by llm_cooldown_seconds -- only the first
    within the window reaches the billed LLM call."""
    from selara.core.config import Settings

    call_count = 0

    async def fake_chat_with_tools(*, messages, tools):
        nonlocal call_count
        call_count += 1
        message = SimpleNamespace(
            content="ok",
            tool_calls=None,
            model_dump=lambda exclude_none=True: {"role": "assistant", "content": "ok"},
        )
        choice = SimpleNamespace(finish_reason="stop", message=message)
        return SimpleNamespace(choices=[choice])

    llm_client = AsyncMock()
    llm_client.chat_with_tools = fake_chat_with_tools
    llm_client.chat_simple = AsyncMock(return_value="summary")

    bot = AsyncMock()
    activity_repo = MagicMock()
    db_session = AsyncMock()
    settings = Settings(llm_cooldown_seconds=60.0)

    last_message_at = None

    async def fake_get_last_user_message_at(*, chat_id, admin_user_id):
        return last_message_at

    async def fake_add_context_message(*, chat_id, role, content, is_context, admin_user_id, tool_call_id=None):
        nonlocal last_message_at
        if role == "user":
            last_message_at = datetime.now(timezone.utc)

    llm_repo_instance = MagicMock()
    llm_repo_instance.get_last_user_message_at = fake_get_last_user_message_at
    llm_repo_instance.add_context_message = fake_add_context_message

    with patch.object(llm_admin_module, "has_permission", new=AsyncMock(return_value=(True, None, None))), \
         patch.object(llm_admin_module, "LlmRepository", return_value=llm_repo_instance), \
         patch.object(llm_admin_module, "load_context", new=AsyncMock(return_value=SimpleNamespace(messages=[]))), \
         patch.object(llm_admin_module, "maybe_compress", new=AsyncMock()):

        for _ in range(5):
            message = _admin_message("? привет")
            await _handle(
                message, bot, activity_repo, _chat_settings(), llm_client, db_session, with_context=False,
                settings=settings,
            )

    # 5 rapid-fire invocations by the same admin -> only the first reaches
    # the billed LLM call; the rest are throttled by the cooldown.
    assert call_count == 1
