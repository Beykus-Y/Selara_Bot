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
            return current >= 1, f"message_count reached {current}", {"value": current, "target": 1}

        if condition_type == "streak_days_gte":
            if context.chat_id is None:
                return False, None, None
            streak_days = await repo.get_user_message_streak_days(chat_id=context.chat_id, user_id=context.user_id)
            threshold = max(0, int(value or 0))
            return streak_days >= threshold, f"streak_days reached {streak_days}", {"value": streak_days, "target": threshold}

        if condition_type == "achievements_count_gte":
            total = await repo.count_total_achievements(user_id=context.user_id, chat_id=context.chat_id)
            threshold = max(0, int(value or 0))
            return total >= threshold, f"achievements_count reached {total}", {"value": total, "target": threshold}

        if condition_type == "active_pair_gte":
            threshold = max(0, int(value or 0))
            current = 1 if await repo.get_active_pair(user_id=context.user_id, chat_id=context.chat_id) is not None else 0
            return current >= threshold, f"active_pair reached {current}", {"value": current, "target": threshold}

        if condition_type == "active_marriage_gte":
            threshold = max(0, int(value or 0))
            current = 1 if await repo.get_active_marriage(user_id=context.user_id, chat_id=context.chat_id) is not None else 0
            return current >= threshold, f"active_marriage reached {current}", {"value": current, "target": threshold}

        if condition_type == "owned_pets_gte":
            threshold = max(0, int(value or 0))
            current = await repo.count_owned_pets(user_id=context.user_id, chat_id=context.chat_id)
            return current >= threshold, f"owned_pets reached {current}", {"value": current, "target": threshold}

        if condition_type == "is_pet_gte":
            threshold = max(0, int(value or 0))
            current = await repo.count_pet_owners(user_id=context.user_id, chat_id=context.chat_id)
            return current >= threshold, f"is_pet reached {current}", {"value": current, "target": threshold}

        if condition_type == "daily_message_count_gte":
            if context.chat_id is None:
                return False, None, None
            current = await repo.get_user_message_count_for_day(
                chat_id=context.chat_id,
                user_id=context.user_id,
                activity_date=context.event_at.date(),
            )
            threshold = max(0, int(value or 0))
            return current >= threshold, f"daily_message_count reached {current}", {"value": current, "target": threshold}

        if condition_type == "joined_chat":
            joined = context.event_type == "member_joined" and context.chat_id is not None
            return joined, "joined_chat", {"chat_id": context.chat_id} if joined else None

        raise ValueError(f"Unsupported achievement condition type: {condition_type}")
