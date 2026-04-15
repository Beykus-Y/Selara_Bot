from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.domain.entities import ChatPersonaAssignment, ChatRoleDefinition, UserChatAward, UserSnapshot
from selara.presentation.handlers import stats as stats_module


def _message() -> SimpleNamespace:
    answers: list[tuple[str, dict]] = []

    async def answer(text: str, **kwargs):
        answers.append((text, kwargs))
        return SimpleNamespace(message_id=123)

    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100, type="group", title="Awards Chat"),
        from_user=SimpleNamespace(
            id=10,
            username="actor",
            first_name="Actor",
            last_name=None,
            is_bot=False,
        ),
        reply_to_message=SimpleNamespace(
            from_user=SimpleNamespace(
                id=20,
                username="target",
                first_name="Target",
                last_name=None,
                is_bot=False,
            )
        ),
        answer=answer,
    )
    message.answers = answers
    return message


def _junior_admin_role() -> tuple[ChatRoleDefinition, bool]:
    return (
        ChatRoleDefinition(
            chat_id=-100,
            role_code="junior_admin",
            title_ru="Мл. админ",
            rank=10,
            permissions=("announce",),
            is_system=True,
        ),
        False,
    )


@pytest.mark.asyncio
async def test_award_reply_text_command_blocks_user_without_internal_role(monkeypatch) -> None:
    message = _message()
    activity_repo = SimpleNamespace(
        get_chat_display_name=AsyncMock(return_value=None),
        add_user_chat_award=AsyncMock(),
    )

    monkeypatch.setattr(stats_module, "_ensure_chat_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(
        stats_module,
        "get_actor_role_definition",
        AsyncMock(return_value=(None, False)),
    )

    await stats_module.award_reply_text_command(message, activity_repo, bot=SimpleNamespace(), title="Лучшая шутка")

    assert activity_repo.add_user_chat_award.await_count == 0
    assert message.answers == [("Выдавать награды могут только админы бота с ролью «Мл. админ» и выше.", {})]


@pytest.mark.asyncio
async def test_award_reply_text_command_allows_junior_admin(monkeypatch) -> None:
    message = _message()
    activity_repo = SimpleNamespace(
        get_chat_display_name=AsyncMock(return_value=None),
        add_user_chat_award=AsyncMock(return_value=SimpleNamespace(id=77)),
    )

    monkeypatch.setattr(stats_module, "_ensure_chat_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(
        stats_module,
        "get_actor_role_definition",
        AsyncMock(return_value=_junior_admin_role()),
    )
    monkeypatch.setattr(stats_module, "_resolve_profile_mention", AsyncMock(return_value="Target"))
    monkeypatch.setattr(stats_module, "log_chat_action", AsyncMock())

    await stats_module.award_reply_text_command(message, activity_repo, bot=SimpleNamespace(), title="Лучшая шутка")

    assert activity_repo.add_user_chat_award.await_count == 1
    assert message.answers == [("Награда выдана <b>Target</b>: Лучшая шутка", {"parse_mode": "HTML"})]


@pytest.mark.asyncio
async def test_award_text_command_allows_username_target(monkeypatch) -> None:
    message = _message()
    message.reply_to_message = None
    activity_repo = SimpleNamespace(
        find_chat_user_by_username=AsyncMock(
            return_value=UserSnapshot(
                telegram_user_id=20,
                username="target",
                first_name="Target",
                last_name=None,
                is_bot=False,
                chat_display_name="Target",
            )
        ),
        add_user_chat_award=AsyncMock(return_value=SimpleNamespace(id=77)),
    )

    monkeypatch.setattr(stats_module, "_ensure_chat_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(stats_module, "get_actor_role_definition", AsyncMock(return_value=_junior_admin_role()))
    monkeypatch.setattr(stats_module, "_resolve_profile_mention", AsyncMock(return_value="Target"))
    monkeypatch.setattr(stats_module, "log_chat_action", AsyncMock())

    await stats_module.award_text_command(
        message,
        activity_repo,
        bot=SimpleNamespace(),
        title="Лучшая шутка",
        target_token="@target",
    )

    activity_repo.find_chat_user_by_username.assert_awaited_once_with(chat_id=-100, username="@target")
    assert activity_repo.add_user_chat_award.await_count == 1
    assert message.answers == [("Награда выдана <b>Target</b>: Лучшая шутка", {"parse_mode": "HTML"})]


@pytest.mark.asyncio
async def test_award_text_command_allows_persona_target(monkeypatch) -> None:
    message = _message()
    message.reply_to_message = None
    target = UserSnapshot(
        telegram_user_id=21,
        username="hutao_main",
        first_name="Hu",
        last_name="Tao",
        is_bot=False,
        chat_display_name="Ху Тао",
    )
    activity_repo = SimpleNamespace(
        find_chat_persona_owner=AsyncMock(return_value=None),
        list_chat_persona_assignments=AsyncMock(
            return_value=[
                ChatPersonaAssignment(
                    chat_id=-100,
                    user=target,
                    persona_label="Ху Тао",
                    persona_label_norm="ху тао",
                    granted_by_user_id=10,
                )
            ]
        ),
        add_user_chat_award=AsyncMock(return_value=SimpleNamespace(id=78)),
    )

    monkeypatch.setattr(stats_module, "_ensure_chat_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(stats_module, "get_actor_role_definition", AsyncMock(return_value=_junior_admin_role()))
    monkeypatch.setattr(stats_module, "_resolve_profile_mention", AsyncMock(return_value="Ху Тао"))
    monkeypatch.setattr(stats_module, "log_chat_action", AsyncMock())

    await stats_module.award_text_command(
        message,
        activity_repo,
        bot=SimpleNamespace(),
        title="Лучшая шутка",
        target_token="Ху Тао",
    )

    activity_repo.list_chat_persona_assignments.assert_awaited_once_with(chat_id=-100)
    assert activity_repo.add_user_chat_award.await_count == 1
    assert message.answers == [("Награда выдана <b>Ху Тао</b>: Лучшая шутка", {"parse_mode": "HTML"})]


@pytest.mark.asyncio
async def test_remove_award_reply_text_command_removes_selected_entry(monkeypatch) -> None:
    message = _message()
    message.reply_to_message = SimpleNamespace(
        from_user=SimpleNamespace(id=999, is_bot=True),
        text=(
            "Награды: Target\n"
            "1. Ждун яйца — 07.03.2026 • 5 дн 22 ч назад\n"
            "2. Лучшая шутка — 06.03.2026 • 6 дн 22 ч назад"
        ),
        caption=None,
        entities=(
            SimpleNamespace(
                type="text_link",
                offset=9,
                length=6,
                url="tg://user?id=20",
            ),
        ),
        caption_entities=(),
    )
    activity_repo = SimpleNamespace(
        list_user_chat_awards=AsyncMock(
            return_value=[
                UserChatAward(
                    id=77,
                    chat_id=-100,
                    user_id=20,
                    title="Ждун яйца",
                    granted_by_user_id=10,
                    created_at=stats_module.datetime(2026, 3, 7, 10, 0, tzinfo=stats_module.timezone.utc),
                ),
                UserChatAward(
                    id=78,
                    chat_id=-100,
                    user_id=20,
                    title="Лучшая шутка",
                    granted_by_user_id=10,
                    created_at=stats_module.datetime(2026, 3, 6, 10, 0, tzinfo=stats_module.timezone.utc),
                ),
            ]
        ),
        remove_user_chat_award=AsyncMock(return_value=True),
    )

    monkeypatch.setattr(stats_module, "_ensure_chat_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(stats_module, "get_actor_role_definition", AsyncMock(return_value=_junior_admin_role()))
    monkeypatch.setattr(stats_module, "_resolve_profile_mention", AsyncMock(return_value="Target"))
    monkeypatch.setattr(stats_module, "log_chat_action", AsyncMock())

    await stats_module.remove_award_reply_text_command(
        message,
        activity_repo,
        bot=SimpleNamespace(),
        award_index=2,
        timezone_name="Asia/Barnaul",
    )

    activity_repo.remove_user_chat_award.assert_awaited_once_with(chat_id=-100, award_id=78)
    assert message.answers == [("Награда снята у <b>Target</b>: Лучшая шутка", {"parse_mode": "HTML"})]
