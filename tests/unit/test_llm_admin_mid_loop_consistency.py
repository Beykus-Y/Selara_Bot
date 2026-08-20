"""Regression tests for finding #22 in docs/STT_LLM_AUDIT_TODO.md:

The whole `_handle` tool-calling loop used to run inside one DB transaction
(commit only at the very end, by the outer DBSessionMiddleware), while tool
calls can trigger real, non-transactional Telegram side effects
(bot.ban_chat_member, bot.unban_chat_member) mid-loop. `bot.send_chat_action`
was called every round with no try/except (unlike edit_text, which is
guarded everywhere) -- if it raised on a *later* round, after an earlier
round's ban had already gone through for real, the whole outer transaction
would roll back: the moderation-state row and the audit row (and thus the
rollback button) would vanish, while the user stayed really banned with no
record of who/why.

Fix: (1) guard `send_chat_action` with try/except so a transient status-ping
failure can't blow up the loop, and (2) commit the DB session immediately
after any tool call that produced a durable action row (result.db_action_id
is not None), so a crash on a *later* round can no longer erase an
already-completed action's audit trail.
"""
from __future__ import annotations

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
async def test_send_chat_action_failure_does_not_crash_the_tool_loop():
    """A transient Telegram error on the per-round 'typing' status ping must
    not abort the whole handler -- it's not guarded today, unlike edit_text."""
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
    bot.send_chat_action = AsyncMock(side_effect=RuntimeError("Telegram hiccup"))
    activity_repo = MagicMock()
    db_session = AsyncMock()

    message = _admin_message("? привет")

    with patch.object(llm_admin_module, "has_permission", new=AsyncMock(return_value=(True, None, None))), \
         patch.object(llm_admin_module, "LlmRepository") as mock_repo_cls, \
         patch.object(llm_admin_module, "load_context", new=AsyncMock(return_value=SimpleNamespace(messages=[]))), \
         patch.object(llm_admin_module, "save_interaction", new=AsyncMock()), \
         patch.object(llm_admin_module, "maybe_compress", new=AsyncMock()):
        repo_mock = MagicMock()
        repo_mock.get_last_user_message_at = AsyncMock(return_value=None)
        mock_repo_cls.return_value = repo_mock

        # Must not raise.
        await _handle(
            message, bot, activity_repo, _chat_settings(), llm_client, db_session, with_context=False
        )

    assert call_count == 1
    message.reply.return_value.edit_text.assert_awaited_once()
    assert "ok" in message.reply.return_value.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_completed_moderation_action_is_committed_before_next_round_can_lose_it():
    """After a tool call that produced a durable audit row (db_action_id is
    not None), the DB session must be committed before the loop proceeds to
    the next round -- otherwise a later-round crash rolls back the whole
    outer transaction and silently erases a real, already-executed
    ban/warn/etc with no audit trail left behind (finding #22)."""
    rounds = 0
    commit_calls_seen_before_round: list[int] = []

    async def fake_chat_with_tools(*, messages, tools):
        nonlocal rounds
        rounds += 1
        commit_calls_seen_before_round.append(db_session.commit.await_count)
        if rounds == 1:
            tool_call = SimpleNamespace(
                id="call-1",
                function=SimpleNamespace(name="grant_rest", arguments='{"target": "@bob", "duration_days": 3}'),
            )
            message = SimpleNamespace(
                content=None,
                tool_calls=[tool_call],
                model_dump=lambda exclude_none=True: {
                    "role": "assistant",
                    "tool_calls": [{"id": "call-1", "type": "function",
                                     "function": {"name": "grant_rest", "arguments": "{}"}}],
                },
            )
            choice = SimpleNamespace(finish_reason="tool_calls", message=message)
            return SimpleNamespace(choices=[choice])
        message = SimpleNamespace(
            content="готово",
            tool_calls=None,
            model_dump=lambda exclude_none=True: {"role": "assistant", "content": "готово"},
        )
        choice = SimpleNamespace(finish_reason="stop", message=message)
        return SimpleNamespace(choices=[choice])

    llm_client = AsyncMock()
    llm_client.chat_with_tools = fake_chat_with_tools
    llm_client.chat_simple = AsyncMock(return_value="summary")

    bot = AsyncMock()
    db_session = AsyncMock()
    activity_repo = MagicMock()
    activity_repo.grant_rest = AsyncMock(return_value=SimpleNamespace())

    target = SimpleNamespace(
        telegram_user_id=222, username="bob", first_name="Bob", last_name=None, is_bot=False
    )
    activity_repo.find_chat_user_by_username = AsyncMock(return_value=target)

    async def _role_for(*, chat_id, user_id):
        if user_id == 111:
            return SimpleNamespace(role_code="senior_admin", rank=20, permissions=["moderate_users"])
        return SimpleNamespace(role_code="participant", rank=0, permissions=[])

    activity_repo.get_effective_role_definition = AsyncMock(side_effect=_role_for)

    action_row = SimpleNamespace(id=555)
    llm_repo_instance = MagicMock()
    llm_repo_instance.add_admin_action = AsyncMock(return_value=action_row)
    llm_repo_instance.get_last_user_message_at = AsyncMock(return_value=None)

    message = _admin_message("? выдай рест бобу")

    with patch.object(llm_admin_module, "has_permission", new=AsyncMock(return_value=(True, None, None))), \
         patch.object(llm_admin_module, "LlmRepository", return_value=llm_repo_instance), \
         patch.object(llm_admin_module, "load_context", new=AsyncMock(return_value=SimpleNamespace(messages=[]))), \
         patch.object(llm_admin_module, "save_interaction", new=AsyncMock()), \
         patch.object(llm_admin_module, "maybe_compress", new=AsyncMock()):
        await _handle(
            message, bot, activity_repo, _chat_settings(), llm_client, db_session, with_context=False
        )

    assert rounds == 2
    # By the time round 2 starts, the round-1 action must already be committed.
    assert commit_calls_seen_before_round[1] >= 1, (
        "round 1's grant_rest produced a durable audit row but the DB session was not "
        "committed before round 2 started -- a crash in round 2 would roll it back "
        "along with the real, already-completed action"
    )
