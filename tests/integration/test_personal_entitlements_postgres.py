from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.feature_access import AccessTier, PersonalQuotaLimits
from selara.application.personal_config import PersonalConfig, StaticPersonalConfigProvider
from selara.application.selara_ai_product import (
    SELARA_AI_PRODUCT_KEY,
    SELARA_PERSONAL_PRODUCT_KEY,
    SELARA_PERSONAL_TERMS_VERSION,
    get_selara_ai_product,
)
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import (
    ChatEntitlementModel,
    ChatModel,
    SelaraAiPaymentModel,
    SelaraAiPurchaseIntentModel,
    UserEntitlementModel,
)
from selara.infrastructure.db.telegram_stars import (
    PurchaseIntentRateLimited,
    SqlAlchemyTelegramStarsRepository,
    SqlAlchemyUserEntitlementResolver,
)
from selara.infrastructure.llm.features import AiFeature

_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
_BUYER = 266_600


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _personal(price_stars: int = 69):
    return get_selara_ai_product(
        product_key=SELARA_PERSONAL_PRODUCT_KEY, price_stars=price_stars, duration=timedelta(days=30)
    )


async def _intent(factory, *, buyer_user_id: int = _BUYER, now: datetime = _NOW):
    return await SqlAlchemyTelegramStarsRepository(factory).create_personal_purchase_intent(
        buyer_user_id=buyer_user_id,
        product=_personal(),
        terms_version=SELARA_PERSONAL_TERMS_VERSION,
        terms_accepted_at=now,
        now=now,
    )


async def _payment(
    repository,
    intent,
    *,
    charge_id: str,
    buyer_user_id: int = _BUYER,
    amount_stars: int = 69,
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
async def test_personal_intent_targets_the_buyer_and_has_no_chat():
    engine, factory = await _database()
    try:
        intent = await _intent(factory)

        assert intent.target_scope == "user"
        assert intent.target_user_id == _BUYER == intent.buyer_user_id
        assert intent.chat_id is None and intent.source_chat_id is None
        assert intent.product_key == SELARA_PERSONAL_PRODUCT_KEY
        assert intent.amount_stars == 69
        assert intent.duration_seconds == 30 * 86_400
        assert intent.terms_version == SELARA_PERSONAL_TERMS_VERSION
        assert intent.status == "open"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_personal_intent_creation_is_rate_limited_per_buyer_and_rejects_chat_products():
    engine, factory = await _database()
    try:
        repository = SqlAlchemyTelegramStarsRepository(factory)
        await _intent(factory)
        with pytest.raises(PurchaseIntentRateLimited):
            await _intent(factory, now=_NOW + timedelta(seconds=30))
        # another buyer and a later attempt are fine
        await _intent(factory, buyer_user_id=_BUYER + 1, now=_NOW + timedelta(seconds=30))
        await _intent(factory, now=_NOW + timedelta(minutes=2))
        with pytest.raises(ValueError):
            await repository.create_personal_purchase_intent(
                buyer_user_id=_BUYER,
                product=get_selara_ai_product(product_key=SELARA_AI_PRODUCT_KEY, price_stars=100),
                terms_version="v2",
                terms_accepted_at=_NOW,
                now=_NOW + timedelta(minutes=10),
            )
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_successful_personal_payment_grants_user_entitlement_once_and_never_a_chat_one():
    engine, factory = await _database()
    try:
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory)

        first = await _payment(repository, intent, charge_id="personal-charge-one")
        duplicate = await _payment(repository, intent, charge_id="personal-charge-one")

        assert first.state == "applied"
        assert first.target_scope == "user" and first.user_id == _BUYER
        assert first.chat_id is None
        assert first.entitlement_action == "created"
        assert first.valid_until == _NOW + timedelta(days=30)
        assert duplicate.state == "duplicate"
        assert duplicate.user_id == _BUYER and duplicate.valid_until == first.valid_until
        async with factory() as session:
            payments = (await session.scalars(select(SelaraAiPaymentModel))).all()
            entitlement = await session.scalar(
                select(UserEntitlementModel).where(UserEntitlementModel.user_id == _BUYER)
            )
            chat_entitlements = await session.scalar(select(func.count(ChatEntitlementModel.id)))
            saved_intent = await session.get(SelaraAiPurchaseIntentModel, intent.id)
        assert len(payments) == 1
        assert payments[0].target_scope == "user" and payments[0].target_user_id == _BUYER
        assert payments[0].target_chat_id is None
        assert payments[0].product_key == SELARA_PERSONAL_PRODUCT_KEY
        assert entitlement.product_key == SELARA_PERSONAL_PRODUCT_KEY
        assert entitlement.status == "active"
        assert entitlement.valid_until == _NOW + timedelta(days=30)
        assert chat_entitlements == 0
        assert saved_intent.status == "consumed" and saved_intent.consumed_at == _NOW
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_active_personal_entitlement_extends_and_expired_one_restarts_at_payment_time():
    engine, factory = await _database()
    try:
        repository = SqlAlchemyTelegramStarsRepository(factory)
        first_intent = await _intent(factory)
        await _payment(repository, first_intent, charge_id="personal-extend-1")

        second_at = _NOW + timedelta(days=10)
        second_intent = await _intent(factory, now=second_at)
        extended = await _payment(
            repository, second_intent, charge_id="personal-extend-2", payment_at=second_at
        )
        assert extended.entitlement_action == "extended"
        assert extended.valid_until == _NOW + timedelta(days=60)

        late_at = _NOW + timedelta(days=100)
        late_intent = await _intent(factory, now=late_at)
        restarted = await _payment(
            repository, late_intent, charge_id="personal-extend-3", payment_at=late_at
        )
        assert restarted.valid_until == late_at + timedelta(days=30)
        async with factory() as session:
            rows = (await session.scalars(select(UserEntitlementModel))).all()
        assert len(rows) == 1 and rows[0].valid_from == late_at
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_updates_for_one_personal_charge_have_one_economic_effect():
    engine, factory = await _database()
    try:
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory)

        results = await asyncio.gather(
            *(_payment(repository, intent, charge_id="personal-race-same") for _ in range(4))
        )

        assert sorted(result.state for result in results) == ["applied", "duplicate", "duplicate", "duplicate"]
        async with factory() as session:
            payment_count = await session.scalar(select(func.count(SelaraAiPaymentModel.id)))
            entitlement = await session.scalar(select(UserEntitlementModel))
        assert payment_count == 1
        assert entitlement.valid_until == _NOW + timedelta(days=30)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_two_concurrent_real_personal_payments_add_sixty_days():
    engine, factory = await _database()
    try:
        repository = SqlAlchemyTelegramStarsRepository(factory)
        first = await _intent(factory)
        second = await _intent(factory, now=_NOW + timedelta(minutes=2))

        results = await asyncio.gather(
            _payment(repository, first, charge_id="personal-race-a"),
            _payment(repository, second, charge_id="personal-race-b"),
        )

        assert all(result.state == "applied" for result in results)
        async with factory() as session:
            entitlement = await session.scalar(select(UserEntitlementModel))
            payment_count = await session.scalar(select(func.count(SelaraAiPaymentModel.id)))
        assert payment_count == 2
        assert entitlement.valid_until == _NOW + timedelta(days=60)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"amount_stars": 70}, "wrong_amount"),
        ({"currency": "USD"}, "wrong_currency"),
        ({"buyer_user_id": _BUYER + 5}, "wrong_buyer"),
    ],
)
async def test_invalid_personal_payment_is_audited_without_entitlement_and_is_refundable(overrides, reason):
    engine, factory = await _database()
    try:
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory)

        result = await _payment(repository, intent, charge_id=f"personal-bad-{reason}", **overrides)

        assert result.state == "rejected" and result.reason == reason
        assert result.payment_id is not None
        async with factory() as session:
            payment = await session.get(SelaraAiPaymentModel, result.payment_id)
            entitlements = await session.scalar(select(func.count(UserEntitlementModel.id)))
            saved_intent = await session.get(SelaraAiPurchaseIntentModel, intent.id)
        assert payment.processing_state == "rejected"
        assert payment.target_scope == "user"
        assert entitlements == 0
        assert saved_intent.status != "consumed"

        claim = await repository.claim_rejected_payment_refund(
            payment_id=result.payment_id, requested_by_user_id=1
        )
        assert claim.state == "claimed"
        assert claim.buyer_user_id == overrides.get("buyer_user_id", _BUYER)
        again = await repository.claim_rejected_payment_refund(
            payment_id=result.payment_id, requested_by_user_id=1
        )
        assert again.state == "pending"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_applied_personal_payment_cannot_be_refunded_by_the_rejected_payment_command():
    engine, factory = await _database()
    try:
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory)
        result = await _payment(repository, intent, charge_id="personal-applied-refund")

        claim = await repository.claim_rejected_payment_refund(
            payment_id=result.payment_id, requested_by_user_id=1
        )

        assert claim.state == "not_rejected"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_personal_payment_insert_rolls_back_with_entitlement_failure_and_retry_applies_once(monkeypatch):
    engine, factory = await _database()
    try:
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory)
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
            await _payment(repository, intent, charge_id="personal-retry-after-rollback")
        async with factory() as session:
            payment_count = await session.scalar(select(func.count(SelaraAiPaymentModel.id)))
            entitlement_count = await session.scalar(select(func.count(UserEntitlementModel.id)))
            saved_intent = await session.get(SelaraAiPurchaseIntentModel, intent.id)
        assert payment_count == entitlement_count == 0
        assert saved_intent.status != "consumed"

        monkeypatch.setattr(stars_module, "_advisory_xact_lock", original_lock)
        result = await _payment(repository, intent, charge_id="personal-retry-after-rollback")
        assert result.state == "applied"
        assert result.valid_until == _NOW + timedelta(days=30)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_personal_pre_checkout_needs_no_chat_and_checks_buyer_amount_currency_and_expiry():
    engine, factory = await _database()
    try:
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory)

        async def check(**overrides):
            arguments = {
                "invoice_payload": intent.invoice_payload,
                "buyer_user_id": _BUYER,
                "amount_stars": 69,
                "currency": "XTR",
                "query_id": "query-personal",
                "checked_chat_id": None,
                "now": _NOW + timedelta(minutes=1),
            }
            arguments.update(overrides)
            return await repository.accept_pre_checkout(**arguments)

        assert (await check(buyer_user_id=_BUYER + 1)).reason == "wrong_buyer"
        assert (await check(amount_stars=1)).reason == "wrong_amount"
        assert (await check(currency="USD")).reason == "wrong_currency"
        assert (await check(now=_NOW + timedelta(hours=1))).reason == "expired_intent"
        accepted = await check()
        assert accepted.accepted
        async with factory() as session:
            saved = await session.get(SelaraAiPurchaseIntentModel, intent.id)
        assert saved.status == "checkout_accepted"
        assert saved.pre_checkout_query_id == "query-personal"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_personal_entitlement_resolver_returns_paid_one_fifty_policy_only_for_personal_chat():
    engine, factory = await _database()
    try:
        repository = SqlAlchemyTelegramStarsRepository(factory)
        intent = await _intent(factory)
        await _payment(repository, intent, charge_id="personal-resolver")
        resolver = SqlAlchemyUserEntitlementResolver(
            factory,
            StaticPersonalConfigProvider(
                PersonalConfig(None, 30, PersonalQuotaLimits(free_daily=5, paid_daily=150), Decimal("1"))
            ),
        )

        paid = await resolver.resolve(user_id=_BUYER, feature=AiFeature.PERSONAL_CHAT, trigger="telegram_message")
        stranger = await resolver.resolve(
            user_id=_BUYER + 1, feature=AiFeature.PERSONAL_CHAT, trigger="telegram_message"
        )
        other_feature = await resolver.resolve(
            user_id=_BUYER, feature=AiFeature.LLM_ADMIN, trigger="telegram_message"
        )

        assert paid.access_tier == AccessTier.PAID
        assert paid.product_key == SELARA_PERSONAL_PRODUCT_KEY and paid.source == "telegram_stars"
        assert paid.valid_until == _NOW + timedelta(days=30)
        assert paid.quota_policy is not None and paid.quota_policy.limit == 150
        assert stranger.access_tier == AccessTier.FREE and stranger.quota_policy is None
        assert other_feature.access_tier == AccessTier.FREE
        assert await repository.active_paid_user_count(now=_NOW) == 1
        assert await repository.active_paid_user_count(now=_NOW + timedelta(days=31)) == 0
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_database_keeps_personal_and_chat_products_apart_and_gifts_off():
    engine, factory = await _database()
    try:
        async with factory() as session:
            session.add(ChatModel(telegram_chat_id=-100_266_601, type="supergroup", title="Group"))
            await session.commit()

        async def insert(row):
            async with factory() as session:
                session.add(row)
                with pytest.raises(IntegrityError):
                    await session.commit()

        # a personal product can never become a chat entitlement
        await insert(
            ChatEntitlementModel(
                chat_id=-100_266_601,
                product_key=SELARA_PERSONAL_PRODUCT_KEY,
                valid_from=_NOW,
                valid_until=_NOW + timedelta(days=1),
            )
        )
        # a chat product can never become a user entitlement
        async with factory() as session:
            from selara.infrastructure.db.models import UserModel

            session.add(UserModel(telegram_user_id=_BUYER, is_bot=False))
            await session.commit()
        await insert(
            UserEntitlementModel(
                user_id=_BUYER,
                product_key=SELARA_AI_PRODUCT_KEY,
                valid_from=_NOW,
                valid_until=_NOW + timedelta(days=1),
            )
        )

        base = {
            "id": "5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
            "buyer_user_id": _BUYER,
            "amount_stars": 69,
            "currency": "XTR",
            "duration_seconds": 2_592_000,
            "invoice_payload": "selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
            "expires_at": _NOW + timedelta(minutes=15),
        }
        # gifts are off: target user must equal the buyer
        await insert(
            SelaraAiPurchaseIntentModel(
                **base,
                product_key=SELARA_PERSONAL_PRODUCT_KEY,
                target_scope="user",
                target_user_id=_BUYER + 1,
            )
        )
        # a user-scope intent cannot also point at a chat
        await insert(
            SelaraAiPurchaseIntentModel(
                **base,
                product_key=SELARA_PERSONAL_PRODUCT_KEY,
                target_scope="user",
                target_user_id=_BUYER,
                chat_id=-100_266_601,
                source_chat_id=-100_266_601,
            )
        )
        # a chat-scope intent cannot buy the personal product or lack a chat
        await insert(
            SelaraAiPurchaseIntentModel(
                **base,
                product_key=SELARA_PERSONAL_PRODUCT_KEY,
                target_scope="chat",
                chat_id=-100_266_601,
                source_chat_id=-100_266_601,
            )
        )
        await insert(
            SelaraAiPurchaseIntentModel(
                **base,
                product_key=SELARA_AI_PRODUCT_KEY,
                target_scope="chat",
            )
        )
    finally:
        await engine.dispose()
