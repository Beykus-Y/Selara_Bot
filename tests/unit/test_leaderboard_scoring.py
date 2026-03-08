from datetime import datetime, timezone

from selara.application.use_cases.leaderboard_scoring import compute_hybrid_score, sort_leaderboard_items
from selara.domain.entities import LeaderboardItem


def test_compute_hybrid_score_with_weights() -> None:
    score = compute_hybrid_score(
        activity_value=50,
        karma_value=10,
        max_activity=100,
        min_karma=-10,
        max_karma=30,
        karma_weight=0.7,
        activity_weight=0.3,
    )
    assert round(score, 3) == 0.5


def test_sort_leaderboard_items_tie_break_mix() -> None:
    items = [
        LeaderboardItem(
            user_id=2,
            username=None,
            first_name=None,
            last_name=None,
            activity_value=10,
            karma_value=2,
            hybrid_score=0.5,
            last_seen_at=datetime(2026, 2, 13, tzinfo=timezone.utc),
        ),
        LeaderboardItem(
            user_id=1,
            username=None,
            first_name=None,
            last_name=None,
            activity_value=8,
            karma_value=2,
            hybrid_score=0.5,
            last_seen_at=datetime(2026, 2, 13, tzinfo=timezone.utc),
        ),
    ]

    sorted_items = sort_leaderboard_items(items, mode="mix")
    assert sorted_items[0].user_id == 2
