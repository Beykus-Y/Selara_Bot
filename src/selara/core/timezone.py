from datetime import datetime
from zoneinfo import ZoneInfo


def to_timezone(value: datetime, timezone_name: str) -> datetime:
    tz = ZoneInfo(timezone_name)
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    return value.astimezone(tz)
