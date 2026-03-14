from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from selara.infrastructure.db.models import AdminSessionModel


class SqlAlchemyAdminAuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_admin_by_session(
        self,
        *,
        session_token: str,
        now: datetime,
        touch: bool,
    ) -> int | None:
        """Проверяет сессию админа и возвращает admin_user_id если валидна."""
        session_row = await self._session.get(AdminSessionModel, session_token)
        if session_row is None:
            return None
        if session_row.revoked_at is not None or session_row.expires_at <= now:
            return None

        if touch:
            session_row.last_seen_at = now
            await self._session.flush()

        return int(session_row.admin_user_id)

    async def create_session(
        self,
        *,
        admin_user_id: int,
        session_token: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        """Создаёт новую сессию админа."""
        self._session.add(
            AdminSessionModel(
                session_token=session_token,
                admin_user_id=admin_user_id,
                expires_at=expires_at,
                last_seen_at=now,
            )
        )
        await self._session.flush()

    async def revoke_session(self, *, session_token: str, now: datetime) -> None:
        """Отзывает сессию админа."""
        row = await self._session.get(AdminSessionModel, session_token)
        if row is None or row.revoked_at is not None:
            return
        row.revoked_at = now
        row.last_seen_at = now
        await self._session.flush()

    async def purge_expired_state(self, *, now: datetime) -> None:
        """Удаляет истёкшие и отозванные сессии."""
        await self._session.execute(
            delete(AdminSessionModel).where(
                (AdminSessionModel.expires_at <= now)
                | (
                    AdminSessionModel.revoked_at.is_not(None)
                    & (AdminSessionModel.revoked_at <= now - timedelta(days=1))
                )
            )
        )
        await self._session.flush()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
