from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.feature_access import AccessReason, AccessTier, FeatureAccessService
from selara.application.selara_ai_product import (
    SELARA_AI_PRODUCT_KEY,
    SELARA_PERSONAL_PRODUCT_KEY,
    get_selara_ai_product,
)
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.chat_migration import migrate_chat_id
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.models import (
    AiFeatureQuotaUsageModel,
    ChatEntitlementModel,
    ChatModel,
    SelaraAiPaymentModel,
    SelaraAiPurchaseIntentModel,
)
from selara.infrastructure.db.selara_ai_payment_refund import SelaraAiPaymentRefundModel
from selara.infrastructure.db.telegram_stars import (
    PreCheckoutResult,
    PurchaseIntentRateLimited,
    SqlAlchemyChatEntitlementResolver,
    SqlAlchemyTelegramStarsRepository,
)
from selara.infrastructure.llm.features import AiFeature

_NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
_BUYER = 155_500


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
        session.add_all(
            ChatModel(telegram_chat_id=chat_id, type="supergroup", title=f"Chat {chat_id}")
            for chat_id in chat_ids
        )
        await session.commit()


def _product(price_stars: int = 137):
    return get_selara_ai_product(product_key=SELARA_AI_PRODUCT_KEY, price_stars=price_stars)


async def _intent(factory, *, chat_id: int, buyer_user_id: int = _BUYER, now: datetime = _NOW):
    return await SqlAlchemyTelegramStarsRepository(factory).create_purchase_intent(
        buyer_user_id=buyer_user_id,
        source_chat_id=chat_id,
        chat_id=chat_id,
        chat_title=f"Chat {chat_id}",
        product=_product(),
        terms_version="v1",
        terms_accepted_at=now,
        now=now,
    )


async def _payment(
    repository,
    intent,
    *,
    charge_id: str,
    buyer_user_id: int = _BUYER,
    amount_stars: int = 137,
    currency: str = "XTR",
    payment_at: datetime = _NOW,
):
    return await repository.process_successful_payment(
        buyer_user_id=buyer_user_id,
        invoice_payload=intent.invoice_payload,
        telegram_payment_charge_id=charge_id,
        provider_payment_charge_id="",
        amount_stars=amount_stars,
        currency=currency,
        payment_at=payment_at,
    )


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_successful_payment_and_duplicate_update_apply_one_thirty_day_entitlement():
    engine, factory = await _database()
    try:
        chat_id = -100_751_001
        await _seed_chats(factory, chat_id)
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory, chat_id=chat_id)
        assert intent.terms_version == "v1"
        assert intent.terms_accepted_at == _NOW

        first = await _payment(repository, intent, charge_id="stars-charge-one")
        duplicate = await _payment(repository, intent, charge_id="stars-charge-one")

        assert first.state == "applied"
        assert first.valid_until == _NOW + timedelta(days=30)
        assert duplicate.state == "duplicate"
        assert duplicate.valid_until == first.valid_until
        async with factory() as session:
            payment_count = await session.scalar(select(func.count(SelaraAiPaymentModel.id)))
            entitlement = await session.scalar(
                select(ChatEntitlementModel).where(ChatEntitlementModel.chat_id == chat_id)
            )
            saved_intent = await session.get(SelaraAiPurchaseIntentModel, intent.id)
        assert payment_count == 1
        assert entitlement.valid_until == _NOW + timedelta(days=30)
        assert saved_intent.status == "consumed"
        assert saved_intent.consumed_at == _NOW
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_price_change_after_invoice_does_not_change_the_server_side_intent():
    engine, factory = await _database()
    try:
        chat_id = -100_751_014
        await _seed_chats(factory, chat_id)
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory, chat_id=chat_id)
        later_catalog_price = _product(price_stars=200)

        assert intent.amount_stars == 137
        assert later_catalog_price.price_stars == 200
        accepted = await repository.accept_pre_checkout(
            invoice_payload=intent.invoice_payload,
            buyer_user_id=_BUYER,
            amount_stars=intent.amount_stars,
            currency=intent.currency,
            query_id="checkout-original-price",
            checked_chat_id=chat_id,
            now=_NOW,
        )
        result = await _payment(repository, intent, charge_id="stars-original-price")

        assert accepted.accepted
        assert result.state == "applied"
        assert result.valid_until == _NOW + timedelta(days=30)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_purchase_intent_creation_is_rate_limited_per_buyer_and_chat():
    engine, factory = await _database()
    try:
        chat_id = -100_751_022
        await _seed_chats(factory, chat_id)
        await _intent(factory, chat_id=chat_id)

        with pytest.raises(PurchaseIntentRateLimited):
            await _intent(factory, chat_id=chat_id, now=_NOW + timedelta(seconds=30))

        allowed_after_cooldown = await _intent(
            factory,
            chat_id=chat_id,
            now=_NOW + timedelta(seconds=61),
        )
        assert allowed_after_cooldown.buyer_user_id == _BUYER
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_two_concurrent_updates_for_one_charge_have_one_economic_effect():
    engine, factory = await _database()
    try:
        chat_id = -100_751_002
        await _seed_chats(factory, chat_id)
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory, chat_id=chat_id)

        results = await asyncio.gather(
            _payment(repository, intent, charge_id="stars-charge-race"),
            _payment(repository, intent, charge_id="stars-charge-race"),
        )

        assert sorted(result.state for result in results) == ["applied", "duplicate"]
        assert all(result.valid_until == _NOW + timedelta(days=30) for result in results)
        async with factory() as session:
            payment_count = await session.scalar(select(func.count(SelaraAiPaymentModel.id)))
            entitlement_count = await session.scalar(select(func.count(ChatEntitlementModel.id)))
        assert payment_count == 1
        assert entitlement_count == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_active_entitlement_extends_and_expired_entitlement_restarts_at_payment_time():
    engine, factory = await _database()
    try:
        active_chat, expired_chat = -100_751_003, -100_751_004
        await _seed_chats(factory, active_chat, expired_chat)
        async with factory() as session:
            session.add_all(
                [
                    ChatEntitlementModel(
                        chat_id=active_chat,
                        product_key=SELARA_AI_PRODUCT_KEY,
                        status="active",
                        valid_from=_NOW - timedelta(days=10),
                        valid_until=_NOW + timedelta(days=10),
                    ),
                    ChatEntitlementModel(
                        chat_id=expired_chat,
                        product_key=SELARA_AI_PRODUCT_KEY,
                        status="active",
                        valid_from=_NOW - timedelta(days=40),
                        valid_until=_NOW - timedelta(days=1),
                    ),
                ]
            )
            await session.commit()
        repository = SqlAlchemyTelegramStarsRepository(factory)
        active_intent = await _intent(factory, chat_id=active_chat)
        expired_intent = await _intent(factory, chat_id=expired_chat)

        active = await _payment(repository, active_intent, charge_id="stars-active-extension")
        expired = await _payment(repository, expired_intent, charge_id="stars-after-expiry")

        assert active.valid_until == _NOW + timedelta(days=40)
        assert expired.valid_until == _NOW + timedelta(days=30)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_two_concurrent_real_payments_for_one_chat_add_sixty_days():
    engine, factory = await _database()
    try:
        chat_id = -100_751_005
        await _seed_chats(factory, chat_id)
        repository = SqlAlchemyTelegramStarsRepository(factory)
        first_intent = await _intent(factory, chat_id=chat_id)
        # A buyer is rate-limited to one new invoice per chat per minute. Create
        # the second legitimate invoice after that cooldown, then let both
        # successful payments race at the same time.
        second_intent = await _intent(
            factory,
            chat_id=chat_id,
            now=_NOW + timedelta(minutes=2),
        )
        payment_time = _NOW + timedelta(minutes=2)

        first, second = await asyncio.gather(
            _payment(
                repository,
                first_intent,
                charge_id="stars-distinct-one",
                payment_at=payment_time,
            ),
            _payment(
                repository,
                second_intent,
                charge_id="stars-distinct-two",
                payment_at=payment_time,
            ),
        )

        assert first.state == second.state == "applied"
        assert {
            first.valid_until,
            second.valid_until,
        } == {
            payment_time + timedelta(days=30),
            payment_time + timedelta(days=60),
        }
        async with factory() as session:
            final_entitlement = await session.scalar(
                select(ChatEntitlementModel).where(ChatEntitlementModel.chat_id == chat_id)
            )
            payment_count = await session.scalar(select(func.count(SelaraAiPaymentModel.id)))
        assert final_entitlement is not None
        assert final_entitlement.valid_until == payment_time + timedelta(days=60)
        assert payment_count == 2
        assert (await repository.payment_totals()).stars_revenue == 274
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("buyer_user_id", "amount_stars", "currency", "expected_reason"),
    [
        (_BUYER, 136, "XTR", "wrong_amount"),
        (_BUYER, 137, "USD", "wrong_currency"),
        (_BUYER + 1, 137, "XTR", "wrong_buyer"),
    ],
)
async def test_invalid_successful_payment_is_audited_without_entitlement(
    buyer_user_id,
    amount_stars,
    currency,
    expected_reason,
):
    engine, factory = await _database()
    try:
        chat_id = -100_751_006
        await _seed_chats(factory, chat_id)
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory, chat_id=chat_id)

        result = await _payment(
            repository,
            intent,
            charge_id=f"stars-invalid-{expected_reason}",
            buyer_user_id=buyer_user_id,
            amount_stars=amount_stars,
            currency=currency,
        )

        assert result.state == "rejected"
        assert result.reason == expected_reason
        async with factory() as session:
            payment = await session.scalar(select(SelaraAiPaymentModel))
            entitlement_count = await session.scalar(select(func.count(ChatEntitlementModel.id)))
        assert payment.processing_state == "rejected"
        assert payment.processing_reason == expected_reason
        assert entitlement_count == 0
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_unprocessable_payment_is_dead_lettered_once_and_stays_refundable():
    engine, factory = await _database()
    try:
        repository = SqlAlchemyTelegramStarsRepository(factory)
        charge_id = "stars-poison-dead-letter"

        first_id = await repository.record_unprocessable_payment(
            buyer_user_id=_BUYER,
            invoice_payload="selara_ai:v1:ffffffff-ffff-4fff-8fff-ffffffffffff",
            telegram_payment_charge_id=charge_id,
            provider_payment_charge_id="",
            amount_stars=137,
            currency="XTR",
            payment_at=_NOW,
        )
        redelivered_id = await repository.record_unprocessable_payment(
            buyer_user_id=_BUYER,
            invoice_payload="tampered-after-redelivery",
            telegram_payment_charge_id=charge_id,
            provider_payment_charge_id="",
            amount_stars=999,
            currency="XTR",
            payment_at=_NOW,
        )

        assert first_id is not None
        assert redelivered_id == first_id
        claim = await repository.claim_rejected_payment_refund(
            payment_id=first_id,
            requested_by_user_id=_BUYER + 10,
        )
        assert claim.state == "claimed"
        assert claim.telegram_payment_charge_id == charge_id
        assert claim.amount_stars == 137
        async with factory() as session:
            payment = await session.get(SelaraAiPaymentModel, first_id)
        assert payment.processing_state == "rejected"
        assert payment.processing_reason == "processing_failed"
        assert payment.amount_stars == 137
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_dead_letter_preserves_intent_scope_target_and_product():
    engine, factory = await _database()
    try:
        chat_id = -100_751_030
        await _seed_chats(factory, chat_id)
        repository = SqlAlchemyTelegramStarsRepository(factory)
        chat_intent = await _intent(factory, chat_id=chat_id)
        personal_product = get_selara_ai_product(
            product_key=SELARA_PERSONAL_PRODUCT_KEY,
            price_stars=99,
            duration=timedelta(days=30),
            paid_daily_limit=40,
        )
        personal_intent = await repository.create_personal_purchase_intent(
            buyer_user_id=_BUYER,
            product=personal_product,
            terms_version="v1",
            terms_accepted_at=_NOW,
            now=_NOW,
        )

        chat_row_id = await repository.record_unprocessable_payment(
            buyer_user_id=_BUYER,
            invoice_payload=chat_intent.invoice_payload,
            telegram_payment_charge_id="stars-dead-letter-chat",
            provider_payment_charge_id="",
            amount_stars=137,
            currency="XTR",
            payment_at=_NOW,
        )
        personal_row_id = await repository.record_unprocessable_payment(
            buyer_user_id=_BUYER,
            invoice_payload=personal_intent.invoice_payload,
            telegram_payment_charge_id="stars-dead-letter-personal",
            provider_payment_charge_id="",
            amount_stars=99,
            currency="XTR",
            payment_at=_NOW,
        )

        assert chat_row_id is not None
        assert personal_row_id is not None
        async with factory() as session:
            chat_row = await session.get(SelaraAiPaymentModel, chat_row_id)
            personal_row = await session.get(SelaraAiPaymentModel, personal_row_id)
        assert chat_row.target_scope == "chat"
        assert chat_row.target_chat_id == chat_id
        assert chat_row.target_user_id is None
        assert chat_row.product_key == SELARA_AI_PRODUCT_KEY
        assert chat_row.purchase_intent_id == chat_intent.id
        assert personal_row.target_scope == "user"
        assert personal_row.target_user_id == _BUYER
        assert personal_row.target_chat_id is None
        assert personal_row.product_key == SELARA_PERSONAL_PRODUCT_KEY
        assert personal_row.purchase_intent_id == personal_intent.id
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_rejected_payment_refund_claim_is_single_use_and_audited():
    engine, factory = await _database()
    try:
        chat_id = -100_751_020
        await _seed_chats(factory, chat_id)
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory, chat_id=chat_id)
        rejected = await _payment(
            repository,
            intent,
            charge_id="stars-refund-claim",
            amount_stars=136,
        )
        assert rejected.state == "rejected"
        assert rejected.payment_id is not None

        claims = await asyncio.gather(
            repository.claim_rejected_payment_refund(
                payment_id=rejected.payment_id,
                requested_by_user_id=_BUYER + 10,
            ),
            repository.claim_rejected_payment_refund(
                payment_id=rejected.payment_id,
                requested_by_user_id=_BUYER + 11,
            ),
        )
        assert sorted(claim.state for claim in claims) == ["claimed", "pending"]
        claimed = next(claim for claim in claims if claim.state == "claimed")
        assert claimed.buyer_user_id == _BUYER
        assert claimed.telegram_payment_charge_id == "stars-refund-claim"
        assert claimed.amount_stars == 136

        assert await repository.finish_rejected_payment_refund(
            payment_id=rejected.payment_id,
            succeeded=True,
            result_code="refunded",
        )
        already_refunded = await repository.claim_rejected_payment_refund(
            payment_id=rejected.payment_id,
            requested_by_user_id=_BUYER + 12,
        )
        assert already_refunded.state == "refunded"

        async with factory() as session:
            audit = await session.get(SelaraAiPaymentRefundModel, rejected.payment_id)
        assert audit is not None
        assert audit.status == "refunded"
        assert audit.completed_at is not None
        assert audit.result_code == "refunded"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_applied_payment_cannot_be_refunded_by_rejected_payment_command():
    engine, factory = await _database()
    try:
        chat_id = -100_751_021
        await _seed_chats(factory, chat_id)
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory, chat_id=chat_id)
        applied = await _payment(repository, intent, charge_id="stars-refund-applied")

        result = await repository.claim_rejected_payment_refund(
            payment_id=applied.payment_id,
            requested_by_user_id=_BUYER,
        )

        assert result.state == "not_rejected"
        async with factory() as session:
            refund_count = await session.scalar(select(func.count(SelaraAiPaymentRefundModel.payment_id)))
        assert refund_count == 0
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_pre_checkout_checks_buyer_amount_currency_expiry_and_consumption():
    engine, factory = await _database()
    try:
        chat_id = -100_751_007
        await _seed_chats(factory, chat_id)
        repository = SqlAlchemyTelegramStarsRepository(factory)
        valid_intent = await _intent(factory, chat_id=chat_id)
        valid = await repository.accept_pre_checkout(
            invoice_payload=valid_intent.invoice_payload,
            buyer_user_id=_BUYER,
            amount_stars=137,
            currency="XTR",
            query_id="checkout-valid",
            checked_chat_id=chat_id,
            now=_NOW,
        )
        assert valid == PreCheckoutResult(True, chat_id=chat_id)

        wrong_buyer = await repository.accept_pre_checkout(
            invoice_payload=valid_intent.invoice_payload,
            buyer_user_id=_BUYER + 1,
            amount_stars=137,
            currency="XTR",
            query_id="checkout-wrong-buyer",
            checked_chat_id=chat_id,
            now=_NOW,
        )
        assert not wrong_buyer.accepted and wrong_buyer.reason == "wrong_buyer"

        wrong_amount_buyer = _BUYER + 2
        wrong_amount_intent = await _intent(
            factory,
            chat_id=chat_id,
            buyer_user_id=wrong_amount_buyer,
        )
        wrong_amount = await repository.accept_pre_checkout(
            invoice_payload=wrong_amount_intent.invoice_payload,
            buyer_user_id=wrong_amount_buyer,
            amount_stars=136,
            currency="XTR",
            query_id="checkout-wrong-amount",
            checked_chat_id=chat_id,
            now=_NOW,
        )
        assert not wrong_amount.accepted and wrong_amount.reason == "wrong_amount"

        wrong_currency_buyer = _BUYER + 3
        wrong_currency_intent = await _intent(
            factory,
            chat_id=chat_id,
            buyer_user_id=wrong_currency_buyer,
        )
        wrong_currency = await repository.accept_pre_checkout(
            invoice_payload=wrong_currency_intent.invoice_payload,
            buyer_user_id=wrong_currency_buyer,
            amount_stars=137,
            currency="USD",
            query_id="checkout-wrong-currency",
            checked_chat_id=chat_id,
            now=_NOW,
        )
        assert not wrong_currency.accepted and wrong_currency.reason == "wrong_currency"

        expired_buyer = _BUYER + 4
        expired_intent = await _intent(
            factory,
            chat_id=chat_id,
            buyer_user_id=expired_buyer,
            now=_NOW - timedelta(hours=1),
        )
        expired = await repository.accept_pre_checkout(
            invoice_payload=expired_intent.invoice_payload,
            buyer_user_id=expired_buyer,
            amount_stars=137,
            currency="XTR",
            query_id="checkout-expired",
            checked_chat_id=chat_id,
            now=_NOW,
        )
        assert not expired.accepted and expired.reason == "expired_intent"

        retried = await repository.accept_pre_checkout(
            invoice_payload=valid_intent.invoice_payload,
            buyer_user_id=_BUYER,
            amount_stars=137,
            currency="XTR",
            query_id="checkout-second-query",
            checked_chat_id=chat_id,
            now=_NOW,
        )
        assert retried == PreCheckoutResult(True, chat_id=chat_id)

        await _payment(repository, valid_intent, charge_id="checkout-consumed-charge")
        consumed_retry = await repository.accept_pre_checkout(
            invoice_payload=valid_intent.invoice_payload,
            buyer_user_id=_BUYER,
            amount_stars=137,
            currency="XTR",
            query_id="checkout-after-payment",
            checked_chat_id=chat_id,
            now=_NOW,
        )
        assert not consumed_retry.accepted and consumed_retry.reason == "intent_already_used"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_paid_entitlement_resolver_integrates_with_manual_free_quota_and_scheduled_gate():
    engine, factory = await _database()
    try:
        chat_id = -100_751_008
        await _seed_chats(factory, chat_id)
        async with factory() as session:
            session.add(
                ChatEntitlementModel(
                    chat_id=chat_id,
                    product_key=SELARA_AI_PRODUCT_KEY,
                    status="active",
                    valid_from=_NOW,
                    valid_until=_NOW + timedelta(days=30),
                )
            )
            await session.commit()
        service = FeatureAccessService(
            SqlAlchemyFeatureQuotaRepository(factory),
            entitlement_resolver=SqlAlchemyChatEntitlementResolver(factory),
        )

        scheduled = await service.resolve_feature_access(
            feature=AiFeature.DAILY_SUMMARY,
            chat_id=chat_id,
            trigger="scheduled",
            now=_NOW,
        )
        manual = await service.reserve_feature_usage(
            feature=AiFeature.DAILY_SUMMARY,
            chat_id=chat_id,
            actor_user_id=None,
            trigger="manual",
            timezone_name="UTC",
            idempotency_key="daily_summary:run:stars-paid-one",
            chat_type="supergroup",
            now=_NOW,
        )
        manual_retry = await service.reserve_feature_usage(
            feature=AiFeature.DAILY_SUMMARY,
            chat_id=chat_id,
            actor_user_id=None,
            trigger="manual",
            timezone_name="UTC",
            idempotency_key="daily_summary:run:stars-paid-one",
            chat_type="supergroup",
            now=_NOW,
        )
        expired = await service.resolve_feature_access(
            feature=AiFeature.DAILY_SUMMARY,
            chat_id=chat_id,
            trigger="scheduled",
            now=_NOW + timedelta(days=31),
        )
        owner = await service.resolve_feature_access(
            feature=AiFeature.DAILY_SUMMARY,
            chat_id=chat_id,
            trigger="scheduled",
            owner_exempt=True,
            now=_NOW + timedelta(days=31),
        )
        free_chat = await service.resolve_feature_access(
            feature=AiFeature.DAILY_SUMMARY,
            chat_id=chat_id - 1,
            trigger="scheduled",
            now=_NOW,
        )

        assert scheduled.allowed and scheduled.access_tier == AccessTier.PAID
        assert scheduled.entitlement_source == "telegram_stars"
        assert scheduled.entitlement_valid_until == _NOW + timedelta(days=30)
        assert scheduled.entitlement_product == SELARA_AI_PRODUCT_KEY
        assert manual.allowed and manual.access_tier == AccessTier.PAID
        assert manual.quota_limit == 10
        assert manual_retry.reused
        assert expired.reason == AccessReason.ACCESS_REQUIRED
        assert not expired.allowed
        assert owner.allowed and owner.access_tier == AccessTier.OWNER_INTERNAL
        assert not free_chat.allowed and free_chat.reason == AccessReason.ACCESS_REQUIRED
        async with factory() as session:
            manual_usage_count = await session.scalar(
                select(func.count(AiFeatureQuotaUsageModel.id)).where(
                    AiFeatureQuotaUsageModel.chat_id == chat_id,
                    AiFeatureQuotaUsageModel.feature == AiFeature.DAILY_SUMMARY.value,
                )
            )
        assert manual_usage_count == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_migrated_purchase_intent_grants_entitlement_to_canonical_supergroup():
    engine, factory = await _database()
    try:
        old_chat_id, new_chat_id = -700_751_009, -100_751_009
        await _seed_chats(factory, old_chat_id)
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory, chat_id=old_chat_id)
        async with factory() as session:
            await migrate_chat_id(
                session,
                old_chat_id=old_chat_id,
                new_chat_id=new_chat_id,
                new_chat_type="supergroup",
                new_chat_title="Migrated chat",
            )
            await session.commit()

        result = await _payment(repository, intent, charge_id="stars-after-migration")

        assert result.state == "applied"
        assert result.chat_id == new_chat_id
        assert result.valid_until == _NOW + timedelta(days=30)
        async with factory() as session:
            saved_intent = await session.get(SelaraAiPurchaseIntentModel, intent.id)
            payment = await session.scalar(select(SelaraAiPaymentModel))
            old_entitlement = await session.scalar(
                select(ChatEntitlementModel).where(ChatEntitlementModel.chat_id == old_chat_id)
            )
            new_entitlement = await session.scalar(
                select(ChatEntitlementModel).where(ChatEntitlementModel.chat_id == new_chat_id)
            )
        assert saved_intent.chat_id == new_chat_id
        assert payment.source_chat_id == old_chat_id
        assert payment.target_chat_id == new_chat_id
        assert old_entitlement is None
        assert new_entitlement is not None
    finally:
        await engine.dispose()



@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_payment_history_for_canonical_chat_includes_pre_migration_payment():
    engine, factory = await _database()
    try:
        old_chat_id, new_chat_id = -700_751_014, -100_751_014
        await _seed_chats(factory, old_chat_id)
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory, chat_id=old_chat_id)
        payment_result = await _payment(repository, intent, charge_id="stars-before-migration")
        assert payment_result.state == "applied"

        async with factory() as session:
            await migrate_chat_id(
                session,
                old_chat_id=old_chat_id,
                new_chat_id=new_chat_id,
                new_chat_type="supergroup",
                new_chat_title="Migrated chat",
            )
            await session.commit()

        history = await repository.list_payment_history(chat_id=new_chat_id)

        assert len(history) == 1
        assert history[0].telegram_payment_charge_id == "stars-before-migration"
        assert history[0].source_chat_id == old_chat_id
        assert history[0].target_chat_id == old_chat_id
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_migration_collision_sums_remaining_paid_time_without_duplicate_entitlement():
    engine, factory = await _database()
    try:
        old_chat_id, new_chat_id = -700_751_010, -100_751_010
        await _seed_chats(factory, old_chat_id, new_chat_id)
        async with factory() as session:
            session.add_all(
                [
                    ChatEntitlementModel(
                        chat_id=old_chat_id,
                        product_key=SELARA_AI_PRODUCT_KEY,
                        status="active",
                        valid_from=_NOW - timedelta(days=4),
                        valid_until=_NOW + timedelta(days=10),
                    ),
                    ChatEntitlementModel(
                        chat_id=new_chat_id,
                        product_key=SELARA_AI_PRODUCT_KEY,
                        status="active",
                        valid_from=_NOW - timedelta(days=2),
                        valid_until=_NOW + timedelta(days=20),
                    ),
                ]
            )
            await session.commit()
        before = datetime.now(timezone.utc)
        expected_remaining_seconds = sum(
            max(0.0, (valid_until - before).total_seconds())
            for valid_until in (_NOW + timedelta(days=10), _NOW + timedelta(days=20))
        )
        expected_valid_until = before + timedelta(seconds=expected_remaining_seconds)
        async with factory() as session:
            await migrate_chat_id(
                session,
                old_chat_id=old_chat_id,
                new_chat_id=new_chat_id,
                new_chat_type="supergroup",
                new_chat_title="Migrated chat",
            )
            await session.commit()

        async with factory() as session:
            rows = list(await session.scalars(select(ChatEntitlementModel)))
        assert len(rows) == 1
        assert rows[0].chat_id == new_chat_id
        assert expected_valid_until - timedelta(seconds=30) <= rows[0].valid_until
        assert rows[0].valid_until <= expected_valid_until + timedelta(seconds=30)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_migration_collision_never_reactivates_a_revoked_period():
    engine, factory = await _database()
    try:
        old_chat_id, new_chat_id = -700_751_013, -100_751_013
        await _seed_chats(factory, old_chat_id, new_chat_id)
        before = datetime.now(timezone.utc)
        async with factory() as session:
            session.add_all(
                [
                    ChatEntitlementModel(
                        chat_id=old_chat_id,
                        product_key=SELARA_AI_PRODUCT_KEY,
                        status="revoked",
                        valid_from=before - timedelta(days=10),
                        valid_until=before + timedelta(days=20),
                    ),
                    ChatEntitlementModel(
                        chat_id=new_chat_id,
                        product_key=SELARA_AI_PRODUCT_KEY,
                        status="active",
                        valid_from=before - timedelta(days=31),
                        valid_until=before - timedelta(days=1),
                    ),
                ]
            )
            await session.commit()

        async with factory() as session:
            await migrate_chat_id(
                session,
                old_chat_id=old_chat_id,
                new_chat_id=new_chat_id,
                new_chat_type="supergroup",
            )
            await session.commit()
        async with factory() as session:
            merged = await session.scalar(
                select(ChatEntitlementModel).where(ChatEntitlementModel.chat_id == new_chat_id)
            )

        assert merged.status == "active"
        assert merged.valid_until <= datetime.now(timezone.utc)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_payment_insert_rolls_back_with_entitlement_failure_and_retry_applies_once(monkeypatch):
    engine, factory = await _database()
    try:
        chat_id = -100_751_011
        await _seed_chats(factory, chat_id)
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory, chat_id=chat_id)
        import selara.infrastructure.db.telegram_stars as stars_module

        original_lock = stars_module._advisory_xact_lock
        lock_calls = 0

        async def fail_after_payment_insert(session, lock_key):
            nonlocal lock_calls
            lock_calls += 1
            if lock_calls == 2:
                raise RuntimeError("simulated crash before entitlement write")
            await original_lock(session, lock_key)

        monkeypatch.setattr(stars_module, "_advisory_xact_lock", fail_after_payment_insert)
        with pytest.raises(RuntimeError, match="simulated crash"):
            await _payment(repository, intent, charge_id="stars-retry-after-rollback")
        async with factory() as session:
            payment_count = await session.scalar(select(func.count(SelaraAiPaymentModel.id)))
            entitlement_count = await session.scalar(select(func.count(ChatEntitlementModel.id)))
        assert payment_count == entitlement_count == 0

        monkeypatch.setattr(stars_module, "_advisory_xact_lock", original_lock)
        result = await _payment(repository, intent, charge_id="stars-retry-after-rollback")
        assert result.state == "applied"
        assert result.valid_until == _NOW + timedelta(days=30)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_expired_intent_with_previously_accepted_checkout_still_honors_confirmed_payment():
    engine, factory = await _database()
    try:
        chat_id = -100_751_012
        await _seed_chats(factory, chat_id)
        repository = SqlAlchemyTelegramStarsRepository(factory)
        created_at = _NOW - timedelta(minutes=14)
        intent = await _intent(factory, chat_id=chat_id, now=created_at)
        accepted = await repository.accept_pre_checkout(
            invoice_payload=intent.invoice_payload,
            buyer_user_id=_BUYER,
            amount_stars=137,
            currency="XTR",
            query_id="accepted-before-expiry",
            checked_chat_id=chat_id,
            now=_NOW,
        )
        # Pay after intent expiry. A confirmed payment is still an economic fact.
        payment_time = intent.expires_at + timedelta(minutes=1)
        result = await _payment(
            repository,
            intent,
            charge_id="stars-payment-after-intent-expiry",
            payment_at=payment_time,
        )

        assert accepted.accepted
        assert result.state == "applied"
        assert result.valid_until == payment_time + timedelta(days=30)
    finally:
        await engine.dispose()
