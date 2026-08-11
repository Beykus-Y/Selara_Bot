from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.core.config import Settings
from selara.domain.entities import RelationshipCleanupSummary
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

logger = logging.getLogger(__name__)


async def run_startup_relationship_cleanup(
    *,
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    now: datetime | None = None,
) -> RelationshipCleanupSummary:
    cleanup_at = now or datetime.now(timezone.utc)
    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        try:
            summary = await repo.cleanup_phantom_relationships(event_at=cleanup_at)
        except Exception:
            await session.rollback()
            raise
        await session.commit()

    report = (
        "Выполнена чистка. "
        f"Удалено фантомных браков: {summary.marriages_removed}. "
        f"Удалено остаточных пар: {summary.pairs_removed}. "
        f"Отменено предложений: {summary.proposals_cancelled}. "
        f"Заархивировано семейных связей: {summary.family_links_archived}."
    )
    logger.info(report)

    if settings.admin_user_id is not None:
        try:
            await bot.send_message(chat_id=settings.admin_user_id, text=report)
        except Exception:
            logger.exception("Could not send startup relationship cleanup report to admin")
    return summary
