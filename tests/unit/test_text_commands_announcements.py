from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.domain.entities import UserSnapshot
from selara.presentation.handlers.text_commands import (
    _announcement_human_name,
    _build_announcement_messages,
    _extract_announcement_body,
)


def test_extract_announcement_body_with_quotes_and_newlines() -> None:
    body, error = _extract_announcement_body('объява "Первая строка\nВторая строка"')
    assert error is None
    assert body == "Первая строка\nВторая строка"


def test_extract_announcement_body_without_quotes() -> None:
    body, error = _extract_announcement_body("объява срочно всем проверить чат")
    assert error is None
    assert body == "срочно всем проверить чат"


def test_extract_announcement_body_rejects_missing_body() -> None:
    body, error = _extract_announcement_body("объява")
    assert body is None
    assert error is not None


def test_extract_announcement_body_rejects_unclosed_quote() -> None:
    body, error = _extract_announcement_body('объява "тест')
    assert body is None
    assert error is not None


def test_build_announcement_messages_groups_mentions_by_five() -> None:
    messages = _build_announcement_messages(
        body="Сбор через 10 минут",
        mentions=[f"<a href='tg://user?id={idx}'>user{idx}</a>" for idx in range(1, 8)],
    )

    assert len(messages) == 2
    assert messages[0].startswith("Сбор через 10 минут\n\n")
    assert messages[0].count("<a href=") == 5
    assert messages[1].startswith("Сбор через 10 минут\n\n")
    assert messages[1].count("<a href=") == 2


@pytest.mark.asyncio
async def test_announcement_human_name_prefers_telegram_name_over_username() -> None:
    message = SimpleNamespace(chat=SimpleNamespace(id=-100123))
    bot = SimpleNamespace(get_chat_member=AsyncMock())

    label = await _announcement_human_name(
        message,
        bot,
        UserSnapshot(
            telegram_user_id=77,
            username="Hislorr",
            first_name="Крис",
            last_name=None,
            is_bot=False,
            chat_display_name=None,
        ),
    )

    assert label == "Крис"
    bot.get_chat_member.assert_not_called()
