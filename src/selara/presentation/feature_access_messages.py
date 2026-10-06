from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from decimal import Decimal

from selara.application.feature_access import FeatureAccessDecision
from selara.application.personal_models import format_ail
from selara.infrastructure.llm.features import AiFeature


def quota_exhausted_message(decision: FeatureAccessDecision, *, timezone_name: str) -> str:
    used = decision.quota_used or 0
    limit = decision.quota_limit or 0
    if decision.feature == AiFeature.LLM_ADMIN:
        label = "AI-ассистента для этого чата на сегодня"
    elif decision.feature == AiFeature.PERSONAL_CHAT:
        label = "личных AI-запросов на сегодня"
    elif decision.feature in (AiFeature.PET_TALK, AiFeature.PET_EVENT_TEXT):
        label = "разговоров с питомцем на сегодня"
    else:
        label = "ручных итогов дня для этого чата в этом месяце"

    reset_text = "в начале следующего периода"
    if decision.period_end is not None:
        try:
            local_tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            local_tz = ZoneInfo("UTC")
        reset_local = decision.period_end.astimezone(local_tz)
        now_local = datetime.now(timezone.utc).astimezone(local_tz)
        if decision.feature in (AiFeature.LLM_ADMIN, AiFeature.PERSONAL_CHAT, AiFeature.PET_TALK) and reset_local.date() == now_local.date() + timedelta(days=1):
            reset_text = "завтра"
        else:
            reset_text = reset_local.strftime("%d.%m.%Y в %H:%M") + " по времени бота"
    return f"Лимит {label} исчерпан: {used}/{limit}. Обновится {reset_text}."


def daily_reset_text(period_end: datetime | None, *, timezone_name: str) -> str:
    """When a daily pool refills: the calendar day boundary in BOT_TIMEZONE, not "in 24 hours"."""
    if period_end is None:
        return "в начале следующих суток"
    try:
        local_tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        local_tz = ZoneInfo("UTC")
        timezone_name = "UTC"
    reset_local = period_end.astimezone(local_tz)
    now_local = datetime.now(timezone.utc).astimezone(local_tz)
    day = "завтра" if reset_local.date() == now_local.date() + timedelta(days=1) else reset_local.strftime("%d.%m.%Y")
    return f"{day} в {reset_local:%H:%M} ({timezone_name})"


def ail_insufficient_message(
    decision: FeatureAccessDecision,
    *,
    profile_name: str,
    cost: Decimal,
    timezone_name: str,
    can_buy: bool,
) -> str:
    """No provider call was made: say what the profile needs, what is left and what to do."""
    remaining = decision.quota_remaining or 0
    lines = [
        f"Для профиля «{profile_name}» нужно {format_ail(cost)} AIL, осталось {format_ail(remaining)}."
        if Decimal(str(remaining)) > 0
        else f"AI Limits на сегодня закончились: {format_ail(decision.quota_used)}/{format_ail(decision.quota_limit)} AIL.",
        "Запрос не выполнялся, AIL не списаны.",
        "",
        "• выберите модель дешевле: /ai → «Модель»",
        f"• или дождитесь обновления лимита: {daily_reset_text(decision.period_end, timezone_name=timezone_name)}",
    ]
    if can_buy:
        lines.append("• Selara Personal даёт больший суточный бюджет AIL")
    return "\n".join(lines)
