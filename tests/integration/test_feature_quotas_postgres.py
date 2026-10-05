from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.feature_access import AccessReason, FeatureAccessService
from selara.domain.entities import ChatSnapshot
from selara.infrastructure.db.ai_accounting import AiAccountingService
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.models import (
    AiFeatureInvocationModel,
    AiFeatureQuotaUsageModel,
    ChatModel,
    LlmUsageLogModel,
)
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.infrastructure.llm.client import LlmAccountingContext, LlmCallUsage
from selara.infrastructure.llm.features import AiFeature
from selara.infrastructure.db.chat_migration import migrate_chat_id

_NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_chats(factory, *chat_ids: int) -> None:
    async with factory() as session:
        session.add_all(ChatModel(telegram_chat_id=chat_id, type="supergroup", title="Quota test") for chat_id in chat_ids)
        await session.commit()


async def _reserve(
    service: FeatureAccessService,
    *,
    feature: AiFeature,
    chat_id: int,
    key: str,
    trigger: str = "telegram_message",
    mode: str | None = None,
    now: datetime = _NOW,
    owner_exempt: bool = False,
    actor_user_id: int = 123,
    summary_run_id: int | None = None,
    source_message_id: int | None = None,
):
    return await service.reserve_feature_usage(
        feature=feature,
        chat_id=chat_id,
        chat_type="supergroup",
        chat_title="Quota test",
        actor_user_id=actor_user_id,
        actor_is_bot=False,
        trigger=trigger,
        timezone_name="Asia/Barnaul",
        idempotency_key=key,
        mode=mode,
        owner_exempt=owner_exempt,
        summary_run_id=summary_run_id,
        source_message_id=source_message_id,
        now=now,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_llm_admin_shared_daily_quota_is_ten_and_denial_creates_no_invocation_or_provider_call():
    engine, factory = await _database()
    try:
        chat_id = -100_700_001
        await _seed_chats(factory, chat_id)
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory))
        first_ten = []
        for index in range(10):
            first_ten.append(await _reserve(
                service,
                feature=AiFeature.LLM_ADMIN,
                chat_id=chat_id,
                key=f"llm_admin:{chat_id}:{index}",
                mode="context" if index % 2 else "no_context",
            ))
        eleventh = await _reserve(
            service, feature=AiFeature.LLM_ADMIN, chat_id=chat_id, key=f"llm_admin:{chat_id}:11",
        )

        assert all(result.allowed and result.quota_limit == 10 for result in first_ten)
        assert eleventh.allowed is False
        assert eleventh.reason == AccessReason.QUOTA_EXHAUSTED
        assert eleventh.quota_used == 10 and eleventh.quota_remaining == 0
        assert eleventh.invocation_id is None
        async with factory() as session:
            invocation_count = await session.scalar(
                select(func.count(AiFeatureInvocationModel.id)).where(AiFeatureInvocationModel.chat_id == chat_id)
            )
            provider_call_count = await session.scalar(
                select(func.count(LlmUsageLogModel.id)).where(LlmUsageLogModel.chat_id == chat_id)
            )
        assert invocation_count == 10
        assert provider_call_count == 0
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_requests_at_nine_of_ten_allow_exactly_one_last_slot():
    engine, factory = await _database()
    try:
        chat_id = -100_700_002
        await _seed_chats(factory, chat_id)
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory))
        for index in range(9):
            result = await _reserve(service, feature=AiFeature.LLM_ADMIN, chat_id=chat_id, key=f"seed:{index}")
            assert result.allowed

        left, right = await asyncio.gather(
            _reserve(service, feature=AiFeature.LLM_ADMIN, chat_id=chat_id, key="race:left"),
            _reserve(service, feature=AiFeature.LLM_ADMIN, chat_id=chat_id, key="race:right"),
        )
        assert sorted([left.allowed, right.allowed]) == [False, True]
        assert any(result.reason == AccessReason.QUOTA_EXHAUSTED for result in (left, right))
        summary = await service.get_usage_summary(
            feature=AiFeature.LLM_ADMIN,
            chat_id=chat_id,
            trigger="telegram_message",
            timezone_name="Asia/Barnaul",
            now=_NOW,
        )
        assert summary.quota_used == 10
        assert summary.quota_remaining == 0
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_telegram_message_reuses_one_quota_event_and_invocation():
    engine, factory = await _database()
    try:
        chat_id = -100_700_003
        await _seed_chats(factory, chat_id)
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory))
        first = await _reserve(service, feature=AiFeature.LLM_ADMIN, chat_id=chat_id, key="llm_admin:-100_700_003:55")
        duplicate = await _reserve(service, feature=AiFeature.LLM_ADMIN, chat_id=chat_id, key="llm_admin:-100_700_003:55")

        assert first.allowed and not first.reused
        assert duplicate.allowed and duplicate.reused
        assert duplicate.invocation_id == first.invocation_id
        async with factory() as session:
            usages = await session.scalar(select(func.count(AiFeatureQuotaUsageModel.id)))
            invocations = await session.scalar(select(func.count(AiFeatureInvocationModel.id)))
        assert usages == invocations == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manual_summary_quota_resets_on_bot_timezone_month_and_scheduled_is_unmetered():
    engine, factory = await _database()
    try:
        chat_id = -100_700_004
        await _seed_chats(factory, chat_id)
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory))
        october = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
        for index in range(10):
            result = await _reserve(
                service,
                feature=AiFeature.DAILY_SUMMARY,
                chat_id=chat_id,
                key=f"daily_summary:{chat_id}:oct:{index}",
                trigger="manual",
                now=october,
            )
            assert result.allowed
        blocked = await _reserve(
            service,
            feature=AiFeature.DAILY_SUMMARY,
            chat_id=chat_id,
            key=f"daily_summary:{chat_id}:oct:11",
            trigger="manual",
            now=october,
        )
        november = await _reserve(
            service,
            feature=AiFeature.DAILY_SUMMARY,
            chat_id=chat_id,
            key=f"daily_summary:{chat_id}:nov:1",
            trigger="manual",
            now=datetime(2026, 10, 31, 17, 1, tzinfo=timezone.utc),
        )
        scheduled = await _reserve(
            service,
            feature=AiFeature.DAILY_SUMMARY,
            chat_id=chat_id,
            key="daily_summary:scheduled:2026-11-01",
            trigger="scheduled",
            now=datetime(2026, 10, 31, 17, 1, tzinfo=timezone.utc),
        )

        assert blocked.reason == AccessReason.QUOTA_EXHAUSTED
        assert november.allowed and november.quota_used == 1
        assert scheduled.allowed and scheduled.reason == AccessReason.NO_COMMERCIAL_QUOTA
        async with factory() as session:
            manual_count = await session.scalar(
                select(func.count(AiFeatureQuotaUsageModel.id)).where(
                    AiFeatureQuotaUsageModel.feature == AiFeature.DAILY_SUMMARY.value,
                )
            )
        assert manual_count == 11
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_owner_exempt_chat_is_unlimited_for_other_actors_and_does_not_consume_free_bucket():
    engine, factory = await _database()
    try:
        chat_id = -100_700_005
        await _seed_chats(factory, chat_id)
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory))
        for index in range(12):
            result = await _reserve(
                service,
                feature=AiFeature.LLM_ADMIN,
                chat_id=chat_id,
                key=f"owner:{index}",
                owner_exempt=True,
                actor_user_id=456,
            )
            assert result.allowed and result.owner_exempt
            assert result.access_tier == "owner_internal"
            assert result.quota_limit is None and result.quota_used is None

        free = await _reserve(service, feature=AiFeature.LLM_ADMIN, chat_id=chat_id, key="free:1")
        assert free.allowed and free.quota_used == 1
        for index in range(12):
            manual_summary = await _reserve(
                service,
                feature=AiFeature.DAILY_SUMMARY,
                chat_id=chat_id,
                key=f"owner-summary:{index}",
                trigger="manual",
                owner_exempt=True,
                actor_user_id=456,
            )
            assert manual_summary.allowed and manual_summary.owner_exempt
            assert manual_summary.access_tier == "owner_internal"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_provider_timeout_keeps_quota_but_pre_provider_failure_can_release_it():
    engine, factory = await _database()
    try:
        chat_id = -100_700_006
        await _seed_chats(factory, chat_id)
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory))
        accounting = AiAccountingService(factory)

        timeout = await _reserve(service, feature=AiFeature.LLM_ADMIN, chat_id=chat_id, key="timeout")
        await accounting.report_provider_attempt(
            LlmAccountingContext(timeout.invocation_id, "llm_admin", "assistant_round", chat_id),
            LlmCallUsage(
                str(uuid4()), "gpt-4o-mini", None, None, None, None, "unknown", 1, "failed",
                error_category="timeout",
            ),
        )
        for attempt in (2, 3):
            await accounting.report_provider_attempt(
                LlmAccountingContext(timeout.invocation_id, "llm_admin", "assistant_round", chat_id),
                LlmCallUsage(
                    str(uuid4()), "gpt-4o-mini", None, None, None, None, "unknown", attempt, "failed",
                    error_category="timeout",
                ),
            )
        await accounting.report_provider_attempt(
            LlmAccountingContext(timeout.invocation_id, "llm_context_compression", "context_compression", chat_id),
            LlmCallUsage(
                str(uuid4()), "gpt-4o-mini", 20, 5, 25, Decimal("0.000005"), "known", 1, "succeeded",
            ),
        )
        assert not await service.release_if_no_provider_attempts(
            invocation_id=timeout.invocation_id,
            reason="timeout",
        )

        pre_provider = await _reserve(service, feature=AiFeature.LLM_ADMIN, chat_id=chat_id, key="pre-provider")
        assert await service.release_if_no_provider_attempts(
            invocation_id=pre_provider.invocation_id,
            reason="context_setup_failed",
        )
        summary = await service.get_usage_summary(
            feature=AiFeature.LLM_ADMIN,
            chat_id=chat_id,
            trigger="telegram_message",
            timezone_name="Asia/Barnaul",
            now=_NOW,
        )
        assert summary.quota_used == 1
        async with factory() as session:
            provider_calls = await session.scalar(
                select(func.count(LlmUsageLogModel.id)).where(
                    LlmUsageLogModel.invocation_id == timeout.invocation_id,
                )
            )
            quota_events = await session.scalar(
                select(func.count(AiFeatureQuotaUsageModel.id)).where(
                    AiFeatureQuotaUsageModel.invocation_id == timeout.invocation_id,
                    AiFeatureQuotaUsageModel.status == "consumed",
                )
            )
        assert provider_calls == 4
        assert quota_events == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_best_effort_provider_accounting_failure_does_not_refund_started_attempt(monkeypatch):
    engine, factory = await _database()
    try:
        chat_id = -100_700_009
        await _seed_chats(factory, chat_id)
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory))
        accounting = AiAccountingService(factory)
        reservation = await _reserve(
            service, feature=AiFeature.LLM_ADMIN, chat_id=chat_id, key="accounting-recorder-failure",
        )

        # This durable pre-request marker is written by LlmClient before it
        # invokes the provider. The subsequent best-effort usage write can fail.
        await accounting.mark_provider_attempt_started(invocation_id=reservation.invocation_id)

        async def fail_usage_insert(context, usage):
            raise RuntimeError("simulated accounting insert failure")

        monkeypatch.setattr(accounting, "record_provider_call", fail_usage_insert)
        with pytest.raises(RuntimeError, match="simulated accounting insert failure"):
            await accounting.report_provider_attempt(
                LlmAccountingContext(reservation.invocation_id, "llm_admin", "assistant_round", chat_id),
                LlmCallUsage(
                    str(uuid4()), "gpt-4o-mini", 10, 5, 15, Decimal("0.0000045"), "known", 1, "succeeded",
                ),
            )
        async with factory() as session:
            invocation = await session.get(AiFeatureInvocationModel, reservation.invocation_id)
            provider_calls = await session.scalar(
                select(func.count(LlmUsageLogModel.id)).where(
                    LlmUsageLogModel.invocation_id == reservation.invocation_id,
                )
            )
        assert invocation.provider_attempt_started_at is not None
        assert provider_calls == 0
        assert not await service.release_if_no_provider_attempts(
            invocation_id=reservation.invocation_id,
            reason="usage_recorder_failed",
        )
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_migration_keeps_old_and_new_chat_usage_without_reset_or_duplicate_bucket():
    engine, factory = await _database()
    try:
        old_chat_id, new_chat_id = -100_700_007, -100_700_008
        await _seed_chats(factory, old_chat_id, new_chat_id)
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory))
        old_use = await _reserve(
            service, feature=AiFeature.LLM_ADMIN, chat_id=old_chat_id,
            key=f"llm_admin:{old_chat_id}:10", source_message_id=10,
        )
        same_message_id_new_origin = await _reserve(
            service, feature=AiFeature.LLM_ADMIN, chat_id=new_chat_id,
            key=f"llm_admin:{new_chat_id}:10", source_message_id=10,
        )
        distinct_new_use = await _reserve(
            service, feature=AiFeature.LLM_ADMIN, chat_id=new_chat_id,
            key=f"llm_admin:{new_chat_id}:11", source_message_id=11,
        )
        async with factory() as session:
            await migrate_chat_id(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
            await session.commit()

        summary = await service.get_usage_summary(
            feature=AiFeature.LLM_ADMIN,
            chat_id=new_chat_id,
            trigger="telegram_message",
            timezone_name="Asia/Barnaul",
            now=_NOW,
        )
        replay = await _reserve(
            service,
            feature=AiFeature.LLM_ADMIN,
            chat_id=new_chat_id,
            key=f"llm_admin:{new_chat_id}:10",
            source_message_id=10,
            now=datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc),
        )
        async with factory() as session:
            usage_rows = (await session.execute(
                select(AiFeatureQuotaUsageModel).where(AiFeatureQuotaUsageModel.chat_id == new_chat_id)
            )).scalars().all()
            invocations = (await session.execute(
                select(AiFeatureInvocationModel).where(AiFeatureInvocationModel.chat_id == new_chat_id)
            )).scalars().all()
        assert old_use.invocation_id != same_message_id_new_origin.invocation_id
        assert old_use.invocation_id != distinct_new_use.invocation_id
        assert len(usage_rows) == 3  # keep the audit history for both source rows
        assert all(row.scope_type == "chat" and row.scope_id == str(new_chat_id) for row in invocations)
        assert summary.quota_used == 3  # origin chat + message_id distinguish requests
        assert replay.allowed and replay.reused
        async with factory() as session:
            total_usage_rows = await session.scalar(select(func.count(AiFeatureQuotaUsageModel.id)))
        assert total_usage_rows == 3  # migrated duplicate replay did not add another reservation
    finally:
        await engine.dispose()
