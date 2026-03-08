import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message

from selara.application.use_cases.track_activity import execute as track_activity
from selara.presentation.commands.normalizer import normalize_text_command
from selara.presentation.filters import is_trackable_message
from selara.presentation.game_state import GAME_STORE

logger = logging.getLogger(__name__)


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
    return normalized == "кто я"


class ActivityTrackerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            settings = data.get("settings")
            repo = data.get("activity_repo")

            if settings is not None and repo is not None and is_trackable_message(event, settings.supported_chat_types):
                if _is_profile_lookup_message(event):
                    return await handler(event, data)
                await track_activity(
                    repo=repo,
                    chat_id=event.chat.id,
                    chat_type=event.chat.type,
                    chat_title=event.chat.title,
                    user_id=event.from_user.id,
                    username=event.from_user.username,
                    first_name=event.from_user.first_name,
                    last_name=event.from_user.last_name,
                    is_bot=event.from_user.is_bot,
                    event_at=event.date,
                )
                try:
                    await GAME_STORE.publish_event(
                        event_type="chat_activity",
                        scope="chat",
                        chat_id=event.chat.id,
                    )
                except Exception:
                    logger.exception("Failed to publish chat activity live event", extra={"chat_id": event.chat.id})

        return await handler(event, data)
