from datetime import datetime, timezone

import pytest

from selara.application.use_cases.track_activity import execute as track_activity
from selara.domain.entities import ActivityStats, ChatSnapshot, UserSnapshot


class FakeRepo:
    def __init__(self) -> None:
        self.calls = []

    async def upsert_activity(
        self,
        *,
        chat: ChatSnapshot,
        user: UserSnapshot,
        event_at: datetime,
        telegram_message_id: int | None = None,
    ) -> ActivityStats:
        self.calls.append((chat, user, event_at, telegram_message_id))
        return ActivityStats(
            chat_id=chat.telegram_chat_id,
            user_id=user.telegram_user_id,
            message_count=1,
            last_seen_at=event_at,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
        )


@pytest.mark.asyncio
async def test_track_activity_use_case_builds_snapshots() -> None:
    repo = FakeRepo()
    now = datetime.now(timezone.utc)

    result = await track_activity(
        repo=repo,
        chat_id=1,
        chat_type="group",
        chat_title="Test Chat",
        user_id=10,
        username="alice",
        first_name="Alice",
        last_name="Doe",
        is_bot=False,
        event_at=now,
        telegram_message_id=555,
    )

    assert result.message_count == 1
    assert result.user_id == 10
    assert len(repo.calls) == 1
    chat, user, event_at, telegram_message_id = repo.calls[0]
    assert chat.telegram_chat_id == 1
    assert user.telegram_user_id == 10
    assert event_at == now
    assert telegram_message_id == 555
