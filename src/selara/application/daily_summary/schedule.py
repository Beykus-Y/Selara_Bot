from __future__ import annotations

from datetime import datetime, timedelta


def compute_scheduled_window_to(*, hour: int, now_local: datetime) -> datetime:
    """The PLANNED end of a scheduled run's 24h window: the most recent moment
    `hour:00` occurred at or before `now_local`, in `now_local`'s own timezone.

    This is deliberately based on the plan, not on when the scheduler actually got
    around to noticing -- see docs/DAILY_SUMMARY_TODO.md. If the bot was down from
    03:00 until 05:13, the window still ends at today's 03:00, not 05:13; otherwise
    every restart/downtime would permanently shift the window forward.
    """
    candidate = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate > now_local:
        candidate -= timedelta(days=1)
    return candidate
