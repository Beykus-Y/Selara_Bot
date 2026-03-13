from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message

from selara.presentation.commands.access import resolve_command_key_input


class CommandCleanupMiddleware(BaseMiddleware):
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

        raw_text = (event.text or "").strip()
        if not raw_text.startswith("/"):
            return await handler(event, data)
        if resolve_command_key_input(raw_text) is None:
            return await handler(event, data)

        result = await handler(event, data)
        if result is UNHANDLED:
            return result

        try:
            await event.delete()
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
        return result
