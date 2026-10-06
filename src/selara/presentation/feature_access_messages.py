from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from selara.application.feature_access import FeatureAccessDecision
from selara.infrastructure.llm.features import AiFeature


def quota_exhausted_message(decision: FeatureAccessDecision, *, timezone_name: str) -> str:
    used = decision.quota_used or 0
    limit = decision.quota_limit or 0
    if decision.feature == AiFeature.LLM_ADMIN:
        label = "AI-ассистента для этого чата на сегодня"
    elif decision.feature == AiFeature.PERSONAL_CHAT:
        label = "личных AI-запросов на сегодня"
    elif decision.feature == AiFeature.PET_TALK:
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
