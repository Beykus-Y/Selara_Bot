from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message

from selara.core.chat_settings import ChatSettings

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
        "pet",
        # Игры
        "game",
        # Прочие активности
        "menu",
    }
)


class ChatWriteLockMiddleware(BaseMiddleware):
    """Блокирует пользовательские команды когда chat_write_locked=True."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        if event.chat.type not in {"group", "supergroup"}:
            return await handler(event, data)

        chat_settings: ChatSettings | None = data.get("chat_settings")
        if chat_settings is None or not chat_settings.chat_write_locked:
            return await handler(event, data)

        raw_text = (event.text or "").strip()
        if not raw_text.startswith("/"):
            return await handler(event, data)

        # Извлекаем имя команды (до пробела и до @)
        command_part = raw_text[1:].split()[0].split("@")[0].lower()
        if command_part not in _LOCKED_COMMANDS:
            return await handler(event, data)

        await event.answer("🔒 Чат заблокирован администратором. Команда недоступна.")
        return None
