from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.domain.entities import UserSnapshot
from selara.infrastructure.db.models import UserModel, WebLoginCodeModel
from selara.infrastructure.db.web_auth import SqlAlchemyWebAuthRepository


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_login_code_can_only_be_consumed_once_concurrently() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_async_engine(database_url, pool_size=4, max_overflow=0)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id = 8_000_000_000 + uuid.uuid4().int % 1_000_000_000
    code_digest = uuid.uuid4().hex
    now = datetime.now(timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyWebAuthRepository(session)
        await repo.create_login_code(
            user=UserSnapshot(
                telegram_user_id=user_id,
                username="concurrent_login",
                first_name="Concurrent",
                last_name=None,
                is_bot=False,
            ),
            code_digest=code_digest,
            expires_at=now + timedelta(minutes=5),
        )
        await session.commit()

    first_consumed = asyncio.Event()
    release_first = asyncio.Event()

    async def consume_first():
        async with session_factory() as session:
            result = await SqlAlchemyWebAuthRepository(session).consume_login_code(
                code_digest=code_digest,
                now=now,
            )
            first_consumed.set()
            await release_first.wait()
            await session.commit()
            return result

    async def consume_second():
        await first_consumed.wait()
        async with session_factory() as session:
            result = await SqlAlchemyWebAuthRepository(session).consume_login_code(
                code_digest=code_digest,
                now=now,
            )
            await session.commit()
            return result

    first_task = asyncio.create_task(consume_first())
    second_task = asyncio.create_task(consume_second())
    await first_consumed.wait()
    await asyncio.sleep(0.1)
    release_first.set()
    results = await asyncio.gather(first_task, second_task)

    assert sum(result is not None for result in results) == 1

    async with session_factory() as session:
        await session.execute(delete(WebLoginCodeModel).where(WebLoginCodeModel.user_id == user_id))
        await session.execute(delete(UserModel).where(UserModel.telegram_user_id == user_id))
        await session.commit()
    await engine.dispose()
