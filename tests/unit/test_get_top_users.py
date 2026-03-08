from datetime import datetime, timezone

import pytest

from selara.application.use_cases.get_top_users import execute as get_top_users, resolve_week_since
from selara.domain.entities import LeaderboardItem


class FakeRepo:
    async def get_leaderboard(self, **kwargs) -> list[LeaderboardItem]:
        assert kwargs["chat_id"] == 77
        assert kwargs["mode"] == "mix"
        assert kwargs["period"] == "all"
        assert kwargs["limit"] == 5
        assert kwargs["since"] is None
        assert kwargs["karma_weight"] == 0.7
        assert kwargs["activity_weight"] == 0.3
        return [
            LeaderboardItem(
                user_id=1,
                username="one",
                first_name="One",
                last_name=None,
                activity_value=10,
                karma_value=3,
                hybrid_score=0.9,
                last_seen_at=datetime(2026, 2, 12, 10, 0, tzinfo=timezone.utc),
            ),
        ]


@pytest.mark.asyncio
async def test_get_top_users_delegates_to_repo() -> None:
    repo = FakeRepo()
    result = await get_top_users(
        repo=repo,
        chat_id=77,
        limit=5,
        mode="mix",
        period="all",
        days=7,
        week_start_weekday=0,
        week_start_hour=0,
        karma_weight=0.7,
        activity_weight=0.3,
    )
    assert len(result) == 1
    assert result[0].user_id == 1


def test_resolve_week_since_uses_configured_week_start() -> None:
    now = datetime(2026, 2, 18, 10, 30, tzinfo=timezone.utc)  # Wednesday
    since = resolve_week_since(
        now=now,
        week_start_weekday=1,  # Tuesday
        week_start_hour=8,
    )
    assert since == datetime(2026, 2, 17, 8, 0, tzinfo=timezone.utc)
