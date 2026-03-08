from datetime import datetime, timezone

import pytest

from selara.application.use_cases.get_last_seen import execute as get_last_seen


class FakeRepo:
    async def get_last_seen(self, *, chat_id: int, user_id: int) -> datetime | None:
        if chat_id == 100 and user_id == 7:
            return datetime(2026, 2, 12, 10, 30, tzinfo=timezone.utc)
        return None


@pytest.mark.asyncio
async def test_get_last_seen_with_existing_data() -> None:
    repo = FakeRepo()
    last_seen = await get_last_seen(repo=repo, chat_id=100, user_id=7)
    assert last_seen is not None
    assert last_seen.year == 2026


@pytest.mark.asyncio
async def test_get_last_seen_without_data() -> None:
    repo = FakeRepo()
    last_seen = await get_last_seen(repo=repo, chat_id=100, user_id=99)
    assert last_seen is None
