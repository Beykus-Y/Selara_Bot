from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure.db.ai_accounting import AiAccountingService
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import AiFeatureInvocationModel, ChatModel, LlmUsageLogModel
from selara.infrastructure.llm.client import LlmAccountingContext, LlmCallUsage


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_parallel_invocations_keep_call_aggregates_isolated_and_survive_deletes():
    engine, factory = await _database()
    try:
        chat_id = -100_876_543
        async with factory() as session:
            session.add(ChatModel(telegram_chat_id=chat_id, type="supergroup", title="accounting"))
            await session.commit()
        service = AiAccountingService(factory)
        invocation_a, invocation_b = await asyncio.gather(
            service.create_invocation(feature="llm_admin", trigger="telegram_message", chat_id=chat_id),
            service.create_invocation(feature="daily_summary", trigger="scheduled", chat_id=chat_id),
        )
        context_a = LlmAccountingContext(invocation_a, "llm_admin", "tool_round", chat_id)
        context_b = LlmAccountingContext(invocation_b, "daily_summary", "segment_topics", chat_id)

        async def write(context, prompt_tokens, call_index):
            await service.record_provider_call(
                context,
                LlmCallUsage(
                    str(uuid4()), "gpt-4o-mini", prompt_tokens, 10, prompt_tokens + 10,
                    Decimal("0.00001"), "known", call_index, "succeeded",
                ),
            )

        await asyncio.gather(
            write(context_a, 101, 1), write(context_b, 1001, 1),
            write(context_a, 202, 2), write(context_b, 2002, 2),
        )
        aggregate_a, aggregate_b = await asyncio.gather(
            service.aggregate_invocation(invocation_id=invocation_a),
            service.aggregate_invocation(invocation_id=invocation_b),
        )
        assert (aggregate_a.provider_calls, aggregate_a.prompt_tokens, aggregate_a.completion_tokens) == (2, 303, 20)
        assert (aggregate_b.provider_calls, aggregate_b.prompt_tokens, aggregate_b.completion_tokens) == (2, 3003, 20)

        # Deleting a feature record detaches its usage rows instead of erasing costs.
        async with factory() as session:
            invocation = await session.get(AiFeatureInvocationModel, invocation_a)
            await session.delete(invocation)
            await session.commit()
            retained = (await session.execute(
                select(LlmUsageLogModel).where(LlmUsageLogModel.invocation_id.is_(None))
            )).scalars().all()
            assert len(retained) == 2

        # Chat deletion also preserves provider calls and clears only their chat scope.
        async with factory() as session:
            chat = await session.get(ChatModel, chat_id)
            await session.delete(chat)
            await session.commit()
            remaining = (await session.execute(select(LlmUsageLogModel))).scalars().all()
            assert len(remaining) == 4
            assert all(row.chat_id is None for row in remaining)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_window_aggregate_uses_invocation_start_for_calls_crossing_midnight():
    engine, factory = await _database()
    try:
        chat_id = -100_876_544
        async with factory() as session:
            session.add(ChatModel(telegram_chat_id=chat_id, type="supergroup", title="window aggregate"))
            await session.commit()
        service = AiAccountingService(factory)
        first = await service.create_invocation(feature="daily_summary", trigger="manual", chat_id=chat_id)
        second = await service.create_invocation(feature="llm_admin", trigger="telegram_message", chat_id=chat_id)
        day_start = datetime(2026, 10, 4, 23, 0, tzinfo=timezone.utc)
        midnight = day_start + timedelta(hours=1)
        day_end = midnight + timedelta(hours=1)
        async with factory() as session:
            first_row = await session.get(AiFeatureInvocationModel, first)
            second_row = await session.get(AiFeatureInvocationModel, second)
            first_row.started_at = day_start + timedelta(minutes=30)
            second_row.started_at = midnight + timedelta(minutes=30)
            await session.commit()
        await service.record_provider_call(
            LlmAccountingContext(first, "daily_summary", "writer", chat_id),
            LlmCallUsage(str(uuid4()), "gpt-4o-mini", 100, 10, 110, Decimal("0.01"), "known", 1,
                         "succeeded", recorded_at=midnight + timedelta(minutes=5)),
        )
        await service.record_provider_call(
            LlmAccountingContext(second, "llm_admin", "assistant_round", chat_id),
            LlmCallUsage(str(uuid4()), "gpt-4o-mini", 200, 20, 220, Decimal("0.02"), "known", 1,
                         "succeeded", recorded_at=midnight + timedelta(minutes=35)),
        )

        first_window = await service.aggregate_window(window_from=day_start, window_to=midnight)
        second_window = await service.aggregate_window(window_from=midnight, window_to=day_end)
        assert (first_window.invocations, first_window.provider_calls, first_window.known_cost_usd) == (
            1, 1, Decimal("0.01")
        )
        assert (second_window.invocations, second_window.provider_calls, second_window.known_cost_usd) == (
            1, 1, Decimal("0.02")
        )
    finally:
        await engine.dispose()
