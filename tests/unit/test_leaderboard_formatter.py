from datetime import datetime, timezone

from selara.domain.entities import LeaderboardItem
from selara.presentation.formatters import format_leaderboard


def _item() -> LeaderboardItem:
    return LeaderboardItem(
        user_id=100,
        username="user100",
        first_name="User",
        last_name="Hundred",
        activity_value=25,
        karma_value=9,
        hybrid_score=12.5,
        last_seen_at=datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc),
        chat_display_name=None,
    )


def test_activity_mode_hides_karma_and_rating() -> None:
    text = format_leaderboard(
        [_item()],
        mode="activity",
        period="all",
        limit=10,
        timezone_name="UTC",
    )
    assert "Топ пользователей за всё время" in text
    assert "tg://user?id=100" in text
    assert "User Hundred" in text
    assert "25" in text
    assert "сообщ." in text
    assert "карма за" not in text
    assert "гибридный балл:" not in text
    assert "16.02.2026 в 12:00" not in text
    assert "последнее сообщение:" not in text


def test_karma_mode_hides_activity_and_rating() -> None:
    text = format_leaderboard(
        [_item()],
        mode="karma",
        period="all",
        limit=10,
        timezone_name="UTC",
    )
    assert "карма за всё время: 9" in text
    assert "сообщений всего: 25" in text
    assert "гибридный балл:" not in text
    assert "последнее сообщение:" not in text


def test_week_period_title_is_rendered() -> None:
    text = format_leaderboard(
        [_item()],
        mode="activity",
        period="week",
        limit=10,
        timezone_name="UTC",
    )
    assert "Топ пользователей за текущую неделю" in text
    assert "User Hundred" in text
    assert "последнее сообщение:" not in text
    assert "сообщ." in text
    assert "16.02.2026 в 12:00" not in text


def test_activity_less_than_filter_title_is_rendered() -> None:
    text = format_leaderboard(
        [_item()],
        mode="activity",
        period="week",
        limit=50,
        timezone_name="UTC",
        activity_less_than=100,
    )
    assert "Пользователи за текущую неделю с активностью меньше" in text
    assert "<code>100</code>" in text
    assert "User Hundred" in text


def test_activity_mode_does_not_render_last_seen_suffix() -> None:
    text = format_leaderboard(
        [_item()],
        mode="activity",
        period="day",
        limit=10,
        timezone_name="UTC",
    )
    assert " | " not in text
    assert "нет данных" not in text


def test_mix_mode_shows_score_and_period_breakdown() -> None:
    text = format_leaderboard(
        [_item()],
        mode="mix",
        period="month",
        limit=10,
        timezone_name="UTC",
    )
    assert "User Hundred" in text
    assert "гибридный балл: 12.500" in text
    assert "сообщений за период: 25 | карма за период: 9" in text
    assert "последнее сообщение:" not in text
