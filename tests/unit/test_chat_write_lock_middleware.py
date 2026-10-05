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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["дрочка", "подрочить", "обнять @user", "/adoptdaughter @user", "/escapefamily", "/escapepet", "удочерить @user"],
)
async def test_lock_blocks_growth_action_social_and_family_variants(text: str) -> None:
    middleware = ChatWriteLockMiddleware()
    handler = AsyncMock(return_value="handled")
    message = _message(text)

    result = await middleware(handler, message, {"chat_settings": _locked_settings()})

    assert result is None
    handler.assert_not_awaited()
    message.answer.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["кто я", "топ", "помощь"])
async def test_lock_allows_read_only_text_commands(text: str) -> None:
    middleware = ChatWriteLockMiddleware()
    handler = AsyncMock(return_value="handled")
    message = _message(text)

    result = await middleware(handler, message, {"chat_settings": _locked_settings()})

    assert result == "handled"
    message.answer.assert_not_awaited()


def test_every_mutating_default_trigger_is_covered_by_lock() -> None:
    from selara.presentation.commands.catalog import (  # noqa: PLC0415
        BUILTIN_TRIGGER_TO_COMMAND_KEY,
        PREFIX_TRIGGER_TO_COMMAND_KEY,
        SOCIAL_TRIGGER_TO_COMMAND_KEY,
    )
    from selara.presentation.middlewares.chat_write_lock import is_write_locked_command  # noqa: PLC0415

    triggers = {**BUILTIN_TRIGGER_TO_COMMAND_KEY, **PREFIX_TRIGGER_TO_COMMAND_KEY, **SOCIAL_TRIGGER_TO_COMMAND_KEY}
    assert triggers
    for trigger, key in triggers.items():
        if key == "growth_action" or key.startswith("social_"):
            assert is_write_locked_command(key), (trigger, key)
