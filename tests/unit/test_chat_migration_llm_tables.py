"""Regression tests for finding #36 in docs/STT_LLM_AUDIT_TODO.md:

None of LlmContextMessageModel/LlmContextSummaryModel/LlmAdminActionModel/
LlmChatGlossaryModel were migrated by chat_migration.py on a group ->
supergroup upgrade, unlike essentially every other chat-scoped table. All
prior LLM context/glossary/audit history became silently inaccessible under
the new chat_id.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure.db.base import Base
from selara.infrastructure.db.chat_migration import migrate_chat_id
from selara.infrastructure.db.models import (
    ChatModel,
    LlmAdminActionModel,
    LlmChatGlossaryModel,
    LlmContextMessageModel,
    LlmContextSummaryModel,
    UserModel,
)


async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_migrate_chat_id_moves_llm_context_messages_and_summaries_and_actions_and_glossary():
    engine, session_factory = await _session_factory()
    try:
        old_chat_id, new_chat_id, admin_id = 501, 1501, 601
        async with session_factory() as session:
            session.add(ChatModel(telegram_chat_id=old_chat_id, type="group", title="Old chat"))
            session.add(UserModel(telegram_user_id=admin_id, username="admin", is_bot=False))
            await session.commit()

            session.add(LlmContextMessageModel(
                chat_id=old_chat_id, role="user", content="привет", admin_user_id=admin_id,
            ))
            session.add(LlmContextSummaryModel(
                chat_id=old_chat_id, content="summary", period_start=datetime.now(timezone.utc),
                period_end=datetime.now(timezone.utc), messages_count=5, level=1,
            ))
            session.add(LlmAdminActionModel(
                chat_id=old_chat_id, admin_user_id=admin_id, tool_name="warn_user",
                action_description="Варн X", undo_payload_json=None,
            ))
            session.add(LlmChatGlossaryModel(chat_id=old_chat_id, term="рест", definition="отпуск от нормы"))
            await session.commit()

            result = await migrate_chat_id(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
            await session.commit()
            assert result.migrated is True

        async with session_factory() as session:
            from sqlalchemy import select

            messages = (await session.execute(
                select(LlmContextMessageModel).where(LlmContextMessageModel.chat_id == new_chat_id)
            )).scalars().all()
            summaries = (await session.execute(
                select(LlmContextSummaryModel).where(LlmContextSummaryModel.chat_id == new_chat_id)
            )).scalars().all()
            actions = (await session.execute(
                select(LlmAdminActionModel).where(LlmAdminActionModel.chat_id == new_chat_id)
            )).scalars().all()
            glossary = (await session.execute(
                select(LlmChatGlossaryModel).where(LlmChatGlossaryModel.chat_id == new_chat_id)
            )).scalars().all()

            assert len(messages) == 1, "LLM context message was not migrated to the new chat_id"
            assert len(summaries) == 1, "LLM context summary was not migrated to the new chat_id"
            assert len(actions) == 1, "LLM admin action (audit trail + rollback source) was not migrated"
            assert len(glossary) == 1, "LLM glossary entry was not migrated"

            old_messages = (await session.execute(
                select(LlmContextMessageModel).where(LlmContextMessageModel.chat_id == old_chat_id)
            )).scalars().all()
            assert old_messages == [], "old chat_id rows must not remain as orphaned duplicates"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migrate_chat_id_merges_glossary_without_violating_unique_term_constraint():
    """New chat already has a 'рест' entry (unlikely in practice but must not
    crash the whole migration with a unique-constraint violation)."""
    engine, session_factory = await _session_factory()
    try:
        old_chat_id, new_chat_id = 502, 1502
        async with session_factory() as session:
            session.add(ChatModel(telegram_chat_id=old_chat_id, type="group", title="Old chat"))
            session.add(ChatModel(telegram_chat_id=new_chat_id, type="supergroup", title="New chat"))
            await session.commit()

            session.add(LlmChatGlossaryModel(chat_id=old_chat_id, term="рест", definition="old definition"))
            session.add(LlmChatGlossaryModel(chat_id=new_chat_id, term="рест", definition="existing definition"))
            await session.commit()

            result = await migrate_chat_id(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
            await session.commit()
            assert result.migrated is True

        async with session_factory() as session:
            from sqlalchemy import select

            glossary = (await session.execute(
                select(LlmChatGlossaryModel).where(LlmChatGlossaryModel.chat_id == new_chat_id)
            )).scalars().all()
            assert len(glossary) == 1
    finally:
        await engine.dispose()
