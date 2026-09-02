"""Integration tests for the 4 read-only query primitives backing the daily summary
analyst tools (get_message_context, get_reply_thread, search_messages,
get_activity_stats_in_window) and count_archived_messages_in_window (eligibility).

These are plain read queries with no concurrency hazard, so unlike
test_daily_summary_claim_postgres.py this just verifies correctness against a real
Postgres (reply-thread BFS, ILIKE search, window boundaries) rather than races.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import MessageArchiveModel, UserChatActivityModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

_CHAT_ID = -100888
_USER_A = 1001
_USER_B = 1002
_BASE = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(repo: SqlAlchemyActivityRepository) -> None:
    await repo._upsert_chat(ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test"))
    await repo._upsert_user(UserSnapshot(telegram_user_id=_USER_A, username="a", first_name="A", last_name=None, is_bot=False))
    await repo._upsert_user(UserSnapshot(telegram_user_id=_USER_B, username="b", first_name="B", last_name=None, is_bot=False))

    def _row(message_id: int, user_id: int, minute: int, text: str, reply_to: int | None = None) -> MessageArchiveModel:
        sent_at = _BASE + timedelta(minutes=minute)
        return MessageArchiveModel(
            chat_id=_CHAT_ID,
            user_id=user_id,
            telegram_message_id=message_id,
            snapshot_kind="created",
            snapshot_at=sent_at,
            sent_at=sent_at,
            message_type="text",
            text=text,
            raw_message_json={"message_id": message_id},
            snapshot_hash=f"hash-{message_id}",
            reply_to_telegram_message_id=reply_to,
        )

    rows = [
        _row(1, _USER_A, 0, "Кто-нибудь смотрел новый сезон?"),
        _row(2, _USER_B, 1, "Да, вчера досмотрел", reply_to=1),
        _row(3, _USER_A, 2, "И как тебе финал?", reply_to=2),
        _row(4, _USER_B, 3, "Слабоват если честно", reply_to=3),
        _row(5, _USER_A, 10, "кстати кто-то видел Reality VPN блокировки?"),
        _row(6, _USER_B, 11, "у меня XHTTP ещё живой"),
    ]
    repo._session.add_all(rows)
    repo._session.add_all(
        [
            UserChatActivityModel(chat_id=_CHAT_ID, user_id=_USER_A, last_seen_at=_BASE),
            UserChatActivityModel(chat_id=_CHAT_ID, user_id=_USER_B, last_seen_at=_BASE),
        ]
    )
    await repo._session.flush()
    await repo._session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_message_context_returns_window_around_anchor() -> None:
    engine, session_factory = await _database()
    try:
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            await _seed(repo)

            context = await repo.get_message_context(chat_id=_CHAT_ID, around_telegram_message_id=3, limit=4)

        ids = [m.telegram_message_id for m in context]
        assert 3 in ids
        assert len(ids) <= 4
        assert ids == sorted(ids)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_message_context_unknown_message_returns_empty() -> None:
    engine, session_factory = await _database()
    try:
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            await _seed(repo)
            context = await repo.get_message_context(chat_id=_CHAT_ID, around_telegram_message_id=999_999, limit=10)
        assert context == []
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_reply_thread_follows_transitive_replies() -> None:
    engine, session_factory = await _database()
    try:
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            await _seed(repo)
            thread = await repo.get_reply_thread(chat_id=_CHAT_ID, root_telegram_message_id=1, limit=50)

        # messages 2, 3, 4 all trace back to 1 (2->1, 3->2, 4->3); message 5/6 don't reply at all
        assert [m.telegram_message_id for m in thread] == [2, 3, 4]
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_reply_thread_respects_row_cap() -> None:
    engine, session_factory = await _database()
    try:
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            await _seed(repo)
            thread = await repo.get_reply_thread(chat_id=_CHAT_ID, root_telegram_message_id=1, limit=2)
        assert len(thread) == 2
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_messages_finds_substring_within_window() -> None:
    engine, session_factory = await _database()
    try:
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            await _seed(repo)
            results = await repo.search_messages(
                chat_id=_CHAT_ID,
                query="VPN",
                window_from=_BASE,
                window_to=_BASE + timedelta(hours=1),
                limit=10,
            )
        assert [m.telegram_message_id for m in results] == [5]
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_messages_outside_window_finds_nothing() -> None:
    engine, session_factory = await _database()
    try:
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            await _seed(repo)
            results = await repo.search_messages(
                chat_id=_CHAT_ID,
                query="VPN",
                window_from=_BASE - timedelta(hours=2),
                window_to=_BASE - timedelta(hours=1),
                limit=10,
            )
        assert results == []
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_activity_stats_in_window_counts_participants_and_replies() -> None:
    engine, session_factory = await _database()
    try:
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            await _seed(repo)
            stats = await repo.get_activity_stats_in_window(
                chat_id=_CHAT_ID,
                window_from=_BASE,
                window_to=_BASE + timedelta(hours=1),
            )
        assert stats.message_count == 6
        assert stats.participant_count == 2
        assert stats.reply_count == 3  # messages 2, 3, 4 each have a reply_to
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_archived_messages_in_window_returns_chronological_order() -> None:
    engine, session_factory = await _database()
    try:
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            await _seed(repo)
            rows = await repo.list_archived_messages_in_window(
                chat_id=_CHAT_ID,
                window_from=_BASE,
                window_to=_BASE + timedelta(hours=1),
            )
        assert [m.telegram_message_id for m in rows] == [1, 2, 3, 4, 5, 6]
        assert rows[1].reply_to_telegram_message_id == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_daily_summary_member_info_reports_membership_and_persona() -> None:
    engine, session_factory = await _database()
    try:
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            await _seed(repo)
            await repo._session.execute(
                update(UserChatActivityModel)
                .where(UserChatActivityModel.chat_id == _CHAT_ID, UserChatActivityModel.user_id == _USER_A)
                .values(persona_label="Кот")
            )
            await repo._session.execute(
                update(UserChatActivityModel)
                .where(UserChatActivityModel.chat_id == _CHAT_ID, UserChatActivityModel.user_id == _USER_B)
                .values(is_active_member=False)
            )
            await repo._session.commit()

            members = await repo.get_daily_summary_member_info(chat_id=_CHAT_ID, user_ids=[_USER_A, _USER_B])

        by_id = {m.user_id: m for m in members}
        assert by_id[_USER_A].is_active_member is True
        assert by_id[_USER_A].persona_label == "Кот"
        assert by_id[_USER_B].is_active_member is False
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_count_archived_messages_in_window_excludes_bots_and_edits() -> None:
    engine, session_factory = await _database()
    try:
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            await _seed(repo)

            # an edited-snapshot row for message 1 must not be double-counted
            edit_row = MessageArchiveModel(
                chat_id=_CHAT_ID,
                user_id=_USER_A,
                telegram_message_id=1,
                snapshot_kind="edited",
                snapshot_at=_BASE + timedelta(minutes=30),
                sent_at=_BASE,
                message_type="text",
                text="Кто-нибудь смотрел новый сезон? (ред.)",
                raw_message_json={"message_id": 1},
                snapshot_hash="hash-1-edited",
            )
            repo._session.add(edit_row)
            await repo._session.commit()

            count = await repo.count_archived_messages_in_window(
                chat_id=_CHAT_ID,
                window_from=_BASE,
                window_to=_BASE + timedelta(hours=1),
            )
        assert count == 6
    finally:
        await engine.dispose()
