import importlib.util
import os
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.domain.entities import ChatSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import ChatModel, ChatSettingsModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.presentation.handlers.settings_common import settings_to_dict


pytestmark = pytest.mark.integration


@pytest.fixture
async def engine():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


async def test_setting_persists_per_chat_and_survives_partial_updates(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    defaults = settings_to_dict(default_chat_settings(Settings(bot_token="123456:TEST", database_url="sqlite:///")))
    async with factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        for chat_id, enabled in ((-100, False), (-200, True)):
            chat = ChatSnapshot(telegram_chat_id=chat_id, chat_type="supergroup", title="STT chat")
            await repo.upsert_chat_settings(chat=chat, values={**defaults, "instant_stt_enabled": enabled})
        await session.commit()
    async with factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.upsert_chat_settings(chat=ChatSnapshot(telegram_chat_id=-100, chat_type="supergroup", title="STT chat"),
                                       values={"daily_summary_include_voice": True})
        await session.commit()
    async with factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        off = await repo.get_chat_settings(chat_id=-100)
        assert off.instant_stt_enabled is False and off.daily_summary_include_voice is True
        assert (await repo.get_chat_settings(chat_id=-200)).instant_stt_enabled is True


async def test_migration_preserves_old_auto_transcription_behavior(engine, monkeypatch):
    factory = async_sessionmaker(engine)
    async with factory() as session:
        session.add(ChatModel(telegram_chat_id=-100, type="supergroup", title="Existing chat"))
        await session.flush()
        await SqlAlchemyActivityRepository(session).upsert_chat_settings(
            chat=ChatSnapshot(telegram_chat_id=-100, chat_type="supergroup", title="Existing chat"), values={},
        )
        await session.commit()
    # Recreate the pre-migration column layout with an existing settings row.
    async with engine.begin() as connection:
        await connection.execute(text("ALTER TABLE chat_settings DROP COLUMN instant_stt_enabled"))
    path = Path(__file__).parents[2] / "alembic/versions/0101_chat_instant_stt.py"
    spec = importlib.util.spec_from_file_location("migration_instant_stt", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def upgrade(connection):
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()

    async with engine.begin() as connection:
        await connection.run_sync(upgrade)
    async with factory() as session:
        row = await session.get(ChatSettingsModel, -100)
        assert row.instant_stt_enabled is True
        session.add(ChatModel(telegram_chat_id=-200, type="supergroup", title="New chat"))
        await session.flush()
        await session.execute(text(
            "INSERT INTO chat_settings (chat_id, top_limit_default, top_limit_max, vote_daily_limit, "
            "leaderboard_hybrid_karma_weight, leaderboard_hybrid_activity_weight, leaderboard_7d_days) "
            "VALUES (-200, 10, 50, 20, 0.7, 0.3, 7)"
        ))
        await session.commit()
    async with factory() as session:
        assert (await session.get(ChatSettingsModel, -200)).instant_stt_enabled is True
