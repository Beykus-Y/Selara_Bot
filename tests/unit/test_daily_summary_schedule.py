from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from selara.application.daily_summary.schedule import compute_scheduled_window_to, is_stale_scheduled_window

_TZ = ZoneInfo("Europe/Moscow")


def test_window_to_is_todays_hour_when_already_past_it() -> None:
    now_local = datetime(2026, 9, 3, 10, 30, tzinfo=_TZ)
    result = compute_scheduled_window_to(hour=7, now_local=now_local)
    assert result == datetime(2026, 9, 3, 7, 0, tzinfo=_TZ)


def test_window_to_falls_back_to_yesterday_when_hour_not_reached_yet() -> None:
    now_local = datetime(2026, 9, 3, 5, 0, tzinfo=_TZ)
    result = compute_scheduled_window_to(hour=7, now_local=now_local)
    assert result == datetime(2026, 9, 2, 7, 0, tzinfo=_TZ)


def test_window_to_at_exact_hour_uses_today() -> None:
    now_local = datetime(2026, 9, 3, 7, 0, tzinfo=_TZ)
    result = compute_scheduled_window_to(hour=7, now_local=now_local)
    assert result == datetime(2026, 9, 3, 7, 0, tzinfo=_TZ)


def test_window_to_survives_downtime_catch_up() -> None:
    # bot was down from 03:00 until 05:13 -- the planned window must still be
    # today's 03:00, not "now minus 24h from whenever the scheduler noticed"
    now_local = datetime(2026, 9, 3, 5, 13, tzinfo=_TZ)
    result = compute_scheduled_window_to(hour=3, now_local=now_local)
    assert result == datetime(2026, 9, 3, 3, 0, tzinfo=_TZ)
    assert now_local - result < timedelta(hours=3)


def test_scheduled_grace_allows_normal_tick_but_skips_seventeen_hour_catchup() -> None:
    tz = ZoneInfo("Asia/Barnaul")
    planned = datetime(2026, 10, 9, 10, 0, tzinfo=tz)
    assert not is_stale_scheduled_window(
        scheduled_at=planned, now=planned + timedelta(minutes=30),
    )
    assert not is_stale_scheduled_window(
        scheduled_at=planned, now=planned + timedelta(minutes=90),
    )
    assert is_stale_scheduled_window(
        scheduled_at=planned, now=planned + timedelta(minutes=91),
    )
    assert is_stale_scheduled_window(
        scheduled_at=planned, now=planned + timedelta(hours=17),
    )


def test_grace_uses_elapsed_instant_across_fall_back_dst() -> None:
    ny = ZoneInfo("America/New_York")
    scheduled = datetime(2026, 11, 1, 1, 0, tzinfo=ny, fold=0)
    observed = datetime(2026, 11, 1, 1, 45, tzinfo=ny, fold=1)
    # Wall clock suggests 45m but the *actual* duration is 1h45m.
    assert is_stale_scheduled_window(scheduled_at=scheduled, now=observed)


def test_grace_requires_aware_datetimes() -> None:
    import pytest

    with pytest.raises(ValueError, match="timezone-aware"):
        is_stale_scheduled_window(
            scheduled_at=datetime(2026, 10, 10, 3, 0),
            now=datetime(2026, 10, 10, 4, 0, tzinfo=_TZ),
        )
