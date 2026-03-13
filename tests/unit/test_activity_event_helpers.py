from datetime import date, datetime, timezone

from selara.infrastructure.db.repositories import (
    _build_synthetic_activity_daily_rows,
    _build_synthetic_activity_minute_rows,
    _build_synthetic_activity_total_rows,
)


def test_build_synthetic_activity_total_rows_spreads_before_covered_date() -> None:
    rows = _build_synthetic_activity_total_rows(
        first_seen_at=datetime(2026, 3, 1, 8, 30, tzinfo=timezone.utc),
        residual_total=5,
        earliest_covered_date=date(2026, 3, 4),
    )

    assert [(bucket_at.date(), message_count) for bucket_at, message_count, _sent_at in rows] == [
        (date(2026, 3, 1), 2),
        (date(2026, 3, 2), 2),
        (date(2026, 3, 3), 1),
    ]
    assert rows[0][2] == datetime(2026, 3, 1, 8, 30, tzinfo=timezone.utc)


def test_build_synthetic_activity_total_rows_uses_first_seen_day_without_coverage() -> None:
    rows = _build_synthetic_activity_total_rows(
        first_seen_at=datetime(2026, 3, 1, 8, 30, tzinfo=timezone.utc),
        residual_total=3,
        earliest_covered_date=None,
    )

    assert rows == [
        (
            datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc),
            3,
            datetime(2026, 3, 1, 8, 30, tzinfo=timezone.utc),
        )
    ]


def test_build_synthetic_activity_minute_rows_merges_same_minute() -> None:
    rows = _build_synthetic_activity_minute_rows(
        daily_rows=[
            (date(2026, 3, 10), 2, datetime(2026, 3, 10, 12, 0, 30, tzinfo=timezone.utc)),
            (date(2026, 3, 11), 3, datetime(2026, 3, 10, 12, 0, 45, tzinfo=timezone.utc)),
        ]
    )

    assert rows == [
        (
            datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc),
            5,
            datetime(2026, 3, 10, 12, 0, 45, tzinfo=timezone.utc),
        )
    ]


def test_build_synthetic_activity_daily_rows_keeps_historical_days_at_midnight() -> None:
    rows = _build_synthetic_activity_daily_rows(
        imported_at=datetime(2026, 3, 12, 9, 0, tzinfo=timezone.utc),
        last_seen_at=datetime(2026, 3, 12, 9, 0, tzinfo=timezone.utc),
        activity_1d=4,
        activity_7d=11,
        activity_30d=30,
    )

    assert all(last_seen_at.time() == datetime.min.time() for _activity_date, _count, last_seen_at in rows[:-1])
    assert rows[-1][2] == datetime(2026, 3, 12, 9, 0, tzinfo=timezone.utc)
    assert next(last_seen_at for activity_date, _count, last_seen_at in rows if activity_date == date(2026, 3, 5)) == datetime(
        2026,
        3,
        5,
        0,
        0,
        tzinfo=timezone.utc,
    )
