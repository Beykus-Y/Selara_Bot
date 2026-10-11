from __future__ import annotations

from datetime import datetime, timedelta, timezone


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


# A scheduler may legitimately observe the due hour at the next 15-minute tick
# or shortly after a deploy. Anything older must not START a new generation.
SCHEDULED_NEW_RUN_GRACE = timedelta(minutes=90)


def is_stale_scheduled_window(
    *, scheduled_at: datetime, now: datetime,
    grace: timedelta = SCHEDULED_NEW_RUN_GRACE,
) -> bool:
    """True when a planned scheduled window is too old for a *new* run.

    Compare absolute instants so timezone/DST offsets cannot turn a 17-hour
    late event into a fresh one. This does not invalidate already-persisted
    generated/send_failed runs; the caller decides their recovery policy.
    """
    if scheduled_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("Scheduled grace requires timezone-aware datetimes")
    if grace < timedelta(0):
        raise ValueError("Scheduled grace cannot be negative")
    return now.astimezone(timezone.utc) - scheduled_at.astimezone(timezone.utc) > grace
