from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import (
    PersonalAiMemoryModel,
    PersonalAiMessageModel,
    PersonalAiProfileModel,
    PersonalAiSummaryModel,
    UserModel,
)
from selara.infrastructure.db.personal_ai_repository import AddMemoryStatus, PersonalAiRepository


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


async def _add(repo, user_id, text, *, limit=20, source="explicit"):
    return await repo.add_memory(user_id=user_id, content=text, source=source, limit=limit)


async def test_memory_is_added_listed_and_scoped_to_its_owner(session):
    repo = PersonalAiRepository(session)

    first = await _add(repo, 1, "я веган")
    await _add(repo, 1, "живу в Казани", source="extracted")
    await _add(repo, 2, "секрет второго пользователя")

    assert first.status == AddMemoryStatus.ADDED and first.memory.source == "explicit"
    mine = await repo.list_memories(user_id=1)
    assert [m.content for m in mine] == ["я веган", "живу в Казани"]
    assert [m.source for m in mine] == ["explicit", "extracted"]
    assert [m.content for m in await repo.list_memories(user_id=2)] == ["секрет второго пользователя"]
    assert await repo.count_memories(user_id=1) == 2


async def test_duplicates_are_ignored_case_insensitively_per_user(session):
    repo = PersonalAiRepository(session)
    await _add(repo, 1, "Я веган")

    again = await _add(repo, 1, "я ВЕГАН")
    other_user = await _add(repo, 2, "я веган")

    assert again.status == AddMemoryStatus.DUPLICATE
    assert other_user.status == AddMemoryStatus.ADDED
    assert await repo.count_memories(user_id=1) == 1


async def test_limit_is_enforced_without_evicting_anything(session):
    repo = PersonalAiRepository(session)
    for i in range(3):
        assert (await _add(repo, 1, f"факт {i}", limit=3)).status == AddMemoryStatus.ADDED

    blocked = await _add(repo, 1, "лишний", limit=3)

    assert blocked.status == AddMemoryStatus.LIMIT_REACHED and blocked.memory is None
    assert [m.content for m in await repo.list_memories(user_id=1)] == ["факт 0", "факт 1", "факт 2"]
    # A duplicate is reported as such even when the list is full.
    assert (await _add(repo, 1, "факт 1", limit=3)).status == AddMemoryStatus.DUPLICATE


async def test_lowering_the_limit_later_does_not_delete_existing_memories(session):
    repo = PersonalAiRepository(session)
    for i in range(5):
        await _add(repo, 1, f"факт {i}", limit=10)

    assert (await _add(repo, 1, "новый", limit=2)).status == AddMemoryStatus.LIMIT_REACHED
    assert await repo.count_memories(user_id=1) == 5


async def test_delete_and_pin_only_touch_the_owners_rows(session):
    repo = PersonalAiRepository(session)
    mine = (await _add(repo, 1, "моё")).memory
    theirs = (await _add(repo, 2, "чужое")).memory

    assert await repo.delete_memory(user_id=1, memory_id=theirs.id) is False
    assert await repo.set_memory_pinned(user_id=1, memory_id=theirs.id, pinned=True) is False
    assert await repo.set_memory_pinned(user_id=1, memory_id=mine.id, pinned=True) is True
    assert [(m.content, m.pinned) for m in await repo.list_memories(user_id=1)] == [("моё", True)]
    assert await repo.delete_memory(user_id=1, memory_id=mine.id) is True
    assert await repo.count_memories(user_id=1) == 0
    assert await repo.count_memories(user_id=2) == 1


async def test_listing_pages_and_prompt_items(session):
    repo = PersonalAiRepository(session)
    for i in range(5):
        await _add(repo, 1, f"факт {i}")

    page = await repo.list_memories(user_id=1, limit=2, offset=2)
    items = await repo.memory_items(user_id=1)

    assert [m.content for m in page] == ["факт 2", "факт 3"]
    assert [i.content for i in items] == [f"факт {n}" for n in range(5)]
    assert all(i.last_used_at is None for i in items)


async def test_touch_marks_only_the_given_users_memories_as_used(session):
    repo = PersonalAiRepository(session)
    mine = (await _add(repo, 1, "моё")).memory
    theirs = (await _add(repo, 2, "чужое")).memory

    await repo.touch_memories(user_id=1, memory_ids=[mine.id, theirs.id])

    by_user = {1: await repo.memory_items(user_id=1), 2: await repo.memory_items(user_id=2)}
    assert by_user[1][0].last_used_at is not None
    assert by_user[2][0].last_used_at is None


async def test_concurrent_adds_cannot_exceed_the_limit(session):
    """Two parallel confirmations on a nearly full list: exactly one wins."""
    repo = PersonalAiRepository(session)
    await _add(repo, 1, "первый", limit=2)

    results = []
    for text in ("второй", "третий"):
        results.append(await _add(repo, 1, text, limit=2))

    assert [r.status for r in results] == [AddMemoryStatus.ADDED, AddMemoryStatus.LIMIT_REACHED]
    await asyncio.sleep(0)
    assert await repo.count_memories(user_id=1) == 2


async def test_extraction_cursor_counts_only_new_user_messages(session):
    repo = PersonalAiRepository(session)
    for i in range(3):
        await repo.add_message(user_id=1, thread="assistant", role="user", content=f"u{i}")
        await repo.add_message(user_id=1, thread="assistant", role="assistant", content=f"a{i}")
    await repo.add_message(user_id=1, thread="roleplay", role="user", content="scene")
    await repo.add_message(user_id=2, thread="assistant", role="user", content="чужое")

    batch = await repo.user_messages_after(user_id=1, thread="assistant", after_id=0, limit=10)
    assert [m.content for m in batch] == ["u0", "u1", "u2"]

    await repo.get_or_create_profile(1)
    assert await repo.advance_extract_cursor(user_id=1, expected=0, new=batch[1].id) is True
    # A concurrent writer that still holds the old cursor loses.
    assert await repo.advance_extract_cursor(user_id=1, expected=0, new=batch[2].id) is False
    cursor = (await repo.get_profile(1)).memory_extract_cursor
    assert cursor == batch[1].id
    rest = await repo.user_messages_after(user_id=1, thread="assistant", after_id=cursor, limit=10)
    assert [m.content for m in rest] == ["u2"]


async def test_profile_exposes_memory_flags_with_safe_defaults(session):
    stored = await PersonalAiRepository(session).get_or_create_profile(1)

    assert stored.memory_enabled is True
    assert stored.auto_memory_enabled is False
    assert stored.memory_extract_cursor == 0


async def test_update_profile_can_toggle_memory_flags(session):
    repo = PersonalAiRepository(session)
    await repo.get_or_create_profile(1)

    updated = await repo.update_profile(1, expected_revision=0, memory_enabled=False, auto_memory_enabled=True)

    assert updated.memory_enabled is False and updated.auto_memory_enabled is True


async def test_forget_all_removes_everything_of_one_user_and_nothing_else(session):
    repo = PersonalAiRepository(session)
    for user_id in (1, 2):
        await repo.get_or_create_profile(user_id)
        await repo.add_message(user_id=user_id, thread="assistant", role="user", content="hi")
        await repo.add_summary(
            user_id=user_id, thread="assistant", content="s",
            period_start=(await _now(session)), period_end=(await _now(session)), messages_count=1,
        )
        await _add(repo, user_id, "факт")

    removed = await repo.delete_all_user_data(user_id=1)

    assert removed.memories == 1 and removed.messages == 1 and removed.summaries == 1 and removed.profile is True
    for model in (PersonalAiMemoryModel, PersonalAiMessageModel, PersonalAiSummaryModel, PersonalAiProfileModel):
        mine = await session.scalar(select(func.count()).select_from(model).where(model.user_id == 1))
        theirs = await session.scalar(select(func.count()).select_from(model).where(model.user_id == 2))
        assert (mine, theirs) == (0, 1), model
    assert await session.get(UserModel, 1) is not None  # the account itself is not touched
    assert (await repo.delete_all_user_data(user_id=1)).profile is False  # idempotent


async def _now(session):
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
