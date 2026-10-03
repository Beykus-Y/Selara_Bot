import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramMigrateToChat
from aiogram.methods import SendMessage, SendPhoto
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


@pytest.mark.asyncio
async def test_concurrent_matching_errors_send_only_one_alert() -> None:
    from selara.presentation.middlewares import error_handler as middleware_module

    middleware_module._recent_alerts.clear()
    configure_error_alerts(True, -100123)
    first_send_entered = asyncio.Event()
    release_first_send = asyncio.Event()

    async def blocked_send(**_kwargs):
        first_send_entered.set()
        await release_first_send.wait()

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=blocked_send))
    event = SimpleNamespace(bot=bot, chat=SimpleNamespace(id=-1001), from_user=None, text="/status")
    exception = ValueError("same concurrent failure")
    first = asyncio.create_task(
        notify_operational_error(session_factory=None, event=event, exc=exception)
    )
    await first_send_entered.wait()
    second = asyncio.create_task(
        notify_operational_error(session_factory=None, event=event, exc=exception)
    )
    await asyncio.sleep(0)
    release_first_send.set()
    await asyncio.gather(first, second)

    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_same_exception_text_at_distinct_raise_lines_is_not_deduplicated() -> None:
    from selara.presentation.middlewares import error_handler as middleware_module

    middleware_module._recent_alerts.clear()
    configure_error_alerts(True, -100123)
    bot = SimpleNamespace(send_message=AsyncMock())
    event = SimpleNamespace(bot=bot, chat=SimpleNamespace(id=-1001), from_user=None, text="/status")

    def raise_at_line(first: bool) -> ValueError:
        if first:
            raise ValueError("boom")
        raise ValueError("boom")

    exceptions = []
    for branch in (True, False):
        try:
            raise_at_line(branch)
        except ValueError as exc:
            exceptions.append(exc)

    for exc in exceptions:
        await notify_operational_error(session_factory=None, event=event, exc=exc)

    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_alert_redacts_feedback_command_and_secret_from_message_and_traceback() -> None:
    from selara.presentation.middlewares import error_handler as middleware_module

    middleware_module._recent_alerts.clear()
    configure_error_alerts(True, -100123)
    bot = SimpleNamespace(send_message=AsyncMock())
    event = SimpleNamespace(
        bot=bot,
        chat=SimpleNamespace(id=-1001),
        from_user=None,
        text="/feedback проблема: мой пароль SECRET_VALUE",
    )
    secret = "Authorization: Bearer SUPER_SECRET token=abc123 password=secret123 api_key=key456"
    try:
        raise ValueError(secret)
    except ValueError as exc:
        await notify_operational_error(session_factory=None, event=event, exc=exc)

    alert_text = bot.send_message.await_args.kwargs["text"]
    violations = []
    if "Команда: /feedback" not in alert_text:
        violations.append("command arguments were included in the command field")
    for sensitive_value in ("SECRET_VALUE", "SUPER_SECRET", "abc123", "secret123", "key456"):
        if sensitive_value in alert_text:
            violations.append(f"secret value leaked: {sensitive_value}")
    assert not violations, "; ".join(violations)


@pytest.mark.asyncio
async def test_alert_delivery_retries_at_new_chat_after_migration() -> None:
    from selara.presentation.middlewares import error_handler as middleware_module

    middleware_module._recent_alerts.clear()
    old_chat_id, new_chat_id = -123, -100123
    configure_error_alerts(True, old_chat_id)
    migration = TelegramMigrateToChat(
        method=SendMessage(chat_id=old_chat_id, text="alert"),
        message="group migrated",
        migrate_to_chat_id=new_chat_id,
    )
    runtime_settings = SimpleNamespace(error_alert_chat_id=old_chat_id)

    class _RuntimeSettingsSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _model, _key):
            return runtime_settings

        async def commit(self):
            return None

    class _RuntimeSettingsFactory:
        def __call__(self):
            return _RuntimeSettingsSession()

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[migration, None]))
    event = SimpleNamespace(bot=bot, chat=SimpleNamespace(id=-1001), from_user=None, text="/status")

    await notify_operational_error(
        session_factory=_RuntimeSettingsFactory(), event=event, exc=ValueError("service down")
    )

    sent_chat_ids = [call.kwargs["chat_id"] for call in bot.send_message.await_args_list]
    assert (
        sent_chat_ids == [old_chat_id, new_chat_id]
        and get_error_alert_config().chat_id == new_chat_id
        and runtime_settings.error_alert_chat_id == new_chat_id
    )
