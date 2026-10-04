from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure.db.ai_accounting import AiAccountingService
from selara.infrastructure.db.base import Base
from selara.infrastructure.llm.client import LlmAccountingContext, LlmCallUsage


@pytest.mark.asyncio
async def test_invocation_aggregate_keeps_unknown_cost_separate_from_known_total():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = AiAccountingService(factory)
    try:
        invocation_id = await service.create_invocation(
            feature="llm_admin", trigger="telegram_message", chat_id=-100, actor_user_id=5, mode="context"
        )
        context = LlmAccountingContext(invocation_id, "llm_admin", "assistant_round", -100, actor_user_id=5)
        await service.record_provider_call(
            context,
            LlmCallUsage(str(uuid4()), "gpt-4o-mini", 100, 20, 120, Decimal("0.000027"), "known", 1, "succeeded"),
        )
        await service.record_provider_call(
            context,
            LlmCallUsage(str(uuid4()), "new-provider-model", 70, 10, 80, None, "unknown", 1, "succeeded"),
        )

        aggregate = await service.aggregate_invocation(invocation_id=invocation_id)

        assert aggregate.provider_calls == 2
        assert aggregate.prompt_tokens == 170
        assert aggregate.completion_tokens == 30
        assert aggregate.known_cost_usd == Decimal("0.000027000")
        assert aggregate.has_unknown_cost is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_structured_validation_update_reuses_one_provider_call_record():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = AiAccountingService(factory)
    try:
        invocation_id = await service.create_invocation(
            feature="daily_summary", trigger="manual", chat_id=-101
        )
        context = LlmAccountingContext(invocation_id, "daily_summary", "merge", -101)
        call_id = str(uuid4())
        received = LlmCallUsage(call_id, "gpt-4o-mini", 100, 20, 120, Decimal("0.000027"), "known", 1, "succeeded")
        invalid = LlmCallUsage(call_id, "gpt-4o-mini", 100, 20, 120, Decimal("0.000027"), "known", 1, "validation_failed")
        await service.record_provider_call(context, received)
        await service.record_provider_call(context, invalid)

        aggregate = await service.aggregate_invocation(invocation_id=invocation_id)

        assert aggregate.provider_calls == 1
        assert aggregate.known_cost_usd == Decimal("0.000027000")
    finally:
        await engine.dispose()
