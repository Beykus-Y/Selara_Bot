"""Regression tests for finding #34 in docs/STT_LLM_AUDIT_TODO.md:

moderate_users used to be the single all-or-nothing gate for both invoking
the `?`/`??` assistant AND for its ability to act -- no way to let a
trusted member query stats/history via the assistant without also trusting
them to have it ban people.

Fix: a new PERM_USE_LLM_READONLY permission lets an actor invoke the
assistant without moderate_users, but every mutating tool (already gated
via _moderation_target_error's moderate_users check, or set_rank's
manage_roles check) still requires the actor to actually hold
moderate_users -- add_to_glossary, which previously had no permission
check of its own at all, now requires it too, since a read-only-tier
actor being able to write persistent, LLM-re-read glossary content would
reopen the #2 injection surface to a wider population than before.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message

from selara.core.chat_settings import ChatSettings
from selara.core.roles import PERM_MODERATE_USERS, PERM_USE_LLM_READONLY
from selara.domain.entities import ChatRoleDefinition
from selara.infrastructure.llm.tools import ToolCall, execute_tool
from selara.presentation.handlers.llm_admin import _handle


def _role(code: str, rank: int, *permissions: str) -> ChatRoleDefinition:
    return ChatRoleDefinition(
        chat_id=-100123, role_code=code, title_ru=code, rank=rank,
        permissions=permissions, is_system=True, template_key=code,
    )


@pytest.fixture
def chat_settings():
    return ChatSettings(
        top_limit_default=10, top_limit_max=50, vote_daily_limit=20,
        leaderboard_hybrid_karma_weight=0.7, leaderboard_hybrid_activity_weight=0.3,
        leaderboard_7d_days=7, leaderboard_week_start_weekday=0, leaderboard_week_start_hour=0,
        mafia_night_seconds=90, mafia_day_seconds=120, mafia_vote_seconds=60,
        mafia_reveal_eliminated_role=True, text_commands_enabled=True, text_commands_locale="ru",
        actions_18_enabled=True, smart_triggers_enabled=True, welcome_enabled=True,
        welcome_text="Привет, {user}!", welcome_button_text="", welcome_button_url="",
        goodbye_enabled=False, goodbye_text="Пока, {user}.", welcome_cleanup_service_messages=True,
        entry_captcha_enabled=False, entry_captcha_timeout_seconds=180, entry_captcha_kick_on_fail=True,
        custom_rp_enabled=True, family_tree_enabled=True, titles_enabled=True, title_price=50000,
        craft_enabled=True, auctions_enabled=True, auction_duration_minutes=10, auction_min_increment=100,
        economy_enabled=True, economy_mode="global", economy_tap_cooldown_seconds=45,
        economy_daily_base_reward=120, economy_daily_streak_cap=7, economy_lottery_ticket_price=150,
        economy_lottery_paid_daily_limit=10, economy_transfer_daily_limit=5000,
        economy_transfer_tax_percent=5, economy_market_fee_percent=2,
        economy_negative_event_chance_percent=22, economy_negative_event_loss_percent=30,
        llm_enabled=True, llm_context_threshold=9999,
    )


def _admin_message(text: str) -> AsyncMock:
    message = AsyncMock(spec=Message)
    message.chat = SimpleNamespace(id=-100123, type="group", title="Chat")
    message.from_user = SimpleNamespace(id=111, username="member", first_name="Member", last_name=None, is_bot=False)
    message.text = text
    thinking_msg = AsyncMock()
    thinking_msg.edit_text = AsyncMock()
    message.reply = AsyncMock(return_value=thinking_msg)
    return message


@pytest.mark.asyncio
async def test_actor_with_only_readonly_permission_can_invoke_the_assistant(chat_settings):
    import selara.presentation.handlers.llm_admin as llm_admin_module

    async def fake_has_permission(*_, permission, **__):
        return (permission == PERM_USE_LLM_READONLY, None, None)

    llm_client = AsyncMock()

    async def fake_chat_with_tools(*, messages, tools):
        message = SimpleNamespace(
            content="ok", tool_calls=None,
            model_dump=lambda exclude_none=True: {"role": "assistant", "content": "ok"},
        )
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=message)])

    llm_client.chat_with_tools = fake_chat_with_tools
    llm_client.chat_simple = AsyncMock(return_value="summary")

    bot = AsyncMock()
    activity_repo = MagicMock()
    db_session = AsyncMock()
    message = _admin_message("? покажи топ")

    with patch.object(llm_admin_module, "has_permission", new=fake_has_permission), \
         patch.object(llm_admin_module, "LlmRepository") as mock_repo_cls, \
         patch.object(llm_admin_module, "load_context", new=AsyncMock(return_value=SimpleNamespace(messages=[]))), \
         patch.object(llm_admin_module, "save_interaction", new=AsyncMock()), \
         patch.object(llm_admin_module, "maybe_compress", new=AsyncMock()):
        repo_mock = MagicMock()
        repo_mock.get_last_user_message_at = AsyncMock(return_value=None)
        mock_repo_cls.return_value = repo_mock

        await _handle(message, bot, activity_repo, chat_settings, llm_client, db_session, with_context=False)

    message.reply.assert_any_await("⏳ Думаю...")


@pytest.mark.asyncio
async def test_actor_with_neither_permission_is_denied(chat_settings):
    import selara.presentation.handlers.llm_admin as llm_admin_module

    async def fake_has_permission(*_, **__):
        return (False, None, None)

    llm_client = AsyncMock()
    bot = AsyncMock()
    activity_repo = MagicMock()
    db_session = AsyncMock()
    message = _admin_message("? покажи топ")

    with patch.object(llm_admin_module, "has_permission", new=fake_has_permission):
        await _handle(message, bot, activity_repo, chat_settings, llm_client, db_session, with_context=False)

    message.reply.assert_awaited_once()
    denial_text = message.reply.await_args.args[0].lower()
    assert "прав" in denial_text
    # #27: junior_admin's default template does NOT grant moderate_users
    # (only senior_admin+ does) -- the old message named the wrong role.
    assert "junior_admin" not in denial_text
    assert "senior_admin" in denial_text


@pytest.mark.asyncio
async def test_readonly_only_actor_cannot_execute_a_mutating_moderation_tool():
    activity_repo = MagicMock()
    activity_repo.get_effective_role_definition = AsyncMock(
        return_value=_role("custom_readonly", 5, PERM_USE_LLM_READONLY)
    )
    activity_repo.find_chat_user_by_username = AsyncMock(
        return_value=SimpleNamespace(telegram_user_id=222, username="target", first_name="T", last_name=None, is_bot=False)
    )

    result = await execute_tool(
        ToolCall(name="warn_user", arguments={"target": "@target"}, call_id="w-1"),
        chat_snapshot=SimpleNamespace(telegram_chat_id=-100123, chat_type="supergroup", title="Chat"),
        actor_snapshot=SimpleNamespace(telegram_user_id=111, is_bot=False),
        activity_repo=activity_repo,
        llm_repo=MagicMock(),
    )

    assert result.success is False
    assert "прав" in result.result_text.lower()


@pytest.mark.asyncio
async def test_readonly_only_actor_cannot_write_to_glossary():
    activity_repo = MagicMock()
    activity_repo.get_effective_role_definition = AsyncMock(
        return_value=_role("custom_readonly", 5, PERM_USE_LLM_READONLY)
    )
    llm_repo = MagicMock()
    llm_repo.upsert_glossary_term = AsyncMock()

    result = await execute_tool(
        ToolCall(name="add_to_glossary", arguments={"term": "рест", "definition": "def"}, call_id="g-1"),
        chat_snapshot=SimpleNamespace(telegram_chat_id=-100123, chat_type="supergroup", title="Chat"),
        actor_snapshot=SimpleNamespace(telegram_user_id=111, is_bot=False),
        activity_repo=activity_repo,
        llm_repo=llm_repo,
    )

    assert result.success is False
    llm_repo.upsert_glossary_term.assert_not_awaited()


@pytest.mark.asyncio
async def test_moderate_users_actor_can_still_write_to_glossary():
    activity_repo = MagicMock()
    activity_repo.get_effective_role_definition = AsyncMock(
        return_value=_role("senior_admin", 20, PERM_MODERATE_USERS)
    )
    llm_repo = MagicMock()
    llm_repo.list_glossary = AsyncMock(return_value=[])
    llm_repo.upsert_glossary_term = AsyncMock(return_value=SimpleNamespace(term="рест", definition="def"))

    result = await execute_tool(
        ToolCall(name="add_to_glossary", arguments={"term": "рест", "definition": "def"}, call_id="g-2"),
        chat_snapshot=SimpleNamespace(telegram_chat_id=-100123, chat_type="supergroup", title="Chat"),
        actor_snapshot=SimpleNamespace(telegram_user_id=111, is_bot=False),
        activity_repo=activity_repo,
        llm_repo=llm_repo,
    )

    assert result.success is True
