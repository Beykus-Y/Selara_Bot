from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.selara_ai_product import SELARA_AI_PRODUCT_KEY
from selara.infrastructure.db.ai_accounting import AiAccountingService
from selara.infrastructure.db.ai_analytics import AdminAiAnalyticsRepository, PaymentFilters
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import (
    AiFeatureInvocationModel,
    ChatEntitlementModel,
    ChatModel,
    LlmUsageLogModel,
    SelaraAiPaymentModel,
    SelaraAiPurchaseIntentModel,
)
from selara.infrastructure.db.selara_ai_payment_refund import SelaraAiPaymentRefundModel

_NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
_FROM = _NOW - timedelta(days=7)


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _invocation(feature: str, status: str, started_at: datetime, *, marker: bool = False):
    return AiFeatureInvocationModel(
        feature=feature,
        trigger="telegram_message",
        status=status,
        started_at=started_at,
        provider_attempt_started_at=started_at if marker else None,
    )


def _usage(invocation_id: int, *, model: str, stage: str, cost: str | None, pricing: str, status: str = "succeeded"):
    return LlmUsageLogModel(
        call_id=str(uuid4()),
        invocation_id=invocation_id,
        feature="ignored",
        stage=stage,
        model=model,
        prompt_tokens=100,
        completion_tokens=10,
        total_tokens=110,
        estimated_cost_usd=Decimal(cost) if cost is not None else None,
        pricing_status=pricing,
        status=status,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_ai_analytics_match_window_aggregate_and_keep_marker_only_attempts():
    engine, factory = await _database()
    try:
        async with factory() as session:
            inside = _FROM + timedelta(hours=1)
            invocations = [
                _invocation("llm_admin", "succeeded", inside),
                _invocation("llm_admin", "failed", inside),
                _invocation("daily_summary", "partial", inside),
                _invocation("daily_summary", "failed", inside, marker=True),  # marker-only provider attempt
                _invocation("autoconfig", "succeeded", inside),
                _invocation("llm_admin", "succeeded", _FROM - timedelta(microseconds=1)),  # before window
                _invocation("llm_admin", "succeeded", _NOW),  # window_to excluded
                _invocation("llm_admin", "succeeded", _FROM),  # window_from included
            ]
            session.add_all(invocations)
            await session.flush()
            ids = [row.id for row in invocations]
            session.add_all(
                [
                    _usage(ids[0], model="gpt-a", stage="tool_round", cost="0.001", pricing="known"),
                    _usage(ids[1], model="gpt-a", stage="tool_round", cost=None, pricing="unknown", status="failed"),
                    _usage(ids[2], model="gpt-b", stage="writer", cost="0.0025", pricing="known"),
                    _usage(ids[2], model="gpt-b", stage="analyst", cost=None, pricing="unknown"),
                    _usage(ids[4], model="gpt-b", stage="autoconfig", cost="0.0005", pricing="known"),
                    _usage(ids[5], model="gpt-a", stage="tool_round", cost="9", pricing="known"),
                    _usage(ids[6], model="gpt-a", stage="tool_round", cost="9", pricing="known"),
                    _usage(ids[7], model="gpt-a", stage="tool_round", cost="0.002", pricing="known"),
                ]
            )
            await session.commit()

        aggregate = await AiAccountingService(factory).aggregate_window(window_from=_FROM, window_to=_NOW)
        async with factory() as session:
            repository = AdminAiAnalyticsRepository(session)
            features = {row["feature"]: row for row in await repository.feature_breakdown(window_from=_FROM, window_to=_NOW)}
            models, marker_only = await repository.model_breakdown(window_from=_FROM, window_to=_NOW)
            stages = await repository.stage_breakdown(window_from=_FROM, window_to=_NOW)
            daily = await repository.daily_ai_series(window_from=_FROM, window_to=_NOW, timezone_name="UTC")

        # Window semantics: [from, to).
        assert aggregate.invocations == 6
        assert aggregate.failed_invocations == 3  # failed + partial + failed(marker)
        assert aggregate.provider_calls == 7  # 6 usage rows + 1 marker-only attempt
        assert aggregate.unknown_cost_calls == 3  # failed usage, unknown usage, marker-only
        assert aggregate.known_cost_usd == Decimal("0.006")

        # Feature breakdown agrees with the window aggregate.
        assert sum(row["invocations"] for row in features.values()) == aggregate.invocations
        assert sum(row["provider_calls"] for row in features.values()) == aggregate.provider_calls
        assert sum(row["unknown_cost_calls"] for row in features.values()) == aggregate.unknown_cost_calls
        assert sum(row["known_cost_usd"] for row in features.values()) == aggregate.known_cost_usd
        assert sum(row["unsuccessful_invocations"] for row in features.values()) == aggregate.failed_invocations
        summary = features["daily_summary"]
        assert (summary["invocations"], summary["provider_calls"], summary["unknown_cost_calls"]) == (2, 3, 2)
        assert summary["unsuccessful_invocations"] == 2
        assert features["llm_admin"]["invocations"] == 3
        assert set(features) == {"llm_admin", "daily_summary", "autoconfig"}

        # Ordered by known cost first.
        ordered = [row["feature"] for row in await _ordered_features(factory)]
        assert ordered == ["llm_admin", "daily_summary", "autoconfig"]

        # Model breakdown has real usage only; the marker-only attempt is surfaced separately.
        by_model = {row["model"]: row for row in models}
        assert by_model["gpt-a"]["provider_calls"] == 3
        assert by_model["gpt-a"]["unknown_cost_calls"] == 1
        assert by_model["gpt-a"]["known_cost_usd"] == Decimal("0.003")
        assert by_model["gpt-b"]["provider_calls"] == 3
        assert by_model["gpt-b"]["prompt_tokens"] == 300
        assert by_model["gpt-b"]["completion_tokens"] == 30
        assert marker_only == 1
        assert {row["stage"] for row in stages} >= {"writer", "tool_round"}
        assert sum(row["provider_calls"] for row in daily) == aggregate.provider_calls
        assert sum(row["invocations"] for row in daily) == aggregate.invocations
    finally:
        await engine.dispose()


async def _ordered_features(factory):
    async with factory() as session:
        return await AdminAiAnalyticsRepository(session).feature_breakdown(window_from=_FROM, window_to=_NOW)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_ai_analytics_empty_and_all_unknown_cost_states():
    engine, factory = await _database()
    try:
        async with factory() as session:
            repository = AdminAiAnalyticsRepository(session)
            assert await repository.feature_breakdown(window_from=_FROM, window_to=_NOW) == []
            assert await repository.model_breakdown(window_from=_FROM, window_to=_NOW) == ([], 0)
            session.add(_invocation("llm_admin", "succeeded", _FROM + timedelta(hours=1)))
            await session.flush()
            invocation_id = (await session.execute(select(AiFeatureInvocationModel.id))).scalar_one()
            session.add(_usage(invocation_id, model="m", stage="s", cost=None, pricing="unknown"))
            await session.commit()
        aggregate = await AiAccountingService(factory).aggregate_window(window_from=_FROM, window_to=_NOW)
        assert (aggregate.known_cost_usd, aggregate.unknown_cost_calls, aggregate.provider_calls) == (Decimal(0), 1, 1)
    finally:
        await engine.dispose()


async def _seed_billing(factory):
    async with factory() as session:
        session.add_all(
            [
                ChatModel(telegram_chat_id=-1001, type="supergroup", title="Paid chat"),
                ChatModel(telegram_chat_id=-1002, type="supergroup", title="Soon chat"),
                ChatModel(telegram_chat_id=-1003, type="supergroup", title="Expired chat"),
                ChatModel(telegram_chat_id=-1004, type="supergroup", title="Revoked chat"),
                ChatModel(telegram_chat_id=-2001, type="supergroup", title="Migrated supergroup"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                ChatEntitlementModel(
                    chat_id=-1001, product_key=SELARA_AI_PRODUCT_KEY, status="active",
                    valid_from=_NOW - timedelta(days=5), valid_until=_NOW + timedelta(days=25),
                ),
                ChatEntitlementModel(
                    chat_id=-1002, product_key=SELARA_AI_PRODUCT_KEY, status="active",
                    valid_from=_NOW - timedelta(days=25), valid_until=_NOW + timedelta(days=3),
                ),
                ChatEntitlementModel(
                    chat_id=-1003, product_key=SELARA_AI_PRODUCT_KEY, status="active",
                    valid_from=_NOW - timedelta(days=40), valid_until=_NOW - timedelta(milliseconds=1),
                ),
                ChatEntitlementModel(
                    chat_id=-1004, product_key=SELARA_AI_PRODUCT_KEY, status="revoked",
                    valid_from=_NOW - timedelta(days=5), valid_until=_NOW + timedelta(days=25),
                ),
            ]
        )
        intent_id = str(uuid4())
        session.add(
            SelaraAiPurchaseIntentModel(
                id=intent_id, buyer_user_id=7, source_chat_id=-2000, chat_id=-2001, chat_title="Old title",
                product_key=SELARA_AI_PRODUCT_KEY, amount_stars=100, currency="XTR", duration_seconds=2_592_000,
                invoice_payload=f"selara_ai:v1:{intent_id}", status="consumed",
                expires_at=_NOW + timedelta(days=1),
            )
        )
        await session.flush()

        def payment(index: int, **kwargs):
            values = dict(
                telegram_payment_charge_id=f"charge-{index}", invoice_payload="p", buyer_user_id=7,
                source_chat_id=-1001, target_chat_id=-1001, product_key=SELARA_AI_PRODUCT_KEY,
                amount_stars=100, currency="XTR", payment_at=_NOW - timedelta(hours=index),
                processing_state="applied",
            )
            values.update(kwargs)
            return SelaraAiPaymentModel(**values)

        rows = [
            payment(1),
            payment(2, amount_stars=250, target_chat_id=-1002, source_chat_id=-1002),
            payment(3, processing_state="rejected", processing_reason="chat_mismatch", amount_stars=40, buyer_user_id=8),
            payment(4, processing_state="rejected", processing_reason="no_intent", amount_stars=60, buyer_user_id=8),
            payment(5, processing_state="rejected", processing_reason="no_intent", amount_stars=70, buyer_user_id=9),
            payment(6, processing_state="rejected", processing_reason="no_intent", amount_stars=80, buyer_user_id=9),
            # Paid under the pre-migration group id; the intent now points at the supergroup.
            payment(7, target_chat_id=-2000, source_chat_id=-2000, purchase_intent_id=intent_id, amount_stars=300),
            # Old payment outside the 7 day window and one for a chat that no longer exists.
            payment(8 * 24, amount_stars=500),
            payment(9, target_chat_id=-9999, source_chat_id=-9999, amount_stars=11),
        ]
        session.add_all(rows)
        await session.flush()
        session.add_all(
            [
                SelaraAiPaymentRefundModel(payment_id=rows[2].id, requested_by_user_id=1, status="pending"),
                SelaraAiPaymentRefundModel(
                    payment_id=rows[3].id, requested_by_user_id=1, status="refunded", completed_at=_NOW,
                ),
                SelaraAiPaymentRefundModel(
                    payment_id=rows[4].id, requested_by_user_id=1, status="failed", completed_at=_NOW,
                    result_code="telegram_error",
                ),
            ]
        )
        await session.commit()
        return [row.id for row in rows]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_stars_analytics_count_only_applied_revenue_and_active_entitlements():
    engine, factory = await _database()
    try:
        await _seed_billing(factory)
        async with factory() as session:
            repository = AdminAiAnalyticsRepository(session)
            summary = await repository.payment_summary(window_from=_FROM, window_to=_NOW + timedelta(seconds=1))
            counts = await repository.entitlement_counts(now=_NOW)
            entitlements = await repository.active_entitlements(now=_NOW)
            stars = await repository.daily_stars_series(
                window_from=_FROM, window_to=_NOW + timedelta(seconds=1), timezone_name="UTC"
            )

        # Applied in window: 100 + 250 + 300 + 11; rejected payments (even refunded) are not revenue.
        assert summary["successful_payments"] == 4
        assert summary["stars_revenue"] == 661
        assert summary["rejected_payments"] == 4
        assert summary["refunds"] == {"pending": 1, "refunded": 1, "failed": 1}
        assert summary["all_time"] == {"successful_payments": 5, "stars_revenue": 1161}
        assert sum(row["stars"] for row in stars) == 661

        # Expired (a millisecond ago) and revoked entitlements are not active paid chats.
        assert counts == {"active_paid_chats": 2, "expiring_within_7_days": 1}
        assert [row["chat_id"] for row in entitlements] == [-1002, -1001]
        assert entitlements[0]["expiring_soon"] is True and entitlements[0]["days_left"] == 3
        assert entitlements[1]["expiring_soon"] is False
        assert entitlements[0]["chat_title"] == "Soon chat"
        assert entitlements[0]["last_purchase_at"] == _NOW - timedelta(hours=2)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_payment_history_pagination_filters_and_canonical_chat():
    engine, factory = await _database()
    try:
        ids = await _seed_billing(factory)
        async with factory() as session:
            repository = AdminAiAnalyticsRepository(session)
            seen: list[int] = []
            cursor = None
            pages = 0
            while True:
                items, cursor = await repository.list_payments(filters=PaymentFilters(), cursor=cursor, limit=4)
                seen.extend(item["id"] for item in items)
                pages += 1
                if cursor is None:
                    break
            # Newest first, deterministic, no duplicates or gaps across pages.
            assert pages == 3
            assert seen == [ids[0], ids[1], ids[2], ids[3], ids[4], ids[5], ids[6], ids[8], ids[7]]

            rejected, _ = await repository.list_payments(
                filters=PaymentFilters(state="rejected"), cursor=None, limit=50
            )
            assert [item["id"] for item in rejected] == ids[2:6]
            pending, _ = await repository.list_payments(
                filters=PaymentFilters(refund="pending"), cursor=None, limit=50
            )
            assert [item["id"] for item in pending] == [ids[2]]
            assert pending[0]["refund"]["status"] == "pending"
            unrefunded, _ = await repository.list_payments(
                filters=PaymentFilters(state="rejected", refund="none"), cursor=None, limit=50
            )
            assert [item["id"] for item in unrefunded] == [ids[5]]
            buyer, _ = await repository.list_payments(
                filters=PaymentFilters(buyer_user_id=9), cursor=None, limit=50
            )
            assert [item["id"] for item in buyer] == [ids[4], ids[5]]
            recent, _ = await repository.list_payments(
                filters=PaymentFilters(since=_FROM), cursor=None, limit=50
            )
            assert ids[7] not in [item["id"] for item in recent]

            # Chat filter matches both the old audit id and the migrated canonical chat.
            for chat_filter in (-2000, -2001):
                migrated, _ = await repository.list_payments(
                    filters=PaymentFilters(chat_id=chat_filter), cursor=None, limit=50
                )
                assert [item["id"] for item in migrated] == [ids[6]]
            assert migrated[0]["chat_id"] == -2001
            assert migrated[0]["target_chat_id"] == -2000
            assert migrated[0]["chat_title"] == "Migrated supergroup"

            # A payment for a chat that no longer exists stays visible without a title.
            gone, _ = await repository.list_payments(
                filters=PaymentFilters(chat_id=-9999), cursor=None, limit=50
            )
            assert [item["id"] for item in gone] == [ids[8]]
            assert gone[0]["chat_title"] is None

            # Telegram charge ids appear only in the detail view.
            assert "telegram_payment_charge_id" not in pending[0]
            detail = await repository.payment_detail(payment_id=ids[6], now=_NOW)
            assert detail is not None
            assert detail["telegram_payment_charge_id"] == "charge-7"
            assert detail["intent"]["chat_id"] == -2001
            assert detail["entitlement"] is None
            assert await repository.payment_detail(payment_id=10_000_000, now=_NOW) is None
    finally:
        await engine.dispose()
