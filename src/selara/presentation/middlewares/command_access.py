from __future__ import annotations

from collections.abc import Awaitable, Callable
from html import escape
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message

from selara.presentation.auth import get_role_label_ru, has_command_access
from selara.presentation.commands.access import resolve_command_key_input


class CommandAccessMiddleware(BaseMiddleware):
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
        if event.from_user is None:
            return await handler(event, data)

        raw_text = (event.text or "").strip()
        if not raw_text.startswith("/"):
            return await handler(event, data)

        command_key = resolve_command_key_input(raw_text)
        if command_key is None:
            return await handler(event, data)

        activity_repo = data.get("activity_repo")
        if activity_repo is None:
            return await handler(event, data)

        allowed, actor_role_code, required_role_code, _ = await has_command_access(
            activity_repo,
            chat_id=event.chat.id,
            chat_type=event.chat.type,
            chat_title=event.chat.title,
            user_id=event.from_user.id,
            username=event.from_user.username,
            first_name=event.from_user.first_name,
            last_name=event.from_user.last_name,
            is_bot=bool(event.from_user.is_bot),
            command_key=command_key,
            bootstrap_if_missing_owner=False,
        )
        if allowed:
            return await handler(event, data)

        required_label = await get_role_label_ru(activity_repo, chat_id=event.chat.id, role_code=required_role_code)
        actor_label = await get_role_label_ru(activity_repo, chat_id=event.chat.id, role_code=actor_role_code)
        await event.answer(
            (
                f"Недостаточно прав для команды <code>{escape(command_key)}</code>.\n"
                f"Ваш ранг: <code>{escape(actor_label)}</code>\n"
                f"Нужный ранг: <code>{escape(required_label)}</code>"
            ),
            parse_mode="HTML",
        )
        return None
