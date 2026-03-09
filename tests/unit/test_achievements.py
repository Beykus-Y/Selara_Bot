from datetime import datetime, timezone
from pathlib import Path

import pytest

from selara.application.achievements.catalog import AchievementCatalogService
from selara.application.achievements.conditions import AchievementConditionEvaluator, AchievementEvaluationContext
from selara.domain.entities import AchievementDefinition, ActivityStats
from selara.infrastructure.db.achievement_metrics import compute_holders_percent


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

    async def get_user_message_count_for_day(self, *, chat_id: int, user_id: int, activity_date) -> int:
        assert chat_id == 1
        assert user_id == 10
        return 100

    async def count_total_achievements(self, *, user_id: int, chat_id: int | None = None) -> int:
        assert user_id == 10
        assert chat_id is None
        return 3

    async def get_active_pair(self, *, user_id: int, chat_id: int | None = None):
        assert user_id == 10
        assert chat_id == 1
        return object()

    async def get_active_marriage(self, *, user_id: int, chat_id: int | None = None):
        assert user_id == 10
        assert chat_id == 1
        return object()

    async def count_owned_pets(self, *, user_id: int, chat_id: int | None = None) -> int:
        assert user_id == 10
        assert chat_id == 1
        return 1

    async def count_pet_owners(self, *, user_id: int, chat_id: int | None = None) -> int:
        assert user_id == 10
        assert chat_id == 1
        return 1


def test_achievement_catalog_loads_and_sorts() -> None:
    catalog = AchievementCatalogService.load(Path("src/selara/core/achievements.json"))

    chat_items = catalog.list_by_scope("chat")
    global_items = catalog.list_by_scope("global")

    assert [item.id for item in chat_items] == [
        "first_message",
        "chat_100_messages",
        "chat_7_days_streak",
        "chat_100_messages_day",
        "chat_200_messages_day",
        "chat_500_messages_day",
        "chat_1000_messages_day",
        "chat_1500_messages_day",
        "chat_3000_messages_day",
        "global_found_pair",
        "global_married",
        "global_pet_owner",
        "global_became_pet",
    ]
    assert [item.id for item in global_items] == ["global_3_achievements"]


def test_compute_holders_percent_is_capped_at_100() -> None:
    assert float(compute_holders_percent(holders_count=3, base_count=1)) == 100.0


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
    daily_def = AchievementDefinition(
        id="chat_100_messages_day",
        scope="chat",
        title="",
        description="",
        hidden=False,
        rarity="rare",
        icon="",
        sort_order=4,
        enabled=True,
        condition_type="daily_message_count_gte",
        condition_payload={"value": 100},
        tags=(),
    )
    pair_def = AchievementDefinition(
        id="global_found_pair",
        scope="chat",
        title="",
        description="",
        hidden=False,
        rarity="rare",
        icon="",
        sort_order=5,
        enabled=True,
        condition_type="active_pair_gte",
        condition_payload={"value": 1},
        tags=(),
    )
    marriage_def = AchievementDefinition(
        id="global_married",
        scope="chat",
        title="",
        description="",
        hidden=False,
        rarity="rare",
        icon="",
        sort_order=6,
        enabled=True,
        condition_type="active_marriage_gte",
        condition_payload={"value": 1},
        tags=(),
    )
    pet_owner_def = AchievementDefinition(
        id="global_pet_owner",
        scope="chat",
        title="",
        description="",
        hidden=False,
        rarity="rare",
        icon="",
        sort_order=7,
        enabled=True,
        condition_type="owned_pets_gte",
        condition_payload={"value": 1},
        tags=(),
    )
    became_pet_def = AchievementDefinition(
        id="global_became_pet",
        scope="chat",
        title="",
        description="",
        hidden=False,
        rarity="rare",
        icon="",
        sort_order=8,
        enabled=True,
        condition_type="is_pet_gte",
        condition_payload={"value": 1},
        tags=(),
    )

    global_context = AchievementEvaluationContext(
        user_id=10,
        chat_id=None,
        event_type="global_refresh",
        event_at=context.event_at,
    )

    assert (await evaluator.is_satisfied(message_def, repo=repo, context=context))[0] is True
    assert (await evaluator.is_satisfied(streak_def, repo=repo, context=context))[0] is True
    assert (await evaluator.is_satisfied(daily_def, repo=repo, context=context))[0] is True
    assert (await evaluator.is_satisfied(global_def, repo=repo, context=global_context))[0] is True
    assert (await evaluator.is_satisfied(pair_def, repo=repo, context=context))[0] is True
    assert (await evaluator.is_satisfied(marriage_def, repo=repo, context=context))[0] is True
    assert (await evaluator.is_satisfied(pet_owner_def, repo=repo, context=context))[0] is True
    assert (await evaluator.is_satisfied(became_pet_def, repo=repo, context=context))[0] is True
