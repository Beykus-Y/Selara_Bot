from datetime import datetime, timezone

from selara.domain.entities import ActivityStats
from selara.presentation.formatters import (
    format_activity_pulse_line,
    format_me,
    format_profile_karma_line,
    format_profile_positions_line,
)


def test_format_activity_pulse_line_has_requested_shape() -> None:
    line = format_activity_pulse_line(day=1, week=3, month=5, all_time=7)
    assert line == "1д 1 • 7д 3 • 30д 5 • всё 7"


def test_format_activity_pulse_line_empty_values() -> None:
    line = format_activity_pulse_line(day=0, week=0, month=0, all_time=0)
    assert line == "1д 0 • 7д 0 • 30д 0 • всё 0"


def test_format_activity_pulse_line_has_iris_shape() -> None:
    line = format_activity_pulse_line(day=1, week=3, month=5, all_time=7, iris_view=True)
    assert line == "1 | 3 | 5 | 7"


def test_format_me_includes_first_seen_when_available() -> None:
    text = format_me(
        ActivityStats(
            chat_id=1,
            user_id=42,
            message_count=12,
            last_seen_at=datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc),
            first_seen_at=datetime(2026, 2, 8, 9, 30, tzinfo=timezone.utc),
            username="cheburek",
        ),
        timezone_name="UTC",
        fallback_user_id=42,
        activity_pulse=None,
    )

    assert "<b>Первое появление:</b> 08.02.2026" in text
    assert 'href="tg://user?id=42"' in text
    assert ">@cheburek<" in text
    assert "<b>Вся активность:</b>" not in text
    assert "<b>Сообщений:</b>" not in text
    assert "<b>Последний актив:</b>" in text


def test_format_me_uses_whole_activity_label() -> None:
    text = format_me(
        ActivityStats(
            chat_id=1,
            user_id=42,
            message_count=12,
            last_seen_at=datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc),
            username="cheburek",
        ),
        timezone_name="UTC",
        fallback_user_id=42,
        activity_pulse="1д 1 • 7д 2 • 30д 3 • всё 4",
    )

    assert "<b>Вся активность:</b> 1д 1 • 7д 2 • 30д 3 • всё 4" in text
    assert "<b>Сообщений:</b>" not in text


def test_format_me_supports_iris_activity_label() -> None:
    text = format_me(
        ActivityStats(
            chat_id=1,
            user_id=42,
            message_count=12,
            last_seen_at=datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc),
            username="cheburek",
        ),
        timezone_name="UTC",
        fallback_user_id=42,
        activity_pulse="917 | 917 | 917 | 917",
        activity_pulse_label="Актив (д|н|м|весь)",
    )

    assert "<b>Актив (д|н|м|весь):</b> 917 | 917 | 917 | 917" in text
    assert "<b>Вся активность:</b>" not in text


def test_format_me_prefers_telegram_name_over_username_for_tag_label() -> None:
    text = format_me(
        ActivityStats(
            chat_id=1,
            user_id=77,
            message_count=0,
            last_seen_at=datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc),
            username="Hislorr",
            first_name="Крис",
        ),
        timezone_name="UTC",
        fallback_user_id=77,
        activity_pulse=None,
    )

    assert 'href="tg://user?id=77"' in text
    assert ">Крис<" in text
    assert "@Hislorr" not in text


def test_format_profile_positions_line_has_compact_shape() -> None:
    line = format_profile_positions_line(rank_all=3, rank_7d=None)
    assert line == "<b>Позиция:</b> всё <code>#3</code> • 7д <code>-</code>"


def test_format_profile_karma_line_has_compact_shape() -> None:
    line = format_profile_karma_line(karma_all=5, karma_7d=2)
    assert line == "<b>Карма:</b> всё <code>5</code> • 7д <code>2</code>"
