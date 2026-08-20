"""Regression tests for findings #6/#17/#18 in docs/STT_LLM_AUDIT_TODO.md:

- #17: glossary entries had no author tracking (created_by/updated_by).
- #18: updates silently overwrote the previous definition with no history.
- #6: no way to delete/recover from a poisoned entry.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure.db.base import Base
from selara.infrastructure.db.llm_repository import LlmRepository
from selara.infrastructure.db.models import ChatModel, UserModel


async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_upsert_records_creator_and_no_history_on_first_write():
    engine, session_factory = await _session_factory()
    try:
        async with session_factory() as session:
            session.add(ChatModel(telegram_chat_id=1, type="supergroup", title="Chat"))
            session.add(UserModel(telegram_user_id=101, username="admin1", is_bot=False))
            await session.commit()

            repo = LlmRepository(session)
            row = await repo.upsert_glossary_term(chat_id=1, term="Рест", definition="v1", actor_user_id=101)
            await session.commit()

            assert row.term == "рест"
            assert row.created_by_user_id == 101
            assert row.updated_by_user_id == 101

            history = await repo.get_glossary_history(chat_id=1, term="рест")
            assert history == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_upsert_records_updater_and_history_on_overwrite():
    engine, session_factory = await _session_factory()
    try:
        async with session_factory() as session:
            session.add(ChatModel(telegram_chat_id=2, type="supergroup", title="Chat"))
            session.add(UserModel(telegram_user_id=201, username="admin1", is_bot=False))
            session.add(UserModel(telegram_user_id=202, username="admin2", is_bot=False))
            await session.commit()

            repo = LlmRepository(session)
            await repo.upsert_glossary_term(chat_id=2, term="рест", definition="v1", actor_user_id=201)
            await session.commit()

            updated = await repo.upsert_glossary_term(chat_id=2, term="рест", definition="v2", actor_user_id=202)
            await session.commit()

            assert updated.definition == "v2"
            assert updated.created_by_user_id == 201, "creator must not change on later edits"
            assert updated.updated_by_user_id == 202

            history = await repo.get_glossary_history(chat_id=2, term="рест")
            assert len(history) == 1
            assert history[0].previous_definition == "v1"
            assert history[0].changed_by_user_id == 202
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_glossary_term_removes_the_row():
    engine, session_factory = await _session_factory()
    try:
        async with session_factory() as session:
            session.add(ChatModel(telegram_chat_id=3, type="supergroup", title="Chat"))
            session.add(UserModel(telegram_user_id=301, username="admin1", is_bot=False))
            await session.commit()

            repo = LlmRepository(session)
            await repo.upsert_glossary_term(chat_id=3, term="рест", definition="v1", actor_user_id=301)
            await session.commit()

            deleted = await repo.delete_glossary_term(chat_id=3, term="Рест")
            await session.commit()
            assert deleted is True

            assert await repo.lookup_glossary_term(chat_id=3, term="рест") is None

            deleted_again = await repo.delete_glossary_term(chat_id=3, term="рест")
            assert deleted_again is False
    finally:
        await engine.dispose()
