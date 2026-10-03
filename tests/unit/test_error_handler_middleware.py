from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto
from aiogram.types import Message

from selara.presentation.middlewares.error_handler import ErrorHandlerMiddleware


class _AlertSettingsSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _model, _key):
        return SimpleNamespace(error_alerts_enabled=True, error_alert_chat_id=-100123)


class _AlertSettingsFactory:
    def __call__(self):
        return _AlertSettingsSession()


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


@pytest.mark.asyncio
async def test_unhandled_error_sends_traceback_once_per_fingerprint() -> None:
    from selara.presentation.middlewares import error_handler as middleware_module

    middleware_module._recent_alerts.clear()
    bot = SimpleNamespace(send_message=AsyncMock())

    def make_event(chat_id: int):
        return SimpleNamespace(
            bot=bot,
            chat=SimpleNamespace(id=chat_id, title=f"chat-{chat_id}"),
            from_user=SimpleNamespace(id=777, full_name="Илья"),
            text="/top",
        )

    async def failing_handler(_event, _data):
        raise ValueError("same failure")

    middleware = ErrorHandlerMiddleware(_AlertSettingsFactory())
    await middleware(failing_handler, make_event(-1001), {})
    await middleware(failing_handler, make_event(-1002), {})

    bot.send_message.assert_awaited_once()
    alert_text = bot.send_message.await_args.kwargs["text"]
    assert "Команда: /top" in alert_text
    assert "Traceback:" in alert_text
