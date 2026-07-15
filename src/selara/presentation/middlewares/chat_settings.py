import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError

from selara.core.chat_settings import default_chat_settings
from selara.presentation.db_recovery import safe_rollback


logger = logging.getLogger(__name__)


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
        settings_source = "global_default"
        if repo is not None and chat_id is not None:
            try:
                saved = await repo.get_chat_settings(chat_id=chat_id)
            except SQLAlchemyError:
                event_update = data.get("event_update")
                update_id = getattr(event_update, "update_id", None)
                logger.exception(
                    "chat_settings_load_failed chat_id=%s update_id=%s",
                    chat_id,
                    update_id,
                    extra={"chat_id": chat_id, "update_id": update_id},
                )
                await safe_rollback(data.get("db_session"))
                saved = None
                settings_source = "default_after_db_error"
            if saved is not None:
                current = saved
                settings_source = "database"

        data["chat_settings"] = current
        data["settings_source"] = settings_source
        return await handler(event, data)
