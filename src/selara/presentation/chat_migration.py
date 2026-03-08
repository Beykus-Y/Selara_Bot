from __future__ import annotations

import logging
import re
from typing import Any

from aiogram.exceptions import TelegramMigrateToChat
from aiogram.types import CallbackQuery, Message

from selara.infrastructure.db.chat_migration import migrate_chat_id
from selara.presentation.game_state import GAME_STORE

logger = logging.getLogger(__name__)

_MIGRATION_PAIR_PATTERN = re.compile(r"supergroup with id (?P<new>-?\d+)\s+from\s+(?P<old>-?\d+)", re.IGNORECASE)
_MIGRATION_TARGET_PATTERN = re.compile(r"supergroup with id (?P<new>-?\d+)", re.IGNORECASE)


def extract_migration_ids_from_exception(event: Any, exc: TelegramMigrateToChat) -> tuple[int | None, int | None]:
    old_chat_id = _extract_chat_id_from_event(event)
    new_chat_id = _extract_migrate_to_chat_id(exc)

    text = str(exc)
    pair_match = _MIGRATION_PAIR_PATTERN.search(text)
    if pair_match is not None:
        old_chat_id = _safe_to_int(pair_match.group("old")) or old_chat_id
        new_chat_id = _safe_to_int(pair_match.group("new")) or new_chat_id
        return old_chat_id, new_chat_id

    if new_chat_id is None:
        target_match = _MIGRATION_TARGET_PATTERN.search(text)
        if target_match is not None:
            new_chat_id = _safe_to_int(target_match.group("new"))

    return old_chat_id, new_chat_id


async def apply_chat_migration(
    *,
    event: Any,
    data: dict[str, Any],
    old_chat_id: int,
    new_chat_id: int,
    reason: str,
) -> bool:
    if old_chat_id == new_chat_id:
        return False

    session = data.get("db_session")
    if session is None:
        logger.warning(
            "Cannot migrate chat: db session missing",
            extra={"old_chat_id": old_chat_id, "new_chat_id": new_chat_id, "reason": reason},
        )
        return False

    chat_type, chat_title = _resolve_new_chat_metadata(event=event, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    result = await migrate_chat_id(
        session,
        old_chat_id=old_chat_id,
        new_chat_id=new_chat_id,
        new_chat_type=chat_type,
        new_chat_title=chat_title,
    )
    await GAME_STORE.migrate_chat_id(old_chat_id=old_chat_id, new_chat_id=new_chat_id, new_chat_title=chat_title)

    logger.info(
        "Chat migration applied",
        extra={
            "old_chat_id": old_chat_id,
            "new_chat_id": new_chat_id,
            "reason": reason,
            "skipped_account_conflicts": result.skipped_account_conflicts,
        },
    )
    return result.migrated


def _extract_chat_id_from_event(event: Any) -> int | None:
    if isinstance(event, Message):
        return _safe_to_int(getattr(event.chat, "id", None))
    if isinstance(event, CallbackQuery) and event.message is not None:
        return _safe_to_int(getattr(event.message.chat, "id", None))
    return None


def _extract_migrate_to_chat_id(exc: TelegramMigrateToChat) -> int | None:
    direct = _safe_to_int(getattr(exc, "migrate_to_chat_id", None))
    if direct is not None:
        return direct

    parameters = getattr(exc, "parameters", None)
    if parameters is not None:
        return _safe_to_int(getattr(parameters, "migrate_to_chat_id", None))

    return None


def _resolve_new_chat_metadata(*, event: Any, old_chat_id: int, new_chat_id: int) -> tuple[str | None, str | None]:
    chat_id = _extract_chat_id_from_event(event)
    chat_type: str | None = None
    chat_title: str | None = None

    if isinstance(event, Message):
        chat_type = getattr(event.chat, "type", None)
        chat_title = getattr(event.chat, "title", None)
        if chat_id == new_chat_id:
            return chat_type, chat_title
        if getattr(event, "migrate_to_chat_id", None) is not None and chat_id == old_chat_id:
            return "supergroup", chat_title
        if chat_id == old_chat_id and chat_type == "group":
            return "supergroup", chat_title
        return None, chat_title

    if isinstance(event, CallbackQuery) and event.message is not None:
        if chat_id == new_chat_id:
            return getattr(event.message.chat, "type", None), getattr(event.message.chat, "title", None)

    return None, None


def _safe_to_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
