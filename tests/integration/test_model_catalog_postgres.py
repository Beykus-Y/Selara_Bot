"""Catalog constraints, atomic aliases, cache writes and historical costs on PostgreSQL."""
from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.feature_access import (
    AccessTier, FeatureEntitlement, FeatureAccessService, PersonalQuotaLimits, QuotaScope, paid_personal_policy,
)
from selara.application.model_catalog import CatalogModel, ModelCapabilities, ModelProfile
from selara.application.model_router import DefaultModelRouter
from selara.infrastructure.db.ai_accounting import AiAccountingService
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.model_catalog import build_model_catalog
from selara.infrastructure.db.models import (
    AiFeatureQuotaUsageModel, LlmModelCatalogModel, LlmModelIdentifierModel, LlmModelProfileModel, LlmUsageLogModel,
)
from selara.infrastructure.llm.client import LlmAccountingContext, LlmClient, LlmConfig
from selara.infrastructure.llm.features import AiFeature

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


async def database():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


MODEL = CatalogModel("one", "provider/one", "One", prompt_price_usd_per_million=Decimal("1"),
                     completion_price_usd_per_million=Decimal("2"), aliases=("provider/snapshot",),
                     capabilities=ModelCapabilities(True, True, True))


async def test_store_updates_cache_and_preserves_cost_profile_history():
    engine, factory = await database()
    try:
        cache, store = build_model_catalog(factory)
        router = DefaultModelRouter("legacy", cache)
        assert (await router.resolve(profile_key="analytics")).is_fallback
        await store.save_model(MODEL)
        await store.save_profile(ModelProfile("analytics", "Аналитик", MODEL.key, Decimal("5")))
        resolved = await router.resolve(profile_key="analytics")
        assert (resolved.model_id, resolved.ail_multiplier) == (MODEL.model_id, Decimal("5"))
        service = AiAccountingService(factory)
        invocation = await service.create_invocation(feature="llm_admin", trigger="test", chat_id=None)
        llm = LlmClient(LlmConfig(api_key="test", model="legacy"), model_catalog=cache, accounting_service=service)
        llm._client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
            model=MODEL.aliases[0], usage=SimpleNamespace(prompt_tokens=1000000, completion_tokens=1000000, total_tokens=2000000),
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        ))
        context = LlmAccountingContext(invocation, "llm_admin", "test", None)
        await llm.chat_simple([], model_profile="analytics", accounting_context=context)
        await store.save_model(replace(MODEL, prompt_price_usd_per_million=Decimal("10")))
        await store.save_profile(ModelProfile("analytics", "Updated", MODEL.key, Decimal("9")))
        await llm.chat_simple([], model_profile="analytics", accounting_context=context)
        async with factory() as session:
            rows = (await session.scalars(select(LlmUsageLogModel).order_by(LlmUsageLogModel.id))).all()
            assert [r.estimated_cost_usd for r in rows] == [Decimal("3"), Decimal("12")]
            assert all(r.model == MODEL.aliases[0] and r.model_profile == "analytics" for r in rows)
        await store.save_model(replace(MODEL, enabled=False))
        assert (await router.resolve(profile_key="analytics")).is_fallback
        # Deletion detaches assignments, but historical accounting is not tied to the catalog by FK.
        async with factory() as session, session.begin():
            await session.execute(delete(LlmModelCatalogModel))
        cache.invalidate()
        assert (await router.resolve(profile_key="analytics")).is_fallback
        async with factory() as session:
            assert (await session.get(LlmModelProfileModel, "analytics")).model_key is None
            assert len((await session.scalars(select(LlmUsageLogModel))).all()) == 2
            assert not (await session.scalars(select(LlmModelIdentifierModel))).all()
    finally:
        await engine.dispose()


async def test_alias_collisions_canonical_collisions_and_invalid_fk_are_atomic():
    engine, factory = await database()
    try:
        cache, store = build_model_catalog(factory)
        await store.save_model(MODEL)
        for name in (MODEL.model_id, MODEL.aliases[0]):
            other = CatalogModel("two", name, "Two")
            with pytest.raises(IntegrityError):
                await store.save_model(other)
            assert list((await store.load()).models_by_key) == [MODEL.key]
            with pytest.raises(IntegrityError):
                await store.save_model(CatalogModel("two", "provider/two", "Two", aliases=(name,)))
        with pytest.raises(IntegrityError):
            await store.save_profile(ModelProfile("basic", "Базовая", "missing"))
        await store.save_model(CatalogModel("two", "provider/two", "Two"))
        results = await asyncio.gather(
            store.save_model(replace(MODEL, aliases=("shared",))),
            store.save_model(CatalogModel("two", "provider/two", "Two", aliases=("shared",))),
            return_exceptions=True,
        )
        assert sum(isinstance(result, IntegrityError) for result in results) == 1
        state = await store.load()
        assert state.models_by_id["shared"].key in {"one", "two"}
    finally:
        await engine.dispose()


@pytest.mark.parametrize("column,value", [
    ("prompt_price_usd_per_million", "-1"), ("prompt_price_usd_per_million", "'NaN'"),
    ("completion_price_usd_per_million", "'Infinity'"), ("completion_price_usd_per_million", "'-Infinity'"),
])
async def test_database_price_constraints(column, value):
    engine, factory = await database()
    try:
        _, store = build_model_catalog(factory)
        await store.save_model(MODEL)
        with pytest.raises(DBAPIError) as exc:
            async with factory() as session, session.begin():
                await session.execute(text(f"UPDATE llm_model_catalog SET {column} = {value} WHERE key = 'one'"))
        assert "price" in str(exc.value) or "numeric field overflow" in str(exc.value)
    finally:
        await engine.dispose()


async def test_database_multiplier_constraints():
    engine, factory = await database()
    try:
        _, store = build_model_catalog(factory)
        await store.save_profile(ModelProfile("basic", "Базовая"))
        for value in ("0", "-1", "1001", "'NaN'", "'Infinity'", "'-Infinity'"):
            with pytest.raises(DBAPIError):
                async with factory() as session, session.begin():
                    await session.execute(text(f"UPDATE llm_model_profiles SET ail_multiplier = {value}"))
        assert (await store.load()).profiles[0].ail_multiplier == Decimal("1")
    finally:
        await engine.dispose()


@pytest.mark.parametrize("paid", [False, True])
async def test_multiplier_changes_do_not_change_personal_request_quota(paid):
    engine, factory = await database()
    try:
        cache, store = build_model_catalog(factory)
        await store.save_model(MODEL)
        limits = PersonalQuotaLimits(free_daily=5, paid_daily=150)
        resolver = SimpleNamespace(resolve=AsyncMock(return_value=FeatureEntitlement(
            access_tier=AccessTier.PAID if paid else AccessTier.FREE,
            quota_policy=paid_personal_policy(limits) if paid else None,
        )))
        access = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory), personal_limits=limits,
                                      user_entitlement_resolver=resolver)
        limit = 150 if paid else 5
        now = datetime(2026, 10, 6, tzinfo=timezone.utc)
        for index in range(limit + 1):
            await store.save_profile(ModelProfile("basic", "Базовая", MODEL.key, Decimal(index + 1)))
            assert (await DefaultModelRouter("legacy", cache).resolve(profile_key="basic")).ail_multiplier == index + 1
            decision = await access.reserve_feature_usage(
                feature=AiFeature.PERSONAL_CHAT, chat_id=700001, actor_user_id=700001,
                scope=QuotaScope.user(700001), chat_type="private", trigger="telegram_message",
                timezone_name="UTC", idempotency_key=f"personal-{index}", now=now,
            )
            assert decision.allowed == (index < limit)
            assert decision.quota_limit == limit
        async with factory() as session:
            rows = (await session.scalars(select(AiFeatureQuotaUsageModel))).all()
            assert len(rows) == limit
            assert all(row.units == Decimal("1") for row in rows)
    finally:
        await engine.dispose()
