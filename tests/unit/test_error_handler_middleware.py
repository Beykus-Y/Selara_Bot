from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto
from aiogram.types import Message

from selara.presentation.middlewares.error_handler import ErrorHandlerMiddleware


@pytest.mark.asyncio
async def test_error_handler_does_not_send_fallback_to_closed_topic() -> None:
    event = AsyncMock(spec=Message)
    event.answer = AsyncMock()
    method = SendPhoto(chat_id=-100123, photo="file-id", message_thread_id=55)

    async def handler(_event, _data):
        raise TelegramBadRequest(method=method, message="TOPIC_CLOSED")

    result = await ErrorHandlerMiddleware()(handler, event, {})

    assert result is None
    event.answer.assert_not_awaited()
