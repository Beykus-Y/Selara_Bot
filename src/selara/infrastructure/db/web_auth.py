from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from selara.domain.entities import UserSnapshot
from selara.infrastructure.db.achievement_metrics import increment_global_users_base_count
from selara.infrastructure.db.models import UserModel, WebLoginCodeModel, WebSessionModel


class SqlAlchemyWebAuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def invalidate_user_login_codes(self, *, user_id: int, now: datetime) -> None:
        stmt = (
            select(WebLoginCodeModel)
            .where(
                WebLoginCodeModel.user_id == user_id,
                WebLoginCodeModel.used_at.is_(None),
            )
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        for row in rows:
            row.used_at = now
        await self._session.flush()

    async def has_active_login_code_digest(self, *, code_digest: str, now: datetime) -> bool:
        stmt = (
            select(WebLoginCodeModel.id)
            .where(
                WebLoginCodeModel.code_digest == code_digest,
                WebLoginCodeModel.used_at.is_(None),
                WebLoginCodeModel.expires_at > now,
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def create_login_code(
        self,
        *,
        user: UserSnapshot,
        code_digest: str,
        expires_at: datetime,
    ) -> None:
        await self._upsert_user(user)
        self._session.add(
            WebLoginCodeModel(
                user_id=user.telegram_user_id,
                code_digest=code_digest,
                expires_at=expires_at,
            )
        )
        await self._session.flush()

    async def consume_login_code(self, *, code_digest: str, now: datetime) -> UserSnapshot | None:
        stmt = (
            select(WebLoginCodeModel, UserModel)
            .join(UserModel, UserModel.telegram_user_id == WebLoginCodeModel.user_id)
            .where(
                WebLoginCodeModel.code_digest == code_digest,
                WebLoginCodeModel.used_at.is_(None),
                WebLoginCodeModel.expires_at > now,
            )
            .order_by(WebLoginCodeModel.created_at.desc(), WebLoginCodeModel.id.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None

        code_row, user_row = row
        code_row.used_at = now
        await self._session.flush()
        return self._to_user_snapshot(user_row)

    async def create_session(
        self,
        *,
        user_id: int,
        session_digest: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        self._session.add(
            WebSessionModel(
                session_digest=session_digest,
                user_id=user_id,
                expires_at=expires_at,
                last_seen_at=now,
            )
        )
        await self._session.flush()

    async def get_user_by_session(self, *, session_digest: str, now: datetime, touch: bool) -> UserSnapshot | None:
        session_row = await self._session.get(WebSessionModel, session_digest)
        if session_row is None:
            return None
        if session_row.revoked_at is not None or session_row.expires_at <= now:
            return None

        if touch:
            session_row.last_seen_at = now

        user_row = await self._session.get(UserModel, int(session_row.user_id))
        if user_row is None:
            return None
        await self._session.flush()
        return self._to_user_snapshot(user_row)

    async def revoke_session(self, *, session_digest: str, now: datetime) -> None:
        row = await self._session.get(WebSessionModel, session_digest)
        if row is None or row.revoked_at is not None:
            return
        row.revoked_at = now
        row.last_seen_at = now
        await self._session.flush()

    async def purge_expired_state(self, *, now: datetime) -> None:
        await self._session.execute(
            delete(WebLoginCodeModel).where(
                (WebLoginCodeModel.expires_at <= now)
                | (
                    WebLoginCodeModel.used_at.is_not(None)
                    & (WebLoginCodeModel.used_at <= now - timedelta(days=1))
                )
            )
        )
        await self._session.execute(
            delete(WebSessionModel).where(
                (WebSessionModel.expires_at <= now)
                | (
                    WebSessionModel.revoked_at.is_not(None)
                    & (WebSessionModel.revoked_at <= now - timedelta(days=1))
                )
            )
        )
        await self._session.flush()

    async def _upsert_user(self, user: UserSnapshot) -> None:
        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"

        if dialect == "postgresql":
            insert_stmt = (
                pg_insert(UserModel)
                .values(
                    telegram_user_id=user.telegram_user_id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    is_bot=user.is_bot,
                )
                .on_conflict_do_nothing(index_elements=[UserModel.telegram_user_id])
                .returning(UserModel.telegram_user_id)
            )
            inserted_user_id = (await self._session.execute(insert_stmt)).scalar_one_or_none()
            if inserted_user_id is not None:
                await increment_global_users_base_count(self._session)

            stmt = pg_insert(UserModel).values(
                telegram_user_id=user.telegram_user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                is_bot=user.is_bot,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[UserModel.telegram_user_id],
                set_={
                    "username": stmt.excluded.username,
                    "first_name": stmt.excluded.first_name,
                    "last_name": stmt.excluded.last_name,
                    "is_bot": stmt.excluded.is_bot,
                    "updated_at": func.now(),
                },
            )
            await self._session.execute(stmt)
            return

        row = await self._session.get(UserModel, user.telegram_user_id)
        if row is None:
            self._session.add(
                UserModel(
                    telegram_user_id=user.telegram_user_id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    is_bot=user.is_bot,
                )
            )
            await self._session.flush()
            await increment_global_users_base_count(self._session)
            return

        row.username = user.username
        row.first_name = user.first_name
        row.last_name = user.last_name
        row.is_bot = user.is_bot

    @staticmethod
    def _to_user_snapshot(row: UserModel) -> UserSnapshot:
        return UserSnapshot(
            telegram_user_id=int(row.telegram_user_id),
            username=row.username,
            first_name=row.first_name,
            last_name=row.last_name,
            is_bot=bool(row.is_bot),
        )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
