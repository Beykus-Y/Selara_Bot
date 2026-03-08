from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository, SqlAlchemyEconomyRepository
from selara.infrastructure.db.web_auth import SqlAlchemyWebAuthRepository


class DBSessionMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        async with self._session_factory() as session:
            data["session_factory"] = self._session_factory
            data["db_session"] = session
            data["activity_repo"] = SqlAlchemyActivityRepository(session)
            data["economy_repo"] = SqlAlchemyEconomyRepository(session)
            data["web_auth_repo"] = SqlAlchemyWebAuthRepository(session)

            try:
                result = await handler(event, data)
            except Exception:
                await session.rollback()
                raise

            await session.commit()
            return result
