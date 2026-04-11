import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message

from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.activity_batcher import ActivityBatcher
from selara.presentation.commands.normalizer import normalize_text_command
from selara.presentation.filters import is_trackable_message

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
    if normalized in {"кто я", "кто ты"}:
        return True
    if normalized.startswith("кто ты "):
        tail = normalized[len("кто ты") :].strip()
        token = tail.split(maxsplit=1)[0].strip(" ,.;!?")
        return bool(token) and (token.startswith("@") and len(token) > 1 or token.lstrip("-").isdigit())
    return False


def _is_membership_service_message(message: Message) -> bool:
    return bool(getattr(message, "new_chat_members", None) or getattr(message, "left_chat_member", None))


def _is_archivable_group_message(message: Message) -> bool:
    return bool(
        message.chat
        and message.chat.type in {"group", "supergroup"}
        and message.from_user
        and not message.from_user.is_bot
        and not _is_membership_service_message(message)
    )


def _message_content_type(message: Message) -> str:
    content_type = getattr(message, "content_type", None)
    if hasattr(content_type, "value"):
        return str(content_type.value)
    if content_type is None:
        return "unknown"
    return str(content_type)


def _build_message_archive_payload(message: Message, *, snapshot_kind: str) -> dict[str, object]:
    raw_message_json = json.loads(message.model_dump_json(exclude_none=False, warnings=False))
    canonical_snapshot = json.dumps(raw_message_json, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    snapshot_at = getattr(message, "edit_date", None) if snapshot_kind == "edited" else message.date
    if snapshot_at is None:
        snapshot_at = message.date

    return {
        "snapshot_kind": snapshot_kind,
        "snapshot_at": snapshot_at,
        "sent_at": message.date,
        "edited_at": getattr(message, "edit_date", None),
        "message_type": _message_content_type(message),
        "text": message.text,
        "caption": message.caption,
        "raw_message_json": raw_message_json,
        "snapshot_hash": hashlib.sha256(canonical_snapshot.encode("utf-8")).hexdigest(),
    }


def _build_reply_capture_payload(
    message: Message,
    *,
    archive_payload: dict[str, object] | None,
) -> dict[str, object]:
    if archive_payload is not None:
        return {
            "message_type": archive_payload["message_type"],
            "text": archive_payload["text"],
            "caption": archive_payload["caption"],
            "raw_message_json": archive_payload["raw_message_json"],
        }

    raw_message_json = json.loads(message.model_dump_json(exclude_none=False, warnings=False))
    return {
        "message_type": _message_content_type(message),
        "text": message.text,
        "caption": message.caption,
        "raw_message_json": raw_message_json,
    }


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

        if _is_membership_service_message(event):
            return result

        settings = data.get("settings")
        is_edited_message = getattr(event, "edit_date", None) is not None
        count_as_activity = bool(
            not is_edited_message
            and settings is not None
            and is_trackable_message(event, settings.supported_chat_types)
            and not _is_profile_lookup_message(event)
        )

        archive_payload: dict[str, object] | None = None
        chat_settings = data.get("chat_settings")
        if _is_archivable_group_message(event) and bool(getattr(chat_settings, "save_message", False)):
            snapshot_kind = "edited" if is_edited_message else "created"
            try:
                archive_payload = _build_message_archive_payload(event, snapshot_kind=snapshot_kind)
            except Exception:
                logger.exception(
                    "Failed to serialize message archive snapshot",
                    extra={
                        "chat_id": getattr(event.chat, "id", None),
                        "user_id": getattr(event.from_user, "id", None),
                        "message_id": getattr(event, "message_id", None),
                        "snapshot_kind": snapshot_kind,
                    },
                )

        activity_repo = data.get("activity_repo")
        if (
            activity_repo is not None
            and not is_edited_message
            and _is_archivable_group_message(event)
            and getattr(event, "reply_to_message", None) is not None
            and getattr(event.reply_to_message, "message_id", None) is not None
        ):
            try:
                reply_payload = _build_reply_capture_payload(event, archive_payload=archive_payload)
                await activity_repo.record_admin_broadcast_reply(
                    chat=ChatSnapshot(
                        telegram_chat_id=event.chat.id,
                        chat_type=event.chat.type,
                        title=event.chat.title,
                    ),
                    user=UserSnapshot(
                        telegram_user_id=event.from_user.id,
                        username=event.from_user.username,
                        first_name=event.from_user.first_name,
                        last_name=event.from_user.last_name,
                        is_bot=bool(event.from_user.is_bot),
                    ),
                    reply_to_message_id=int(event.reply_to_message.message_id),
                    telegram_message_id=int(event.message_id),
                    message_type=str(reply_payload["message_type"]),
                    text=reply_payload["text"],
                    caption=reply_payload["caption"],
                    raw_message_json=reply_payload["raw_message_json"],
                    sent_at=event.date,
                )
            except Exception:
                logger.exception(
                    "Failed to capture admin broadcast reply",
                    extra={
                        "chat_id": getattr(event.chat, "id", None),
                        "user_id": getattr(event.from_user, "id", None),
                        "message_id": getattr(event, "message_id", None),
                        "reply_to_message_id": getattr(event.reply_to_message, "message_id", None),
                    },
                )

        if not count_as_activity and archive_payload is None:
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
            count_as_activity=count_as_activity,
            snapshot_kind=archive_payload["snapshot_kind"] if archive_payload is not None else None,
            snapshot_at=archive_payload["snapshot_at"] if archive_payload is not None else None,
            sent_at=archive_payload["sent_at"] if archive_payload is not None else None,
            edited_at=archive_payload["edited_at"] if archive_payload is not None else None,
            message_type=archive_payload["message_type"] if archive_payload is not None else None,
            text=archive_payload["text"] if archive_payload is not None else None,
            caption=archive_payload["caption"] if archive_payload is not None else None,
            raw_message_json=archive_payload["raw_message_json"] if archive_payload is not None else None,
            snapshot_hash=archive_payload["snapshot_hash"] if archive_payload is not None else None,
        )
        return result
