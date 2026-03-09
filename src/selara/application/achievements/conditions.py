from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from selara.domain.entities import AchievementDefinition


@dataclass(frozen=True)
class AchievementEvaluationContext:
    user_id: int
    chat_id: int | None
    event_type: str
    event_at: datetime


class AchievementConditionEvaluator:
    async def is_satisfied(
        self,
        definition: AchievementDefinition,
        *,
        repo: Any,
        context: AchievementEvaluationContext,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        condition_type = definition.condition_type
        value = definition.condition_payload.get("value")

        if condition_type == "message_count_gte":
            if context.chat_id is None:
                return False, None, None
            stats = await repo.get_user_stats(chat_id=context.chat_id, user_id=context.user_id)
            current = int(stats.message_count if stats is not None else 0)
            threshold = max(0, int(value or 0))
            return current >= threshold, f"message_count reached {current}", {"value": current, "target": threshold}

        if condition_type == "first_message":
            if context.chat_id is None:
                return False, None, None
            stats = await repo.get_user_stats(chat_id=context.chat_id, user_id=context.user_id)
            current = int(stats.message_count if stats is not None else 0)
            return current == 1, "first_message", {"value": current}

        if condition_type == "streak_days_gte":
            if context.chat_id is None:
                return False, None, None
            streak_days = await repo.get_user_message_streak_days(chat_id=context.chat_id, user_id=context.user_id)
            threshold = max(0, int(value or 0))
            return streak_days >= threshold, f"streak_days reached {streak_days}", {"value": streak_days, "target": threshold}

        if condition_type == "achievements_count_gte":
            total = await repo.count_total_achievements(user_id=context.user_id)
            threshold = max(0, int(value or 0))
            return total >= threshold, f"achievements_count reached {total}", {"value": total, "target": threshold}

        if condition_type == "joined_chat":
            joined = context.event_type == "member_joined" and context.chat_id is not None
            return joined, "joined_chat", {"chat_id": context.chat_id} if joined else None

        raise ValueError(f"Unsupported achievement condition type: {condition_type}")
