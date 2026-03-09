from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from selara.infrastructure.db.models import (
    ChatAchievementStatsModel,
    ChatMetricsModel,
    GlobalAchievementStatsModel,
    GlobalMetricsModel,
)

_PERCENT_QUANT = Decimal("0.01")


def compute_holders_percent(*, holders_count: int, base_count: int) -> Decimal:
    normalized_holders = max(0, int(holders_count))
    normalized_base = max(0, int(base_count))
    if normalized_holders <= 0 or normalized_base <= 0:
        return Decimal("0.00")
    return min(
        Decimal("100.00"),
        ((Decimal(normalized_holders) * Decimal("100")) / Decimal(normalized_base)).quantize(
            _PERCENT_QUANT,
            rounding=ROUND_HALF_UP,
        ),
    )


async def increment_global_users_base_count(session: AsyncSession) -> int:
    dialect = session.bind.dialect.name if session.bind else "unknown"
    if dialect == "postgresql":
        stmt = pg_insert(GlobalMetricsModel).values(id=1, global_users_base_count=1).on_conflict_do_update(
            index_elements=[GlobalMetricsModel.id],
            set_={
                "global_users_base_count": GlobalMetricsModel.global_users_base_count + 1,
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)
    else:
        row = await session.get(GlobalMetricsModel, 1)
        if row is None:
            session.add(GlobalMetricsModel(id=1, global_users_base_count=1))
        else:
            row.global_users_base_count += 1
            row.updated_at = datetime.now(timezone.utc)

    await session.flush()
    row = await session.get(GlobalMetricsModel, 1)
    return int(row.global_users_base_count if row is not None else 0)


async def refresh_global_achievement_stats_base(session: AsyncSession, *, base_count: int) -> None:
    rows = (
        await session.execute(select(GlobalAchievementStatsModel).order_by(GlobalAchievementStatsModel.id.asc()))
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for row in rows:
        row.global_base_count = int(base_count)
        row.holders_percent = compute_holders_percent(holders_count=row.holders_count, base_count=base_count)
        row.updated_at = now
    await session.flush()


async def set_global_users_base_count(session: AsyncSession, *, base_count: int) -> None:
    normalized_base = max(0, int(base_count))
    row = await session.get(GlobalMetricsModel, 1)
    if row is None:
        session.add(GlobalMetricsModel(id=1, global_users_base_count=normalized_base))
    else:
        row.global_users_base_count = normalized_base
        row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    await refresh_global_achievement_stats_base(session, base_count=normalized_base)


async def adjust_chat_active_members_count(session: AsyncSession, *, chat_id: int, delta: int) -> int:
    normalized_delta = int(delta)
    if normalized_delta == 0:
        row = await session.get(ChatMetricsModel, chat_id)
        return int(row.active_members_count if row is not None else 0)

    dialect = session.bind.dialect.name if session.bind else "unknown"
    if dialect == "postgresql":
        initial_value = max(normalized_delta, 0)
        stmt = pg_insert(ChatMetricsModel).values(
            chat_id=chat_id,
            active_members_count=initial_value,
        ).on_conflict_do_update(
            index_elements=[ChatMetricsModel.chat_id],
            set_={
                "active_members_count": func.greatest(0, ChatMetricsModel.active_members_count + normalized_delta),
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)
    else:
        row = await session.get(ChatMetricsModel, chat_id)
        if row is None:
            session.add(ChatMetricsModel(chat_id=chat_id, active_members_count=max(normalized_delta, 0)))
        else:
            row.active_members_count = max(0, int(row.active_members_count) + normalized_delta)
            row.updated_at = datetime.now(timezone.utc)

    await session.flush()
    row = await session.get(ChatMetricsModel, chat_id)
    normalized_count = int(row.active_members_count if row is not None else 0)
    await refresh_chat_achievement_stats_base(session, chat_id=chat_id, active_members_count=normalized_count)
    return normalized_count


async def refresh_chat_achievement_stats_base(
    session: AsyncSession,
    *,
    chat_id: int,
    active_members_count: int,
) -> None:
    rows = (
        await session.execute(
            select(ChatAchievementStatsModel)
            .where(ChatAchievementStatsModel.chat_id == chat_id)
            .order_by(ChatAchievementStatsModel.id.asc())
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for row in rows:
        row.active_members_base_count = int(active_members_count)
        row.holders_percent = compute_holders_percent(
            holders_count=row.holders_count,
            base_count=active_members_count,
        )
        row.updated_at = now
    await session.flush()


async def set_chat_active_members_count(session: AsyncSession, *, chat_id: int, active_members_count: int) -> None:
    normalized_count = max(0, int(active_members_count))
    row = await session.get(ChatMetricsModel, chat_id)
    if row is None:
        session.add(ChatMetricsModel(chat_id=chat_id, active_members_count=normalized_count))
    else:
        row.active_members_count = normalized_count
        row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    await refresh_chat_achievement_stats_base(session, chat_id=chat_id, active_members_count=normalized_count)
