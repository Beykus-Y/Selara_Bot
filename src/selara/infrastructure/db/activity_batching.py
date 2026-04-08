from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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


@dataclass(frozen=True)
class ActivityBatchFlushResult:
    latest_event_at_by_pair: dict[tuple[int, int], datetime] = field(default_factory=dict)
    impacted_chat_ids: set[int] = field(default_factory=set)
