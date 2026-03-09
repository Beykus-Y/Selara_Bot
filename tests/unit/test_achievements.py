from datetime import datetime, timezone
from pathlib import Path

import pytest

from selara.application.achievements.catalog import AchievementCatalogService
from selara.application.achievements.conditions import AchievementConditionEvaluator, AchievementEvaluationContext
from selara.domain.entities import AchievementDefinition, ActivityStats


class FakeAchievementRepo:
    def __init__(self) -> None:
        self.stats = ActivityStats(
            chat_id=1,
            user_id=10,
            message_count=100,
            last_seen_at=datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc),
        )

    async def get_user_stats(self, *, chat_id: int, user_id: int) -> ActivityStats | None:
        if chat_id == 1 and user_id == 10:
            return self.stats
        return None

    async def get_user_message_streak_days(self, *, chat_id: int, user_id: int) -> int:
        assert chat_id == 1
        assert user_id == 10
        return 7

    async def count_total_achievements(self, *, user_id: int) -> int:
        assert user_id == 10
        return 3


def test_achievement_catalog_loads_and_sorts() -> None:
    catalog = AchievementCatalogService.load(Path("src/selara/core/achievements.json"))

    chat_items = catalog.list_by_scope("chat")
    global_items = catalog.list_by_scope("global")

    assert [item.id for item in chat_items] == ["first_message", "chat_100_messages", "chat_7_days_streak"]
    assert [item.id for item in global_items] == ["global_3_achievements"]


@pytest.mark.asyncio
async def test_achievement_condition_evaluator_supports_core_conditions() -> None:
    evaluator = AchievementConditionEvaluator()
    repo = FakeAchievementRepo()
    context = AchievementEvaluationContext(
        user_id=10,
        chat_id=1,
        event_type="message",
        event_at=datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc),
    )

    message_def = AchievementDefinition(
        id="chat_100_messages",
        scope="chat",
        title="",
        description="",
        hidden=False,
        rarity="common",
        icon="",
        sort_order=1,
        enabled=True,
        condition_type="message_count_gte",
        condition_payload={"value": 100},
        tags=(),
    )
    streak_def = AchievementDefinition(
        id="chat_7_days_streak",
        scope="chat",
        title="",
        description="",
        hidden=False,
        rarity="common",
        icon="",
        sort_order=2,
        enabled=True,
        condition_type="streak_days_gte",
        condition_payload={"value": 7},
        tags=(),
    )
    global_def = AchievementDefinition(
        id="global_3_achievements",
        scope="global",
        title="",
        description="",
        hidden=False,
        rarity="rare",
        icon="",
        sort_order=3,
        enabled=True,
        condition_type="achievements_count_gte",
        condition_payload={"value": 3},
        tags=(),
    )

    assert (await evaluator.is_satisfied(message_def, repo=repo, context=context))[0] is True
    assert (await evaluator.is_satisfied(streak_def, repo=repo, context=context))[0] is True
    assert (
        await evaluator.is_satisfied(
            global_def,
            repo=repo,
            context=AchievementEvaluationContext(
                user_id=10,
                chat_id=None,
                event_type="global_refresh",
                event_at=context.event_at,
            ),
        )
    )[0] is True
