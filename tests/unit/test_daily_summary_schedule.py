from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

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
    with pytest.raises(ValueError, match="timezone-aware"):
        is_stale_scheduled_window(
            scheduled_at=datetime(2026, 10, 10, 3, 0),
            now=datetime(2026, 10, 10, 4, 0, tzinfo=_TZ),
        )


def test_window_to_uses_bot_timezone_day_boundary_for_asia_barnaul() -> None:
    # 03:00 Barnaul is 20:00 UTC of the previous calendar day; a 10:24 local tick
    # must plan today's 03:00 Barnaul window, not a UTC-hour-based one.
    barnaul = ZoneInfo("Asia/Barnaul")
    now_local = datetime(2026, 10, 10, 10, 24, tzinfo=barnaul)
    result = compute_scheduled_window_to(hour=3, now_local=now_local)
    assert result == datetime(2026, 10, 10, 3, 0, tzinfo=barnaul)
    assert result.astimezone(timezone.utc) == datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome_sent,reason,recent_send,expected_event", [
    (True, "sent", False, "scheduled_sent"),
    (False, "not_eligible:not_enough_messages", False, "scheduled_skipped_low_activity"),
    (False, "claim_lost", True, "scheduled_skipped_recent_send"),
    (False, "claim_lost", False, "scheduled_claim_lost"),
    (False, "not_eligible:disabled", False, "scheduled_not_sent"),
])
async def test_scheduler_emits_structured_event_for_each_scheduled_outcome(
    monkeypatch, caplog, outcome_sent, reason, recent_send, expected_event,
) -> None:
    import logging
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from selara.presentation import daily_summary as module

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class FakeRepo:
        def __init__(self, session):
            self.get_chat_settings = AsyncMock(return_value=SimpleNamespace(
                daily_summary_enabled=True, daily_summary_hour=3))
            self.get_daily_summary_run = AsyncMock(return_value=None)
            self.has_recent_scheduled_send = AsyncMock(return_value=recent_send)

    monkeypatch.setattr(module, "SqlAlchemyActivityRepository", FakeRepo)
    monkeypatch.setattr(
        module, "attempt_daily_summary_run",
        AsyncMock(return_value=module.DailySummaryOutcome(outcome_sent, reason)),
    )

    barnaul = ZoneInfo("Asia/Barnaul")
    scheduler = module.DailySummaryScheduler(
        bot=SimpleNamespace(), session_factory=lambda: Session(), llm_client=SimpleNamespace(),
        settings=SimpleNamespace(), feature_access_service=SimpleNamespace(),
    )
    chat = SimpleNamespace(telegram_chat_id=-100123)
    # Inside the 90-minute new-run grace after the 03:00 Barnaul plan.
    now_utc = datetime(2026, 10, 10, 3, 20, tzinfo=barnaul).astimezone(timezone.utc)

    with caplog.at_level(logging.INFO, logger=module.__name__):
        sent = await scheduler._process_chat(chat=chat, now_utc=now_utc, local_tz=barnaul)

    assert sent is outcome_sent
    names = [r.getMessage() for r in caplog.records if r.name == module.__name__]
    assert names == ["scheduled_due", expected_event]
    record = caplog.records[-1]
    assert record.timezone == "Asia/Barnaul"
    assert record.hour == 3
    assert record.summary_date == "2026-10-10"
    assert record.window_to == "2026-10-09T20:00:00+00:00"
    assert record.window_from == "2026-10-08T20:00:00+00:00"
    assert record.trigger == "scheduled"
