"""PR 13 on PostgreSQL: AI Limits reservations, concurrency, idempotency, mode switch and migration 0091."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.feature_access import (
    PERSONAL_AIL_POOL_KEY,
    PERSONAL_POOL_KEY,
    AccessReason,
    FeatureAccessService,
    QuotaScope,
)
from selara.core.config import Settings
from selara.infrastructure.db.ai_analytics import AdminAiAnalyticsRepository
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.models import AiFeatureQuotaUsageModel, UserModel
from selara.infrastructure.db.personal_config import build_personal_config
from selara.infrastructure.db.telegram_stars import SqlAlchemyUserEntitlementResolver
from selara.infrastructure.llm.features import AiFeature

pytestmark = [pytest.mark.integration, pytest.mark.postgres]

_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
_ROOT = Path(__file__).resolve().parents[2]


def _settings(monkeypatch) -> Settings:
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    return Settings(_env_file=None)


@pytest.fixture
async def env(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(UserModel(telegram_user_id=uid, is_bot=False) for uid in (1, 2, 3, 99))
        await session.commit()
    settings = _settings(monkeypatch)
    provider, store = build_personal_config(factory, settings)
    service = FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(factory),
        user_entitlement_resolver=SqlAlchemyUserEntitlementResolver(factory, provider),
        personal_config=provider,
    )
    yield factory, provider, store, service
    await engine.dispose()


async def _reserve(service, *, user: int, message_id: int, units: str | None = None, profile: str | None = None,
                   owner: bool = False):
    return await service.reserve_feature_usage(
        feature=AiFeature.PERSONAL_CHAT, chat_id=user, chat_type="private", scope=QuotaScope.user(user),
        actor_user_id=user, trigger="telegram_message", timezone_name="UTC",
        idempotency_key=f"personal_chat:{user}:{message_id}", source_message_id=message_id, now=_NOW,
        units=Decimal(units) if units is not None else None, model_profile=profile, owner_exempt=owner,
    )


async def _consumed(factory, pool: str, user: int) -> Decimal:
    async with factory() as session:
        value = await session.scalar(
            select(func.coalesce(func.sum(AiFeatureQuotaUsageModel.units), 0)).where(
                AiFeatureQuotaUsageModel.pool_key == pool,
                AiFeatureQuotaUsageModel.quota_scope_id == user,
                AiFeatureQuotaUsageModel.status == "consumed",
                AiFeatureQuotaUsageModel.owner_exempt.is_(False),
            )
        )
    return Decimal(value)


async def _enable_ail(store, free: int = 10, paid: int = 100):
    return await store.save_quota_mode(quota_mode="ail", free_daily_ail=free, paid_daily_ail=paid)


async def test_requests_mode_is_unchanged_and_ignores_multipliers(env):
    factory, provider, _store, service = env
    for message_id in range(5):
        assert (await _reserve(service, user=1, message_id=message_id, units="5", profile="creative")).allowed
    denied = await _reserve(service, user=1, message_id=99, units="5")
    assert denied.reason == AccessReason.QUOTA_EXHAUSTED and denied.quota_limit == 5
    assert await _consumed(factory, PERSONAL_POOL_KEY, 1) == Decimal("5")  # 5 requests, not 25
    async with factory() as session:
        assert await session.scalar(select(func.count()).where(AiFeatureQuotaUsageModel.model_profile.is_not(None))) == 0


async def test_ail_mode_charges_multipliers_and_denies_without_partial_charge(env):
    factory, _provider, store, service = env
    await _enable_ail(store)
    assert (await _reserve(service, user=2, message_id=1, units="1", profile="basic")).quota_remaining == 9
    assert (await _reserve(service, user=2, message_id=2, units="2", profile="analytics")).quota_remaining == 7
    assert (await _reserve(service, user=2, message_id=3, units="2.5", profile="analytics")).quota_remaining == 4.5
    denied = await _reserve(service, user=2, message_id=4, units="5", profile="creative")
    assert not denied.allowed and denied.reason == AccessReason.QUOTA_EXHAUSTED and denied.quota_remaining == 4.5
    assert await _consumed(factory, PERSONAL_AIL_POOL_KEY, 2) == Decimal("5.5")  # nothing taken for the denial
    async with factory() as session:
        rows = {row["profile_key"]: row for row in await AdminAiAnalyticsRepository(session).ail_breakdown(
            window_from=datetime(2000, 1, 1, tzinfo=timezone.utc), window_to=datetime(2100, 1, 1, tzinfo=timezone.utc))}
    assert rows["analytics"]["ail_consumed"] == Decimal("4.5") and rows["analytics"]["requests"] == 2
    assert rows["basic"]["ail_consumed"] == Decimal("1")


async def test_two_concurrent_requests_for_the_last_five_ail_let_exactly_one_through(env):
    factory, _provider, store, service = env
    await _enable_ail(store, free=5, paid=50)
    results = await asyncio.gather(
        _reserve(service, user=3, message_id=10, units="5", profile="creative"),
        _reserve(service, user=3, message_id=11, units="5", profile="creative"),
    )
    assert sorted(r.allowed for r in results) == [False, True]
    assert [r.reason for r in results if not r.allowed] == [AccessReason.QUOTA_EXHAUSTED]
    assert await _consumed(factory, PERSONAL_AIL_POOL_KEY, 3) == Decimal("5")


async def test_a_replayed_message_is_charged_once(env):
    factory, _provider, store, service = env
    await _enable_ail(store)
    first = await _reserve(service, user=2, message_id=77, units="5", profile="creative")
    replays = [await _reserve(service, user=2, message_id=77, units="5", profile="creative") for _ in range(3)]
    assert first.allowed and not first.reused and all(r.reused for r in replays)
    assert await _consumed(factory, PERSONAL_AIL_POOL_KEY, 2) == Decimal("5")
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(AiFeatureQuotaUsageModel)) == 1


async def test_owner_selects_profiles_without_spending_the_pool(env):
    factory, _provider, store, service = env
    await _enable_ail(store)
    decision = await _reserve(service, user=99, message_id=1, units="5", profile="creative", owner=True)
    assert decision.allowed and decision.unlimited and decision.invocation_id is not None
    assert await _consumed(factory, PERSONAL_AIL_POOL_KEY, 99) == 0
    async with factory() as session:
        row = await session.scalar(select(AiFeatureQuotaUsageModel))
    assert (row.owner_exempt, row.model_profile, row.units) == (True, "creative", Decimal("5.00"))


async def test_internal_operations_do_not_touch_the_budget(env):
    factory, _provider, store, service = env
    await _enable_ail(store)
    await _reserve(service, user=2, message_id=1, units="2", profile="analytics")
    for feature in (AiFeature.PERSONAL_MEMORY_EXTRACT, AiFeature.LLM_CONTEXT_COMPRESSION):
        internal = await service.reserve_feature_usage(
            feature=feature, chat_id=2, scope=QuotaScope.user(2), actor_user_id=2, trigger="internal",
            timezone_name="UTC", idempotency_key=f"{feature.value}:2", now=_NOW, units=Decimal("5"),
        )
        assert internal.reason == AccessReason.NO_COMMERCIAL_QUOTA
    assert await _consumed(factory, PERSONAL_AIL_POOL_KEY, 2) == Decimal("2")


async def test_mode_switch_needs_budgets_applies_without_restart_and_keeps_history(env):
    factory, provider, store, service = env
    with pytest.raises(ValueError):
        await store.save_quota_mode(quota_mode="ail", free_daily_ail=None, paid_daily_ail=None)
    assert (await provider.get()).quota_mode == "requests"
    for message_id in range(3):
        await _reserve(service, user=1, message_id=message_id)  # 3 requests in the morning

    await _enable_ail(store, free=10, paid=100)
    morning_in_ail = await _reserve(service, user=1, message_id=10, units="2", profile="analytics")
    assert morning_in_ail.quota_used == 2  # the 3 requests did not become 3 AIL

    back = await store.save_quota_mode(quota_mode="requests", free_daily_ail=10, paid_daily_ail=100)
    assert back.quota_mode == "requests"
    again = await _reserve(service, user=1, message_id=20, units="5")
    assert again.quota_used == 4 and again.quota_limit == 5  # old pool and policy again
    assert await _consumed(factory, PERSONAL_AIL_POOL_KEY, 1) == Decimal("2")  # AIL history kept
    # Other overrides keep the mode: the full form never resets it.
    from selara.application.personal_config import PersonalConfigOverride

    await _enable_ail(store)
    await store.save_override(PersonalConfigOverride(price_stars=50))
    assert (await provider.get()).ail_enabled


# --- migration 0091 -------------------------------------------------------------------------


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=_ROOT, env=env, capture_output=True,
                          text=True, timeout=600)


def _sql(dsn: str, *statements: str):
    async def run():
        connection = await asyncpg.connect(dsn)
        try:
            return [await connection.fetch(statement) for statement in statements]
        finally:
            await connection.close()

    return asyncio.run(run())


def test_migration_defaults_keep_the_old_image_working_and_downgrade_refuses_to_lose_choices():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    server = url.replace("postgresql+asyncpg://", "postgresql://")
    name = f"mig_ail_{uuid.uuid4().hex[:10]}"
    _sql(server, f'CREATE DATABASE "{name}"')
    db_dsn = server.rsplit("/", 1)[0] + "/" + name
    db_url = db_dsn.replace("postgresql://", "postgresql+asyncpg://")
    try:
        assert _alembic(db_url, "upgrade", "0090_web_tainted_history").returncode == 0
        assert (result := _alembic(db_url, "upgrade", "0091_personal_model_ail")).returncode == 0, result.stderr[-2000:]
        # The previous image inserts without the new columns.
        _sql(
            db_dsn,
            "INSERT INTO users (telegram_user_id, is_bot) VALUES (5, false)",
            "INSERT INTO personal_ai_profiles (user_id) VALUES (5)",
            "INSERT INTO selara_personal_config (id, free_daily_limit) VALUES (1, 6)",
        )
        rows = _sql(db_dsn, "SELECT model_profile_key FROM personal_ai_profiles",
                    "SELECT quota_mode FROM selara_personal_config")
        assert rows[0][0]["model_profile_key"] == "basic" and rows[1][0]["quota_mode"] is None
        with pytest.raises(asyncpg.CheckViolationError):
            _sql(db_dsn, "UPDATE selara_personal_config SET quota_mode = 'ail'")  # no budgets
        with pytest.raises(asyncpg.CheckViolationError):
            _sql(db_dsn, "UPDATE personal_ai_profiles SET model_profile_key = 'gpt-4o'")

        _sql(db_dsn, "UPDATE personal_ai_profiles SET model_profile_key = 'analytics'")
        refused = _alembic(db_url, "downgrade", "0090_web_tainted_history")
        assert refused.returncode != 0 and "Cannot downgrade 0091_personal_model_ail" in refused.stderr
        assert _sql(db_dsn, "SELECT model_profile_key FROM personal_ai_profiles")[0][0]["model_profile_key"] == "analytics"

        _sql(db_dsn, "UPDATE personal_ai_profiles SET model_profile_key = 'basic'")
        assert (result := _alembic(db_url, "downgrade", "0090_web_tainted_history")).returncode == 0, result.stderr[-2000:]
        assert (result := _alembic(db_url, "upgrade", "head")).returncode == 0, result.stderr[-2000:]
    finally:
        _sql(server, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
