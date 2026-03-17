from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError

from selara.presentation.db_recovery import safe_rollback


class BotBanMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        activity_repo = data.get("activity_repo")
        if activity_repo is None:
            return await handler(event, data)

        chat_id: int | None = None
        chat_type: str | None = None
        user_id: int | None = None
        is_bot_user = False

        if isinstance(event, Message):
            if event.chat is not None:
                chat_id = event.chat.id
                chat_type = event.chat.type
            if event.from_user is not None:
                user_id = event.from_user.id
                is_bot_user = bool(event.from_user.is_bot)

        elif isinstance(event, CallbackQuery):
            if event.message is not None and event.message.chat is not None:
                chat_id = event.message.chat.id
                chat_type = event.message.chat.type
            if event.from_user is not None:
                user_id = event.from_user.id
                is_bot_user = bool(event.from_user.is_bot)

        if chat_id is None or user_id is None or is_bot_user:
            return await handler(event, data)

        if chat_type not in {"group", "supergroup"}:
            return await handler(event, data)

        try:
            state = await activity_repo.get_moderation_state(chat_id=chat_id, user_id=user_id)
        except SQLAlchemyError:
            # If moderation tables are not migrated yet, do not block message handling.
            await safe_rollback(data.get("db_session"))
            return await handler(event, data)

        if state is None or not state.is_banned:
            return await handler(event, data)

        if isinstance(event, Message):
            return None

        if isinstance(event, CallbackQuery):
            # Acknowledge callback silently so client doesn't hang, but don't show any message.
            await event.answer()
            return None

        return await handler(event, data)
