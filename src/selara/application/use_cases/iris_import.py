from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


_URL_USERNAME_RE = re.compile(r"https?://t\.me/([A-Za-z0-9_]{3,32})", re.IGNORECASE)
_KARMA_RE = re.compile(r"➕\s*([+-]?\d+)")
_FIRST_SEEN_RE = re.compile(r"Первое появление:\s*(\d{2}\.\d{2}\.\d{4})", re.IGNORECASE)
_LAST_ACTIVE_RE = re.compile(r"Последний актив:\s*(.+)", re.IGNORECASE)
_ACTIVITY_RE = re.compile(r"Актив\s*\(д\|н\|м\|весь\):\s*([^\n]+)", re.IGNORECASE)
_AWARD_LINE_RE = re.compile(r"^\s*\d+\.\s*(.+?)\s*\|\s*(\d{2}\.\d{2}\.\d{4})\s*$")
_IRIS_AWARD_PREFIX_RE = re.compile(r"^\s*🎗(?:\ufe0f)?[₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹]*\s*")
_RELATIVE_PART_RE = re.compile(
    r"(?P<value>\d+)\s*(?P<unit>мин(?:ут[аы]?)?|ч(?:ас(?:а|ов)?)?|д(?:н(?:я|ей)?)?|мес(?:яц(?:а|ев)?)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IrisProfileImportData:
    target_username: str
    karma_all_time: int
    first_seen_at: datetime
    last_seen_at: datetime | None
    activity_1d: int
    activity_7d: int
    activity_30d: int
    activity_all: int


@dataclass(frozen=True)
class IrisAwardsImportData:
    target_username: str
    awards: tuple[tuple[str, datetime], ...]


def strip_iris_award_prefix(title: str) -> str:
    normalized = " ".join((title or "").split()).strip()
    if not normalized:
        return ""
    return _IRIS_AWARD_PREFIX_RE.sub("", normalized).strip()


def extract_t_me_username(*, text: str, entities: list[Any] | tuple[Any, ...] | None) -> str | None:
    for entity in entities or ():
        entity_type = str(getattr(entity, "type", "") or "").lower()
        if entity_type == "text_link":
            username = _extract_username_from_url(getattr(entity, "url", None))
            if username is not None:
                return username
        if entity_type == "url":
            offset = int(getattr(entity, "offset", 0) or 0)
            length = int(getattr(entity, "length", 0) or 0)
            if length > 0:
                username = _extract_username_from_url(text[offset : offset + length])
                if username is not None:
                    return username

    match = _URL_USERNAME_RE.search(text or "")
    if match is None:
        return None
    return _normalize_username(match.group(1))


def parse_forwarded_profile_message(
    *,
    text: str,
    entities: list[Any] | tuple[Any, ...] | None,
    timezone_name: str,
    now: datetime | None = None,
) -> IrisProfileImportData:
    body = (text or "").strip()
    if not body:
        raise ValueError("Пустой профиль Iris.")

    target_username = extract_t_me_username(text=body, entities=entities)
    if target_username is None:
        raise ValueError("Не удалось определить пользователя по ссылке Iris.")

    karma_match = _KARMA_RE.search(body)
    if karma_match is None:
        raise ValueError("Не найдена карма Iris.")
    karma_all_time = int(karma_match.group(1))

    first_seen_match = _FIRST_SEEN_RE.search(body)
    if first_seen_match is None:
        raise ValueError("Не найдена дата первого появления.")
    first_seen_at = _date_to_utc_noon(first_seen_match.group(1), timezone_name)

    last_seen_at = None
    last_active_match = _LAST_ACTIVE_RE.search(body)
    if last_active_match is not None:
        last_seen_at = _parse_relative_or_datetime(last_active_match.group(1), timezone_name=timezone_name, now=now)

    activity_match = _ACTIVITY_RE.search(body)
    if activity_match is None:
        raise ValueError("Не найден блок активности Iris.")
    parts = [part.strip() for part in activity_match.group(1).split("|")]
    if len(parts) != 4:
        raise ValueError("Блок активности Iris имеет неверный формат.")

    activity_1d, activity_7d, activity_30d, activity_all = (_parse_compact_count(part) for part in parts)
    if not (0 <= activity_1d <= activity_7d <= activity_30d <= activity_all):
        raise ValueError("Активность Iris не проходит базовую проверку.")

    return IrisProfileImportData(
        target_username=target_username,
        karma_all_time=karma_all_time,
        first_seen_at=first_seen_at,
        last_seen_at=last_seen_at,
        activity_1d=activity_1d,
        activity_7d=activity_7d,
        activity_30d=activity_30d,
        activity_all=activity_all,
    )


def parse_forwarded_awards_message(
    *,
    text: str,
    entities: list[Any] | tuple[Any, ...] | None,
    timezone_name: str,
) -> IrisAwardsImportData:
    body = (text or "").strip()
    if not body:
        raise ValueError("Пустой список наград Iris.")

    target_username = extract_t_me_username(text=body, entities=entities)
    if target_username is None:
        raise ValueError("Не удалось определить пользователя по ссылке Iris.")

    awards: list[tuple[str, datetime]] = []
    for raw_line in body.splitlines():
        match = _AWARD_LINE_RE.match(raw_line)
        if match is None:
            continue
        title = strip_iris_award_prefix(match.group(1) or "")
        if not title:
            continue
        awards.append((title, _date_to_utc_noon(match.group(2), timezone_name)))

    if not awards:
        raise ValueError("Не удалось распарсить награды Iris.")

    return IrisAwardsImportData(target_username=target_username, awards=tuple(awards))


def _extract_username_from_url(raw_url: str | None) -> str | None:
    if not raw_url:
        return None
    raw_value = str(raw_url).strip()
    match = _URL_USERNAME_RE.search(raw_value)
    if match is not None:
        return _normalize_username(match.group(1))
    parsed = urlparse(raw_value)
    path = (parsed.path or "").strip("/")
    if not path:
        return None
    return _normalize_username(path.split("/", maxsplit=1)[0])


def _normalize_username(value: str) -> str:
    return value.strip().lstrip("@").lower()


def _parse_compact_count(raw_value: str) -> int:
    value = (raw_value or "").strip().lower().replace(" ", "")
    if not value:
        raise ValueError("Пустое значение активности Iris.")
    if value.endswith("k"):
        numeric = float(value[:-1].replace(",", "."))
        return int(round(numeric * 1000))
    return int(value.replace(",", ""))


def _date_to_utc_noon(raw_date: str, timezone_name: str) -> datetime:
    local_tz = ZoneInfo(timezone_name)
    parsed_date = datetime.strptime(raw_date, "%d.%m.%Y").date()
    local_dt = datetime.combine(parsed_date, time(hour=12), tzinfo=local_tz)
    return local_dt.astimezone(timezone.utc)


def _parse_relative_or_datetime(raw_value: str, *, timezone_name: str, now: datetime | None) -> datetime | None:
    value = " ".join((raw_value or "").strip().split()).lower()
    if not value:
        return None

    reference = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(timezone_name))
    if value == "только что":
        return reference.astimezone(timezone.utc)
    if value.startswith("сегодня в "):
        return _parse_clock_reference(reference=reference, raw_value=value.removeprefix("сегодня в "))
    if value.startswith("вчера в "):
        return _parse_clock_reference(reference=reference - timedelta(days=1), raw_value=value.removeprefix("вчера в "))
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value):
        return _date_to_utc_noon(value, timezone_name)

    delta = timedelta()
    matches = list(_RELATIVE_PART_RE.finditer(value))
    if not matches:
        return None
    for match in matches:
        amount = int(match.group("value"))
        unit = match.group("unit").lower()
        if unit.startswith("мин"):
            delta += timedelta(minutes=amount)
        elif unit.startswith("ч"):
            delta += timedelta(hours=amount)
        elif unit.startswith("д"):
            delta += timedelta(days=amount)
        elif unit.startswith("мес"):
            delta += timedelta(days=amount * 30)
    return (reference - delta).astimezone(timezone.utc)


def _parse_clock_reference(*, reference: datetime, raw_value: str) -> datetime | None:
    normalized = raw_value.strip()
    try:
        parsed_time = datetime.strptime(normalized, "%H:%M").time()
    except ValueError:
        return None
    return reference.replace(
        hour=parsed_time.hour,
        minute=parsed_time.minute,
        second=0,
        microsecond=0,
    ).astimezone(timezone.utc)
