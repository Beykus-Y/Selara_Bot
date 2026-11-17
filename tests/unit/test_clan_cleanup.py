"""Clans of members who left the chat: leave/kick cleanup and startup reconcile."""
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
from selara.infrastructure.db.models import ClanMemberModel, ClanModel, UserChatActivityModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.infrastructure.relationship_cleanup import run_startup_relationship_cleanup

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")

CHAT = ChatSnapshot(telegram_chat_id=-100901, chat_type="group", title="Clans")
OTHER_CHAT = ChatSnapshot(telegram_chat_id=-100902, chat_type="group", title="Other")
T0 = datetime(2026, 11, 1, 12, 0)


def _user(user_id: int) -> UserSnapshot:
    return UserSnapshot(telegram_user_id=user_id, username=f"u{user_id}", first_name=f"U{user_id}", last_name=None, is_bot=False)


async def _setup():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, session_factory


async def _make_clan(session, *, chat: ChatSnapshot, name: str, creator: int, members: list[int]) -> int:
    """members are listed in join order; the creator is added first."""
    repo = SqlAlchemyActivityRepository(session)
    for uid in [creator, *members]:
        await repo.upsert_activity(chat=chat, user=_user(uid), event_at=T0)
    clan = ClanModel(chat_id=chat.telegram_chat_id, name=name, creator_user_id=creator)
    session.add(clan)
    await session.flush()
    for index, uid in enumerate([creator, *members]):
        session.add(
            ClanMemberModel(
                clan_id=clan.id,
                user_id=uid,
                chat_id=chat.telegram_chat_id,
                joined_at=T0 + timedelta(minutes=index),
            )
        )
    await session.flush()
    return int(clan.id)


async def _leave(session, *, chat: ChatSnapshot, user_id: int, at: datetime | None = None) -> None:
    repo = SqlAlchemyActivityRepository(session)
    await repo.set_chat_member_active(chat=chat, user=_user(user_id), is_active=False, event_at=at or T0 + timedelta(days=1))


async def _member_ids(session, clan_id: int) -> list[int]:
    return sorted((await session.execute(select(ClanMemberModel.user_id).where(ClanMemberModel.clan_id == clan_id))).scalars().all())


@pytest.mark.asyncio
async def test_regular_member_leaving_is_removed_and_clan_stays() -> None:
    engine, factory = await _setup()
    async with factory() as session:
        clan_id = await _make_clan(session, chat=CHAT, name="Alpha", creator=1, members=[2, 3])
        await _leave(session, chat=CHAT, user_id=2)

        clan = await session.get(ClanModel, clan_id)
        assert clan is not None and clan.creator_user_id == 1
        assert await _member_ids(session, clan_id) == [1, 3]
    await engine.dispose()


@pytest.mark.asyncio
async def test_creator_leaving_hands_clan_to_oldest_active_member() -> None:
    engine, factory = await _setup()
    async with factory() as session:
        clan_id = await _make_clan(session, chat=CHAT, name="Alpha", creator=1, members=[2, 3, 4])
        # Member 2 joined first but is already gone from the chat (stale data): skipped.
        await session.execute(
            update(UserChatActivityModel).where(UserChatActivityModel.user_id == 2).values(is_active_member=False)
        )
        await _leave(session, chat=CHAT, user_id=1)

        clan = await session.get(ClanModel, clan_id)
        assert clan is not None
        assert clan.creator_user_id == 3
        assert await _member_ids(session, clan_id) == [2, 3, 4]
    await engine.dispose()


@pytest.mark.asyncio
async def test_last_member_creator_leaving_disbands_clan_and_frees_name() -> None:
    engine, factory = await _setup()
    async with factory() as session:
        clan_id = await _make_clan(session, chat=CHAT, name="Alpha", creator=1, members=[])
        await _leave(session, chat=CHAT, user_id=1)

        assert await session.get(ClanModel, clan_id) is None
        assert await _member_ids(session, clan_id) == []

        # The name is free again and a new clan can be created with it.
        session.add(ClanModel(chat_id=CHAT.telegram_chat_id, name="Alpha", creator_user_id=5))
        await session.flush()
    await engine.dispose()


@pytest.mark.asyncio
async def test_creator_leaving_with_only_inactive_members_disbands_clan() -> None:
    engine, factory = await _setup()
    async with factory() as session:
        clan_id = await _make_clan(session, chat=CHAT, name="Alpha", creator=1, members=[2])
        await session.execute(
            update(UserChatActivityModel).where(UserChatActivityModel.user_id == 2).values(is_active_member=False)
        )
        await _leave(session, chat=CHAT, user_id=1)

        assert await session.get(ClanModel, clan_id) is None
        assert await _member_ids(session, clan_id) == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_repeated_leave_events_and_other_chats_are_untouched() -> None:
    engine, factory = await _setup()
    async with factory() as session:
        clan_id = await _make_clan(session, chat=CHAT, name="Alpha", creator=1, members=[2])
        other_id = await _make_clan(session, chat=OTHER_CHAT, name="Alpha", creator=1, members=[2])

        await _leave(session, chat=CHAT, user_id=1)
        await _leave(session, chat=CHAT, user_id=1)  # left_chat_member + chat_member both arrive

        clan = await session.get(ClanModel, clan_id)
        assert clan is not None and clan.creator_user_id == 2
        assert await _member_ids(session, clan_id) == [2]

        other = await session.get(ClanModel, other_id)
        assert other is not None and other.creator_user_id == 1
        assert await _member_ids(session, other_id) == [1, 2]
    await engine.dispose()


@pytest.mark.asyncio
async def test_rejoining_chat_allows_joining_a_clan_again_without_stale_membership() -> None:
    engine, factory = await _setup()
    async with factory() as session:
        clan_id = await _make_clan(session, chat=CHAT, name="Alpha", creator=1, members=[2])
        await _leave(session, chat=CHAT, user_id=2)
        repo = SqlAlchemyActivityRepository(session)
        await repo.set_chat_member_active(chat=CHAT, user=_user(2), is_active=True, event_at=T0 + timedelta(days=2))

        # Profile lookup (as in stats.py) shows no clan; re-joining does not hit the PK/unique constraints.
        profile_clan = (
            await session.execute(
                select(ClanModel.id)
                .join(ClanMemberModel, ClanMemberModel.clan_id == ClanModel.id)
                .where(ClanMemberModel.chat_id == CHAT.telegram_chat_id, ClanMemberModel.user_id == 2)
            )
        ).first()
        assert profile_clan is None
        session.add(ClanMemberModel(clan_id=clan_id, user_id=2, chat_id=CHAT.telegram_chat_id))
        await session.flush()
        assert await _member_ids(session, clan_id) == [1, 2]
    await engine.dispose()


@pytest.mark.asyncio
async def test_startup_cleanup_reconciles_orphaned_clans_and_reports() -> None:
    engine, factory = await _setup()
    async with factory() as session:
        # Clan 1: creator gone -> handed to the oldest active member.
        handed = await _make_clan(session, chat=CHAT, name="Alpha", creator=1, members=[2, 3])
        # Clan 2: everyone gone -> disbanded.
        dead = await _make_clan(session, chat=CHAT, name="Beta", creator=10, members=[11])
        # Clan 3: only a regular member gone -> membership removed, clan stays.
        trimmed = await _make_clan(session, chat=CHAT, name="Gamma", creator=20, members=[21, 22])
        # Clan 4: creator gone but not listed as a member (orphan) -> handed over / disbanded.
        orphan = await _make_clan(session, chat=CHAT, name="Delta", creator=30, members=[31])
        await session.execute(
            update(UserChatActivityModel)
            .where(UserChatActivityModel.user_id.in_((1, 10, 11, 21, 30)))
            .values(is_active_member=False)
        )
        await session.execute(
            ClanMemberModel.__table__.delete().where(ClanMemberModel.clan_id == orphan, ClanMemberModel.user_id == 30)
        )
        await session.commit()

    bot = SimpleNamespace(send_message=AsyncMock())
    settings = SimpleNamespace(admin_user_id=99)
    first = await run_startup_relationship_cleanup(bot=bot, settings=settings, session_factory=factory, now=T0 + timedelta(days=3))
    second = await run_startup_relationship_cleanup(bot=bot, settings=settings, session_factory=factory, now=T0 + timedelta(days=3, minutes=1))

    assert first.clans_removed == 1
    assert first.clans_transferred == 2
    assert first.clan_memberships_removed == 4
    assert second.clans_removed == 0 and second.clans_transferred == 0 and second.clan_memberships_removed == 0
    assert "расформировано 1" in bot.send_message.await_args_list[0].kwargs["text"]

    async with factory() as session:
        h = await session.get(ClanModel, handed)
        assert h is not None and h.creator_user_id == 2
        assert await _member_ids(session, handed) == [2, 3]
        assert await session.get(ClanModel, dead) is None
        assert await _member_ids(session, dead) == []
        t = await session.get(ClanModel, trimmed)
        assert t is not None and t.creator_user_id == 20
        assert await _member_ids(session, trimmed) == [20, 22]
        o = await session.get(ClanModel, orphan)
        assert o is not None and o.creator_user_id == 31
        assert await session.scalar(select(func.count(ClanModel.id))) == 3
    await engine.dispose()
