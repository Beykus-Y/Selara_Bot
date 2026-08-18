from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.core.config import Settings
from selara.domain.entities import RelationshipCleanupSummary
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.presentation.gacha_reel_orchestration import is_gacha_animation_cache_ready

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

    try:
        async with session_factory() as gacha_session:
            gacha_repo = SqlAlchemyActivityRepository(gacha_session)
            gacha_ready = await is_gacha_animation_cache_ready(settings, gacha_repo)
        gacha_status_line = (
            "Гача-анимации: прогреты." if gacha_ready else "Гача-анимации: не прогреты, прогрев запускается в фоне."
        )
    except Exception:
        logger.exception("Could not check gacha animation cache readiness for the startup report")
        gacha_status_line = "Гача-анимации: не удалось проверить статус прогрева."

    report = (
        "Выполнена чистка. "
        f"Удалено фантомных браков: {summary.marriages_removed}. "
        f"Удалено остаточных пар: {summary.pairs_removed}. "
        f"Отменено предложений: {summary.proposals_cancelled}. "
        f"Заархивировано семейных связей: {summary.family_links_archived}. "
        f"{gacha_status_line}"
    )
    logger.info(report)

    if settings.admin_user_id is not None:
        try:
            await bot.send_message(chat_id=settings.admin_user_id, text=report)
        except Exception:
            logger.exception("Could not send startup relationship cleanup report to admin")
    return summary
