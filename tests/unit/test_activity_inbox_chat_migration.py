import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.achievements import AchievementCatalogService
from selara.infrastructure.db.activity_batcher import ActivityBatcher
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.chat_migration import migrate_chat_id
from selara.infrastructure.db.models import ChatModel, MessageArchiveModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")

OLD_CHAT_ID = -1001
NEW_CHAT_ID = -1002
UNRELATED_CHAT_ID = -1003
USER_ID = 1101


def _catalog() -> AchievementCatalogService:
    return AchievementCatalogService.load(Path("src/selara/core/achievements.json"))


async def _enqueue_message(batcher: ActivityBatcher, *, chat_id: int, message_id: int) -> None:
    sent_at = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)
    await batcher.enqueue_message(
        chat_id=chat_id,
        chat_type="group",
        chat_title="Before upgrade",
        user_id=USER_ID,
        username="frank",
        first_name="Frank",
        last_name=None,
        is_bot=False,
        event_at=sent_at,
        telegram_message_id=message_id,
        snapshot_kind="created",
        snapshot_at=sent_at,
        sent_at=sent_at,
        message_type="text",
        text=f"message {message_id}",
        raw_message_json={"message_id": message_id},
        snapshot_hash=f"hash-{chat_id}-{message_id}",
    )


@pytest.mark.asyncio
async def test_pending_inbox_rows_follow_a_chat_migration_and_keep_the_upgraded_chat() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    batcher = ActivityBatcher(session_factory=session_factory, catalog=_catalog(), flush_seconds=60, max_events=1000)
    for message_id in range(1, 4):
        await _enqueue_message(batcher, chat_id=OLD_CHAT_ID, message_id=message_id)
    await _enqueue_message(batcher, chat_id=UNRELATED_CHAT_ID, message_id=1)

    # The group becomes a supergroup while its three messages still wait in the inbox.
    async with session_factory() as session:
        result = await migrate_chat_id(
            session,
            old_chat_id=OLD_CHAT_ID,
            new_chat_id=NEW_CHAT_ID,
            new_chat_type="supergroup",
            new_chat_title="Upgraded",
        )
        await session.commit()
    assert result.migrated is True

    await batcher.start()
    await batcher.close()

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        migrated_stats = await repo.get_user_stats(chat_id=NEW_CHAT_ID, user_id=USER_ID)
        old_stats = await repo.get_user_stats(chat_id=OLD_CHAT_ID, user_id=USER_ID)
        unrelated_stats = await repo.get_user_stats(chat_id=UNRELATED_CHAT_ID, user_id=USER_ID)
        chat = await session.get(ChatModel, NEW_CHAT_ID)
        archived = (
            await session.execute(
                select(func.count()).select_from(MessageArchiveModel).where(MessageArchiveModel.chat_id == NEW_CHAT_ID)
            )
        ).scalar_one()

    assert migrated_stats is not None
    assert migrated_stats.message_count == 3
    assert old_stats is None
    assert unrelated_stats is not None
    assert unrelated_stats.message_count == 1
    assert archived == 3
    # The flusher writes each row's chat metadata back to the chat row. Stale "group" / "Before upgrade"
    # values would undo the upgrade, so the migrated chat must keep the new type and title.
    assert chat is not None
    assert (chat.type, chat.title) == ("supergroup", "Upgraded")
    await engine.dispose()
