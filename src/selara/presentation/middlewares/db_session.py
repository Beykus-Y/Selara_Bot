import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.achievements import (
    AchievementAwardService,
    AchievementConditionEvaluator,
    AchievementOrchestrator,
    get_achievement_catalog_from_settings,
)
from selara.core.config import get_settings
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository, SqlAlchemyEconomyRepository
from selara.infrastructure.db.session import DatabaseAdmission
from selara.infrastructure.db.web_auth import SqlAlchemyWebAuthRepository

logger = logging.getLogger(__name__)


class DBSessionMiddleware(BaseMiddleware):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        admission: DatabaseAdmission | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._admission = admission

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        if self._admission is None:
            return await self._handle_with_session(handler, event, data)
        # The slot is taken before the session, so an update that waits here holds no pooled connection.
        async with self._admission.slot():
            return await self._handle_with_session(handler, event, data)

    async def _handle_with_session(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        async with self._session_factory() as session:
            settings = data.get("settings") or get_settings()
            data["session_factory"] = self._session_factory
            data["db_session"] = session
            data["activity_repo"] = SqlAlchemyActivityRepository(session)
            data["economy_repo"] = SqlAlchemyEconomyRepository(session)
            data["web_auth_repo"] = SqlAlchemyWebAuthRepository(session)
            catalog = get_achievement_catalog_from_settings(settings)
            data["achievement_orchestrator"] = AchievementOrchestrator(
                catalog=catalog,
                evaluator=AchievementConditionEvaluator(),
                award_service=AchievementAwardService(session, catalog),
                repo=data["activity_repo"],
            )

            try:
                result = await handler(event, data)
            except Exception:
                await _rollback_failed_update(session)
                raise

            await session.commit()
            return result


async def _rollback_failed_update(session: AsyncSession) -> None:
    # The handler's exception is the one to report. A rollback that fails too must not replace it. Closing the
    # session still returns the connection to the pool, which discards it when it is broken.
    try:
        await session.rollback()
    except Exception:
        logger.warning("Rollback after a failed update did not complete", exc_info=True)
