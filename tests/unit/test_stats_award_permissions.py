from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.domain.entities import ChatRoleDefinition
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
        AsyncMock(
            return_value=(
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
        ),
    )
    monkeypatch.setattr(stats_module, "_resolve_profile_mention", AsyncMock(return_value="Target"))
    monkeypatch.setattr(stats_module, "log_chat_action", AsyncMock())

    await stats_module.award_reply_text_command(message, activity_repo, bot=SimpleNamespace(), title="Лучшая шутка")

    assert activity_repo.add_user_chat_award.await_count == 1
    assert message.answers == [("Награда выдана <b>Target</b>: Лучшая шутка", {"parse_mode": "HTML"})]
