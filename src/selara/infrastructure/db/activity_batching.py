from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


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


@dataclass(frozen=True)
class ActivityBatchFlushResult:
    latest_event_at_by_pair: dict[tuple[int, int], datetime] = field(default_factory=dict)
    impacted_chat_ids: set[int] = field(default_factory=set)
