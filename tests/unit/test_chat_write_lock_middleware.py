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


# Ключи каталога, которые ничего не мутируют (просмотр, справка, админские/настроечные).
# Любой новый ключ каталога вне этого списка обязан попасть в блокировку chat_write_locked,
# иначе тест падает: добавляя команду, нужно осознанно выбрать одно из двух.
_NON_MUTATING_CATALOG_KEYS: frozenset[str] = frozenset(
    {
        "achievements", "active", "alive", "announce", "announce_reg", "announce_unreg",
        "antiraid_off", "antiraid_on", "article", "chat_lock", "chat_unlock", "family",
        "help", "inactive", "lastseen", "marriage", "marriages", "me", "naming", "quote",
        "relation", "rep", "role", "shipperim", "start", "top", "zhmyh",
    }
)


def test_every_catalog_key_is_either_locked_or_explicitly_non_mutating() -> None:
    from selara.presentation.commands.catalog import (  # noqa: PLC0415
        BUILTIN_TRIGGER_TO_COMMAND_KEY,
        PREFIX_TRIGGER_TO_COMMAND_KEY,
        SOCIAL_TRIGGER_TO_COMMAND_KEY,
    )
    from selara.presentation.middlewares.chat_write_lock import is_write_locked_command  # noqa: PLC0415

    keys = {
        key
        for mapping in (BUILTIN_TRIGGER_TO_COMMAND_KEY, PREFIX_TRIGGER_TO_COMMAND_KEY, SOCIAL_TRIGGER_TO_COMMAND_KEY)
        for key in mapping.values()
    }
    assert keys
    unclassified = sorted(k for k in keys if not is_write_locked_command(k) and k not in _NON_MUTATING_CATALOG_KEYS)
    assert unclassified == []
    stale = sorted(k for k in _NON_MUTATING_CATALOG_KEYS if is_write_locked_command(k))
    assert stale == []


@pytest.mark.parametrize("key", ["gacha_pull", "gacha_skip", "growth_action", "social_hug"])
def test_resolver_only_mutating_keys_are_locked(key: str) -> None:
    from selara.presentation.middlewares.chat_write_lock import is_write_locked_command  # noqa: PLC0415

    assert is_write_locked_command(key)


@pytest.mark.parametrize("key", ["gacha_profile", "gacha_info", "gacha_on", "gacha_off", "me", "top", "help"])
def test_read_only_and_admin_keys_are_not_locked(key: str) -> None:
    from selara.presentation.middlewares.chat_write_lock import is_write_locked_command  # noqa: PLC0415

    assert not is_write_locked_command(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["создать клан Тест", "Вступить в клан Тест", "выйти из клана", "удалить клан"])
async def test_lock_blocks_clan_mutations(text: str) -> None:
    middleware = ChatWriteLockMiddleware()
    handler = AsyncMock(return_value="handled")
    message = _message(text)

    result = await middleware(handler, message, {"chat_settings": _locked_settings()})

    assert result is None
    handler.assert_not_awaited()
    message.answer.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["кланы", "мой клан", "клан"])
async def test_lock_allows_clan_views(text: str) -> None:
    middleware = ChatWriteLockMiddleware()
    handler = AsyncMock(return_value="handled")
    message = _message(text)

    result = await middleware(handler, message, {"chat_settings": _locked_settings()})

    assert result == "handled"
    message.answer.assert_not_awaited()



@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["/", "/   ", "  / ", "/\\t"])
async def test_locked_chat_passes_empty_slash_to_handler(raw: str) -> None:
    middleware = ChatWriteLockMiddleware()
    handler = AsyncMock(return_value="handled")
    message = _message(raw)

    data = {"chat_settings": _locked_settings()}
    result = await middleware(handler, message, data)

    assert result == "handled"
    handler.assert_awaited_once_with(message, data)
    message.answer.assert_not_awaited()
