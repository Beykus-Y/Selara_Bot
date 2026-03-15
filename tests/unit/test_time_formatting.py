from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import selara.presentation.formatters as formatters_module
from selara.presentation.handlers.relationships import _format_relationship_duration


def test_format_elapsed_compact_shows_months_and_days(monkeypatch) -> None:
    fake_now = datetime(2026, 3, 15, 12, 0, tzinfo=ZoneInfo("Asia/Barnaul"))

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fake_now
            return fake_now.astimezone(tz)

    monkeypatch.setattr(formatters_module, "datetime", _FrozenDateTime)
    value = datetime(2026, 2, 8, 5, 0, tzinfo=timezone.utc)

    assert formatters_module.format_elapsed_compact(value, "Asia/Barnaul") == "1 мес 5 дн назад"


def test_format_relationship_duration_shows_months_and_days() -> None:
    started_at = datetime(2026, 2, 8, 5, 0, tzinfo=timezone.utc)
    now = datetime(2026, 3, 15, 7, 0, tzinfo=timezone.utc)

    assert _format_relationship_duration(started_at=started_at, now=now) == "1 мес. 5 дн."
