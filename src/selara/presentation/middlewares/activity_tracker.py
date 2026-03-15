from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message

from selara.infrastructure.db.activity_batcher import ActivityBatcher
from selara.presentation.commands.normalizer import normalize_text_command
from selara.presentation.filters import is_trackable_message


def _is_profile_lookup_message(message: Message) -> bool:
    text = (message.text or "").strip()
    if not text:
        return False

    first_token = text.split(maxsplit=1)[0].lower()
    if first_token.startswith("/me"):
        command_token = first_token.split("@", maxsplit=1)[0]
        if command_token == "/me":
            return True

    normalized = normalize_text_command(text)
    if normalized in {"кто я", "кто ты"}:
        return True
    if normalized.startswith("кто ты "):
        tail = normalized[len("кто ты") :].strip()
        token = tail.split(maxsplit=1)[0].strip(" ,.;!?")
        return bool(token) and (token.startswith("@") and len(token) > 1 or token.lstrip("-").isdigit())
    return False


def _is_membership_service_message(message: Message) -> bool:
    return bool(getattr(message, "new_chat_members", None) or getattr(message, "left_chat_member", None))


class ActivityTrackerMiddleware(BaseMiddleware):
    def __init__(self, activity_batcher: ActivityBatcher) -> None:
        self._activity_batcher = activity_batcher

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        result = await handler(event, data)

        if not isinstance(event, Message):
            return result

        settings = data.get("settings")
        if settings is None or not is_trackable_message(event, settings.supported_chat_types):
            return result
        if _is_membership_service_message(event):
            return result
        if _is_profile_lookup_message(event):
            return result

        await self._activity_batcher.enqueue_message(
            chat_id=event.chat.id,
            chat_type=event.chat.type,
            chat_title=event.chat.title,
            user_id=event.from_user.id,
            username=event.from_user.username,
            first_name=event.from_user.first_name,
            last_name=event.from_user.last_name,
            is_bot=event.from_user.is_bot,
            event_at=event.date,
            telegram_message_id=event.message_id,
        )
        return result
