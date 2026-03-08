from datetime import datetime, timezone

import pytest

from selara.application.use_cases.vote_karma import execute as vote_karma
from selara.domain.entities import UserSnapshot


class FakeRepo:
    def __init__(self, votes_24h: int = 0) -> None:
        self.votes_24h = votes_24h
        self.recorded = False

    async def count_votes_by_voter_since(self, *, chat_id: int, voter_user_id: int, since: datetime) -> int:
        assert chat_id == 100
        assert voter_user_id == 1
        return self.votes_24h

    async def record_vote(self, **kwargs) -> None:
        self.recorded = True

    async def get_karma_value(self, *, chat_id: int, user_id: int, period: str, since: datetime | None = None) -> int:
        if period == "all":
            return 5
        return 2


@pytest.mark.asyncio
async def test_vote_karma_rejects_self_vote() -> None:
    repo = FakeRepo()
    user = UserSnapshot(telegram_user_id=1, username="u", first_name=None, last_name=None, is_bot=False)

    result = await vote_karma(
        repo=repo,
        chat_id=100,
        chat_type="group",
        chat_title="chat",
        voter=user,
        target=user,
        vote_value=1,
        event_at=datetime.now(timezone.utc),
        daily_limit=20,
        days_for_7d=7,
    )

    assert not result.accepted
    assert result.reason == "Нельзя голосовать за себя"


@pytest.mark.asyncio
async def test_vote_karma_rejects_daily_limit() -> None:
    repo = FakeRepo(votes_24h=20)
    voter = UserSnapshot(telegram_user_id=1, username="v", first_name=None, last_name=None, is_bot=False)
    target = UserSnapshot(telegram_user_id=2, username="t", first_name=None, last_name=None, is_bot=False)

    result = await vote_karma(
        repo=repo,
        chat_id=100,
        chat_type="group",
        chat_title="chat",
        voter=voter,
        target=target,
        vote_value=1,
        event_at=datetime.now(timezone.utc),
        daily_limit=20,
        days_for_7d=7,
    )

    assert not result.accepted
    assert result.reason == "Достигнут дневной лимит голосов"


@pytest.mark.asyncio
async def test_vote_karma_success() -> None:
    repo = FakeRepo(votes_24h=0)
    voter = UserSnapshot(telegram_user_id=1, username="v", first_name=None, last_name=None, is_bot=False)
    target = UserSnapshot(telegram_user_id=2, username="t", first_name=None, last_name=None, is_bot=False)

    result = await vote_karma(
        repo=repo,
        chat_id=100,
        chat_type="group",
        chat_title="chat",
        voter=voter,
        target=target,
        vote_value=-1,
        event_at=datetime.now(timezone.utc),
        daily_limit=20,
        days_for_7d=7,
    )

    assert result.accepted
    assert result.target_karma_all_time == 5
    assert result.target_karma_7d == 2
    assert repo.recorded
