from selara.domain.entities import LeaderboardItem, LeaderboardMode


def compute_hybrid_score(
    *,
    activity_value: int,
    karma_value: int,
    max_activity: int,
    min_karma: int,
    max_karma: int,
    karma_weight: float,
    activity_weight: float,
) -> float:
    activity_norm = (activity_value / max_activity) if max_activity > 0 else 0.0
    karma_span = max_karma - min_karma
    karma_norm = ((karma_value - min_karma) / karma_span) if karma_span > 0 else 0.0
    return (karma_weight * karma_norm) + (activity_weight * activity_norm)


def sort_leaderboard_items(items: list[LeaderboardItem], *, mode: LeaderboardMode) -> list[LeaderboardItem]:
    if mode == "activity":
        return sorted(items, key=lambda x: (-x.activity_value, -x.karma_value, x.user_id))
    if mode == "karma":
        return sorted(items, key=lambda x: (-x.karma_value, -x.activity_value, x.user_id))
    return sorted(items, key=lambda x: (-x.hybrid_score, -x.karma_value, -x.activity_value, x.user_id))
