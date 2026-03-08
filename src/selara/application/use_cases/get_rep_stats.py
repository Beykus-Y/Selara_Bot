from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from selara.application.dto import RepStats
from selara.application.interfaces import ActivityRepository
from selara.domain.entities import LeaderboardItem, LeaderboardPeriod


@dataclass(frozen=True)
class _ScoreConfig:
    karma_weight: float
    activity_weight: float
    days: int


async def execute(
    repo: ActivityRepository,
    *,
    chat_id: int,
    user_id: int,
    limit: int,
    karma_weight: float,
    activity_weight: float,
    days: int,
) -> RepStats:
    now = datetime.now(timezone.utc)
    since_1d = now - timedelta(days=1)
    since_7d = now - timedelta(days=days)
    since_30d = now - timedelta(days=30)

    activity_all, karma_all, _ = await repo.get_representation_stats(chat_id=chat_id, user_id=user_id, since=None)
    activity_1d, _, _ = await repo.get_representation_stats(chat_id=chat_id, user_id=user_id, since=since_1d)
    activity_7d, karma_7d, _ = await repo.get_representation_stats(chat_id=chat_id, user_id=user_id, since=since_7d)
    activity_30d, _, _ = await repo.get_representation_stats(chat_id=chat_id, user_id=user_id, since=since_30d)

    rank_all = await _resolve_rank(
        repo=repo,
        chat_id=chat_id,
        user_id=user_id,
        period="all",
        limit=limit,
        config=_ScoreConfig(karma_weight=karma_weight, activity_weight=activity_weight, days=days),
    )
    rank_7d = await _resolve_rank(
        repo=repo,
        chat_id=chat_id,
        user_id=user_id,
        period="7d",
        limit=limit,
        config=_ScoreConfig(karma_weight=karma_weight, activity_weight=activity_weight, days=days),
    )

    return RepStats(
        user_id=user_id,
        karma_all=karma_all,
        karma_7d=karma_7d,
        activity_1d=activity_1d,
        activity_all=activity_all,
        activity_7d=activity_7d,
        activity_30d=activity_30d,
        rank_all=rank_all,
        rank_7d=rank_7d,
    )


async def _resolve_rank(
    repo: ActivityRepository,
    *,
    chat_id: int,
    user_id: int,
    period: LeaderboardPeriod,
    limit: int,
    config: _ScoreConfig,
) -> int | None:
    since = None
    if period == "7d":
        since = datetime.now(timezone.utc) - timedelta(days=config.days)

    leaderboard: list[LeaderboardItem] = await repo.get_leaderboard(
        chat_id=chat_id,
        mode="mix",
        period=period,
        since=since,
        limit=max(limit, 1000),
        karma_weight=config.karma_weight,
        activity_weight=config.activity_weight,
    )
    for idx, item in enumerate(leaderboard, start=1):
        if item.user_id == user_id:
            return idx
    return None
