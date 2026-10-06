from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import UserModel
from selara.infrastructure.db.personal_ai_repository import PersonalAiRepository


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all([UserModel(telegram_user_id=1, is_bot=False), UserModel(telegram_user_id=2, is_bot=False)])
        await s.commit()
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_profile_is_created_with_defaults_and_creating_twice_is_idempotent(session):
    repo = PersonalAiRepository(session)

    first = await repo.get_or_create_profile(1)
    second = await repo.get_or_create_profile(1)

    assert first.profile.display_name == "Selara"
    assert first.profile.mode == "assistant" and first.profile.formality == "ty"
    assert first.profile.emoji_enabled is True and first.revision == 0
    assert second.revision == 0


@pytest.mark.asyncio
async def test_profile_for_an_unknown_user_creates_the_user_row_first(session):
    stored = await PersonalAiRepository(session).get_or_create_profile(777)

    assert stored.profile.display_name == "Selara"
    assert await session.get(UserModel, 777) is not None


@pytest.mark.asyncio
async def test_update_bumps_revision_and_stale_revision_is_rejected(session):
    repo = PersonalAiRepository(session)
    await repo.get_or_create_profile(1)

    updated = await repo.update_profile(1, expected_revision=0, display_name="Селя", emoji_enabled=False)
    stale = await repo.update_profile(1, expected_revision=0, display_name="Другая")

    assert updated is not None and updated.revision == 1
    assert updated.profile.display_name == "Селя" and updated.profile.emoji_enabled is False
    assert stale is None
    assert (await repo.get_profile(1)).profile.display_name == "Селя"


@pytest.mark.asyncio
async def test_update_rejects_unknown_columns(session):
    repo = PersonalAiRepository(session)

    with pytest.raises(ValueError):
        await repo.update_profile(1, expected_revision=0, revision=99)


@pytest.mark.asyncio
async def test_history_is_per_user_and_per_thread_oldest_first(session):
    repo = PersonalAiRepository(session)
    for i in range(3):
        await repo.add_message(user_id=1, thread="assistant", role="user", content=f"u1-{i}")
    await repo.add_message(user_id=1, thread="roleplay", role="user", content="scene")
    await repo.add_message(user_id=2, thread="assistant", role="user", content="other user")

    recent = await repo.recent_messages(user_id=1, thread="assistant", limit=2)

    assert [m.content for m in recent] == ["u1-1", "u1-2"]
    assert [m.content for m in await repo.recent_messages(user_id=1, thread="roleplay", limit=10)] == ["scene"]
    assert [m.content for m in await repo.recent_messages(user_id=2, thread="assistant", limit=10)] == ["other user"]


@pytest.mark.asyncio
async def test_compression_marks_messages_and_summary_is_returned_latest_first(session):
    repo = PersonalAiRepository(session)
    rows = [await repo.add_message(user_id=1, thread="assistant", role="user", content=str(i)) for i in range(4)]
    now = datetime.now(timezone.utc)

    await repo.add_summary(
        user_id=1, thread="assistant", content="old", period_start=now - timedelta(days=2),
        period_end=now - timedelta(days=1), messages_count=2,
    )
    await repo.add_summary(
        user_id=1, thread="assistant", content="new", period_start=now - timedelta(days=1),
        period_end=now, messages_count=2,
    )
    await repo.mark_compressed(user_id=1, message_ids=[rows[0].id, rows[1].id])

    assert await repo.count_uncompressed(user_id=1, thread="assistant") == 2
    assert [m.content for m in await repo.oldest_uncompressed(user_id=1, thread="assistant", limit=10)] == ["2", "3"]
    assert (await repo.latest_summary(user_id=1, thread="assistant")).content == "new"
    assert await repo.latest_summary(user_id=1, thread="roleplay") is None


@pytest.mark.asyncio
async def test_mark_compressed_never_touches_another_users_messages(session):
    repo = PersonalAiRepository(session)
    mine = await repo.add_message(user_id=1, thread="assistant", role="user", content="mine")
    theirs = await repo.add_message(user_id=2, thread="assistant", role="user", content="theirs")

    await repo.mark_compressed(user_id=1, message_ids=[mine.id, theirs.id])

    assert await repo.count_uncompressed(user_id=2, thread="assistant") == 1


@pytest.mark.asyncio
async def test_reset_deletes_only_the_active_thread_and_keeps_profile(session):
    repo = PersonalAiRepository(session)
    await repo.update_profile(1, expected_revision=0, display_name="Селя")
    await repo.add_message(user_id=1, thread="assistant", role="user", content="a")
    await repo.add_message(user_id=1, thread="roleplay", role="user", content="r")
    await repo.add_message(user_id=2, thread="assistant", role="user", content="other")
    now = datetime.now(timezone.utc)
    await repo.add_summary(user_id=1, thread="assistant", content="s", period_start=now, period_end=now, messages_count=1)

    removed = await repo.reset_thread(user_id=1, thread="assistant")

    assert removed == 1
    assert await repo.latest_summary(user_id=1, thread="assistant") is None
    assert len(await repo.recent_messages(user_id=1, thread="roleplay", limit=5)) == 1
    assert len(await repo.recent_messages(user_id=2, thread="assistant", limit=5)) == 1
    assert (await repo.get_profile(1)).profile.display_name == "Селя"


@pytest.mark.asyncio
async def test_old_messages_are_never_deleted_without_a_user_action(session):
    repo = PersonalAiRepository(session)
    row = await repo.add_message(user_id=1, thread="assistant", role="user", content="ancient")
    row.created_at = datetime.now(timezone.utc) - timedelta(days=3650)
    await session.flush()

    await repo.add_message(user_id=1, thread="assistant", role="assistant", content="new")

    assert len(await repo.recent_messages(user_id=1, thread="assistant", limit=10)) == 2
