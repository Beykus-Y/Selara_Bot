from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.domain.entities import ChatPersonaAssignment, UserSnapshot
from selara.presentation.handlers import moderation


def _message() -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100123, type="group", title="Test chat"),
        from_user=SimpleNamespace(id=1, username="actor", first_name="Actor", last_name=None, is_bot=False),
        reply_to_message=None,
        answer=AsyncMock(),
    )


@pytest.fixture(autouse=True)
def _clear_pending_persona_conflicts() -> None:
    moderation._PENDING_PERSONA_CONFLICTS.clear()
    yield
    moderation._PENDING_PERSONA_CONFLICTS.clear()


@pytest.mark.asyncio
async def test_apply_moderation_action_silent_without_moderation_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message()
    activity_repo = SimpleNamespace()

    monkeypatch.setattr(moderation, "has_command_access", AsyncMock(return_value=(True, "admin", "admin", False)))
    monkeypatch.setattr(moderation, "has_permission", AsyncMock(return_value=(False, None, False)))

    await moderation._apply_moderation_action(
        message=message,
        activity_repo=activity_repo,
        bot=SimpleNamespace(),
        command_name="ban",
        raw_tail="@target",
        use_reply_target=False,
    )

    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_moderation_action_ignores_telegram_admin_target(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message()
    target = UserSnapshot(
        telegram_user_id=2,
        username="target",
        first_name="Target",
        last_name=None,
        is_bot=False,
    )
    activity_repo = SimpleNamespace(
        get_effective_role_definition=AsyncMock(
            side_effect=[
                SimpleNamespace(role_code="owner", rank=100),
                None,
            ]
        ),
        apply_moderation_action=AsyncMock(),
    )
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")))

    monkeypatch.setattr(moderation, "has_command_access", AsyncMock(return_value=(True, "owner", "admin", False)))
    monkeypatch.setattr(moderation, "has_permission", AsyncMock(return_value=(True, "owner", False)))
    monkeypatch.setattr(moderation, "_resolve_target_user", AsyncMock(return_value=target))

    await moderation._apply_moderation_action(
        message=message,
        activity_repo=activity_repo,
        bot=bot,
        command_name="ban",
        raw_tail="@target",
        use_reply_target=False,
    )

    activity_repo.apply_moderation_action.assert_not_awaited()
    message.answer.assert_not_awaited()
    bot.get_chat_member.assert_awaited_once_with(chat_id=message.chat.id, user_id=target.telegram_user_id)


@pytest.mark.asyncio
async def test_rest_grant_text_command_grants_rest_with_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message()
    message.text = "выдать рест 7 @target"
    target = UserSnapshot(
        telegram_user_id=2,
        username="target",
        first_name="Target",
        last_name=None,
        is_bot=False,
    )
    expires_at = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)
    activity_repo = SimpleNamespace(
        grant_rest=AsyncMock(return_value=SimpleNamespace(expires_at=expires_at)),
    )

    monkeypatch.setattr(moderation, "has_command_access", AsyncMock(return_value=(True, "junior_admin", "junior_admin", False)))
    monkeypatch.setattr(moderation, "_resolve_target_user", AsyncMock(return_value=target))

    await moderation.rest_grant_text_command(message, activity_repo)

    activity_repo.grant_rest.assert_awaited_once()
    assert activity_repo.grant_rest.await_args.kwargs["duration_days"] == 7
    assert activity_repo.grant_rest.await_args.kwargs["target"] == target
    assert "Рест выдан" in message.answer.await_args.args[0]
    assert "09.04.2026 12:00 UTC" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_rest_list_text_command_outputs_active_rests(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message()
    message.text = "Ресты"
    activity_repo = SimpleNamespace(
        list_active_rest_entries=AsyncMock(
            return_value=[
                SimpleNamespace(
                    user=UserSnapshot(
                        telegram_user_id=2,
                        username="target",
                        first_name="Target",
                        last_name=None,
                        is_bot=False,
                    ),
                    expires_at=datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc),
                )
            ]
        )
    )

    monkeypatch.setattr(moderation, "has_command_access", AsyncMock(return_value=(True, "junior_admin", "junior_admin", False)))

    await moderation.rest_list_text_command(message, activity_repo)

    activity_repo.list_active_rest_entries.assert_awaited_once_with(chat_id=message.chat.id)
    assert "Активные ресты" in message.answer.await_args.args[0]
    assert "Target" in message.answer.await_args.args[0]
    assert "09.04.2026 12:00 UTC" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_persona_grant_text_command_assigns_persona(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message()
    message.text = 'выдать образ @target "Аль-Хайтам"'
    target = UserSnapshot(
        telegram_user_id=2,
        username="target",
        first_name="Target",
        last_name=None,
        is_bot=False,
    )
    activity_repo = SimpleNamespace(
        get_chat_persona_label=AsyncMock(return_value=None),
        find_chat_persona_owner=AsyncMock(return_value=None),
        set_chat_persona_label=AsyncMock(return_value="Аль-Хайтам"),
        add_audit_log=AsyncMock(),
    )

    monkeypatch.setattr(moderation, "_ensure_text_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(moderation, "_resolve_target_user", AsyncMock(return_value=target))

    await moderation.persona_grant_text_command(
        message,
        activity_repo,
        SimpleNamespace(persona_enabled=True),
    )

    activity_repo.set_chat_persona_label.assert_awaited_once()
    assert activity_repo.set_chat_persona_label.await_args.kwargs["persona_label"] == "Аль-Хайтам"
    activity_repo.add_audit_log.assert_awaited_once()
    assert "Образ выдан" in message.answer.await_args.args[0]
    assert "Аль-Хайтам" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_persona_grant_text_command_accepts_guillemets(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message()
    message.text = "ВЫДАть образ @target «Аль-Хайтам»"
    target = UserSnapshot(
        telegram_user_id=2,
        username="target",
        first_name="Target",
        last_name=None,
        is_bot=False,
    )
    activity_repo = SimpleNamespace(
        get_chat_persona_label=AsyncMock(return_value=None),
        find_chat_persona_owner=AsyncMock(return_value=None),
        set_chat_persona_label=AsyncMock(return_value="Аль-Хайтам"),
        add_audit_log=AsyncMock(),
    )

    monkeypatch.setattr(moderation, "_ensure_text_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(moderation, "_resolve_target_user", AsyncMock(return_value=target))

    await moderation.persona_grant_text_command(
        message,
        activity_repo,
        SimpleNamespace(persona_enabled=True),
    )

    assert activity_repo.set_chat_persona_label.await_args.kwargs["persona_label"] == "Аль-Хайтам"
    assert "Образ выдан" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_persona_grant_text_command_accepts_unquoted_label(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message()
    message.text = "выдать образ @target Аль-Хайтам"
    target = UserSnapshot(
        telegram_user_id=2,
        username="target",
        first_name="Target",
        last_name=None,
        is_bot=False,
    )
    activity_repo = SimpleNamespace(
        get_chat_persona_label=AsyncMock(return_value=None),
        find_chat_persona_owner=AsyncMock(return_value=None),
        set_chat_persona_label=AsyncMock(return_value="Аль-Хайтам"),
        add_audit_log=AsyncMock(),
    )

    monkeypatch.setattr(moderation, "_ensure_text_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(moderation, "_resolve_target_user", AsyncMock(return_value=target))

    await moderation.persona_grant_text_command(
        message,
        activity_repo,
        SimpleNamespace(persona_enabled=True),
    )

    assert activity_repo.set_chat_persona_label.await_args.kwargs["persona_label"] == "Аль-Хайтам"
    assert "Образ выдан" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_persona_grant_text_command_prompts_replace_for_taken_persona(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message()
    message.text = 'выдать образ @target "Аль-Хайтам"'
    target = UserSnapshot(
        telegram_user_id=2,
        username="target",
        first_name="Target",
        last_name=None,
        is_bot=False,
    )
    owner = ChatPersonaAssignment(
        chat_id=message.chat.id,
        user=UserSnapshot(
            telegram_user_id=3,
            username="owner",
            first_name="Owner",
            last_name=None,
            is_bot=False,
        ),
        persona_label="Аль-Хайтам",
        persona_label_norm="аль-хайтам",
        granted_by_user_id=1,
    )
    activity_repo = SimpleNamespace(
        get_chat_persona_label=AsyncMock(return_value=None),
        find_chat_persona_owner=AsyncMock(return_value=owner),
        set_chat_persona_label=AsyncMock(),
    )

    monkeypatch.setattr(moderation, "_ensure_text_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(moderation, "_resolve_target_user", AsyncMock(return_value=target))

    await moderation.persona_grant_text_command(
        message,
        activity_repo,
        SimpleNamespace(persona_enabled=True),
    )

    activity_repo.set_chat_persona_label.assert_not_awaited()
    assert "уже занят" in message.answer.await_args.args[0]
    assert message.answer.await_args.kwargs["reply_markup"] is not None
    assert len(moderation._PENDING_PERSONA_CONFLICTS) == 1
    pending = next(iter(moderation._PENDING_PERSONA_CONFLICTS.values()))
    assert pending.target_user_id == target.telegram_user_id
    assert pending.current_owner_user_id == owner.user.telegram_user_id
    assert pending.persona_label == "Аль-Хайтам"


@pytest.mark.asyncio
async def test_persona_clear_text_command_removes_persona(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message()
    message.text = "снять образ @target"
    target = UserSnapshot(
        telegram_user_id=2,
        username="target",
        first_name="Target",
        last_name=None,
        is_bot=False,
    )
    activity_repo = SimpleNamespace(
        get_chat_persona_label=AsyncMock(return_value="Аль-Хайтам"),
        clear_chat_persona_label=AsyncMock(return_value=True),
        add_audit_log=AsyncMock(),
    )

    monkeypatch.setattr(moderation, "_ensure_text_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(moderation, "_resolve_target_user", AsyncMock(return_value=target))

    await moderation.persona_clear_text_command(
        message,
        activity_repo,
        SimpleNamespace(persona_enabled=True),
    )

    activity_repo.clear_chat_persona_label.assert_awaited_once_with(chat_id=message.chat.id, user_id=target.telegram_user_id)
    activity_repo.add_audit_log.assert_awaited_once()
    assert "Образ снят" in message.answer.await_args.args[0]
    assert "Аль-Хайтам" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_persona_list_text_command_outputs_assignments(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message()
    message.text = "образы"
    activity_repo = SimpleNamespace(
        list_chat_persona_assignments=AsyncMock(
            return_value=[
                ChatPersonaAssignment(
                    chat_id=message.chat.id,
                    user=UserSnapshot(
                        telegram_user_id=2,
                        username="target",
                        first_name="Target",
                        last_name=None,
                        is_bot=False,
                    ),
                    persona_label="Аль-Хайтам",
                    persona_label_norm="аль-хайтам",
                    granted_by_user_id=1,
                )
            ]
        )
    )

    monkeypatch.setattr(moderation, "_ensure_text_command_access", AsyncMock(return_value=True))

    await moderation.persona_list_text_command(
        message,
        activity_repo,
        SimpleNamespace(persona_enabled=True),
    )

    activity_repo.list_chat_persona_assignments.assert_awaited_once_with(chat_id=message.chat.id)
    assert "Образы чата" in message.answer.await_args.args[0]
    assert "Аль-Хайтам" in message.answer.await_args.args[0]
    assert "Target" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_persona_conflict_callback_replaces_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    request_id = "req123"
    moderation._PENDING_PERSONA_CONFLICTS[request_id] = moderation.PendingPersonaConflict(
        request_id=request_id,
        chat_id=-100123,
        actor_user_id=1,
        target_user_id=2,
        current_owner_user_id=3,
        persona_label="Аль-Хайтам",
        created_at=datetime.now(timezone.utc),
    )
    target = UserSnapshot(
        telegram_user_id=2,
        username="target",
        first_name="Target",
        last_name=None,
        is_bot=False,
    )
    owner = ChatPersonaAssignment(
        chat_id=-100123,
        user=UserSnapshot(
            telegram_user_id=3,
            username="owner",
            first_name="Owner",
            last_name=None,
            is_bot=False,
        ),
        persona_label="Аль-Хайтам",
        persona_label_norm="аль-хайтам",
        granted_by_user_id=1,
    )
    activity_repo = SimpleNamespace(
        find_chat_persona_owner=AsyncMock(return_value=owner),
        get_user_snapshot=AsyncMock(return_value=target),
        get_chat_persona_label=AsyncMock(return_value="Венти"),
        clear_chat_persona_label=AsyncMock(return_value=True),
        set_chat_persona_label=AsyncMock(return_value="Аль-Хайтам"),
        add_audit_log=AsyncMock(),
    )
    query = SimpleNamespace(
        data=f"persona:confirm:{request_id}",
        from_user=SimpleNamespace(id=1),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-100123, type="group", title="Test chat"),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )

    await moderation.persona_conflict_callback(query, activity_repo)

    activity_repo.clear_chat_persona_label.assert_awaited_once_with(chat_id=-100123, user_id=3)
    activity_repo.set_chat_persona_label.assert_awaited_once()
    activity_repo.add_audit_log.assert_awaited_once()
    assert request_id not in moderation._PENDING_PERSONA_CONFLICTS
    assert "теперь закреплён" in query.message.edit_text.await_args.args[0]
    query.answer.assert_awaited_once_with("Готово")
