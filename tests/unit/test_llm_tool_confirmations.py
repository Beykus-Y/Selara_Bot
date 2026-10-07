"""Tests for #51: high-impact LLM moderation tools (ban_user, set_rank) run
only after an explicit admin confirmation.

Acceptance matrix (from the issue):
- a high-impact mutating tool never touches a side effect on the model's
  tool call itself -- it is parked as a pending confirmation bound to the
  actor/chat/exact payload with a TTL;
- the model cannot confirm its own pending action (repeating the call
  dedupes onto the same pending row and never executes);
- the stored payload cannot be quietly changed between preview and confirm
  (payload-hash check; confirm executes the stored arguments verbatim);
- expiry, double-click (idempotent claim), actor mismatch and permissions
  revoked between preview and confirm all refuse to execute.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.domain.entities import ChatRoleDefinition, ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.llm_repository import LlmRepository
from selara.infrastructure.db.models import ChatModel, UserModel
from selara.infrastructure.llm.tools import (
    DIRECT_ADMIN_CONFIRMATION,
    TOOL_CONFIRMATION_TTL_SECONDS,
    ToolCall,
    ToolConfirmationGrant,
    ToolResult,
    _confirmation_payload_hash,
    build_rollback_call,
    execute_tool,
)

CHAT_ID = -100123
ACTOR_ID = 111
TARGET_ID = 222
OTHER_ADMIN_ID = 333


def _role(code: str, rank: int, *permissions: str) -> ChatRoleDefinition:
    return ChatRoleDefinition(
        chat_id=CHAT_ID, role_code=code, title_ru=code, rank=rank,
        permissions=permissions, is_system=True, template_key=code,
    )


def _target_user() -> SimpleNamespace:
    return SimpleNamespace(
        telegram_user_id=TARGET_ID, username="target_user", first_name="Target",
        last_name=None, is_bot=False, chat_display_name="Target",
    )


def _activity_repo(*, actor_role: ChatRoleDefinition | None = None) -> AsyncMock:
    repo = AsyncMock()
    repo.find_chat_user_by_username = AsyncMock(return_value=_target_user())
    repo.apply_moderation_action = AsyncMock(
        return_value=SimpleNamespace(
            state=SimpleNamespace(warn_count=0, pending_preds=0),
            auto_ban_triggered=False, auto_warns_added=0,
        )
    )
    repo.get_bot_role = AsyncMock(return_value=None)
    repo.set_bot_role = AsyncMock()
    repo.get_chat_role_definition = AsyncMock(
        return_value=_role("junior_admin", 10, "moderate_users")
    )
    # `_resolve_target`'s digit-id branch (a rollback call passes a user_id).
    target_row = MagicMock()
    target_row.scalar_one_or_none.return_value = SimpleNamespace(
        telegram_user_id=TARGET_ID, username="target_user",
        first_name="Target", last_name=None, is_bot=False,
    )
    repo._session = SimpleNamespace(execute=AsyncMock(return_value=target_row))

    async def _role_by_user(*, chat_id: int, user_id: int):
        if user_id == ACTOR_ID:
            return actor_role or _role("senior_admin", 20, "moderate_users")
        return _role("participant", 0)

    repo.get_effective_role_definition = AsyncMock(side_effect=_role_by_user)
    return repo


async def _session_with_repo():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()
    session.add(ChatModel(telegram_chat_id=CHAT_ID, type="supergroup", title="Chat"))
    session.add(UserModel(telegram_user_id=ACTOR_ID, username="actor", is_bot=False))
    session.add(UserModel(telegram_user_id=TARGET_ID, username="target_user", is_bot=False))
    session.add(UserModel(telegram_user_id=OTHER_ADMIN_ID, username="other", is_bot=False))
    await session.commit()
    return engine, session, LlmRepository(session)


def _chat_snapshot() -> ChatSnapshot:
    return ChatSnapshot(telegram_chat_id=CHAT_ID, chat_type="supergroup", title="Chat")


def _actor(user_id: int = ACTOR_ID) -> UserSnapshot:
    return UserSnapshot(
        telegram_user_id=user_id, username="actor", first_name="Actor",
        last_name=None, is_bot=False,
    )


def _ban_call(call_id: str = "ban-1") -> ToolCall:
    return ToolCall(name="ban_user", arguments={"target": "@target_user", "reason": "abuse"}, call_id=call_id)


def _set_rank_call(call_id: str = "rank-1") -> ToolCall:
    return ToolCall(name="set_rank", arguments={"target": "@target_user", "rank": "junior_admin"}, call_id=call_id)


@pytest.mark.asyncio
async def test_ban_user_preview_persists_pending_row_without_side_effect():
    engine, session, llm_repo = await _session_with_repo()
    try:
        activity_repo = _activity_repo()
        bot = AsyncMock()

        result = await execute_tool(
            _ban_call(),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=bot,
        )

        assert result.success is True
        assert result.pending_confirmation_token
        assert result.db_action_id is None
        activity_repo.apply_moderation_action.assert_not_awaited()
        bot.ban_chat_member.assert_not_awaited()

        row = await llm_repo.get_tool_confirmation(token=result.pending_confirmation_token)
        assert row is not None
        assert row.status == "pending"
        assert row.tool_name == "ban_user"
        assert row.chat_id == CHAT_ID
        assert row.actor_user_id == ACTOR_ID
        assert row.arguments_json == {"target": "@target_user", "reason": "abuse"}
        assert row.payload_hash == _confirmation_payload_hash("ban_user", {"target": "@target_user", "reason": "abuse"})
        assert "Бан" in row.action_description
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_set_rank_preview_persists_pending_row_without_side_effect():
    engine, session, llm_repo = await _session_with_repo()
    try:
        activity_repo = _activity_repo(
            actor_role=_role("co_owner", 30, "moderate_users", "manage_roles")
        )

        result = await execute_tool(
            _set_rank_call(),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo,
        )

        assert result.success is True
        assert result.pending_confirmation_token
        activity_repo.set_bot_role.assert_not_awaited()
        row = await llm_repo.get_tool_confirmation(token=result.pending_confirmation_token)
        assert row is not None
        assert row.tool_name == "set_rank"
        assert row.arguments_json == {"target": "@target_user", "rank": "junior_admin"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_model_cannot_confirm_by_repeating_the_call():
    """Repeating the identical call only returns the SAME pending row
    (dedupe) and never executes; a changed payload gets a NEW pending row --
    there is no tool-level path to approval at all."""
    engine, session, llm_repo = await _session_with_repo()
    try:
        activity_repo = _activity_repo()
        ctx = dict(
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=AsyncMock(),
        )

        first = await execute_tool(_ban_call(), **ctx)
        second = await execute_tool(_ban_call(call_id="ban-2"), **ctx)
        assert first.pending_confirmation_token == second.pending_confirmation_token

        changed = ToolCall(
            name="ban_user", arguments={"target": "@target_user", "reason": "другая причина"}, call_id="ban-3",
        )
        third = await execute_tool(changed, **ctx)
        assert third.pending_confirmation_token not in {None, first.pending_confirmation_token}

        activity_repo.apply_moderation_action.assert_not_awaited()
        # Exactly two distinct pending rows exist for this admin/chat.
        for token in {first.pending_confirmation_token, third.pending_confirmation_token}:
            row = await llm_repo.get_tool_confirmation(token=token)
            assert row is not None and row.status == "pending"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_confirm_grant_executes_side_effect_once():
    engine, session, llm_repo = await _session_with_repo()
    try:
        activity_repo = _activity_repo()
        bot = AsyncMock()
        preview = await execute_tool(
            _ban_call(),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=bot,
        )
        token = preview.pending_confirmation_token

        confirmed = await execute_tool(
            _ban_call(call_id="confirm-1"),
            confirmation=ToolConfirmationGrant(token=token),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=bot,
        )

        assert confirmed.success is True
        assert confirmed.pending_confirmation_token is None
        activity_repo.apply_moderation_action.assert_awaited_once()
        bot.ban_chat_member.assert_awaited_once_with(chat_id=CHAT_ID, user_id=TARGET_ID)

        row = await llm_repo.get_tool_confirmation(token=token)
        assert row.status == "confirmed"
        assert row.resolved_by_user_id == ACTOR_ID
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_double_click_confirms_only_once():
    engine, session, llm_repo = await _session_with_repo()
    try:
        activity_repo = _activity_repo()
        bot = AsyncMock()
        preview = await execute_tool(
            _ban_call(),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=bot,
        )
        token = preview.pending_confirmation_token
        confirm_ctx = dict(
            confirmation=ToolConfirmationGrant(token=token),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=bot,
        )

        first = await execute_tool(_ban_call(call_id="c1"), **confirm_ctx)
        second = await execute_tool(_ban_call(call_id="c2"), **confirm_ctx)

        assert first.success is True
        assert second.success is False
        assert "уже обработано" in second.result_text.lower()
        activity_repo.apply_moderation_action.assert_awaited_once()
        bot.ban_chat_member.assert_awaited_once()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_confirmation_is_refused_and_marked_expired():
    engine, session, llm_repo = await _session_with_repo()
    try:
        activity_repo = _activity_repo()
        preview = await execute_tool(
            _ban_call(),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=AsyncMock(),
        )
        token = preview.pending_confirmation_token
        row = await llm_repo.get_tool_confirmation(token=token)
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.add(row)
        await session.flush()

        result = await execute_tool(
            _ban_call(call_id="late"),
            confirmation=ToolConfirmationGrant(token=token),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=AsyncMock(),
        )

        assert result.success is False
        assert "истёк" in result.result_text.lower()
        activity_repo.apply_moderation_action.assert_not_awaited()
        refreshed = await llm_repo.get_tool_confirmation(token=token)
        assert refreshed.status == "expired"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_other_admin_cannot_confirm_the_pending_action():
    engine, session, llm_repo = await _session_with_repo()
    try:
        activity_repo = _activity_repo()
        preview = await execute_tool(
            _ban_call(),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=AsyncMock(),
        )
        token = preview.pending_confirmation_token

        result = await execute_tool(
            _ban_call(call_id="stranger"),
            confirmation=ToolConfirmationGrant(token=token),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(user_id=OTHER_ADMIN_ID),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=AsyncMock(),
        )

        assert result.success is False
        assert "не соответствует" in result.result_text
        activity_repo.apply_moderation_action.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tampered_preview_payload_is_refused():
    """The confirm click executes the STORED payload only: the callback builds
    the call from arguments_json, and the gate re-hashes what it is about to
    run against the hash stored at preview time -- a quietly edited payload
    (or a call with different arguments riding a stolen token) is refused."""
    engine, session, llm_repo = await _session_with_repo()
    try:
        from sqlalchemy import update

        from selara.infrastructure.db.models import LlmToolConfirmationModel

        activity_repo = _activity_repo()
        preview = await execute_tool(
            _ban_call(),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=AsyncMock(),
        )
        token = preview.pending_confirmation_token
        # Someone quietly edits the stored payload after the preview; the
        # stored hash column no longer matches the stored arguments.
        await session.execute(
            update(LlmToolConfirmationModel)
            .where(LlmToolConfirmationModel.token == token)
            .values(arguments_json={"target": "@target_user", "reason": "подменённая причина"})
        )
        await session.flush()

        # The confirm callback would build the call from the stored arguments.
        tampered_call = ToolCall(
            name="ban_user", arguments={"target": "@target_user", "reason": "подменённая причина"}, call_id="tampered",
        )
        result = await execute_tool(
            tampered_call,
            confirmation=ToolConfirmationGrant(token=token),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=AsyncMock(),
        )

        assert result.success is False
        assert "не соответствует" in result.result_text
        activity_repo.apply_moderation_action.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_revoked_permission_between_preview_and_confirm_blocks_execution():
    """Authorization is re-run at confirm time: losing moderate_users after
    the preview means the click must not fire the ban."""
    engine, session, llm_repo = await _session_with_repo()
    try:
        activity_repo = _activity_repo()
        preview = await execute_tool(
            _ban_call(),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=AsyncMock(),
        )
        token = preview.pending_confirmation_token

        # Permission stripped after the preview.
        async def _no_moderation(*, chat_id: int, user_id: int):
            if user_id == ACTOR_ID:
                return _role("junior_admin", 5)
            return _role("participant", 0)

        activity_repo.get_effective_role_definition = AsyncMock(side_effect=_no_moderation)

        result = await execute_tool(
            _ban_call(call_id="revoked"),
            confirmation=ToolConfirmationGrant(token=token),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=AsyncMock(),
        )

        assert result.success is False
        assert "прав" in result.result_text.lower()
        activity_repo.apply_moderation_action.assert_not_awaited()
        # Nothing happened, so the confirmation is re-armed for a retry...
        row = await llm_repo.get_tool_confirmation(token=token)
        assert row.status == "pending"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rejected_confirmation_cannot_be_confirmed_afterwards():
    engine, session, llm_repo = await _session_with_repo()
    try:
        activity_repo = _activity_repo()
        preview = await execute_tool(
            _ban_call(),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=AsyncMock(),
        )
        token = preview.pending_confirmation_token

        rejected = await llm_repo.claim_tool_confirmation(
            token=token, resolved_by_user_id=ACTOR_ID, new_status="rejected",
        )
        assert rejected is True

        result = await execute_tool(
            _ban_call(call_id="after-reject"),
            confirmation=ToolConfirmationGrant(token=token),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo, bot=AsyncMock(),
        )

        assert result.success is False
        assert "уже обработано" in result.result_text.lower()
        activity_repo.apply_moderation_action.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_set_rank_rollback_click_bypasses_the_confirmation_gate():
    """The DM rollback click IS the explicit admin approval: a set_rank undo
    executes immediately instead of being parked as another pending
    confirmation (the regular authorization checks still run)."""
    engine, session, llm_repo = await _session_with_repo()
    try:
        activity_repo = _activity_repo(
            actor_role=_role("owner", 40, "moderate_users", "manage_roles")
        )
        call = build_rollback_call(
            {"tool": "set_rank", "target_user_id": TARGET_ID, "previous_rank": "participant", "chat_id": CHAT_ID},
            call_id="rollback:1",
        )

        result = await execute_tool(
            call,
            confirmation=DIRECT_ADMIN_CONFIRMATION,
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo,
        )

        assert result.success is True
        activity_repo.set_bot_role.assert_awaited_once()
        pending = await llm_repo.find_active_tool_confirmation(
            chat_id=CHAT_ID, actor_user_id=ACTOR_ID,
            tool_name="set_rank",
            payload_hash=_confirmation_payload_hash(call.name, call.arguments),
        )
        assert pending is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_low_impact_tool_still_executes_immediately():
    engine, session, llm_repo = await _session_with_repo()
    try:
        activity_repo = _activity_repo()

        result = await execute_tool(
            ToolCall(name="warn_user", arguments={"target": "@target_user", "reason": "спам"}, call_id="w-1"),
            chat_snapshot=_chat_snapshot(), actor_snapshot=_actor(),
            activity_repo=activity_repo, llm_repo=llm_repo,
        )

        assert result.success is True
        assert result.pending_confirmation_token is None
        assert result.db_action_id is not None
        activity_repo.apply_moderation_action.assert_awaited_once()
        pending = await llm_repo.find_active_tool_confirmation(
            chat_id=CHAT_ID, actor_user_id=ACTOR_ID, tool_name="warn_user", payload_hash="x",
        )
        assert pending is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_confirmation_request_message_carries_preview_and_buttons():
    from selara.presentation.handlers.llm_admin import _send_confirmation_request

    message = AsyncMock()
    pending = ToolResult(
        call_id="ban-1", name="ban_user",
        result_text=json.dumps({"pending_confirmation": True, "description": "Бан @target_user: abuse"},
                               ensure_ascii=False),
        action_description="Ожидает подтверждения: Бан @target_user: abuse",
        success=True,
        pending_confirmation_token="tok123",
    )

    await _send_confirmation_request(message, pending)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "Бан @target_user: abuse" in text
    assert "Подтвердить" in text
    keyboard = message.reply.await_args.kwargs["reply_markup"]
    callback_data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "llm_confirm:tok123" in callback_data
    assert "llm_reject:tok123" in callback_data


def test_confirmation_ttl_is_bounded():
    assert 30 <= TOOL_CONFIRMATION_TTL_SECONDS <= 3600
