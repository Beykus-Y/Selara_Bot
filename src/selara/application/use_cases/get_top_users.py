from datetime import datetime, timedelta, timezone

from selara.application.interfaces import ActivityRepository
from selara.domain.entities import LeaderboardItem, LeaderboardMode, LeaderboardPeriod


def resolve_week_since(*, now: datetime, week_start_weekday: int, week_start_hour: int) -> datetime:
    safe_weekday = max(0, min(6, int(week_start_weekday)))
    safe_hour = max(0, min(23, int(week_start_hour)))

    candidate = now.replace(hour=safe_hour, minute=0, second=0, microsecond=0)
    days_back = (now.weekday() - safe_weekday) % 7
    week_start = candidate - timedelta(days=days_back)
    if week_start > now:
        week_start -= timedelta(days=7)
    return week_start


def resolve_period_since(
    *,
    period: LeaderboardPeriod,
    days: int,
    week_start_weekday: int,
    week_start_hour: int,
) -> datetime | None:
    if period == "all":
        return None

    now = datetime.now(timezone.utc)
    if period == "7d":
        return now - timedelta(days=days)
    if period == "hour":
        return now - timedelta(hours=1)
    if period == "day":
        return now - timedelta(days=1)
    if period == "month":
        return now - timedelta(days=30)
    if period == "week":
        return resolve_week_since(
            now=now,
            week_start_weekday=week_start_weekday,
            week_start_hour=week_start_hour,
        )
    return now - timedelta(days=days)


async def execute(
    repo: ActivityRepository,
    *,
    chat_id: int,
    limit: int,
    mode: LeaderboardMode,
    period: LeaderboardPeriod,
    days: int,
    week_start_weekday: int,
    week_start_hour: int,
    karma_weight: float,
    activity_weight: float,
    activity_less_than: int | None = None,
) -> list[LeaderboardItem]:
    since = resolve_period_since(
        period=period,
        days=days,
        week_start_weekday=week_start_weekday,
        week_start_hour=week_start_hour,
    )
    return await repo.get_leaderboard(
        chat_id=chat_id,
        mode=mode,
        period=period,
        since=since,
        limit=limit,
        karma_weight=karma_weight,
        activity_weight=activity_weight,
        activity_less_than=activity_less_than,
    )
