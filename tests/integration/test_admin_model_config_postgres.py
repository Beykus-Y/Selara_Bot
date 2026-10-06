"""Owner API edits, runtime TTL, CAS concurrency and historical PostgreSQL accounting."""
import asyncio
import os
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.model_catalog import ModelProfile
from selara.application.model_router import DefaultModelRouter
from selara.core.config import Settings
from selara.infrastructure.db.ai_accounting import AiAccountingService
from selara.infrastructure.db.ai_analytics import AdminAiAnalyticsRepository
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.model_catalog import build_model_catalog
from selara.infrastructure.db.models import LlmUsageLogModel
from selara.infrastructure.llm.client import LlmClient, LlmConfig, LlmAccountingContext
from selara.web.miniapp_admin import build_miniapp_admin_router

pytestmark = [pytest.mark.integration, pytest.mark.postgres]
PREFIX = "/api/miniapp/admin/ai"


@pytest.fixture
async def api():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cache, store = build_model_catalog(factory)
    await store.save_profile(ModelProfile("analytics", "Аналитик"))
    async def load_user(session, request):
        return SimpleNamespace(telegram_user_id=77)
    app = FastAPI()
    app.include_router(build_miniapp_admin_router(
        settings=Settings(BOT_TOKEN="123456:test", DATABASE_URL=url, ADMIN_USER_ID=77),
        session_factory=factory, load_user=load_user,
        broadcast_preview_handler=AsyncMock(), broadcast_start_handler=AsyncMock(),
        broadcast_status_handler=AsyncMock(), telegram_bot_probe=AsyncMock(),
    ))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, cache, store, factory
    await engine.dispose()


async def test_api_prices_assignments_and_actual_usage_history(api):
    client, cache, store, factory = api
    model = {
        "key": "one", "model_id": "provider/one", "display_name": "One",
        "prompt_price_usd_per_million": "1", "completion_price_usd_per_million": "2",
        "aliases": ["provider/snapshot"], "capabilities": {"supports_tools": True},
    }
    assert (await client.post(PREFIX + "/models", json=model)).status_code == 201
    profile = {"display_name": "Аналитик", "enabled": True, "model_key": "one", "ail_multiplier": "5", "revision": 1}
    assert (await client.put(PREFIX + "/model-profiles/analytics", json=profile)).status_code == 200
    router = DefaultModelRouter("legacy", cache)
    assert (await router.resolve(profile_key="analytics")).model_id == model["model_id"]
    clock = [0.]
    # Simulate a separate bot worker using the production TTL; web cannot invalidate its memory.
    cache._clock = lambda: clock[0]
    cache.invalidate()
    await cache.get()
    service = AiAccountingService(factory)
    invocation = await service.create_invocation(feature="llm_admin", trigger="test", chat_id=None)
    llm = LlmClient(LlmConfig(api_key="test", model="legacy"), model_catalog=cache, accounting_service=service)
    llm._client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
        model="provider/snapshot",
        usage=SimpleNamespace(prompt_tokens=1000000, completion_tokens=1000000, total_tokens=2000000),
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
    ))
    context = LlmAccountingContext(invocation, "llm_admin", "test", None)
    await llm.chat_simple([], model_profile="analytics", accounting_context=context)
    edit = {k: v for k, v in model.items() if k != "key"}
    edit.update(revision=1, prompt_price_usd_per_million="10")
    assert (await client.put(PREFIX + "/models/one", json=edit)).status_code == 200
    clock[0] = 16.
    await llm.chat_simple([], model_profile="analytics", accounting_context=context)
    assert (await client.post(PREFIX + "/models", json={
        **model, "key": "two", "model_id": "provider/two", "aliases": [],
    })).status_code == 201
    assert (await client.put(PREFIX + "/model-profiles/analytics", json={
        **profile, "revision": 2, "model_key": "two", "ail_multiplier": "9",
    })).status_code == 200
    clock[0] = 32.
    assert (await router.resolve(profile_key="analytics")).model_id == "provider/two"
    async with factory() as session:
        rows = (await session.scalars(select(LlmUsageLogModel).order_by(LlmUsageLogModel.id))).all()
        assert [r.estimated_cost_usd for r in rows] == [Decimal("3"), Decimal("12")]
        assert all(r.model == "provider/snapshot" and r.model_profile == "analytics" for r in rows)
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        stats = await AdminAiAnalyticsRepository(session).profile_breakdown(
            window_from=now - timedelta(days=1), window_to=now + timedelta(days=1),
        )
        assert stats == [{"profile_key": "analytics", "provider_calls": 2,
                          "known_cost_usd": Decimal("15"), "unknown_cost_calls": 0}]
    # Concurrent same-revision API writes cannot overwrite each other.
    responses = await asyncio.gather(*[
        client.put(PREFIX + "/model-profiles/analytics", json={
            **profile, "revision": 3, "model_key": "two", "ail_multiplier": str(multiplier),
        }) for multiplier in (2, 3)
    ])
    assert sorted(r.status_code for r in responses) == [200, 409]


@pytest.mark.parametrize("paid", [False, True])
async def test_admin_multiplier_edit_still_charges_one_request(api, paid):
    from datetime import datetime, timezone
    from selara.application.feature_access import (
        AccessTier, FeatureEntitlement, FeatureAccessService, PersonalQuotaLimits, QuotaScope, paid_personal_policy,
    )
    from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
    from selara.infrastructure.db.models import AiFeatureQuotaUsageModel
    from selara.infrastructure.llm.features import AiFeature

    client, cache, _, factory = api
    response = await client.put(PREFIX + "/model-profiles/analytics", json={
        "display_name": "Аналитик", "enabled": True, "model_key": None, "ail_multiplier": "999", "revision": 1,
    })
    assert response.status_code == 200, response.text
    assert (await DefaultModelRouter("legacy", cache).resolve(profile_key="analytics")).ail_multiplier == 999
    limits = PersonalQuotaLimits(free_daily=5, paid_daily=150)
    resolver = SimpleNamespace(resolve=AsyncMock(return_value=FeatureEntitlement(
        access_tier=AccessTier.PAID if paid else AccessTier.FREE,
        quota_policy=paid_personal_policy(limits) if paid else None,
    )))
    access = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(factory), personal_limits=limits,
                                  user_entitlement_resolver=resolver)
    decision = await access.reserve_feature_usage(
        feature=AiFeature.PERSONAL_CHAT, chat_id=700001, actor_user_id=700001,
        scope=QuotaScope.user(700001), chat_type="private", trigger="telegram_message",
        timezone_name="UTC", idempotency_key="admin-multiplier", now=datetime(2026, 10, 6, tzinfo=timezone.utc),
    )
    assert decision.allowed
    assert decision.quota_limit == (150 if paid else 5)
    async with factory() as session:
        row = (await session.scalars(select(AiFeatureQuotaUsageModel))).one()
        assert row.units == Decimal("1")


async def test_owner_switches_personal_quota_mode_only_with_budgets_basic_and_confirmation(api):
    client, _cache, store, _factory = api
    url = "/api/miniapp/admin/monetization/quota-mode"
    state = (await client.get(url)).json()
    assert state["quota_mode"] == "requests" and state["requests"] == {"free_daily": 5, "paid_daily": 150}
    assert state["activation_problems"]  # basic has no model yet

    assert (await client.put(url, json={"quota_mode": "ail"})).status_code == 422  # no budgets
    budgets = {"free_daily_ail": 10, "paid_daily_ail": 100}
    assert (await client.put(url, json={"quota_mode": "ail", **budgets})).status_code == 422  # no usable basic
    assert (await client.put(url, json={"quota_mode": "ail", "free_daily_ail": 100, "paid_daily_ail": 10})).status_code == 422

    model = {"key": "base", "model_id": "provider/base", "display_name": "Base", "capabilities": {}}
    assert (await client.post(PREFIX + "/models", json=model)).status_code == 201
    await store.save_profile(ModelProfile("basic", "Базовая", "base", Decimal("1")))
    # Saving budgets in requests mode is allowed and changes nothing for users.
    saved = (await client.put(url, json={"quota_mode": "requests", **budgets})).json()
    assert saved["quota_mode"] == "requests" and saved["free_daily_ail"] == 10

    unconfirmed = await client.put(url, json={"quota_mode": "ail", **budgets})
    assert unconfirmed.status_code == 409 and "Подтвердите" in unconfirmed.json()["detail"]
    enabled = await client.put(url, json={"quota_mode": "ail", "confirm": True, **budgets})
    assert enabled.status_code == 200 and enabled.json()["quota_mode"] == "ail"
    assert (await client.put(url, json={"quota_mode": "requests", **budgets})).json()["quota_mode"] == "requests"
    assert (await client.put(url, json={"quota_mode": "ail", "unknown": 1})).status_code == 422
