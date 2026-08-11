from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import (
    FamilyRelationshipArchiveModel,
    MarriageModel,
    PairModel,
    RelationshipActionUsageModel,
    RelationshipGraphModel,
    RelationshipProposalModel,
    UserChatActivityModel,
)
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.infrastructure.relationship_cleanup import run_startup_relationship_cleanup

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")


async def _create_marriage(
    repo: SqlAlchemyActivityRepository,
    *,
    chat: ChatSnapshot,
    user_a: UserSnapshot,
    user_b: UserSnapshot,
    event_at: datetime,
) -> int:
    await repo.upsert_activity(chat=chat, user=user_a, event_at=event_at - timedelta(minutes=2))
    await repo.upsert_activity(chat=chat, user=user_b, event_at=event_at - timedelta(minutes=1))
    proposal, error = await repo.create_marriage_proposal(
        chat=chat,
        proposer=user_a,
        target=user_b,
        kind="marriage",
        expires_at=event_at + timedelta(hours=1),
        event_at=event_at,
    )
    assert proposal is not None
    assert error is None
    _, marriage, error = await repo.respond_relationship_proposal(
        proposal_id=proposal.id,
        actor_user_id=user_b.telegram_user_id,
        accept=True,
        event_at=event_at,
    )
    assert marriage is not None
    assert error is None
    await repo.upsert_graph_relationship(
        chat=chat,
        user_a=user_a,
        user_b=user_b,
        relation_type="spouse",
        actor_user_id=user_b.telegram_user_id,
    )
    relationship = await repo.get_active_relationship(
        user_id=user_a.telegram_user_id,
        chat_id=chat.telegram_chat_id,
    )
    assert relationship is not None
    await repo.set_relationship_action_last_used_at(
        relationship=relationship,
        actor_user_id=user_a.telegram_user_id,
        action_code="love",
        used_at=event_at,
    )
    return marriage.id


@pytest.mark.asyncio
async def test_member_leave_closes_marriage_and_archives_family_links() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100701, chat_type="group", title="Cleanup")
    user_a = UserSnapshot(telegram_user_id=701, username="a", first_name="A", last_name=None, is_bot=False)
    user_b = UserSnapshot(telegram_user_id=702, username="b", first_name="B", last_name=None, is_bot=False)
    child = UserSnapshot(telegram_user_id=703, username="child", first_name="Child", last_name=None, is_bot=False)
    married_at = datetime(2026, 8, 10, 12, 0)
    left_at = married_at + timedelta(days=1)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        marriage_id = await _create_marriage(
            repo,
            chat=chat,
            user_a=user_a,
            user_b=user_b,
            event_at=married_at,
        )
        await repo.upsert_activity(chat=chat, user=child, event_at=married_at)
        await repo.upsert_graph_relationship(
            chat=chat,
            user_a=user_a,
            user_b=child,
            relation_type="parent",
            actor_user_id=user_a.telegram_user_id,
        )

        await repo.set_chat_member_active(
            chat=chat,
            user=user_a,
            is_active=False,
            event_at=left_at,
        )
        marriage_row = await session.get(MarriageModel, marriage_id)
        assert marriage_row is not None
        assert marriage_row.is_active is False
        assert marriage_row.ended_at == left_at
        assert marriage_row.ended_by_user_id == user_a.telegram_user_id
        assert marriage_row.ended_reason == "member_left_chat"
        assert await repo.get_active_marriage(user_id=user_b.telegram_user_id, chat_id=chat.telegram_chat_id) is None
        assert await repo.list_graph_relationships(chat_id=chat.telegram_chat_id, user_id=user_a.telegram_user_id) == []
        assert await session.scalar(select(func.count(RelationshipActionUsageModel.relationship_id))) == 0
        assert await session.scalar(select(func.count(FamilyRelationshipArchiveModel.id))) == 2

        # Telegram may deliver both left_chat_member and chat_member updates.
        await repo.set_chat_member_active(
            chat=chat,
            user=user_a,
            is_active=False,
            event_at=left_at,
        )
        assert await session.scalar(select(func.count(FamilyRelationshipArchiveModel.id))) == 2

        # A message queued before the leave update may be flushed afterwards.
        await repo.upsert_activity(
            chat=chat,
            user=user_a,
            event_at=left_at - timedelta(minutes=1),
            telegram_message_id=70101,
        )
        activity_row = await session.get(
            UserChatActivityModel,
            {"chat_id": chat.telegram_chat_id, "user_id": user_a.telegram_user_id},
        )
        assert activity_row is not None
        assert activity_row.is_active_member is False

    await engine.dispose()


@pytest.mark.asyncio
async def test_startup_cleanup_repairs_existing_phantom_and_reports_every_run() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100801, chat_type="group", title="Startup cleanup")
    user_a = UserSnapshot(telegram_user_id=801, username="a", first_name="A", last_name=None, is_bot=False)
    user_b = UserSnapshot(telegram_user_id=802, username="b", first_name="B", last_name=None, is_bot=False)
    user_c = UserSnapshot(telegram_user_id=803, username="c", first_name="C", last_name=None, is_bot=False)
    user_d = UserSnapshot(telegram_user_id=804, username="d", first_name="D", last_name=None, is_bot=False)
    married_at = datetime(2026, 8, 9, 12, 0)
    cleanup_at = married_at + timedelta(days=2)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        marriage_id = await _create_marriage(
            repo,
            chat=chat,
            user_a=user_a,
            user_b=user_b,
            event_at=married_at,
        )
        await repo.upsert_activity(chat=chat, user=user_c, event_at=married_at)
        await repo.upsert_activity(chat=chat, user=user_d, event_at=married_at)
        pair_proposal, error = await repo.create_marriage_proposal(
            chat=chat,
            proposer=user_c,
            target=user_d,
            kind="pair",
            expires_at=married_at + timedelta(hours=1),
            event_at=married_at,
        )
        assert pair_proposal is not None
        assert error is None
        _, pair, error = await repo.respond_relationship_proposal(
            proposal_id=pair_proposal.id,
            actor_user_id=user_d.telegram_user_id,
            accept=True,
            event_at=married_at,
        )
        assert pair is not None
        assert error is None
        await repo.set_relationship_action_last_used_at(
            relationship=pair,
            actor_user_id=user_c.telegram_user_id,
            action_code="care",
            used_at=married_at,
        )
        pending_proposal, error = await repo.create_marriage_proposal(
            chat=chat,
            proposer=user_c,
            target=user_d,
            kind="marriage",
            expires_at=married_at + timedelta(hours=1),
            event_at=married_at,
        )
        assert pending_proposal is not None
        assert error is None
        # Simulate data left behind by the old runtime, bypassing the new leave cleanup.
        await session.execute(
            update(UserChatActivityModel)
            .where(
                UserChatActivityModel.chat_id == chat.telegram_chat_id,
                UserChatActivityModel.user_id.in_((user_a.telegram_user_id, user_c.telegram_user_id)),
            )
            .values(is_active_member=False, last_seen_at=cleanup_at)
        )
        await session.commit()

    bot = SimpleNamespace(send_message=AsyncMock())
    settings = SimpleNamespace(admin_user_id=99)

    first = await run_startup_relationship_cleanup(
        bot=bot,
        settings=settings,
        session_factory=session_factory,
        now=cleanup_at,
    )
    second = await run_startup_relationship_cleanup(
        bot=bot,
        settings=settings,
        session_factory=session_factory,
        now=cleanup_at + timedelta(minutes=1),
    )

    assert first.marriages_removed == 1
    assert first.pairs_removed == 1
    assert first.proposals_cancelled == 1
    assert second.marriages_removed == 0
    assert second.pairs_removed == 0
    assert second.proposals_cancelled == 0
    assert bot.send_message.await_count == 2
    assert "Удалено фантомных браков: 1" in bot.send_message.await_args_list[0].kwargs["text"]
    assert "Удалено фантомных браков: 0" in bot.send_message.await_args_list[1].kwargs["text"]

    async with session_factory() as session:
        marriage_row = await session.get(MarriageModel, marriage_id)
        assert marriage_row is not None
        assert marriage_row.is_active is False
        assert marriage_row.ended_reason == "member_left_reconcile"
        assert await session.scalar(select(func.count(PairModel.id))) == 0
        assert await session.scalar(
            select(func.count(RelationshipProposalModel.id)).where(RelationshipProposalModel.status == "pending")
        ) == 0
        assert await session.scalar(
            select(func.count(RelationshipProposalModel.id)).where(RelationshipProposalModel.status == "cancelled")
        ) == 1
        assert await session.scalar(select(func.count(RelationshipActionUsageModel.relationship_id))) == 0
        assert await session.scalar(select(func.count(RelationshipGraphModel.id))) == 0
        assert await session.scalar(select(func.count(FamilyRelationshipArchiveModel.id))) == 1

    await engine.dispose()
