from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage
from aiogram.types import Message

from selara.presentation.middlewares.command_cleanup import CommandCleanupMiddleware


def _message(*, text: str, chat_type: str = "group") -> Message:
    message = AsyncMock(spec=Message)
    message.text = text
    message.chat = SimpleNamespace(type=chat_type)
    message.delete = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_command_cleanup_deletes_handled_group_slash_command() -> None:
    middleware = CommandCleanupMiddleware()
    handler = AsyncMock(return_value=None)
    message = _message(text="/iris_perenos@selara_ru_bot")

    await middleware(handler, message, {})

    message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_command_cleanup_skips_unhandled_command_message() -> None:
    middleware = CommandCleanupMiddleware()
    handler = AsyncMock(return_value=UNHANDLED)
    message = _message(text="/unknown")

    result = await middleware(handler, message, {})

    assert result is UNHANDLED
    message.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_command_cleanup_ignores_delete_errors() -> None:
    middleware = CommandCleanupMiddleware()
    handler = AsyncMock(return_value=None)
    message = _message(text="/help")
    message.delete.side_effect = TelegramBadRequest(
        DeleteMessage(chat_id=-100123, message_id=5),
        "message can't be deleted",
    )

    await middleware(handler, message, {})

    message.delete.assert_awaited_once()
