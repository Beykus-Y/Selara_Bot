from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository


class _OneResult:
    def __init__(self, row: tuple[object, ...]) -> None:
        self._row = row

    def one(self) -> tuple[object, ...]:
        return self._row


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one(self) -> object:
        return self._value

    def scalar_one_or_none(self) -> object:
        return self._value


@pytest.mark.asyncio
async def test_get_representation_stats_from_events_normalizes_naive_last_seen() -> None:
    naive_last_seen = datetime(2026, 3, 13, 8, 8, 39)
    session = SimpleNamespace(
        get=AsyncMock(return_value=None),
        execute=AsyncMock(
            side_effect=[
                _OneResult((2, naive_last_seen)),
                _ScalarResult(0),
            ]
        )
    )
    repo = SqlAlchemyActivityRepository(session)
    repo._is_chat_event_synced = AsyncMock(return_value=True)

    activity_value, karma_value, last_seen_at = await repo.get_representation_stats(
        chat_id=1,
        user_id=2,
        since=None,
    )

    assert activity_value == 2
    assert karma_value == 0
    assert last_seen_at == naive_last_seen.replace(tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_get_last_seen_from_events_normalizes_naive_datetime() -> None:
    naive_last_seen = datetime(2026, 3, 13, 8, 8, 39)
    session = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(naive_last_seen)))
    repo = SqlAlchemyActivityRepository(session)
    repo._is_chat_event_synced = AsyncMock(return_value=True)

    last_seen_at = await repo.get_last_seen(chat_id=1, user_id=2)

    assert last_seen_at == naive_last_seen.replace(tzinfo=timezone.utc)
