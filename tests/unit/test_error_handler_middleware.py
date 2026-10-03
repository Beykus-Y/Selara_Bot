from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto
from aiogram.types import Message

from selara.core.config import Settings
from selara.presentation.middlewares.error_alert_config import (
    configure_error_alerts,
    get_error_alert_config,
    load_error_alert_config,
)
from selara.presentation.middlewares.error_handler import ErrorHandlerMiddleware, notify_operational_error


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
    configure_error_alerts(True, -100123)
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


@pytest.mark.asyncio
async def test_failed_alert_delivery_is_retried_on_next_matching_error() -> None:
    from selara.presentation.middlewares import error_handler as middleware_module

    middleware_module._recent_alerts.clear()
    configure_error_alerts(True, -100123)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[RuntimeError("temporary Telegram failure"), None]))
    event = SimpleNamespace(
        bot=bot,
        chat=SimpleNamespace(id=-1001, title="chat"),
        from_user=SimpleNamespace(id=777, full_name="Илья"),
        text="/top",
    )

    async def failing_handler(_event, _data):
        raise ValueError("same failure")

    middleware = ErrorHandlerMiddleware()
    await middleware(failing_handler, event, {})
    await middleware(failing_handler, event, {})

    assert bot.send_message.await_count == 2
    assert len(middleware_module._recent_alerts) == 1


@pytest.mark.asyncio
async def test_alert_destination_uses_environment_fallback_when_database_is_unavailable() -> None:
    class _UnavailableFactory:
        def __call__(self):
            raise RuntimeError("database unavailable")

    settings = Settings(bot_token="token", database_url="postgresql://localhost/db", error_alert_chat_id=-100456)
    configure_error_alerts(False, None)
    await load_error_alert_config(settings, _UnavailableFactory())

    assert get_error_alert_config().enabled is True
    assert get_error_alert_config().chat_id == -100456
    bot = SimpleNamespace(send_message=AsyncMock())
    await notify_operational_error(
        session_factory=_UnavailableFactory(),
        event=SimpleNamespace(bot=bot, chat=SimpleNamespace(id=-1001), from_user=None, text="/status"),
        exc=ValueError("database connection failed"),
    )

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == -100456
