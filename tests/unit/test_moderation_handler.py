from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.domain.entities import UserSnapshot
from selara.presentation.handlers import moderation


def _message() -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100123, type="group", title="Test chat"),
        from_user=SimpleNamespace(id=1, username="actor", first_name="Actor", last_name=None, is_bot=False),
        reply_to_message=None,
        answer=AsyncMock(),
    )


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
