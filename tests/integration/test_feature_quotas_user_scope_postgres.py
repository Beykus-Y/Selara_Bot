from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.personal_config import PersonalConfig, StaticPersonalConfigProvider
from selara.application.feature_access import (
    AccessReason,
    AccessTier,
    FeatureAccessService,
    PersonalQuotaLimits,
    QuotaScope,
)
from selara.application.selara_ai_product import SELARA_PERSONAL_PRODUCT_KEY
from selara.application.usage_pricing import QuotaCost
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.chat_migration import migrate_chat_id
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.models import (
    AiFeatureInvocationModel,
    AiFeatureQuotaUsageModel,
    ChatModel,
    UserEntitlementModel,
    UserModel,
)
from selara.infrastructure.db.telegram_stars import SqlAlchemyUserEntitlementResolver
from selara.infrastructure.llm.features import AiFeature

_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
_LIMITS = PersonalQuotaLimits(free_daily=5, paid_daily=150)
_CONFIG = StaticPersonalConfigProvider(PersonalConfig(None, 30, _LIMITS, Decimal("1")))


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _reserve(
    service: FeatureAccessService,
    *,
    user_id: int,
    key: str,
    feature: AiFeature = AiFeature.PERSONAL_CHAT,
    owner_exempt: bool = False,
    now: datetime = _NOW,
    source_message_id: int | None = None,
    chat_id: int | None = None,
    scope: QuotaScope | None = None,
):
    return await service.reserve_feature_usage(
        feature=feature,
        chat_id=user_id if chat_id is None else chat_id,
        scope=QuotaScope.user(user_id) if scope is None else scope,
        actor_user_id=user_id,
        actor_is_bot=False,
        trigger="telegram_message",
        timezone_name="UTC",
        idempotency_key=key,
        chat_type="private",
        owner_exempt=owner_exempt,
        source_message_id=source_message_id,
        now=now,
    )


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_free_user_gets_five_requests_per_day_and_denial_creates_no_invocation():
    engine, factory = await _database()
    try:
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory), personal_limits=_LIMITS)
        user_id = 611_001

        granted = [await _reserve(service, user_id=user_id, key=f"personal_chat:{user_id}:{i}") for i in range(5)]
        denied = await _reserve(service, user_id=user_id, key=f"personal_chat:{user_id}:99")

        assert all(decision.allowed for decision in granted)
        assert granted[0].quota_limit == 5 and granted[0].quota_used == 1 and granted[0].quota_remaining == 4
        assert granted[-1].quota_used == 5 and granted[-1].quota_remaining == 0
        assert granted[0].scope_type == "user" and granted[0].scope_id == str(user_id)
        assert not denied.allowed and denied.reason == AccessReason.QUOTA_EXHAUSTED
        assert denied.invocation_id is None
        async with factory() as session:
            usage = (await session.scalars(select(AiFeatureQuotaUsageModel))).all()
            invocations = (await session.scalars(select(AiFeatureInvocationModel))).all()
        assert len(usage) == len(invocations) == 5
        assert {row.quota_scope_type for row in usage} == {"user"}
        assert {row.quota_scope_id for row in usage} == {user_id}
        assert {row.pool_key for row in usage} == {"personal_daily"}
        assert {row.units for row in usage} == {Decimal("1")}
        assert {row.quota_limit for row in usage} == {5}
        assert {(row.scope_type, row.scope_id) for row in invocations} == {("user", str(user_id))}

        next_day = await _reserve(
            service, user_id=user_id, key=f"personal_chat:{user_id}:next", now=_NOW + timedelta(days=1)
        )
        assert next_day.allowed and next_day.quota_used == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_users_are_counted_independently_and_scopes_do_not_collide():
    engine, factory = await _database()
    try:
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory), personal_limits=_LIMITS)
        for i in range(5):
            await _reserve(service, user_id=611_011, key=f"personal_chat:611011:{i}")
        exhausted = await _reserve(service, user_id=611_011, key="personal_chat:611011:x")
        other = await _reserve(service, user_id=611_012, key="personal_chat:611012:1")
        # the same numeric id as a *chat* scope is a different bucket
        chat_scoped = await _reserve(
            service,
            user_id=611_013,
            key="llm_admin:611011:1",
            feature=AiFeature.LLM_ADMIN,
            chat_id=611_011,
            scope=QuotaScope.chat(611_011),
        )

        assert not exhausted.allowed
        assert other.allowed and other.quota_used == 1
        assert chat_scoped.allowed and chat_scoped.quota_used == 1
        assert chat_scoped.scope_type == "chat" and chat_scoped.scope_id == "611011"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_requests_at_four_of_five_allow_exactly_one_last_slot():
    engine, factory = await _database()
    try:
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory), personal_limits=_LIMITS)
        user_id = 611_021
        for i in range(4):
            await _reserve(service, user_id=user_id, key=f"personal_chat:{user_id}:{i}")

        results = await asyncio.gather(
            *(_reserve(service, user_id=user_id, key=f"personal_chat:{user_id}:race{i}") for i in range(6))
        )

        assert sum(1 for result in results if result.allowed) == 1
        assert sum(1 for result in results if result.reason == AccessReason.QUOTA_EXHAUSTED) == 5
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_replayed_message_reuses_its_reservation_and_another_scope_cannot_steal_the_key():
    engine, factory = await _database()
    try:
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory), personal_limits=_LIMITS)
        user_id = 611_031
        first = await _reserve(
            service, user_id=user_id, key=f"personal_chat:{user_id}:7", source_message_id=7
        )
        replay = await _reserve(
            service, user_id=user_id, key=f"personal_chat:{user_id}:7", source_message_id=7
        )
        stolen = await _reserve(
            service, user_id=611_032, chat_id=user_id, key=f"personal_chat:{user_id}:7", source_message_id=7
        )

        assert first.allowed and not first.reused
        assert replay.allowed and replay.reused and replay.invocation_id == first.invocation_id
        assert replay.quota_used == 1
        assert not stolen.allowed and stolen.reason == AccessReason.DUPLICATE_REQUEST
        async with factory() as session:
            count = await session.scalar(select(func.count(AiFeatureQuotaUsageModel.id)))
        assert count == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_active_personal_entitlement_raises_the_limit_to_one_fifty_until_it_expires():
    engine, factory = await _database()
    try:
        user_id = 611_041
        async with factory() as session:
            session.add(UserModel(telegram_user_id=user_id, is_bot=False))
            await session.commit()
            session.add(
                UserEntitlementModel(
                    user_id=user_id,
                    product_key=SELARA_PERSONAL_PRODUCT_KEY,
                    status="active",
                    valid_from=_NOW - timedelta(days=1),
                    valid_until=_NOW + timedelta(days=5),
                )
            )
            await session.commit()
        service = FeatureAccessService(
            SqlAlchemyFeatureQuotaRepository(factory),
            user_entitlement_resolver=SqlAlchemyUserEntitlementResolver(factory, _CONFIG),
            personal_limits=_LIMITS,
        )

        paid = await _reserve(service, user_id=user_id, key=f"personal_chat:{user_id}:1")
        for i in range(2, 8):
            sixth_plus = await _reserve(service, user_id=user_id, key=f"personal_chat:{user_id}:{i}")
            assert sixth_plus.allowed
        summary = await service.get_usage_summary(
            feature=AiFeature.PERSONAL_CHAT,
            chat_id=user_id,
            scope=QuotaScope.user(user_id),
            trigger="telegram_message",
            timezone_name="UTC",
            now=_NOW,
        )
        expired = await _reserve(
            service,
            user_id=user_id,
            key=f"personal_chat:{user_id}:late",
            now=_NOW + timedelta(days=6),
        )

        assert paid.access_tier == AccessTier.PAID
        assert paid.quota_limit == 150 and paid.quota_used == 1 and paid.quota_remaining == 149
        assert paid.entitlement_product == SELARA_PERSONAL_PRODUCT_KEY
        assert summary.quota_limit == 150 and summary.quota_used == 7
        assert summary.access_tier == AccessTier.PAID
        assert summary.scope_type == "user" and summary.scope_id == str(user_id)
        assert expired.access_tier == AccessTier.FREE and expired.quota_limit == 5
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_unit_costs_are_summed_and_a_request_that_does_not_fit_is_denied():
    engine, factory = await _database()
    try:
        pricer = SimpleNamespace(price=lambda **_: QuotaCost(Decimal("2")))
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory), pricer=pricer, personal_limits=_LIMITS)
        user_id = 611_051

        one = await _reserve(service, user_id=user_id, key=f"personal_chat:{user_id}:1")
        two = await _reserve(service, user_id=user_id, key=f"personal_chat:{user_id}:2")
        denied = await _reserve(service, user_id=user_id, key=f"personal_chat:{user_id}:3")

        assert one.allowed and one.quota_used == 2
        assert two.allowed and two.quota_used == 4 and two.quota_remaining == 1
        assert not denied.allowed and denied.reason == AccessReason.QUOTA_EXHAUSTED
        assert denied.quota_used == 4
        async with factory() as session:
            total = await session.scalar(select(func.sum(AiFeatureQuotaUsageModel.units)))
        assert total == Decimal("4")
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_owner_exempt_dm_is_unlimited_and_does_not_consume_the_pool():
    engine, factory = await _database()
    try:
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory), personal_limits=_LIMITS)
        user_id = 611_061

        decisions = [
            await _reserve(service, user_id=user_id, key=f"personal_chat:{user_id}:{i}", owner_exempt=True)
            for i in range(8)
        ]
        regular = await _reserve(service, user_id=user_id, key=f"personal_chat:{user_id}:regular")

        assert all(d.allowed and d.owner_exempt and d.access_tier == AccessTier.OWNER_INTERNAL for d in decisions)
        assert regular.allowed and regular.quota_used == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_autocfg_and_memory_extraction_in_a_dm_never_draw_from_the_personal_pool():
    engine, factory = await _database()
    try:
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory), personal_limits=_LIMITS)
        user_id = 611_071
        for i in range(5):
            await _reserve(service, user_id=user_id, key=f"personal_chat:{user_id}:{i}")

        autoconfig = await _reserve(service, user_id=user_id, key="autoconfig:1", feature=AiFeature.AUTOCONFIG)
        extraction = await _reserve(
            service, user_id=user_id, key="personal_memory_extract:1", feature=AiFeature.PERSONAL_MEMORY_EXTRACT
        )
        summary = await service.get_usage_summary(
            feature=AiFeature.PERSONAL_CHAT,
            chat_id=user_id,
            scope=QuotaScope.user(user_id),
            trigger="telegram_message",
            timezone_name="UTC",
            now=_NOW,
        )

        assert autoconfig.allowed and autoconfig.reason == AccessReason.NO_COMMERCIAL_QUOTA
        assert extraction.allowed and extraction.reason == AccessReason.NO_COMMERCIAL_QUOTA
        assert summary.quota_used == 5 and summary.quota_remaining == 0
        async with factory() as session:
            usage_count = await session.scalar(select(func.count(AiFeatureQuotaUsageModel.id)))
        assert usage_count == 5
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_release_before_provider_attempt_returns_the_slot_for_user_scope():
    engine, factory = await _database()
    try:
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory), personal_limits=_LIMITS)
        user_id = 611_081
        decisions = [
            await _reserve(service, user_id=user_id, key=f"personal_chat:{user_id}:{i}") for i in range(5)
        ]

        released = await service.release_if_no_provider_attempts(
            invocation_id=decisions[-1].invocation_id, reason="provider_unavailable"
        )
        retry = await _reserve(service, user_id=user_id, key=f"personal_chat:{user_id}:again")

        assert released is True
        assert retry.allowed and retry.quota_used == 5
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_chat_migration_rescopes_chat_quota_but_leaves_user_scope_alone():
    engine, factory = await _database()
    try:
        old_chat_id, new_chat_id, user_id = -100_611_091, -100_611_092, 611_093
        async with factory() as session:
            session.add_all(
                [
                    ChatModel(telegram_chat_id=old_chat_id, type="group", title="Old"),
                    ChatModel(telegram_chat_id=new_chat_id, type="supergroup", title="New"),
                ]
            )
            await session.commit()
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory), personal_limits=_LIMITS)
        for i in range(3):
            await _reserve(
                service,
                user_id=user_id,
                chat_id=old_chat_id,
                scope=QuotaScope.chat(old_chat_id),
                feature=AiFeature.LLM_ADMIN,
                key=f"llm_admin:{old_chat_id}:{i}",
                source_message_id=i,
            )
        user_scoped = await _reserve(
            service, user_id=user_id, chat_id=old_chat_id, key=f"personal_chat:{user_id}:odd"
        )

        async with factory() as session:
            await migrate_chat_id(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
            await session.commit()

        summary = await service.get_usage_summary(
            feature=AiFeature.LLM_ADMIN,
            chat_id=new_chat_id,
            trigger="telegram_message",
            timezone_name="UTC",
            now=_NOW,
        )
        personal = await service.get_usage_summary(
            feature=AiFeature.PERSONAL_CHAT,
            chat_id=user_id,
            scope=QuotaScope.user(user_id),
            trigger="telegram_message",
            timezone_name="UTC",
            now=_NOW,
        )
        async with factory() as session:
            chat_rows = (
                await session.scalars(
                    select(AiFeatureQuotaUsageModel).where(AiFeatureQuotaUsageModel.quota_scope_type == "chat")
                )
            ).all()
            user_rows = (
                await session.scalars(
                    select(AiFeatureQuotaUsageModel).where(AiFeatureQuotaUsageModel.quota_scope_type == "user")
                )
            ).all()
        assert user_scoped.allowed
        assert summary.quota_used == 3 and summary.scope_id == str(new_chat_id)
        assert {row.quota_scope_id for row in chat_rows} == {new_chat_id}
        assert {row.chat_id for row in chat_rows} == {new_chat_id}
        assert {row.quota_scope_id for row in user_rows} == {user_id}
        assert personal.quota_used == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_orphaned_legacy_rows_are_not_counted_and_scope_check_is_enforced():
    engine, factory = await _database()
    try:
        period_start = datetime(2026, 10, 6, tzinfo=timezone.utc)
        common = dict(
            feature="llm_admin",
            trigger="telegram_message",
            period_start=period_start,
            period_end=period_start + timedelta(days=1),
            policy_key="llm_admin_free_daily_v1",
            quota_limit=10,
            access_tier="free",
            owner_exempt=False,
            status="consumed",
            pool_key="llm_admin",
        )
        async with factory() as session:
            session.add(
                AiFeatureQuotaUsageModel(
                    chat_id=None,
                    quota_scope_type="legacy_orphan",
                    quota_scope_id=None,
                    idempotency_key="legacy-orphan-1",
                    **common,
                )
            )
            await session.commit()
        async with factory() as session:
            session.add(
                AiFeatureQuotaUsageModel(
                    chat_id=None,
                    quota_scope_type="chat",
                    quota_scope_id=None,
                    idempotency_key="bad-chat-scope-1",
                    **common,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
        async with factory() as session:
            session.add(
                AiFeatureQuotaUsageModel(
                    chat_id=None,
                    quota_scope_type="team",
                    quota_scope_id=1,
                    idempotency_key="bad-scope-type-1",
                    **common,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_configured_limits_drive_the_free_and_paid_pool_in_the_database():
    engine, factory = await _database()
    try:
        limits = PersonalQuotaLimits(free_daily=2, paid_daily=4)
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory), personal_limits=limits)
        user_id = 611_101

        results = [await _reserve(service, user_id=user_id, key=f"personal_chat:{user_id}:{i}") for i in range(3)]

        assert [result.allowed for result in results] == [True, True, False]
        assert results[0].quota_limit == 2
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_saved_override_changes_price_limits_and_weights_without_restart():
    from selara.application.personal_config import PersonalConfigOverride
    from selara.core.config import Settings
    from selara.infrastructure.db.personal_config import build_personal_config

    engine, factory = await _database()
    try:
        os.environ.setdefault("BOT_TOKEN", "123:TEST")
        settings = Settings(_env_file=None, database_url=os.environ["TEST_DATABASE_URL"])
        provider, store = build_personal_config(factory, settings, ttl_seconds=3600)
        service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory), personal_config=provider)
        user_id = 611_201

        assert (await provider.get()).limits == PersonalQuotaLimits(5, 150)
        # Cached for an hour, so only the save below can change what the service sees.
        effective = await store.save_override(
            PersonalConfigOverride(price_stars=99, free_daily_limit=1, paid_daily_limit=10), updated_by=7
        )
        assert effective.price_stars == 99

        first = await _reserve(service, user_id=user_id, key="ovr:1")
        second = await _reserve(service, user_id=user_id, key="ovr:2")
        assert first.quota_limit == 1 and first.allowed and not second.allowed

        with pytest.raises(ValueError):
            await store.save_override(PersonalConfigOverride(free_daily_limit=500))
        assert (await provider.get()).limits.free_daily == 1  # rejected save left the row untouched

        await store.save_override(PersonalConfigOverride())  # clear: back to .env values
        assert (await provider.get()).limits == PersonalQuotaLimits(5, 150)
        assert await store.load_override() == PersonalConfigOverride()
    finally:
        await engine.dispose()
