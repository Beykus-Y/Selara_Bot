from __future__ import annotations

from datetime import datetime

from selara.application.achievements.award import AchievementAwardService
from selara.application.achievements.catalog import AchievementCatalogService
from selara.application.achievements.conditions import AchievementConditionEvaluator, AchievementEvaluationContext
from selara.domain.entities import AchievementAwardResult


class AchievementOrchestrator:
    def __init__(
        self,
        *,
        catalog: AchievementCatalogService,
        evaluator: AchievementConditionEvaluator,
        award_service: AchievementAwardService,
        repo,
    ) -> None:
        self._catalog = catalog
        self._evaluator = evaluator
        self._award_service = award_service
        self._repo = repo

    async def process_message(self, *, chat_id: int, user_id: int, event_at: datetime) -> list[AchievementAwardResult]:
        results = await self._evaluate_scope(
            scope="chat",
            context=AchievementEvaluationContext(
                user_id=user_id,
                chat_id=chat_id,
                event_type="message",
                event_at=event_at,
            ),
        )
        results.extend(await self._evaluate_global(user_id=user_id, event_at=event_at))
        return [item for item in results if item.awarded]

    async def process_membership(
        self,
        *,
        chat_id: int,
        user_id: int,
        is_active: bool,
        event_at: datetime,
    ) -> list[AchievementAwardResult]:
        event_type = "member_joined" if is_active else "member_left"
        results = await self._evaluate_scope(
            scope="chat",
            context=AchievementEvaluationContext(
                user_id=user_id,
                chat_id=chat_id,
                event_type=event_type,
                event_at=event_at,
            ),
        )
        results.extend(await self._evaluate_global(user_id=user_id, event_at=event_at))
        return [item for item in results if item.awarded]

    async def _evaluate_scope(self, *, scope: str, context: AchievementEvaluationContext) -> list[AchievementAwardResult]:
        results: list[AchievementAwardResult] = []
        for definition in self._catalog.list_by_scope(scope, enabled_only=True):
            matched, reason, meta_json = await self._evaluator.is_satisfied(
                definition,
                repo=self._repo,
                context=context,
            )
            if not matched:
                continue
            results.append(
                await self._award_service.award(
                    definition,
                    user_id=context.user_id,
                    chat_id=context.chat_id,
                    awarded_at=context.event_at,
                    award_reason=reason,
                    meta_json=meta_json,
                )
            )
        return results

    async def _evaluate_global(self, *, user_id: int, event_at: datetime) -> list[AchievementAwardResult]:
        return await self._evaluate_scope(
            scope="global",
            context=AchievementEvaluationContext(
                user_id=user_id,
                chat_id=None,
                event_type="global_refresh",
                event_at=event_at,
            ),
        )
