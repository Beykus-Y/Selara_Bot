from datetime import datetime, timezone

from selara.infrastructure.db.repositories import _coerce_utc_datetime


def test_coerce_utc_datetime_accepts_unix_timestamp_for_edited_messages() -> None:
    value = 1773050700

    assert _coerce_utc_datetime(value) == datetime.fromtimestamp(value, tz=timezone.utc)
