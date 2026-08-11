from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, Message

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.presentation.middlewares.chat_write_lock import ChatWriteLockMiddleware


def _locked_settings():
    settings = Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/test",
        }
    )
    return replace(default_chat_settings(settings), chat_write_locked=True)


def _message(text: str) -> MagicMock:
    message = MagicMock(spec=Message)
    message.chat = SimpleNamespace(type="group")
    message.text = text
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_lock_blocks_natural_language_economy_command() -> None:
    middleware = ChatWriteLockMiddleware()
    handler = AsyncMock(return_value="handled")
    message = _message("тап")

    result = await middleware(handler, message, {"chat_settings": _locked_settings()})

    assert result is None
    handler.assert_not_awaited()
    message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_lock_blocks_mutating_callback() -> None:
    middleware = ChatWriteLockMiddleware()
    handler = AsyncMock(return_value="handled")
    callback = MagicMock(spec=CallbackQuery)
    callback.data = "eco:tap:g"
    callback.message = SimpleNamespace(chat=SimpleNamespace(type="supergroup"))
    callback.answer = AsyncMock()

    result = await middleware(handler, callback, {"chat_settings": _locked_settings()})

    assert result is None
    handler.assert_not_awaited()
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_lock_allows_regular_chat_message() -> None:
    middleware = ChatWriteLockMiddleware()
    handler = AsyncMock(return_value="handled")
    message = _message("всем привет")

    result = await middleware(handler, message, {"chat_settings": _locked_settings()})

    assert result == "handled"
    handler.assert_awaited_once()
    message.answer.assert_not_awaited()
