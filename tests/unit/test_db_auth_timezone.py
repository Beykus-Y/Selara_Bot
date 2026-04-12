from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.domain.entities import UserSnapshot
from selara.infrastructure.db.admin_auth import SqlAlchemyAdminAuthRepository
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import UserModel
from selara.infrastructure.db.web_auth import SqlAlchemyWebAuthRepository

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")


@pytest.mark.asyncio
async def test_admin_auth_session_accepts_sqlite_naive_timestamps() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(timezone.utc)
    token = "admin-session"

    async with session_factory() as session:
        repo = SqlAlchemyAdminAuthRepository(session)
        await repo.create_session(
            admin_user_id=77,
            session_token=token,
            expires_at=now + timedelta(hours=1),
            now=now,
        )
        await session.commit()

    async with session_factory() as session:
        repo = SqlAlchemyAdminAuthRepository(session)
        admin_user_id = await repo.get_admin_by_session(
            session_token=token,
            now=now,
            touch=True,
        )

    assert admin_user_id == 77
    await engine.dispose()


@pytest.mark.asyncio
async def test_web_auth_session_accepts_sqlite_naive_timestamps() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(timezone.utc)
    session_digest = "viewer-session"

    async with session_factory() as session:
        session.add(
            UserModel(
                telegram_user_id=501,
                username="viewer",
                first_name="View",
                last_name="Er",
                is_bot=False,
            )
        )
        repo = SqlAlchemyWebAuthRepository(session)
        await repo.create_session(
            user_id=501,
            session_digest=session_digest,
            expires_at=now + timedelta(hours=1),
            now=now,
        )
        await session.commit()

    async with session_factory() as session:
        repo = SqlAlchemyWebAuthRepository(session)
        user = await repo.get_user_by_session(
            session_digest=session_digest,
            now=now,
            touch=True,
        )

    assert user == UserSnapshot(
        telegram_user_id=501,
        username="viewer",
        first_name="View",
        last_name="Er",
        is_bot=False,
    )
    await engine.dispose()
