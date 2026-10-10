"""Integration tests for the daily summary orchestration (attempt_daily_summary_run):
claim -> generate -> finalize -> send, and the resend/skip paths around it. Uses a
real Postgres for the repository layer (claim atomicity, message counting, run
state) and a fake LlmClient (no network) to keep this fast and deterministic.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.daily_summary.schemas import MergedTheme, MergedThemeList, SegmentTopicCard, SegmentTopicCardList
from selara.application.selara_ai_product import SELARA_AI_PRODUCT_KEY
from selara.application.feature_access import AccessTier, FeatureEntitlement, FeatureAccessService
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.chat_migration import migrate_chat_id
from selara.infrastructure.db.ai_accounting import AiAccountingService
from selara.infrastructure.db.models import (
    AiFeatureInvocationModel,
    AiFeatureQuotaUsageModel,
    ChatEntitlementModel,
    DailySummaryRunModel,
    LlmUsageLogModel,
    MessageArchiveModel,
)
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.llm.client import LlmCallResult, LlmCallUsage
from decimal import Decimal
from uuid import uuid4
from selara.presentation.daily_summary import attempt_daily_summary_run
from selara.presentation import daily_summary as daily_summary_module
from selara.core.config import Settings
from selara.infrastructure.llm.features import AiFeature

_CHAT_ID = -100555
_USER_ID = 1001
_NOW = datetime.now(timezone.utc)


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_chat(
    session_factory,
    *,
    message_count: int,
    min_messages: int,
    chat_id: int = _CHAT_ID,
    daily_summary_hour: int | None = None,
) -> None:
    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        chat = ChatSnapshot(telegram_chat_id=chat_id, chat_type="supergroup", title="Test Chat")
        await repo._upsert_chat(chat)
        await repo._upsert_user(
            UserSnapshot(telegram_user_id=_USER_ID, username="vasya", first_name="Vasya", last_name=None, is_bot=False)
        )
        await repo.upsert_chat_settings(
            chat=chat,
            values={
                "daily_summary_enabled": True,
                "daily_summary_min_messages": min_messages,
                "daily_summary_style": "neutral",
                "save_message": True,
                **({"daily_summary_hour": daily_summary_hour} if daily_summary_hour is not None else {}),
            },
        )
        rows = []
        for i in range(message_count):
            sent_at = _NOW - timedelta(hours=1) + timedelta(minutes=i)
            rows.append(
                MessageArchiveModel(
                    chat_id=chat_id,
                    user_id=_USER_ID,
                    telegram_message_id=i + 1,
                    snapshot_kind="created",
                    snapshot_at=sent_at,
                    sent_at=sent_at,
                    message_type="text",
                    text=f"сообщение {i}",
                    raw_message_json={"message_id": i + 1},
                    snapshot_hash=f"hash-{i}",
                )
            )
        session.add_all(rows)
        await session.commit()


@dataclass
class _FakeLlmClient:
    accounting_service: object | None = None
    _structured_calls: int = field(default=0, init=False)

    @staticmethod
    def _usage():
        return LlmCallUsage(str(uuid4()), "gpt-4o-mini", 10, 5, 15, Decimal("0.0000045"), "known", 1, "succeeded")

    async def _result(self, value, accounting_context):
        usage = self._usage()
        if accounting_context is not None and self.accounting_service is not None:
            await self.accounting_service.report_provider_attempt(accounting_context, usage)
        return LlmCallResult(value, (usage,))

    async def chat_structured(self, messages, *, response_model, max_tokens=None, accounting_context=None):
        self._structured_calls += 1
        if response_model is SegmentTopicCardList:
            return await self._result(SegmentTopicCardList(
                topics=[SegmentTopicCard(title="Разговор", start_message_id=1, end_message_id=2, blurb="Поболтали.")]
            ), accounting_context)
        return await self._result(MergedThemeList(
            themes=[MergedTheme(title="Разговор", source_card_indexes=[0], blurb="Итог.", importance=3)]
        ), accounting_context)

    async def chat_with_tools(self, messages, tools, *, max_tokens=None, accounting_context=None):
        message = SimpleNamespace(content="[]", tool_calls=None)
        return await self._result(SimpleNamespace(choices=[SimpleNamespace(message=message)]), accounting_context)

    async def chat_simple(self, messages, *, max_tokens=None, accounting_context=None):
        return await self._result("Итоги дня: сегодня поболтали в чате.", accounting_context)


def _fake_bot(*, owner_status: str | None = None) -> SimpleNamespace:
    bot = SimpleNamespace(send_message=AsyncMock())
    if owner_status is not None:
        bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status=owner_status))
    return bot


def _test_settings(*, admin_user_id: int | None = None) -> Settings:
    return Settings(
        bot_token="123:TEST",
        database_url="postgresql+asyncpg://localhost/test",
        bot_timezone="UTC",
        admin_user_id=admin_user_id,
    )


def _paid_access(session_factory, *, resolver=None) -> FeatureAccessService:
    if resolver is None:
        class PaidResolver:
            async def resolve(self, *, chat_id, feature, trigger):
                return FeatureEntitlement(access_tier=AccessTier.PAID, source="test_only")

        resolver = PaidResolver()
    return FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(session_factory),
        entitlement_resolver=resolver,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_attempt_daily_summary_run_full_cycle_sends_and_marks_sent() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        bot = _fake_bot()
        llm_client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")

        outcome = await attempt_daily_summary_run(
            bot=bot,
            session_factory=session_factory,
            llm_client=llm_client,
            chat=chat,
            trigger="manual",
            window_to=_NOW,
            summary_date=_NOW.date(),
            now_utc=_NOW,
        )

        assert outcome.sent is True
        assert outcome.reason == "sent"
        bot.send_message.assert_awaited_once()
        assert "поболтали" in bot.send_message.await_args.kwargs["text"]

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            run = await repo.get_daily_summary_run(chat_id=_CHAT_ID, summary_date=_NOW.date(), trigger="manual")
        assert run is not None
        assert run.status == "sent"
        assert run.pipeline_cost_usd > 0
        assert run.diagnostics_json is not None
        assert run.diagnostics_json["message_count"] == 60
        assert run.diagnostics_json["cards_before_merge_count"] >= 1
        async with session_factory() as session:
            invocations = (await session.execute(select(AiFeatureInvocationModel))).scalars().all()
            usage_rows = (await session.execute(select(LlmUsageLogModel))).scalars().all()
        assert len(invocations) == 1 and invocations[0].status == "succeeded"
        assert invocations[0].trigger == "manual" and invocations[0].summary_run_id == run.id
        assert len(usage_rows) == 5
        assert {row.stage for row in usage_rows} == {
            "segment_topics",
            "merge",
            "analyst",
            "writer",
            "infographic",
        }
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_daily_summary_late_pipeline_failure_keeps_completed_provider_calls():
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)

        class _WriterFailureClient(_FakeLlmClient):
            async def chat_simple(self, messages, *, max_tokens=None, accounting_context=None):
                raise RuntimeError("writer stage failed after earlier provider calls")

        client = _WriterFailureClient(accounting_service=AiAccountingService(session_factory))
        outcome = await attempt_daily_summary_run(
            bot=_fake_bot(), session_factory=session_factory, llm_client=client,
            chat=ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat"),
            trigger="scheduled", window_to=_NOW, summary_date=_NOW.date(), now_utc=_NOW,
            settings=_test_settings(),
            feature_access_service=_paid_access(session_factory),
        )

        assert outcome.reason == "pipeline_failed"
        async with session_factory() as session:
            usage_rows = (await session.execute(select(LlmUsageLogModel))).scalars().all()
            invocations = (await session.execute(select(AiFeatureInvocationModel))).scalars().all()
            run = (await session.execute(select(DailySummaryRunModel))).scalar_one()
            assert len(usage_rows) == 3
            assert all(row.invocation_id == invocations[0].id for row in usage_rows)
            assert invocations[0].feature == "daily_summary"
            assert invocations[0].trigger == "scheduled"
            assert invocations[0].status == "partial"
            assert run.status == "failed"
            assert run.pipeline_cost_usd > 0
            assert run.pipeline_has_unknown_cost is False
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_daily_summary_cancellation_finalizes_invocation_without_failing_run(monkeypatch):
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        real_pipeline = daily_summary_module.run_daily_summary_pipeline
        access = _paid_access(session_factory)
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))

        async def cancel_after_provider_start(**kwargs):
            await client.accounting_service.mark_provider_attempt_started(
                invocation_id=kwargs["invocation_id"],
            )
            raise asyncio.CancelledError

        monkeypatch.setattr(daily_summary_module, "run_daily_summary_pipeline", cancel_after_provider_start)
        with pytest.raises(asyncio.CancelledError):
            await attempt_daily_summary_run(
                bot=_fake_bot(), session_factory=session_factory, llm_client=client,
                chat=ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat"),
                trigger="scheduled", window_to=_NOW, summary_date=_NOW.date(), now_utc=_NOW,
                settings=_test_settings(),
                feature_access_service=access,
            )

        async with session_factory() as session:
            invocation = (await session.execute(select(AiFeatureInvocationModel))).scalar_one()
            run = (await session.execute(select(DailySummaryRunModel))).scalar_one()
            usage_rows = (await session.execute(select(LlmUsageLogModel))).scalars().all()
        aggregate = await client.accounting_service.aggregate_summary_run(summary_run_id=run.id)
        assert invocation.status == "partial"
        assert invocation.error_category == "cancelled"
        assert invocation.completed_at is not None
        assert invocation.provider_attempt_started_at is not None
        assert usage_rows == []
        assert aggregate.provider_calls == 1
        assert aggregate.known_cost_usd == 0
        assert aggregate.has_unknown_cost
        # A provider-start marker without a persisted usage row remains visible
        # as one unknown-cost attempt, and the run stays recoverable.
        assert run.status not in {"failed", "sent"}

        async with session_factory() as session:
            run_row = await session.get(DailySummaryRunModel, run.id)
            run_row.lease_until = _NOW - timedelta(seconds=1)
            await session.commit()
        monkeypatch.setattr(daily_summary_module, "run_daily_summary_pipeline", real_pipeline)
        recovered = await attempt_daily_summary_run(
            bot=_fake_bot(), session_factory=session_factory, llm_client=client,
            chat=ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat"),
            trigger="scheduled", window_to=_NOW + timedelta(minutes=40), summary_date=_NOW.date(),
            now_utc=_NOW + timedelta(minutes=40), settings=_test_settings(),
            feature_access_service=access,
        )

        assert recovered.sent
        async with session_factory() as session:
            stored_run = await SqlAlchemyActivityRepository(session).get_daily_summary_run_by_id(run_id=run.id)
            invocations = (await session.execute(select(AiFeatureInvocationModel))).scalars().all()
        assert stored_run.status == "sent"
        assert stored_run.pipeline_cost_usd > 0
        assert stored_run.pipeline_has_unknown_cost is True
        assert len(invocations) == 2
        assert {item.summary_run_id for item in invocations} == {run.id}
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manual_trigger_works_even_when_scheduled_automation_is_disabled() -> None:
    # /summary must work regardless of the daily_summary_enabled automation toggle --
    # only the scheduled path is gated on that setting (see docs/DAILY_SUMMARY_TODO.md)
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")
            await repo.upsert_chat_settings(chat=chat, values={"daily_summary_enabled": False})
            await session.commit()

        bot = _fake_bot()
        llm_client = _FakeLlmClient()
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")

        outcome = await attempt_daily_summary_run(
            bot=bot, session_factory=session_factory, llm_client=llm_client, chat=chat,
            trigger="manual", window_to=_NOW, summary_date=_NOW.date(), now_utc=_NOW,
        )

        assert outcome.sent is True
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manual_summary_quota_denial_expires_but_preserves_run_and_never_starts_pipeline():
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        access = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(session_factory))
        for index in range(10):
            decision = await access.reserve_feature_usage(
                feature=AiFeature.DAILY_SUMMARY,
                chat_id=_CHAT_ID,
                chat_type="supergroup",
                chat_title="Test Chat",
                actor_user_id=None,
                actor_is_bot=False,
                trigger="manual",
                timezone_name="UTC",
                idempotency_key=f"daily_summary:{_CHAT_ID}:seed:{index}",
                now=_NOW,
            )
            assert decision.allowed

        llm_client = _FakeLlmClient()
        outcome = await attempt_daily_summary_run(
            bot=_fake_bot(),
            session_factory=session_factory,
            llm_client=llm_client,
            chat=ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat"),
            trigger="manual",
            window_to=_NOW,
            summary_date=_NOW.date(),
            now_utc=_NOW,
            actor_user_id=_USER_ID,
            source_message_id=999,
            settings=Settings(bot_token="123:TEST", database_url="postgresql+asyncpg://localhost/test"),
        )

        assert outcome.sent is False
        assert outcome.reason == "quota_exhausted"
        assert outcome.access_decision.quota_used == 10
        assert llm_client._structured_calls == 0
        async with session_factory() as session:
            runs = (await session.execute(select(DailySummaryRunModel))).scalars().all()
            provider_call_count = await session.scalar(select(func.count(LlmUsageLogModel.id)))
            manual_quota_count = await session.scalar(
                select(func.count(AiFeatureQuotaUsageModel.id)).where(
                    AiFeatureQuotaUsageModel.feature == AiFeature.DAILY_SUMMARY.value,
                )
            )
        assert len(runs) == 1
        assert runs[0].status == "claimed"
        assert runs[0].lease_until < runs[0].claimed_at
        assert provider_call_count == 0
        assert manual_quota_count == 10
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manual_summary_reclaims_same_run_and_quota_after_worker_dies(monkeypatch):
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")
        settings = Settings(
            bot_token="123:TEST",
            database_url="postgresql+asyncpg://localhost/test",
            bot_timezone="UTC",
            admin_user_id=None,
        )

        async def cancel_before_provider_attempt(**kwargs):
            raise asyncio.CancelledError

        real_pipeline = daily_summary_module.run_daily_summary_pipeline
        monkeypatch.setattr(
            daily_summary_module,
            "run_daily_summary_pipeline",
            cancel_before_provider_attempt,
        )
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))

        with pytest.raises(asyncio.CancelledError):
            await attempt_daily_summary_run(
                bot=_fake_bot(), session_factory=session_factory, llm_client=client, chat=chat,
                trigger="manual", window_to=_NOW, summary_date=_NOW.date(), now_utc=_NOW,
                actor_user_id=_USER_ID, source_message_id=4242, settings=settings,
            )

        async with session_factory() as session:
            run = (await session.execute(select(DailySummaryRunModel))).scalar_one()
            original_run_id = run.id
            assert run.status == "claimed"
            run.lease_until = _NOW - timedelta(seconds=1)
            await session.commit()
            quota_before_reclaim = (await session.execute(select(AiFeatureQuotaUsageModel))).scalars().all()
        assert len(quota_before_reclaim) == 1
        assert quota_before_reclaim[0].status == "released"
        original_invocation_id = quota_before_reclaim[0].invocation_id
        async with session_factory() as session:
            invocation = await session.get(AiFeatureInvocationModel, original_invocation_id)
            provider_call_count = await session.scalar(select(func.count(LlmUsageLogModel.id)))
        assert invocation.status == "failed"
        assert invocation.provider_attempt_started_at is None
        assert provider_call_count == 0

        monkeypatch.setattr(daily_summary_module, "run_daily_summary_pipeline", real_pipeline)
        recovered = await attempt_daily_summary_run(
            bot=_fake_bot(), session_factory=session_factory, llm_client=client, chat=chat,
            trigger="manual", window_to=_NOW + timedelta(minutes=40), summary_date=_NOW.date(),
            now_utc=_NOW + timedelta(minutes=40), actor_user_id=_USER_ID,
            source_message_id=9999, settings=settings,
        )

        assert recovered.sent is True
        async with session_factory() as session:
            runs = (await session.execute(select(DailySummaryRunModel))).scalars().all()
            quota = (await session.execute(select(AiFeatureQuotaUsageModel))).scalars().all()
            invocation = await session.get(AiFeatureInvocationModel, original_invocation_id)
            provider_calls = await session.scalar(
                select(func.count(LlmUsageLogModel.id)).where(
                    LlmUsageLogModel.invocation_id == original_invocation_id,
                )
            )
        assert len(runs) == 1 and runs[0].id == original_run_id and runs[0].status == "sent"
        assert len(quota) == 1 and quota[0].invocation_id == original_invocation_id
        assert quota[0].status == "consumed"
        assert quota[0].released_at is None and quota[0].release_reason is None
        assert quota[0].source_message_id == 4242  # first message remains audit metadata
        assert invocation.summary_run_id == original_run_id
        assert invocation.status == "succeeded"
        assert provider_calls > 0
        usage_summary = await FeatureAccessService(
            SqlAlchemyFeatureQuotaRepository(session_factory),
        ).get_usage_summary(
            feature=AiFeature.DAILY_SUMMARY,
            chat_id=_CHAT_ID,
            trigger="manual",
            timezone_name="UTC",
            now=_NOW + timedelta(minutes=40),
        )
        assert usage_summary.quota_used == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manual_recovery_after_marker_only_attempt_preserves_unknown_cost(monkeypatch):
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")
        settings = _test_settings()
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))

        async def cancel_after_provider_start(**kwargs):
            await client.accounting_service.mark_provider_attempt_started(
                invocation_id=kwargs["invocation_id"],
            )
            raise asyncio.CancelledError

        real_pipeline = daily_summary_module.run_daily_summary_pipeline
        monkeypatch.setattr(daily_summary_module, "run_daily_summary_pipeline", cancel_after_provider_start)
        with pytest.raises(asyncio.CancelledError):
            await attempt_daily_summary_run(
                bot=_fake_bot(), session_factory=session_factory, llm_client=client, chat=chat,
                trigger="manual", window_to=_NOW, summary_date=_NOW.date(), now_utc=_NOW,
                actor_user_id=_USER_ID, source_message_id=4242, settings=settings,
            )

        async with session_factory() as session:
            run = (await session.execute(select(DailySummaryRunModel))).scalar_one()
            quota = (await session.execute(select(AiFeatureQuotaUsageModel))).scalar_one()
            original_invocation = await session.get(AiFeatureInvocationModel, quota.invocation_id)
            original_run_id = run.id
            original_invocation_id = original_invocation.id
            assert run.status == "claimed"
            run.lease_until = _NOW - timedelta(seconds=1)
            await session.commit()
            assert await session.scalar(select(func.count(LlmUsageLogModel.id))) == 0
        assert quota.status == "consumed"
        assert original_invocation.provider_attempt_started_at is not None
        assert original_invocation.status == "partial"

        monkeypatch.setattr(daily_summary_module, "run_daily_summary_pipeline", real_pipeline)
        recovered = await attempt_daily_summary_run(
            bot=_fake_bot(), session_factory=session_factory, llm_client=client, chat=chat,
            trigger="manual", window_to=_NOW + timedelta(minutes=40), summary_date=_NOW.date(),
            now_utc=_NOW + timedelta(minutes=40), actor_user_id=_USER_ID,
            source_message_id=9999, settings=settings,
        )

        assert recovered.sent
        aggregate = await client.accounting_service.aggregate_summary_run(summary_run_id=original_run_id)
        async with session_factory() as session:
            run = await SqlAlchemyActivityRepository(session).get_daily_summary_run_by_id(run_id=original_run_id)
            quota_rows = (await session.execute(select(AiFeatureQuotaUsageModel))).scalars().all()
            invocations = (await session.execute(
                select(AiFeatureInvocationModel).where(
                    AiFeatureInvocationModel.summary_run_id == original_run_id,
                ).order_by(AiFeatureInvocationModel.id)
            )).scalars().all()
            usage_rows = (await session.execute(
                select(LlmUsageLogModel).where(LlmUsageLogModel.invocation_id != original_invocation_id)
            )).scalars().all()

        assert run.status == "sent"
        assert run.pipeline_has_unknown_cost is True
        assert aggregate.has_unknown_cost is True
        assert aggregate.provider_calls == len(usage_rows) + 1
        assert len(quota_rows) == 1 and quota_rows[0].status == "consumed"
        assert quota_rows[0].invocation_id == original_invocation_id
        assert len(invocations) == 2
        assert invocations[0].status == "partial" and invocations[0].provider_attempt_started_at is not None
        assert invocations[1].status == "succeeded" and invocations[1].id != original_invocation_id
        assert usage_rows
        usage_summary = await FeatureAccessService(SqlAlchemyFeatureQuotaRepository(session_factory)).get_usage_summary(
            feature=AiFeature.DAILY_SUMMARY,
            chat_id=_CHAT_ID,
            trigger="manual",
            timezone_name="UTC",
            now=_NOW + timedelta(minutes=40),
        )
        assert usage_summary.quota_used == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scheduled_trigger_is_blocked_when_automation_is_disabled() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")
            await repo.upsert_chat_settings(chat=chat, values={"daily_summary_enabled": False})
            await session.commit()

        bot = _fake_bot()
        llm_client = _FakeLlmClient()
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")

        outcome = await attempt_daily_summary_run(
            bot=bot, session_factory=session_factory, llm_client=llm_client, chat=chat,
            trigger="scheduled", window_to=_NOW, summary_date=_NOW.date(), now_utc=_NOW,
            settings=_test_settings(),
        )

        assert outcome.sent is False
        assert outcome.reason == "not_eligible:disabled"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scheduled_free_chat_is_denied_before_count_claim_quota_or_provider(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=daily_summary_module.__name__)
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        count_messages = AsyncMock(return_value=60)
        monkeypatch.setattr(
            daily_summary_module.SqlAlchemyActivityRepository,
            "count_archived_messages_in_window",
            count_messages,
        )
        daily_summary_module._ACCESS_LOG_KEYS.clear()
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))
        bot = _fake_bot()
        kwargs = {
            "bot": bot,
            "session_factory": session_factory,
            "llm_client": client,
            "chat": ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat"),
            "trigger": "scheduled",
            "window_to": _NOW,
            "summary_date": _NOW.date(),
            "now_utc": _NOW,
            "settings": _test_settings(),
        }

        first = await attempt_daily_summary_run(**kwargs)
        second = await attempt_daily_summary_run(**kwargs)

        assert first.reason == "access_required"
        assert first.access_decision.reason.value == "access_required"
        assert second.reason == "access_required"
        assert client._structured_calls == 0
        count_messages.assert_not_awaited()
        bot.send_message.assert_not_awaited()
        assert sum(
            "Scheduled Daily Summary skipped: access required" in record.getMessage()
            for record in caplog.records
        ) == 1

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            chat_settings = await repo.get_chat_settings(chat_id=_CHAT_ID)
            runs = (await session.execute(select(DailySummaryRunModel))).scalars().all()
            invocations = (await session.execute(select(AiFeatureInvocationModel))).scalars().all()
            quota_rows = (await session.execute(select(AiFeatureQuotaUsageModel))).scalars().all()
            provider_rows = (await session.execute(select(LlmUsageLogModel))).scalars().all()
        assert chat_settings.daily_summary_enabled is True
        assert runs == []
        assert invocations == []
        assert quota_rows == []
        assert provider_rows == []
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_persistent_paid_entitlement_runs_scheduled_summary_and_expired_tier_stays_free():
    engine, session_factory = await _database()
    try:
        paid_chat_id = _CHAT_ID - 1
        expired_chat_id = _CHAT_ID - 2
        await _seed_chat(session_factory, message_count=60, min_messages=50, chat_id=paid_chat_id)
        await _seed_chat(session_factory, message_count=60, min_messages=50, chat_id=expired_chat_id)
        async with session_factory() as session:
            session.add_all(
                [
                    ChatEntitlementModel(
                        chat_id=paid_chat_id,
                        product_key=SELARA_AI_PRODUCT_KEY,
                        status="active",
                        valid_from=_NOW - timedelta(days=1),
                        valid_until=_NOW + timedelta(days=29),
                    ),
                    ChatEntitlementModel(
                        chat_id=expired_chat_id,
                        product_key=SELARA_AI_PRODUCT_KEY,
                        status="active",
                        valid_from=_NOW - timedelta(days=31),
                        valid_until=_NOW - timedelta(days=1),
                    ),
                ]
            )
            await session.commit()

        bot = _fake_bot()
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))
        paid_outcome = await attempt_daily_summary_run(
            bot=bot,
            session_factory=session_factory,
            llm_client=client,
            chat=ChatSnapshot(telegram_chat_id=paid_chat_id, chat_type="supergroup", title="Paid Chat"),
            trigger="scheduled",
            window_to=_NOW,
            summary_date=_NOW.date(),
            now_utc=_NOW,
            settings=_test_settings(),
        )
        assert paid_outcome.sent
        assert paid_outcome.access_decision.access_tier == AccessTier.PAID
        assert paid_outcome.access_decision.entitlement_source == "telegram_stars"
        assert paid_outcome.access_decision.entitlement_product == SELARA_AI_PRODUCT_KEY
        paid_provider_calls = client._structured_calls
        assert paid_provider_calls > 0

        expired_outcome = await attempt_daily_summary_run(
            bot=bot,
            session_factory=session_factory,
            llm_client=client,
            chat=ChatSnapshot(telegram_chat_id=expired_chat_id, chat_type="supergroup", title="Expired Chat"),
            trigger="scheduled",
            window_to=_NOW,
            summary_date=_NOW.date(),
            now_utc=_NOW,
            settings=_test_settings(),
        )
        assert not expired_outcome.sent and expired_outcome.reason == "access_required"
        assert client._structured_calls == paid_provider_calls
        assert bot.send_message.await_count == 1
        async with session_factory() as session:
            run_count = await session.scalar(select(func.count(DailySummaryRunModel.id)))
            invocation_count = await session.scalar(select(func.count(AiFeatureInvocationModel.id)))
            provider_count = await session.scalar(select(func.count(LlmUsageLogModel.id)))
        assert run_count == 1
        assert invocation_count > 0
        assert provider_count > 0
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scheduled_generated_send_failed_resends_require_entitlement_and_reuse_run():
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=0, min_messages=50)
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            run = await repo.claim_daily_summary_run(
                chat=chat,
                summary_date=_NOW.date(),
                window_from=_NOW - timedelta(hours=24),
                window_to=_NOW,
                trigger="scheduled",
                lease_seconds=1800,
                now=_NOW,
            )
            assert run is not None
            await repo.finalize_daily_summary_run_generated(
                run_id=run.id,
                claimed_at=run.claimed_at,
                generated_text="Ранее созданные итоги.",
                topics_json=[],
                pipeline_cost_usd=Decimal("0.0000045"),
                context_stt_cost_usd=0,
            )
            await session.commit()
        run_id = run.id
        bot = _fake_bot()
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))
        call_kwargs = {
            "bot": bot,
            "session_factory": session_factory,
            "llm_client": client,
            "chat": chat,
            "trigger": "scheduled",
            "window_to": _NOW,
            "summary_date": _NOW.date(),
            "now_utc": _NOW,
            "settings": _test_settings(),
        }

        denied = await attempt_daily_summary_run(**call_kwargs)
        assert denied.reason == "access_required"
        bot.send_message.assert_not_awaited()

        bot.send_message.side_effect = [RuntimeError("temporary Telegram failure"), None]
        access = _paid_access(session_factory)
        failed_send = await attempt_daily_summary_run(
            **call_kwargs,
            feature_access_service=access,
        )
        assert not failed_send.sent and failed_send.reason == "send_failed"
        async with session_factory() as session:
            failed_run = await SqlAlchemyActivityRepository(session).get_daily_summary_run_by_id(run_id=run_id)
        assert failed_run.status == "send_failed"

        resent = await attempt_daily_summary_run(
            **call_kwargs,
            feature_access_service=access,
        )
        assert resent.sent and resent.reason == "sent"
        assert client._structured_calls == 0
        assert bot.send_message.await_count == 2
        async with session_factory() as session:
            final_run = await SqlAlchemyActivityRepository(session).get_daily_summary_run_by_id(run_id=run_id)
            runs = (await session.execute(select(DailySummaryRunModel))).scalars().all()
            invocations = (await session.execute(select(AiFeatureInvocationModel))).scalars().all()
            quota_rows = (await session.execute(select(AiFeatureQuotaUsageModel))).scalars().all()
        assert len(runs) == 1 and final_run.status == "sent"
        assert final_run.id == run_id
        assert invocations == []
        assert quota_rows == []
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_group_migration_after_claim_reloads_canonical_chat_before_generation():
    engine, session_factory = await _database()
    try:
        new_chat_id = _CHAT_ID - 100
        await _seed_chat(session_factory, message_count=60, min_messages=50)

        class MigrateDuringAccessRecheckResolver:
            calls = 0

            async def resolve(self, *, chat_id, feature, trigger):
                self.calls += 1
                if self.calls == 2:
                    async with session_factory() as session:
                        await migrate_chat_id(
                            session,
                            old_chat_id=_CHAT_ID,
                            new_chat_id=new_chat_id,
                            new_chat_type="supergroup",
                            new_chat_title="Test Chat",
                        )
                        await session.commit()
                return FeatureEntitlement(access_tier=AccessTier.PAID, source="test_only")

        resolver = MigrateDuringAccessRecheckResolver()
        access = _paid_access(session_factory, resolver=resolver)
        bot = _fake_bot()
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))
        outcome = await attempt_daily_summary_run(
            bot=bot,
            session_factory=session_factory,
            llm_client=client,
            chat=ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="group", title="Test Chat"),
            trigger="scheduled",
            window_to=_NOW,
            summary_date=_NOW.date(),
            now_utc=_NOW,
            settings=_test_settings(),
            feature_access_service=access,
        )

        assert outcome.sent
        assert resolver.calls == 3
        bot.send_message.assert_awaited_once()
        assert bot.send_message.await_args.kwargs["chat_id"] == new_chat_id
        async with session_factory() as session:
            runs = (await session.execute(select(DailySummaryRunModel))).scalars().all()
            invocations = (await session.execute(select(AiFeatureInvocationModel))).scalars().all()
            usages = (await session.execute(select(LlmUsageLogModel))).scalars().all()
        assert len(runs) == 1 and runs[0].chat_id == new_chat_id and runs[0].status == "sent"
        assert len(invocations) == 1 and invocations[0].chat_id == new_chat_id
        assert invocations[0].summary_run_id == runs[0].id
        assert usages and all(row.chat_id == new_chat_id for row in usages)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parallel_scheduled_claims_start_only_one_provider_pipeline(monkeypatch):
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        original_count = SqlAlchemyActivityRepository.count_archived_messages_in_window
        both_counted = asyncio.Event()
        count_calls = 0

        async def wait_until_both_counted(self, **kwargs):
            nonlocal count_calls
            result = await original_count(self, **kwargs)
            count_calls += 1
            if count_calls == 2:
                both_counted.set()
            await both_counted.wait()
            return result

        monkeypatch.setattr(
            SqlAlchemyActivityRepository,
            "count_archived_messages_in_window",
            wait_until_both_counted,
        )
        bot = _fake_bot()
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))
        kwargs = {
            "bot": bot,
            "session_factory": session_factory,
            "llm_client": client,
            "chat": ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat"),
            "trigger": "scheduled",
            "window_to": _NOW,
            "summary_date": _NOW.date(),
            "now_utc": _NOW,
            "settings": _test_settings(),
            "feature_access_service": _paid_access(session_factory),
        }

        left, right = await asyncio.gather(
            attempt_daily_summary_run(**kwargs),
            attempt_daily_summary_run(**kwargs),
        )

        assert sum(outcome.sent for outcome in (left, right)) == 1
        assert sorted(outcome.reason for outcome in (left, right)) == ["claim_lost", "sent"]
        assert client._structured_calls == 2
        bot.send_message.assert_awaited_once()
        async with session_factory() as session:
            runs = (await session.execute(select(DailySummaryRunModel))).scalars().all()
            invocations = (await session.execute(select(AiFeatureInvocationModel))).scalars().all()
            usages = (await session.execute(select(LlmUsageLogModel))).scalars().all()
        assert len(runs) == 1 and runs[0].status == "sent"
        assert len(invocations) == 1 and invocations[0].trigger == "scheduled"
        assert len(usages) == 5
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_claim_fences_stale_worker_finalize_and_delivery():
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=0, min_messages=50)
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")
        window_from = _NOW - timedelta(hours=24)

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            worker_a = await repo.claim_daily_summary_run(
                chat=chat,
                summary_date=_NOW.date(),
                window_from=window_from,
                window_to=_NOW,
                trigger="scheduled",
                lease_seconds=60,
                now=_NOW,
            )
            await session.commit()
        assert worker_a is not None

        # Simulate worker A's lease expiring while its process is still alive.
        async with session_factory() as session:
            await session.execute(
                update(DailySummaryRunModel)
                .where(DailySummaryRunModel.id == worker_a.id)
                .values(lease_until=_NOW + timedelta(seconds=30))
            )
            await session.commit()

        reclaim_time = _NOW + timedelta(minutes=2)
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            worker_b = await repo.claim_daily_summary_run(
                chat=chat,
                summary_date=_NOW.date(),
                window_from=window_from,
                window_to=_NOW,
                trigger="scheduled",
                lease_seconds=60,
                now=reclaim_time,
            )
            await session.commit()
        assert worker_b is not None and worker_b.id == worker_a.id
        assert worker_b.claimed_at != worker_a.claimed_at

        # A stale worker cannot fail or finalize the run after B reclaimed it.
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            stale_failure = await repo.mark_daily_summary_run_failed(
                run_id=worker_a.id, claimed_at=worker_a.claimed_at, error="stale worker"
            )
            stale_finalize = await repo.finalize_daily_summary_run_generated(
                run_id=worker_a.id,
                claimed_at=worker_a.claimed_at,
                generated_text="Сводка от A",
                topics_json=[],
                pipeline_cost_usd=0.01,
                context_stt_cost_usd=0,
            )
            worker_b_finalize = await repo.finalize_daily_summary_run_generated(
                run_id=worker_b.id,
                claimed_at=worker_b.claimed_at,
                generated_text="Сводка от B",
                topics_json=[],
                pipeline_cost_usd=0.02,
                context_stt_cost_usd=0,
            )
            stale_sent = await repo.mark_daily_summary_run_sent(
                run_id=worker_a.id, claimed_at=worker_a.claimed_at, sent_at=reclaim_time
            )
            stale_send_failed = await repo.mark_daily_summary_run_send_failed(
                run_id=worker_a.id, claimed_at=worker_a.claimed_at, error="stale sender"
            )
            await session.commit()

        assert not stale_failure
        assert not stale_finalize
        assert worker_b_finalize
        assert not stale_sent
        assert not stale_send_failed

        stale_bot = _fake_bot()
        stale_send = await daily_summary_module._send_and_mark(
            bot=stale_bot,
            session_factory=session_factory,
            chat_id=_CHAT_ID,
            run_id=worker_a.id,
            claimed_at=worker_a.claimed_at,
        )
        assert stale_send is None  # mapped to claim_lost by the orchestrator
        stale_bot.send_message.assert_not_awaited()

        async with session_factory() as session:
            stored = await SqlAlchemyActivityRepository(session).get_daily_summary_run_by_id(run_id=worker_b.id)
        assert stored is not None
        assert stored.status == "generated"
        assert stored.generated_text == "Сводка от B"
        assert stored.pipeline_cost_usd == Decimal("0.02")
        assert stored.sent_at is None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scheduled_owner_internal_chat_runs_without_paid_entitlement_or_manual_quota():
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        bot = _fake_bot(owner_status="administrator")
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))

        outcome = await attempt_daily_summary_run(
            bot=bot,
            session_factory=session_factory,
            llm_client=client,
            chat=ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat"),
            trigger="scheduled",
            window_to=_NOW,
            summary_date=_NOW.date(),
            now_utc=_NOW,
            settings=_test_settings(admin_user_id=_USER_ID),
        )

        assert outcome.sent
        assert outcome.access_decision.allowed
        assert outcome.access_decision.access_tier == AccessTier.OWNER_INTERNAL
        bot.get_chat_member.assert_awaited()  # the whole chat, not a particular command actor, is exempt
        async with session_factory() as session:
            invocation = (await session.execute(select(AiFeatureInvocationModel))).scalar_one()
            quota_count = await session.scalar(select(func.count(AiFeatureQuotaUsageModel.id)))
            run = (await session.execute(select(DailySummaryRunModel))).scalar_one()
        assert invocation.trigger == "scheduled"
        assert invocation.summary_run_id == run.id
        assert quota_count == 0
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manual_and_scheduled_runs_share_date_but_keep_access_and_quota_independent():
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")
        bot = _fake_bot()
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))
        access = _paid_access(session_factory)
        settings = _test_settings()

        scheduled, manual = await asyncio.gather(
            attempt_daily_summary_run(
                bot=bot,
                session_factory=session_factory,
                llm_client=client,
                chat=chat,
                trigger="scheduled",
                window_to=_NOW,
                summary_date=_NOW.date(),
                now_utc=_NOW,
                settings=settings,
                feature_access_service=access,
            ),
            attempt_daily_summary_run(
                bot=bot,
                session_factory=session_factory,
                llm_client=client,
                chat=chat,
                trigger="manual",
                window_to=_NOW,
                summary_date=_NOW.date(),
                now_utc=_NOW,
                actor_user_id=_USER_ID,
                source_message_id=987,
                settings=settings,
                feature_access_service=access,
            ),
        )

        assert scheduled.sent and manual.sent
        async with session_factory() as session:
            runs = (await session.execute(select(DailySummaryRunModel))).scalars().all()
            invocations = (await session.execute(select(AiFeatureInvocationModel))).scalars().all()
            quota = (await session.execute(select(AiFeatureQuotaUsageModel))).scalars().all()
        assert {run.trigger for run in runs} == {"manual", "scheduled"}
        assert {invocation.trigger for invocation in invocations} == {"manual", "scheduled"}
        assert len(quota) == 1 and quota[0].trigger == "manual"
        assert {invocation.summary_run_id for invocation in invocations} == {run.id for run in runs}
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scheduled_entitlement_revoked_after_initial_check_releases_claim_and_recovers():
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)

        class RevokeBetweenChecksResolver:
            calls = 0

            async def resolve(self, *, chat_id, feature, trigger):
                self.calls += 1
                if self.calls == 2:
                    return FeatureEntitlement(access_tier=AccessTier.FREE)
                return FeatureEntitlement(access_tier=AccessTier.PAID, source="test_only")

        resolver = RevokeBetweenChecksResolver()
        access = _paid_access(session_factory, resolver=resolver)
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")
        base_kwargs = {
            "bot": _fake_bot(),
            "session_factory": session_factory,
            "llm_client": client,
            "chat": chat,
            "trigger": "scheduled",
            "window_to": _NOW,
            "summary_date": _NOW.date(),
            "settings": _test_settings(),
            "feature_access_service": access,
        }

        denied = await attempt_daily_summary_run(now_utc=_NOW, **base_kwargs)

        assert denied.reason == "access_required"
        assert client._structured_calls == 0
        async with session_factory() as session:
            run = (await session.execute(select(DailySummaryRunModel))).scalar_one()
            invocations = (await session.execute(select(AiFeatureInvocationModel))).scalars().all()
            assert await session.scalar(select(func.count(LlmUsageLogModel.id))) == 0
        assert run.status == "claimed"
        assert run.lease_until < run.claimed_at
        assert invocations == []

        recovery_kwargs = {
            **base_kwargs,
            "now_utc": _NOW + timedelta(minutes=40),
            "window_to": _NOW + timedelta(minutes=40),
        }
        recovered = await attempt_daily_summary_run(**recovery_kwargs)

        assert recovered.sent
        assert resolver.calls == 4
        async with session_factory() as session:
            runs = (await session.execute(select(DailySummaryRunModel))).scalars().all()
            invocation = (await session.execute(select(AiFeatureInvocationModel))).scalar_one()
        assert len(runs) == 1 and runs[0].id == run.id and runs[0].status == "sent"
        assert invocation.trigger == "scheduled" and invocation.summary_run_id == run.id
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scheduled_toggle_disabled_during_access_recheck_stops_pipeline():
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")

        class DisableToggleDuringRecheckResolver:
            calls = 0

            async def resolve(self, *, chat_id, feature, trigger):
                self.calls += 1
                if self.calls == 2:
                    async with session_factory() as session:
                        repo = SqlAlchemyActivityRepository(session)
                        await repo.upsert_chat_settings(
                            chat=chat,
                            values={"daily_summary_enabled": False},
                        )
                        await session.commit()
                return FeatureEntitlement(access_tier=AccessTier.PAID, source="test_only")

        resolver = DisableToggleDuringRecheckResolver()
        bot = _fake_bot()
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))
        outcome = await attempt_daily_summary_run(
            bot=bot,
            session_factory=session_factory,
            llm_client=client,
            chat=chat,
            trigger="scheduled",
            window_to=_NOW,
            summary_date=_NOW.date(),
            now_utc=_NOW,
            settings=_test_settings(),
            feature_access_service=_paid_access(session_factory, resolver=resolver),
        )

        assert outcome.reason == "not_eligible:disabled"
        assert resolver.calls == 2
        assert client._structured_calls == 0
        bot.send_message.assert_not_awaited()
        async with session_factory() as session:
            run = (await session.execute(select(DailySummaryRunModel))).scalar_one()
            invocations = (await session.execute(select(AiFeatureInvocationModel))).scalars().all()
            chat_settings = await SqlAlchemyActivityRepository(session).get_chat_settings(chat_id=_CHAT_ID)
            assert await session.scalar(select(func.count(LlmUsageLogModel.id))) == 0
        assert chat_settings.daily_summary_enabled is False
        assert run.status == "claimed" and run.lease_until < run.claimed_at
        assert invocations == []
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scheduler_fails_closed_per_chat_and_continues_after_entitlement_error():
    engine, session_factory = await _database()
    try:
        allowed_chat_id = _CHAT_ID + 1
        await _seed_chat(session_factory, message_count=60, min_messages=50, chat_id=_CHAT_ID, daily_summary_hour=_NOW.hour)
        await _seed_chat(session_factory, message_count=60, min_messages=50, chat_id=allowed_chat_id, daily_summary_hour=_NOW.hour)
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            chat_settings = await repo.get_chat_settings(chat_id=_CHAT_ID)
            assert chat_settings is not None
            scheduled_window_to = daily_summary_module.compute_scheduled_window_to(
                hour=chat_settings.daily_summary_hour,
                now_local=_NOW,
            )
            messages = (await session.execute(select(MessageArchiveModel))).scalars().all()
            for index, message in enumerate(messages):
                # Keep both chats' full seed sets inside the scheduled
                # window. Use the configured window boundary, not a fixed clock.
                message.snapshot_at = scheduled_window_to - timedelta(hours=8) + timedelta(minutes=index % 60)
                message.sent_at = message.snapshot_at
            await session.commit()

        class OneChatResolver:
            async def resolve(self, *, chat_id, feature, trigger):
                if chat_id == _CHAT_ID:
                    raise RuntimeError("entitlement store unavailable")
                return FeatureEntitlement(access_tier=AccessTier.PAID, source="test_only")

        bot = _fake_bot()
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))
        scheduler = daily_summary_module.DailySummaryScheduler(
            bot=bot,
            session_factory=session_factory,
            llm_client=client,
            settings=_test_settings(),
            feature_access_service=_paid_access(session_factory, resolver=OneChatResolver()),
        )

        sent_count = await scheduler.run_once(now=_NOW)

        assert sent_count == 1
        assert bot.send_message.await_count == 1
        async with session_factory() as session:
            runs = (await session.execute(select(DailySummaryRunModel))).scalars().all()
            invocations = (await session.execute(select(AiFeatureInvocationModel))).scalars().all()
        assert len(runs) == 1 and runs[0].chat_id == allowed_chat_id and runs[0].status == "sent"
        assert len(invocations) == 1 and invocations[0].chat_id == allowed_chat_id
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_attempt_daily_summary_run_skips_below_message_threshold() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=5, min_messages=50)
        bot = _fake_bot()
        llm_client = _FakeLlmClient()
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")

        outcome = await attempt_daily_summary_run(
            bot=bot,
            session_factory=session_factory,
            llm_client=llm_client,
            chat=chat,
            trigger="manual",
            window_to=_NOW,
            summary_date=_NOW.date(),
            now_utc=_NOW,
        )

        assert outcome.sent is False
        assert outcome.reason == "not_eligible:not_enough_messages"
        bot.send_message.assert_not_awaited()

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            run = await repo.get_daily_summary_run(chat_id=_CHAT_ID, summary_date=_NOW.date(), trigger="manual")
        assert run is None  # never even claimed -- no wasted LLM cost
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_second_manual_run_same_day_is_a_no_op_after_first_sends() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        bot = _fake_bot()
        llm_client = _FakeLlmClient()
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")

        first = await attempt_daily_summary_run(
            bot=bot, session_factory=session_factory, llm_client=llm_client, chat=chat,
            trigger="manual", window_to=_NOW, summary_date=_NOW.date(), now_utc=_NOW,
        )
        assert first.sent is True

        second = await attempt_daily_summary_run(
            bot=bot, session_factory=session_factory, llm_client=llm_client, chat=chat,
            trigger="manual", window_to=_NOW, summary_date=_NOW.date(), now_utc=_NOW + timedelta(minutes=1),
        )

        assert second.sent is False
        assert second.reason == "already_run_today"
        bot.send_message.assert_awaited_once()  # still just the one send from `first`
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_daily_summary_resends_claim_delivery_and_send_once(monkeypatch):
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=0, min_messages=50)
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            run = await repo.claim_daily_summary_run(
                chat=chat,
                summary_date=_NOW.date(),
                window_from=_NOW - timedelta(hours=24),
                window_to=_NOW,
                trigger="manual",
                lease_seconds=1800,
                now=_NOW,
            )
            assert run is not None
            assert await repo.finalize_daily_summary_run_generated(
                run_id=run.id,
                claimed_at=run.claimed_at,
                generated_text="Одна сохранённая сводка.",
                topics_json=[],
                pipeline_cost_usd=0,
                context_stt_cost_usd=0,
            )
            await session.commit()

        # Force both resenders to read the same old token before either can
        # perform the atomic delivery claim.
        read_barrier = asyncio.Barrier(2)
        initial_reads = 0
        real_repository = daily_summary_module.SqlAlchemyActivityRepository

        class _ReadBarrierRepository(real_repository):
            async def get_daily_summary_run_by_id(self, *, run_id):
                nonlocal initial_reads
                row = await super().get_daily_summary_run_by_id(run_id=run_id)
                if initial_reads < 2:
                    initial_reads += 1
                    await read_barrier.wait()
                return row

        monkeypatch.setattr(daily_summary_module, "SqlAlchemyActivityRepository", _ReadBarrierRepository)
        bot = _fake_bot()
        send_started = asyncio.Event()
        release_send = asyncio.Event()

        async def _blocked_send(**kwargs):
            send_started.set()
            await release_send.wait()
            return SimpleNamespace(message_id=123)

        bot.send_message.side_effect = _blocked_send
        attempts = [
            asyncio.create_task(daily_summary_module._send_and_mark(
                bot=bot, session_factory=session_factory, chat_id=_CHAT_ID, run_id=run.id,
                claimed_at=run.claimed_at,
            ))
            for _ in range(2)
        ]
        await asyncio.wait_for(send_started.wait(), timeout=5)
        await asyncio.sleep(0)
        assert bot.send_message.await_count == 1
        release_send.set()
        results = await asyncio.gather(*attempts)

        assert sorted(results, key=lambda value: str(value)) == [None, True]
        assert bot.send_message.await_count == 1
        async with session_factory() as session:
            stored = await SqlAlchemyActivityRepository(session).get_daily_summary_run_by_id(run_id=run.id)
        assert stored.status == "sent"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_generation_lease_stops_before_next_provider_stage():
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)

        class _ExpireLeaseAfterFirstProviderCall(_FakeLlmClient):
            async def chat_structured(self, messages, *, response_model, max_tokens=None, accounting_context=None):
                result = await super().chat_structured(
                    messages, response_model=response_model, max_tokens=max_tokens,
                    accounting_context=accounting_context,
                )
                if self._structured_calls == 1:
                    async with session_factory() as session:
                        await session.execute(
                            update(DailySummaryRunModel)
                            .where(
                                DailySummaryRunModel.chat_id == _CHAT_ID,
                                DailySummaryRunModel.summary_date == _NOW.date(),
                                DailySummaryRunModel.trigger == "manual",
                            )
                            .values(lease_until=datetime.now(timezone.utc) - timedelta(seconds=1))
                        )
                        await session.commit()
                return result

        client = _ExpireLeaseAfterFirstProviderCall(
            accounting_service=AiAccountingService(session_factory)
        )
        bot = _fake_bot()
        outcome = await attempt_daily_summary_run(
            bot=bot,
            session_factory=session_factory,
            llm_client=client,
            chat=ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat"),
            trigger="manual",
            window_to=_NOW,
            summary_date=_NOW.date(),
            now_utc=_NOW,
        )

        assert outcome.reason == "claim_lost"
        assert client._structured_calls == 1
        bot.send_message.assert_not_awaited()
        async with session_factory() as session:
            run = await SqlAlchemyActivityRepository(session).get_daily_summary_run(
                chat_id=_CHAT_ID, summary_date=_NOW.date(), trigger="manual"
            )
            usage_rows = (await session.execute(select(LlmUsageLogModel))).scalars().all()
        assert run is not None
        assert run.status in {"claimed", "generating"}
        assert run.lease_until <= datetime.now(timezone.utc)
        assert len(usage_rows) == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_crashed_daily_summary_delivery_lease_can_be_reclaimed():
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=0, min_messages=50)
        now = datetime.now(timezone.utc)
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            run = await repo.claim_daily_summary_run(
                chat=chat,
                summary_date=now.date(),
                window_from=now - timedelta(hours=24),
                window_to=now,
                trigger="manual",
                lease_seconds=1800,
                now=now,
            )
            assert run is not None
            assert await repo.finalize_daily_summary_run_generated(
                run_id=run.id,
                claimed_at=run.claimed_at,
                generated_text="Сводка для проверки lease.",
                topics_json=[],
                pipeline_cost_usd=0,
                context_stt_cost_usd=0,
            )
            await session.commit()

        delivery_start = datetime.now(timezone.utc) + timedelta(seconds=1)
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            first_token = await repo.claim_daily_summary_delivery(
                run_id=run.id, claimed_at=run.claimed_at, lease_seconds=1800, now=delivery_start,
            )
            await session.commit()
        assert first_token is not None

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            premature = await repo.claim_daily_summary_delivery(
                run_id=run.id, claimed_at=first_token, lease_seconds=1800,
                now=delivery_start + timedelta(minutes=15),
            )
            await session.commit()
        assert premature is None

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            recovered_token = await repo.claim_daily_summary_delivery(
                run_id=run.id, claimed_at=first_token, lease_seconds=1800,
                now=delivery_start + timedelta(minutes=31),
            )
            await session.commit()
        assert recovered_token is not None
        assert recovered_token != first_token
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scheduled_paid_chat_does_not_call_get_chat_member() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        bot = _fake_bot(owner_status="administrator")
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))

        outcome = await attempt_daily_summary_run(
            bot=bot,
            session_factory=session_factory,
            llm_client=client,
            chat=ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat"),
            trigger="scheduled",
            window_to=_NOW,
            summary_date=_NOW.date(),
            now_utc=_NOW,
            settings=_test_settings(admin_user_id=_USER_ID),
            feature_access_service=_paid_access(session_factory),
        )

        assert outcome.sent
        assert outcome.access_decision.access_tier == AccessTier.PAID
        bot.get_chat_member.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scheduled_free_chat_owner_check_is_cached_between_ticks() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        bot = _fake_bot(owner_status="left")
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))
        cache: dict[int, datetime] = {}
        kwargs = {
            "bot": bot,
            "session_factory": session_factory,
            "llm_client": client,
            "chat": ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat"),
            "trigger": "scheduled",
            "window_to": _NOW,
            "summary_date": _NOW.date(),
            "settings": _test_settings(admin_user_id=_USER_ID),
            "owner_denial_cache": cache,
        }

        first = await attempt_daily_summary_run(now_utc=_NOW, **kwargs)
        second = await attempt_daily_summary_run(now_utc=_NOW + timedelta(minutes=15), **kwargs)
        assert first.reason == "access_required" and second.reason == "access_required"
        assert bot.get_chat_member.await_count == 1

        later = await attempt_daily_summary_run(now_utc=_NOW + timedelta(hours=3), **kwargs)
        assert later.reason == "access_required"
        assert bot.get_chat_member.await_count == 2

        # The cache only remembers denials: once the owner is an admin the chat is served.
        bot.get_chat_member.return_value = SimpleNamespace(status="administrator")
        promoted = await attempt_daily_summary_run(now_utc=_NOW + timedelta(hours=6), **kwargs)
        assert promoted.sent
        assert promoted.access_decision.access_tier == AccessTier.OWNER_INTERNAL
        assert _CHAT_ID not in cache
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scheduler_takes_a_fresh_clock_per_chat_unless_now_is_pinned(monkeypatch) -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50, chat_id=_CHAT_ID, daily_summary_hour=_NOW.hour)
        await _seed_chat(session_factory, message_count=60, min_messages=50, chat_id=_CHAT_ID + 1, daily_summary_hour=_NOW.hour)
        seen: list[datetime] = []

        async def fake_attempt(**kwargs):
            seen.append(kwargs["now_utc"])
            await asyncio.sleep(0.01)  # a slow chat: the next one must not reuse the tick's start time
            return SimpleNamespace(sent=False)

        monkeypatch.setattr(daily_summary_module, "attempt_daily_summary_run", fake_attempt)
        scheduler = daily_summary_module.DailySummaryScheduler(
            bot=_fake_bot(),
            session_factory=session_factory,
            llm_client=_FakeLlmClient(),
            settings=_test_settings(),
        )

        await scheduler.run_once()
        assert len(seen) == 2
        assert seen[1] > seen[0]

        seen.clear()
        await scheduler.run_once(now=_NOW)
        assert seen == [_NOW, _NOW]
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manual_retry_after_failed_run_explains_the_failure() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        bot = _fake_bot()
        client = _FakeLlmClient()
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")
        kwargs = {
            "bot": bot,
            "session_factory": session_factory,
            "llm_client": client,
            "chat": chat,
            "trigger": "manual",
            "window_to": _NOW,
            "summary_date": _NOW.date(),
        }
        await attempt_daily_summary_run(now_utc=_NOW, **kwargs)
        async with session_factory() as session:
            await session.execute(update(DailySummaryRunModel).values(status="failed"))
            await session.commit()

        again = await attempt_daily_summary_run(now_utc=_NOW + timedelta(minutes=1), **kwargs)

        assert again.sent is False
        assert again.reason == "already_failed_today"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failed_owner_lookup_is_not_cached_as_a_denial() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        bot = _fake_bot(owner_status="administrator")
        bot.get_chat_member = AsyncMock(side_effect=RuntimeError("telegram timeout"))
        client = _FakeLlmClient(accounting_service=AiAccountingService(session_factory))
        cache: dict[int, datetime] = {}
        kwargs = {
            "bot": bot,
            "session_factory": session_factory,
            "llm_client": client,
            "chat": ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat"),
            "trigger": "scheduled",
            "window_to": _NOW,
            "summary_date": _NOW.date(),
            "settings": _test_settings(admin_user_id=_USER_ID),
            "owner_denial_cache": cache,
        }

        failed = await attempt_daily_summary_run(now_utc=_NOW, **kwargs)
        assert failed.reason == "access_required"
        assert cache == {}

        bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status="administrator"))
        recovered = await attempt_daily_summary_run(now_utc=_NOW + timedelta(minutes=15), **kwargs)
        assert recovered.sent
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scheduler_skips_stale_new_generation_without_a_claim(monkeypatch) -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(
            session_factory, message_count=60, min_messages=50,
            daily_summary_hour=10,
        )
        candidate = AsyncMock(return_value=SimpleNamespace(sent=False))
        monkeypatch.setattr(daily_summary_module, "attempt_daily_summary_run", candidate)
        scheduler = daily_summary_module.DailySummaryScheduler(
            bot=_fake_bot(), session_factory=session_factory,
            llm_client=_FakeLlmClient(), settings=_test_settings(),
        )

        # Before the planned 10:00 UTC today, yesterday's 10:00 is the
        # computed window; it is NOT a license to bill an ancient report.
        early = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)
        assert await scheduler.run_once(now=early) == 0
        candidate.assert_not_awaited()

        # The next ordinary 15-minute tick is in the 90-minute grace.
        normal = datetime(2026, 10, 10, 10, 30, tzinfo=timezone.utc)
        assert await scheduler.run_once(now=normal) == 0
        assert candidate.await_count == 1
        assert candidate.await_args.kwargs["summary_date"] == normal.date()
        assert candidate.await_args.kwargs["window_to"] == normal.replace(
            hour=10, minute=0, second=0, microsecond=0,
        )

        # A first attempt over the grace limit cannot start a new run.
        too_late = datetime(2026, 10, 10, 11, 31, tzinfo=timezone.utc)
        assert await scheduler.run_once(now=too_late) == 0
        assert candidate.await_count == 1

        async with session_factory() as session:
            persisted = (await session.execute(select(DailySummaryRunModel))).scalars().all()
        assert persisted == []
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scheduled_claims_of_different_dates_are_serialized_per_chat() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=0, min_messages=0)
        start = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
        chat = ChatSnapshot(
            telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat",
        )

        async def claim(day_delta: int):
            window_to = start + timedelta(days=day_delta)
            async with session_factory() as session:
                repo = SqlAlchemyActivityRepository(session)
                result = await repo.claim_daily_summary_run(
                    chat=chat,
                    summary_date=window_to.date(),
                    window_from=window_to - timedelta(days=1),
                    window_to=window_to,
                    trigger="scheduled",
                    lease_seconds=1800,
                    now=start,
                )
                await session.commit()
                return result

        # Each worker uses its own database session; only the first may
        # hold an active scheduled claim, even though dates differ.
        first, second = await asyncio.gather(claim(0), claim(1))
        assert (first is None) != (second is None)
        async with session_factory() as session:
            runs = (await session.execute(select(DailySummaryRunModel))).scalars().all()
        assert len(runs) == 1 and runs[0].status == "claimed"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scheduled_delivery_reservation_and_twenty_hour_gap_are_atomic() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=0, min_messages=0)
        base = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
        chat = ChatSnapshot(
            telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat",
        )
        async with session_factory() as session:
            old, next_day = [], []
            for offset in (0, 1):
                day = base + timedelta(days=offset)
                run = DailySummaryRunModel(
                    chat_id=_CHAT_ID, summary_date=day.date(), trigger="scheduled",
                    window_from=day - timedelta(days=1), window_to=day,
                    status="generated", generated_text="test summary",
                    claimed_at=base - timedelta(minutes=1),
                    lease_until=base - timedelta(seconds=1),
                )
                session.add(run)
                if offset == 0:
                    old.append(run)
                else:
                    next_day.append(run)
            await session.commit()
            first_run, other_run = old[0], next_day[0]
            first_id, other_id = first_run.id, other_run.id
            first_claimed, other_claimed = first_run.claimed_at, other_run.claimed_at

        at = base + timedelta(minutes=1)
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            token = await repo.claim_daily_summary_delivery(
                run_id=first_id, claimed_at=first_claimed,
                lease_seconds=1800, now=at,
            )
            assert token is not None
            await session.commit()

        # Do not pay for a new scheduled generation while the previous date
        # still owns its live delivery lease (even before status='sent').
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            while_sending = await repo.claim_daily_summary_run(
                chat=chat, summary_date=(base + timedelta(days=2)).date(),
                window_from=base, window_to=base + timedelta(days=1),
                trigger="scheduled", lease_seconds=1800,
                now=at + timedelta(seconds=1),
            )
            assert while_sending is None
            await session.rollback()

        # Distinct summary_date does not bypass the live delivery claim.
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            duplicate = await repo.claim_daily_summary_delivery(
                run_id=other_id, claimed_at=other_claimed,
                lease_seconds=1800, now=at + timedelta(seconds=1),
            )
            assert duplicate is None
            await session.rollback()

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            assert await repo.mark_daily_summary_run_sent(
                run_id=first_id, claimed_at=token,
                sent_at=base + timedelta(minutes=2),
            )
            await session.commit()

        # A new date cannot claim a scheduled run only seven hours later,
        # while an independent manual request remains permitted.
        seven_hours_later = base + timedelta(hours=7)
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            refused = await repo.claim_daily_summary_run(
                chat=chat, summary_date=(base + timedelta(days=2)).date(),
                window_from=base, window_to=base + timedelta(days=1),
                trigger="scheduled", lease_seconds=1800,
                now=seven_hours_later,
            )
            manual = await repo.claim_daily_summary_run(
                chat=chat, summary_date=(base + timedelta(days=2)).date(),
                window_from=base, window_to=base + timedelta(days=1),
                trigger="manual", lease_seconds=1800,
                now=seven_hours_later,
            )
            assert refused is None and manual is not None
            await session.commit()

        # Even the older pre-generated run must not be delivered inside 20h.
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            blocked = await repo.claim_daily_summary_delivery(
                run_id=other_id, claimed_at=other_claimed,
                lease_seconds=1800, now=seven_hours_later,
            )
            assert blocked is None
            await session.rollback()

        # The last successful scheduled send is old enough after 21 hours,
        # and a fresh scheduled date can be claimed normally.
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            allowed = await repo.claim_daily_summary_run(
                chat=chat, summary_date=(base + timedelta(days=3)).date(),
                window_from=base + timedelta(days=1),
                window_to=base + timedelta(days=2),
                trigger="scheduled", lease_seconds=1800,
                now=base + timedelta(hours=21),
            )
            assert allowed is not None
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["claimed", "generating", "generated", "send_failed"])
async def test_scheduler_never_retries_seven_hour_old_scheduled_generated_run(monkeypatch, status) -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(
            session_factory, message_count=0, min_messages=0, daily_summary_hour=10,
        )
        due = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
        async with session_factory() as session:
            session.add(DailySummaryRunModel(
                chat_id=_CHAT_ID, summary_date=due.date(), trigger="scheduled",
                window_from=due - timedelta(days=1), window_to=due,
                status=status, generated_text="old generated report",
                claimed_at=due, lease_until=due,
            ))
            await session.commit()
        run_mock = AsyncMock(return_value=SimpleNamespace(sent=False))
        monkeypatch.setattr(daily_summary_module, "attempt_daily_summary_run", run_mock)
        scheduler = daily_summary_module.DailySummaryScheduler(
            bot=_fake_bot(), session_factory=session_factory,
            llm_client=_FakeLlmClient(), settings=_test_settings(),
        )
        # A fresh generated report is still recoverable within the chosen 6h.
        await scheduler.run_once(now=due + timedelta(hours=5))
        run_mock.assert_awaited_once()
        run_mock.reset_mock()

        # Previous generated/send_failed work is no longer published after 6h,
        # even though it has a valid summary_date and the normal claim exists.
        assert await scheduler.run_once(now=due + timedelta(hours=7)) == 0
        run_mock.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_direct_scheduled_delivery_checks_staleness_before_telegram_send() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=0, min_messages=0)
        due = datetime.now(timezone.utc) - timedelta(hours=7)
        async with session_factory() as session:
            run = DailySummaryRunModel(
                chat_id=_CHAT_ID, summary_date=due.date(), trigger="scheduled",
                window_from=due - timedelta(days=1), window_to=due,
                status="generated", generated_text="late report must not send",
                claimed_at=due, lease_until=due,
            )
            session.add(run)
            await session.commit()
            run_id, claimed_at = run.id, run.claimed_at

        bot = _fake_bot()
        delivered = await daily_summary_module._send_and_mark(
            bot=bot, session_factory=session_factory,
            chat_id=_CHAT_ID, run_id=run_id, claimed_at=claimed_at,
        )
        assert delivered is False
        bot.send_message.assert_not_awaited()
        async with session_factory() as session:
            persisted = await session.get(DailySummaryRunModel, run_id)
            assert persisted.status == "generated"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_recovery_deadline_uses_original_window_after_schedule_hour_change(monkeypatch) -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=0, min_messages=0, daily_summary_hour=15)
        original_due = datetime(2026, 10, 10, 8, tzinfo=timezone.utc)
        async with session_factory() as session:
            session.add(DailySummaryRunModel(
                chat_id=_CHAT_ID, summary_date=original_due.date(), trigger='scheduled',
                window_from=original_due-timedelta(days=1), window_to=original_due,
                status='generated', generated_text='old generated report',
                claimed_at=original_due, lease_until=original_due,
            ))
            await session.commit()
        run_mock = AsyncMock(return_value=SimpleNamespace(sent=False))
        monkeypatch.setattr(daily_summary_module, 'attempt_daily_summary_run', run_mock)
        scheduler = daily_summary_module.DailySummaryScheduler(
            bot=_fake_bot(), session_factory=session_factory,
            llm_client=_FakeLlmClient(), settings=_test_settings(),
        )
        # The new 15:00 schedule is fresh, but the persisted 08:00 run
        # must expire at 14:00, rather than acquiring a renewed deadline.
        assert await scheduler.run_once(now=original_due+timedelta(hours=7)) == 0
        run_mock.assert_not_awaited()
    finally:
        await engine.dispose()
