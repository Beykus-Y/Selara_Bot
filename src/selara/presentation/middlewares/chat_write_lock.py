from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from selara.core.chat_settings import ChatSettings
from selara.presentation.commands.catalog import match_builtin_command

# Команды, которые блокируются при chat_write_locked.
# Сюда входят все пользовательские активности: экономика, игры, отношения, социальные действия.
# Команды управления (настройки, модерация, помощь, просмотр статистики) не блокируются.
_LOCKED_COMMANDS: frozenset[str] = frozenset(
    {
        # Экономика
        "eco",
        "tap",
        "daily",
        "farm",
        "shop",
        "inventory",
        "lottery",
        "market",
        "pay",
        "craft",
        "auction",
        "bid",
        "growth",
        "growth_action",
        # Гача текстом (ключи resolver, не каталога)
        "gacha_pull",
        "gacha_skip",
        "title",
        # Отношения
        "pair",
        "marry",
        "breakup",
        "love",
        "care",
        "date",
        "gift",
        "support",
        "flirt",
        "surprise",
        "vow",
        "divorce",
        # Семья
        "adopt",
        "adoptdaughter",
        "pet",
        "escape_family",
        "escape_pet",
        "escapefamily",
        "escapepet",
        # Игры
        "game",
        # Прочие активности
        "menu",
    }
)

# Callback-префиксы пользовательских действий. Административные, справочные и
# статистические кнопки намеренно не входят в список: блокировка чата не должна
# мешать управлению группой и просмотру информации.
_LOCKED_CALLBACK_PREFIXES: tuple[str, ...] = (
    # Экономика и gacha
    "eco:",
    "farm:",
    "shop:",
    "inv:",
    "grw:",
    "lot:",
    "mkt:",
    "gacha:",
    # Игры
    "game:",
    "gcfg:",
    "gquiz:",
    "gdice:",
    "gbred:",
    "gbredcat:",
    "gzlobp:",
    "gzlobv:",
    "gbkr:",
    "gbkv:",
    "gwho:",
    "gspy:",
    "gmact:",
    "gmvote:",
    "gmconfirm:",
    # Отношения, семья, кланы и другие социальные действия
    "rel:",
    "relend:",
    "relact:",
    "famreq:",
    "famleave:",
    "clan:",
    "cap:",
    "ipm:",
    "menu:",
)


# Мутирующие текстовые команды кланов (handlers/clans.py): отдельный роутер с text-фильтрами,
# ключей каталога у них нет. Просмотр (`клан`, `кланы`) не блокируется.
_LOCKED_CLAN_TEXT_PREFIXES: tuple[str, ...] = ("создать клан ", "вступить в клан ")
_LOCKED_CLAN_TEXT_EXACT: frozenset[str] = frozenset({"удалить клан", "выйти из клана"})

CHAT_WRITE_LOCK_ANSWER = "🔒 Чат заблокирован администратором. Команда недоступна."


def is_write_locked_command(command_key: str | None) -> bool:
    """Ключ команды блокируется при chat_write_locked (включая все соцдействия)."""
    if command_key is None:
        return False
    return command_key in _LOCKED_COMMANDS or command_key.startswith("social_")


class ChatWriteLockMiddleware(BaseMiddleware):
    """Блокирует пользовательские команды когда chat_write_locked=True."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            return await self._handle_callback(handler, event, data)

        if not isinstance(event, Message):
            return await handler(event, data)

        if event.chat.type not in {"group", "supergroup"}:
            return await handler(event, data)

        chat_settings: ChatSettings | None = data.get("chat_settings")
        if chat_settings is None or not chat_settings.chat_write_locked:
            return await handler(event, data)

        raw_text = (event.text or "").strip()
        lowered = raw_text.lower()
        if lowered.startswith(_LOCKED_CLAN_TEXT_PREFIXES) or lowered in _LOCKED_CLAN_TEXT_EXACT:
            await event.answer(CHAT_WRITE_LOCK_ANSWER)
            return None
        if raw_text.startswith("/"):
            # Извлекаем имя команды (до пробела и до @).
            command_key = raw_text[1:].split()[0].split("@")[0].lower()
        else:
            match = match_builtin_command(raw_text)
            command_key = match.command_key if match is not None else None

        if not is_write_locked_command(command_key):
            return await handler(event, data)

        await event.answer(CHAT_WRITE_LOCK_ANSWER)
        return None

    async def _handle_callback(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        message = event.message
        if message is None or message.chat.type not in {"group", "supergroup"}:
            return await handler(event, data)

        chat_settings: ChatSettings | None = data.get("chat_settings")
        if chat_settings is None or not chat_settings.chat_write_locked:
            return await handler(event, data)

        callback_data = event.data or ""
        if not callback_data.startswith(_LOCKED_CALLBACK_PREFIXES):
            return await handler(event, data)

        await event.answer("🔒 Чат заблокирован администратором.", show_alert=True)
        return None
