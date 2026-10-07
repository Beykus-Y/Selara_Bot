from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.selara_ai_product import (
    SELARA_PERSONAL_PRODUCT_KEY,
    SELARA_PERSONAL_TERMS_VERSION,
    get_selara_ai_product,
)
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.entitlement_grants import EntitlementGrantService
from selara.infrastructure.db.models import EntitlementGrantModel, UserEntitlementModel
from selara.infrastructure.db.telegram_stars import SqlAlchemyTelegramStarsRepository

_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
_PERSON = 266_700
_OWNER = 77


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _grant(service, key: str, days: int = 30, **extra):
    return service.grant(
        scope="user", target_id=_PERSON, days=days, reason="test", idempotency_key=key,
        actor_user_id=_OWNER, source="command", now=_NOW, **extra,
    )


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_grants_with_different_keys_add_up_and_one_key_applies_once():
    engine, factory = await _database()
    try:
        service = EntitlementGrantService(factory, admin_user_id=_OWNER)
        await asyncio.gather(_grant(service, "a"), _grant(service, "b"), _grant(service, "c"))
        replays = await asyncio.gather(*(_grant(service, "same", days=5) for _ in range(4)))
        assert sorted(item.duplicate for item in replays) == [False, True, True, True]
        async with factory() as session:
            entitlement = await session.scalar(select(UserEntitlementModel))
            journal = await session.scalar(select(func.count(EntitlementGrantModel.id)))
        assert entitlement.valid_until == _NOW + timedelta(days=95)
        assert journal == 4
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_a_grant_and_a_real_payment_at_the_same_time_lose_no_days():
    engine, factory = await _database()
    try:
        repository = SqlAlchemyTelegramStarsRepository(factory)
        product = get_selara_ai_product(
            product_key=SELARA_PERSONAL_PRODUCT_KEY, price_stars=69, duration=timedelta(days=30), paid_daily_limit=150
        )
        intent = await repository.create_personal_purchase_intent(
            buyer_user_id=_PERSON, product=product, terms_version=SELARA_PERSONAL_TERMS_VERSION,
            terms_accepted_at=_NOW, now=_NOW,
        )
        service = EntitlementGrantService(factory, admin_user_id=_OWNER)
        _, payment = await asyncio.gather(
            _grant(service, "race"),
            repository.process_successful_payment(
                buyer_user_id=_PERSON, invoice_payload=intent.invoice_payload,
                telegram_payment_charge_id="grant-race", provider_payment_charge_id="", amount_stars=69,
                currency="XTR", payment_at=_NOW,
            ),
        )
        assert payment.state == "applied"
        async with factory() as session:
            entitlement = await session.scalar(select(UserEntitlementModel))
        assert entitlement.valid_until == _NOW + timedelta(days=60)
        assert entitlement.status == "active"
    finally:
        await engine.dispose()
