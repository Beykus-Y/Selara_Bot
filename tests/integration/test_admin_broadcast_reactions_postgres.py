from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.domain.entities import AdminBroadcastTarget, ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_inline_votes_leave_one_active_choice_per_user() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    now = datetime.now(UTC)
    chat = ChatSnapshot(telegram_chat_id=-1008001, chat_type="supergroup", title="Concurrency")
    user = UserSnapshot(telegram_user_id=8001, username="parallel", first_name="Parallel", last_name=None, is_bot=False)
    try:
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            await repo.upsert_activity(chat=chat, user=user, event_at=now)
            broadcast = await repo.create_admin_broadcast(
                body="Тест",
                rendered_body="Тест",
                reaction_options=[
                    {"key": "r1", "emoji": "👍", "label": "Да"},
                    {"key": "r2", "emoji": "👎", "label": "Нет"},
                ],
                active_since_days=3,
                created_by_user_id=77,
            )
            delivery = (
                await repo.create_admin_broadcast_deliveries(
                    broadcast_id=broadcast.id,
                    targets=[AdminBroadcastTarget(chat.telegram_chat_id, chat.chat_type, chat.title, now)],
                )
            )[0]
            await repo.mark_admin_broadcast_delivery_sent(
                delivery_id=delivery.id,
                telegram_message_id=9801,
                reaction_mode="inline",
                bot_member_status="member",
                sent_at=now,
            )
            await session.commit()

        async def vote(option_key: str) -> str:
            async with session_factory() as session:
                repo = SqlAlchemyActivityRepository(session)
                result = await repo.toggle_admin_broadcast_inline_reaction(
                    delivery_id=delivery.id,
                    chat_id=chat.telegram_chat_id,
                    telegram_message_id=9801,
                    user=user,
                    option_key=option_key,
                    reacted_at=now,
                )
                await session.commit()
                return result

        assert await asyncio.gather(vote("r1"), vote("r2")) == ["selected", "selected"]

        async with session_factory() as session:
            active = await SqlAlchemyActivityRepository(session).list_admin_broadcast_reactions(
                broadcast_id=broadcast.id
            )
            assert len(active) == 1
            assert active[0].option_key in {"r1", "r2"}
    finally:
        await engine.dispose()
