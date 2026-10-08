from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ActivityBatchMessage:
    chat_id: int
    chat_type: str
    chat_title: str | None
    user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    is_bot: bool
    event_at: datetime
    telegram_message_id: int | None = None
    count_as_activity: bool = True
    snapshot_kind: str | None = None
    snapshot_at: datetime | None = None
    sent_at: datetime | None = None
    edited_at: datetime | None = None
    message_type: str | None = None
    text: str | None = None
    caption: str | None = None
    raw_message_json: dict[str, Any] | None = None
    snapshot_hash: str | None = None
    reply_to_telegram_message_id: int | None = None


@dataclass(frozen=True)
class ActivityBatchFlushResult:
    latest_event_at_by_pair: dict[tuple[int, int], datetime] = field(default_factory=dict)
    impacted_chat_ids: set[int] = field(default_factory=set)


_PAYLOAD_DATETIME_FIELDS = ("event_at", "snapshot_at", "sent_at", "edited_at")
# The chat is stored in the inbox row's own columns, so a chat migration retargets a pending row without rewriting it.
_CHAT_FIELDS = frozenset({"chat_id", "chat_type", "chat_title"})


def activity_batch_message_to_payload(event: ActivityBatchMessage) -> dict[str, Any]:
    """Serialize an event for the activity inbox, storing datetimes as UTC ISO-8601 strings."""
    payload: dict[str, Any] = {
        item.name: getattr(event, item.name) for item in fields(event) if item.name not in _CHAT_FIELDS
    }
    for name in _PAYLOAD_DATETIME_FIELDS:
        value = payload[name]
        if value is not None:
            payload[name] = as_utc_datetime(value).isoformat()
    return payload


def activity_batch_message_from_payload(
    payload: Mapping[str, Any],
    *,
    chat_id: int,
    chat_type: str,
    chat_title: str | None,
) -> ActivityBatchMessage:
    """Inverse of `activity_batch_message_to_payload`. The chat comes from the inbox row's columns.

    Unknown keys are dropped so older code can read newer rows.
    """
    known_fields = {item.name for item in fields(ActivityBatchMessage)} - _CHAT_FIELDS
    values: dict[str, Any] = {key: value for key, value in payload.items() if key in known_fields}
    for name in _PAYLOAD_DATETIME_FIELDS:
        value = values.get(name)
        if isinstance(value, str):
            values[name] = datetime.fromisoformat(value)
    return ActivityBatchMessage(chat_id=chat_id, chat_type=chat_type, chat_title=chat_title, **values)


# Telegram sends Unix seconds. A number above this bound can only be milliseconds (in seconds, that is year 5138).
_UNIX_MILLISECONDS_THRESHOLD = 100_000_000_000


def as_utc_datetime(value: datetime | int | float) -> datetime:
    """Return an aware UTC datetime. Numbers are Unix timestamps in seconds, or in milliseconds above 1e11."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Unsupported timestamp type: {type(value).__name__}")
    seconds = value / 1000 if value > _UNIX_MILLISECONDS_THRESHOLD else value
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("Timestamp is out of range") from exc
