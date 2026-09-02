from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from selara.application.daily_summary.schedule import compute_scheduled_window_to

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
