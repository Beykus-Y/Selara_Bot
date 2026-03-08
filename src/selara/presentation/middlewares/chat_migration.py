from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message

from selara.presentation.chat_migration import apply_chat_migration


class ChatMigrationMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            migration_service_message = False
            migrate_to_chat_id = getattr(event, "migrate_to_chat_id", None)
            if migrate_to_chat_id is not None:
                migration_service_message = True
                old_chat_id = int(event.chat.id)
                new_chat_id = int(migrate_to_chat_id)
                if old_chat_id != new_chat_id:
                    await apply_chat_migration(
                        event=event,
                        data=data,
                        old_chat_id=old_chat_id,
                        new_chat_id=new_chat_id,
                        reason="service_message:migrate_to_chat_id",
                    )

            migrate_from_chat_id = getattr(event, "migrate_from_chat_id", None)
            if migrate_from_chat_id is not None:
                migration_service_message = True
                old_chat_id = int(migrate_from_chat_id)
                new_chat_id = int(event.chat.id)
                if old_chat_id != new_chat_id:
                    await apply_chat_migration(
                        event=event,
                        data=data,
                        old_chat_id=old_chat_id,
                        new_chat_id=new_chat_id,
                        reason="service_message:migrate_from_chat_id",
                    )
            if migration_service_message:
                # Telegram service updates about chat migration should not be processed further.
                return None

        return await handler(event, data)
