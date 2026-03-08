from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError

from selara.core.chat_settings import default_chat_settings
from selara.presentation.db_recovery import safe_rollback


class ChatSettingsMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        settings = data.get("settings")
        repo = data.get("activity_repo")

        if settings is None:
            return await handler(event, data)

        chat_id: int | None = None
        if isinstance(event, Message):
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery) and event.message is not None:
            chat_id = event.message.chat.id

        current = default_chat_settings(settings)
        if repo is not None and chat_id is not None:
            try:
                saved = await repo.get_chat_settings(chat_id=chat_id)
            except SQLAlchemyError:
                await safe_rollback(data.get("db_session"))
                saved = None
            if saved is not None:
                current = saved

        data["chat_settings"] = current
        return await handler(event, data)
