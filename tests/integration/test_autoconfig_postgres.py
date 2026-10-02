"""Real locking/lease tests: one paid turn or save wins across workers."""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure.db.autoconfig_repository import AutoConfigRepository
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import ChatModel, UserModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.presentation.handlers.settings_common import settings_to_dict
from selara.presentation.handlers.autoconfig import action, _data


@pytest.fixture
async def factory():
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL is not set')
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([ChatModel(telegram_chat_id=-1001, type='supergroup', title='Test'), UserModel(telegram_user_id=1, is_bot=False)])
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.mark.integration
async def test_concurrent_turn_claims_only_one_wins(factory):
    async with factory() as session:
        row = await AutoConfigRepository(session).start(1, [])
        row.state = 'active'
        await session.commit()
    async def claim():
        async with factory() as session:
            repository = AutoConfigRepository(session)
            row = await repository.get(1)
            return await repository.claim_turn(row, cooldown=5)
    results = await asyncio.gather(claim(), claim())
    assert sum(r is not None for r in results) == 1


@pytest.mark.integration
async def test_concurrent_save_callbacks_only_one_audit_entry(factory, monkeypatch):
    import selara.presentation.handlers.autoconfig as handler
    monkeypatch.setattr(handler, 'can_configure', AsyncMock(return_value=True))
    settings = Settings(bot_token='123:TEST', database_url=os.environ['TEST_DATABASE_URL'])
    async with factory() as session:
        row = await AutoConfigRepository(session).start(1, [])
        row.chat_id, row.state = -1001, 'review'
        row.baseline = settings_to_dict(default_chat_settings(settings))
        row.draft = {**row.baseline, 'welcome_enabled': not row.baseline['welcome_enabled']}
        data = _data(row, 'save')
        await session.commit()
    async def save():
        async with factory() as session:
            cb = SimpleNamespace(data=data, from_user=SimpleNamespace(id=1), answer=AsyncMock(),
                message=SimpleNamespace(chat=SimpleNamespace(type='private', id=1), answer=AsyncMock()))
            await action(cb, SimpleNamespace(), session, SqlAlchemyActivityRepository(session), settings)
            await session.commit()
    await asyncio.gather(save(), save())
    async with factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        assert (await repo.get_chat_settings(chat_id=-1001)).welcome_enabled != default_chat_settings(settings).welcome_enabled
        assert len(await repo.list_audit_logs(chat_id=-1001)) == 1
