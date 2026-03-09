from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from selara.application.achievements.catalog import AchievementCatalogService
from selara.domain.entities import AchievementAwardResult, AchievementDefinition
from selara.infrastructure.db.achievement_metrics import compute_holders_percent
from selara.infrastructure.db.models import (
    ChatAchievementStatsModel,
    GlobalAchievementStatsModel,
    UserChatActivityModel,
    UserChatAchievementModel,
    UserGlobalAchievementModel,
    UserModel,
)


class AchievementAwardService:
    def __init__(self, session: AsyncSession, catalog: AchievementCatalogService) -> None:
        self._session = session
        self._catalog = catalog

    async def award(
        self,
        definition: AchievementDefinition,
        *,
        user_id: int,
        chat_id: int | None,
        awarded_at: datetime,
        award_reason: str | None,
        meta_json: dict[str, Any] | None,
    ) -> AchievementAwardResult:
        if not definition.enabled:
            raise ValueError(f"Achievement {definition.id} is disabled.")
        if definition.scope == "chat":
            if chat_id is None:
                raise ValueError(f"Achievement {definition.id} requires chat_id.")
            return await self._award_chat(
                definition,
                chat_id=chat_id,
                user_id=user_id,
                awarded_at=awarded_at,
                award_reason=award_reason,
                meta_json=meta_json,
            )
        return await self._award_global(
            definition,
            user_id=user_id,
            awarded_at=awarded_at,
            award_reason=award_reason,
            meta_json=meta_json,
        )

    async def _award_chat(
        self,
        definition: AchievementDefinition,
        *,
        chat_id: int,
        user_id: int,
        awarded_at: datetime,
        award_reason: str | None,
        meta_json: dict[str, Any] | None,
    ) -> AchievementAwardResult:
        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"
        inserted = False
        if dialect == "postgresql":
            insert_stmt = (
                pg_insert(UserChatAchievementModel)
                .values(
                    chat_id=chat_id,
                    user_id=user_id,
                    achievement_id=definition.id,
                    awarded_at=awarded_at,
                    award_reason=award_reason,
                    meta_json=meta_json,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        UserChatAchievementModel.chat_id,
                        UserChatAchievementModel.user_id,
                        UserChatAchievementModel.achievement_id,
                    ]
                )
                .returning(UserChatAchievementModel.id)
            )
            inserted = (await self._session.execute(insert_stmt)).scalar_one_or_none() is not None
        else:
            existing = (
                await self._session.execute(
                    select(UserChatAchievementModel.id).where(
                        UserChatAchievementModel.chat_id == chat_id,
                        UserChatAchievementModel.user_id == user_id,
                        UserChatAchievementModel.achievement_id == definition.id,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                self._session.add(
                    UserChatAchievementModel(
                        chat_id=chat_id,
                        user_id=user_id,
                        achievement_id=definition.id,
                        awarded_at=awarded_at,
                        award_reason=award_reason,
                        meta_json=meta_json,
                    )
                )
                inserted = True

        await self._session.flush()
        base_count = await self._get_chat_base_count(chat_id)
        stats = await self._bump_chat_stats(
            chat_id=chat_id,
            achievement_id=definition.id,
            delta=1 if inserted else 0,
            base_count=base_count,
            awarded_at=awarded_at,
        )
        await self._session.flush()
        return AchievementAwardResult(
            awarded=inserted,
            achievement_id=definition.id,
            scope=definition.scope,
            awarded_at=awarded_at if inserted else None,
            holders_count=int(stats.holders_count),
            holders_percent=float(stats.holders_percent or 0),
        )

    async def _award_global(
        self,
        definition: AchievementDefinition,
        *,
        user_id: int,
        awarded_at: datetime,
        award_reason: str | None,
        meta_json: dict[str, Any] | None,
    ) -> AchievementAwardResult:
        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"
        inserted = False
        if dialect == "postgresql":
            insert_stmt = (
                pg_insert(UserGlobalAchievementModel)
                .values(
                    user_id=user_id,
                    achievement_id=definition.id,
                    awarded_at=awarded_at,
                    award_reason=award_reason,
                    meta_json=meta_json,
                )
                .on_conflict_do_nothing(
                    index_elements=[UserGlobalAchievementModel.user_id, UserGlobalAchievementModel.achievement_id]
                )
                .returning(UserGlobalAchievementModel.id)
            )
            inserted = (await self._session.execute(insert_stmt)).scalar_one_or_none() is not None
        else:
            existing = (
                await self._session.execute(
                    select(UserGlobalAchievementModel.id).where(
                        UserGlobalAchievementModel.user_id == user_id,
                        UserGlobalAchievementModel.achievement_id == definition.id,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                self._session.add(
                    UserGlobalAchievementModel(
                        user_id=user_id,
                        achievement_id=definition.id,
                        awarded_at=awarded_at,
                        award_reason=award_reason,
                        meta_json=meta_json,
                    )
                )
                inserted = True

        await self._session.flush()
        base_count = await self._get_global_base_count()
        stats = await self._bump_global_stats(
            achievement_id=definition.id,
            delta=1 if inserted else 0,
            base_count=base_count,
            awarded_at=awarded_at,
        )
        await self._session.flush()
        return AchievementAwardResult(
            awarded=inserted,
            achievement_id=definition.id,
            scope=definition.scope,
            awarded_at=awarded_at if inserted else None,
            holders_count=int(stats.holders_count),
            holders_percent=float(stats.holders_percent or 0),
        )

    async def _get_chat_base_count(self, chat_id: int) -> int:
        stmt = select(func.count()).select_from(UserChatActivityModel).where(
            UserChatActivityModel.chat_id == chat_id,
            UserChatActivityModel.is_active_member.is_(True),
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def _get_global_base_count(self) -> int:
        stmt = select(func.count()).select_from(UserModel)
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def _get_or_create_chat_stats(self, *, chat_id: int, achievement_id: str) -> ChatAchievementStatsModel:
        stmt = select(ChatAchievementStatsModel).where(
            ChatAchievementStatsModel.chat_id == chat_id,
            ChatAchievementStatsModel.achievement_id == achievement_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = ChatAchievementStatsModel(chat_id=chat_id, achievement_id=achievement_id)
            self._session.add(row)
            await self._session.flush()
        return row

    async def _get_or_create_global_stats(self, *, achievement_id: str) -> GlobalAchievementStatsModel:
        stmt = select(GlobalAchievementStatsModel).where(GlobalAchievementStatsModel.achievement_id == achievement_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = GlobalAchievementStatsModel(achievement_id=achievement_id)
            self._session.add(row)
            await self._session.flush()
        return row

    async def _bump_chat_stats(
        self,
        *,
        chat_id: int,
        achievement_id: str,
        delta: int,
        base_count: int,
        awarded_at: datetime,
    ) -> ChatAchievementStatsModel:
        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"
        holders_percent = compute_holders_percent(holders_count=max(0, delta), base_count=base_count)
        if dialect == "postgresql":
            stmt = pg_insert(ChatAchievementStatsModel).values(
                chat_id=chat_id,
                achievement_id=achievement_id,
                holders_count=max(0, delta),
                active_members_base_count=base_count,
                holders_percent=holders_percent,
                updated_at=awarded_at,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[ChatAchievementStatsModel.chat_id, ChatAchievementStatsModel.achievement_id],
                set_={
                    "holders_count": ChatAchievementStatsModel.holders_count + delta,
                    "active_members_base_count": base_count,
                    "updated_at": awarded_at,
                },
            )
            await self._session.execute(stmt)
            await self._session.flush()
            row = await self._get_or_create_chat_stats(chat_id=chat_id, achievement_id=achievement_id)
            row.active_members_base_count = base_count
            row.holders_percent = compute_holders_percent(holders_count=row.holders_count, base_count=base_count)
            row.updated_at = awarded_at
            return row

        row = await self._get_or_create_chat_stats(chat_id=chat_id, achievement_id=achievement_id)
        row.holders_count = max(0, int(row.holders_count) + delta)
        row.active_members_base_count = base_count
        row.holders_percent = compute_holders_percent(holders_count=row.holders_count, base_count=base_count)
        row.updated_at = awarded_at
        return row

    async def _bump_global_stats(
        self,
        *,
        achievement_id: str,
        delta: int,
        base_count: int,
        awarded_at: datetime,
    ) -> GlobalAchievementStatsModel:
        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"
        holders_percent = compute_holders_percent(holders_count=max(0, delta), base_count=base_count)
        if dialect == "postgresql":
            stmt = pg_insert(GlobalAchievementStatsModel).values(
                achievement_id=achievement_id,
                holders_count=max(0, delta),
                global_base_count=base_count,
                holders_percent=holders_percent,
                updated_at=awarded_at,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[GlobalAchievementStatsModel.achievement_id],
                set_={
                    "holders_count": GlobalAchievementStatsModel.holders_count + delta,
                    "global_base_count": base_count,
                    "updated_at": awarded_at,
                },
            )
            await self._session.execute(stmt)
            await self._session.flush()
            row = await self._get_or_create_global_stats(achievement_id=achievement_id)
            row.global_base_count = base_count
            row.holders_percent = compute_holders_percent(holders_count=row.holders_count, base_count=base_count)
            row.updated_at = awarded_at
            return row

        row = await self._get_or_create_global_stats(achievement_id=achievement_id)
        row.holders_count = max(0, int(row.holders_count) + delta)
        row.global_base_count = base_count
        row.holders_percent = compute_holders_percent(holders_count=row.holders_count, base_count=base_count)
        row.updated_at = awarded_at
        return row
