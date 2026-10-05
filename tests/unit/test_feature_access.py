from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.application.feature_access import (
    AccessReason,
    AccessTier,
    FeatureAccessDecision,
    FeatureAccessService,
    FeatureEntitlement,
    FeatureQuotaPolicy,
    FeatureUsageSummary,
    NoPaidChatEntitlementResolver,
    QuotaPeriod,
    quota_period_bounds,
    resolve_feature_policy,
)
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.auth import is_telegram_chat_admin, resolve_owner_admin_exemption


def test_llm_admin_has_one_shared_daily_chat_policy():
    policy = resolve_feature_policy(feature=AiFeature.LLM_ADMIN, trigger="telegram_message")

    assert policy is not None
    assert policy.limit == 10
    assert policy.period == QuotaPeriod.DAY
    assert policy.policy_key == "llm_admin_free_daily_v1"


def test_daily_summary_policy_only_applies_to_manual_trigger():
    policy = resolve_feature_policy(feature=AiFeature.DAILY_SUMMARY, trigger="manual")

    assert policy is not None
    assert policy.limit == 10
    assert policy.period == QuotaPeriod.MONTH
    assert resolve_feature_policy(feature=AiFeature.DAILY_SUMMARY, trigger="scheduled") is None


@pytest.mark.parametrize("feature", [AiFeature.AUTOCONFIG, AiFeature.LLM_CONTEXT_COMPRESSION])
def test_internal_ai_features_have_no_commercial_quota(feature):
    assert resolve_feature_policy(feature=feature, trigger="internal") is None


def test_unsupported_daily_summary_trigger_is_not_silently_unlimited():
    with pytest.raises(ValueError, match="Unsupported Daily Summary trigger"):
        resolve_feature_policy(feature=AiFeature.DAILY_SUMMARY, trigger="unexpected")


@pytest.mark.asyncio
async def test_default_entitlement_resolver_fails_closed_without_reserving_quota():
    repository = SimpleNamespace(reserve=AsyncMock(side_effect=AssertionError("must not reserve")))
    service = FeatureAccessService(repository, entitlement_resolver=NoPaidChatEntitlementResolver())

    decision = await service.resolve_feature_access(
        feature=AiFeature.DAILY_SUMMARY,
        chat_id=-100500,
        trigger="scheduled",
    )

    assert not decision.allowed
    assert decision.reason == AccessReason.ACCESS_REQUIRED
    assert decision.access_tier == AccessTier.FREE
    repository.reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_paid_entitlement_is_typed_and_kept_separate_from_quota():
    resolver = SimpleNamespace(
        resolve=AsyncMock(return_value=FeatureEntitlement(
            access_tier=AccessTier.PAID,
            valid_until=datetime(2026, 11, 1, tzinfo=timezone.utc),
            source="test_entitlement",
        )),
    )
    service = FeatureAccessService(SimpleNamespace(), entitlement_resolver=resolver)

    decision = await service.resolve_feature_access(
        feature=AiFeature.DAILY_SUMMARY,
        chat_id=-100500,
        trigger="scheduled",
        now=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )

    assert decision.allowed
    assert decision.access_tier == AccessTier.PAID
    assert decision.quota_limit is None
    assert decision.entitlement_source == "test_entitlement"
    assert decision.entitlement_valid_until == datetime(2026, 11, 1, tzinfo=timezone.utc)
    resolver.resolve.assert_awaited_once_with(
        chat_id=-100500,
        feature=AiFeature.DAILY_SUMMARY,
        trigger="scheduled",
    )


@pytest.mark.asyncio
async def test_paid_manual_quota_is_supplied_as_policy_without_a_product_default():
    paid_policy = FeatureQuotaPolicy(
        feature=AiFeature.DAILY_SUMMARY,
        policy_key="test_paid_manual_policy",
        limit=17,
        period=QuotaPeriod.MONTH,
    )
    entitlement = FeatureEntitlement(
        access_tier=AccessTier.PAID,
        valid_until=datetime(2026, 11, 1, tzinfo=timezone.utc),
        source="test_only",
        quota_policy=paid_policy,
    )
    free_decision = FeatureAccessDecision(
        allowed=True,
        feature=AiFeature.DAILY_SUMMARY,
        scope_type="chat",
        scope_id="-100500",
        access_tier=AccessTier.FREE,
        quota_limit=17,
        quota_used=1,
        quota_remaining=16,
        period_start=datetime(2026, 10, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 11, 1, tzinfo=timezone.utc),
        policy_key=paid_policy.policy_key,
    )
    usage = FeatureUsageSummary(
        feature=AiFeature.DAILY_SUMMARY,
        scope_type="chat",
        scope_id="-100500",
        access_tier=AccessTier.FREE,
        quota_limit=17,
        quota_used=1,
        quota_remaining=16,
        period_start=datetime(2026, 10, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 11, 1, tzinfo=timezone.utc),
        reset_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        unlimited=False,
        owner_exempt=False,
        policy_key=paid_policy.policy_key,
    )
    repository = SimpleNamespace(
        reserve=AsyncMock(return_value=free_decision),
        usage_summary=AsyncMock(return_value=usage),
    )
    resolver = SimpleNamespace(resolve=AsyncMock(return_value=entitlement))
    service = FeatureAccessService(repository, entitlement_resolver=resolver)
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)

    decision = await service.reserve_feature_usage(
        feature=AiFeature.DAILY_SUMMARY,
        chat_id=-100500,
        actor_user_id=123,
        trigger="manual",
        timezone_name="UTC",
        idempotency_key="daily_summary:run:42",
        now=now,
    )
    summary = await service.get_usage_summary(
        feature=AiFeature.DAILY_SUMMARY,
        chat_id=-100500,
        trigger="manual",
        timezone_name="UTC",
        now=now,
    )

    assert repository.reserve.await_args.kwargs["policy"] == paid_policy
    assert repository.reserve.await_args.kwargs["access_tier"] == AccessTier.PAID
    assert repository.usage_summary.await_args.kwargs["policy"] == paid_policy
    assert decision.access_tier == AccessTier.PAID
    assert decision.entitlement_source == "test_only"
    assert summary.access_tier == AccessTier.PAID
    assert summary.quota_limit == 17


@pytest.mark.asyncio
async def test_paid_tier_keeps_existing_free_manual_limit_until_a_paid_policy_is_defined():
    decision_from_repository = FeatureAccessDecision(
        allowed=True,
        feature=AiFeature.DAILY_SUMMARY,
        scope_type="chat",
        scope_id="-100500",
        access_tier=AccessTier.FREE,
        quota_limit=10,
        quota_used=1,
        quota_remaining=9,
        period_start=datetime(2026, 10, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 11, 1, tzinfo=timezone.utc),
        policy_key="daily_summary_manual_free_monthly_v1",
    )
    repository = SimpleNamespace(reserve=AsyncMock(return_value=decision_from_repository))
    resolver = SimpleNamespace(resolve=AsyncMock(return_value=FeatureEntitlement(
        access_tier=AccessTier.PAID,
        valid_until=datetime(2026, 11, 1, tzinfo=timezone.utc),
        source="telegram_stars",
    )))
    service = FeatureAccessService(repository, entitlement_resolver=resolver)

    decision = await service.reserve_feature_usage(
        feature=AiFeature.DAILY_SUMMARY,
        chat_id=-100500,
        actor_user_id=None,
        trigger="manual",
        timezone_name="UTC",
        idempotency_key="daily_summary:run:paid-free-policy",
        now=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )

    assert repository.reserve.await_args.kwargs["policy"].limit == 10
    assert repository.reserve.await_args.kwargs["access_tier"] == AccessTier.PAID
    assert decision.access_tier == AccessTier.PAID
    assert decision.quota_limit == 10
    assert decision.entitlement_source == "telegram_stars"


@pytest.mark.asyncio
async def test_paid_llm_admin_keeps_daily_free_quota_but_resolves_paid_tier():
    decision_from_repository = FeatureAccessDecision(
        allowed=True,
        feature=AiFeature.LLM_ADMIN,
        scope_type="chat",
        scope_id="-100500",
        access_tier=AccessTier.FREE,
        quota_limit=10,
        quota_used=1,
        quota_remaining=9,
        period_start=datetime(2026, 10, 5, tzinfo=timezone.utc),
        period_end=datetime(2026, 10, 6, tzinfo=timezone.utc),
        policy_key="llm_admin_free_daily_v1",
    )
    repository = SimpleNamespace(reserve=AsyncMock(return_value=decision_from_repository))
    resolver = SimpleNamespace(resolve=AsyncMock(return_value=FeatureEntitlement(
        access_tier=AccessTier.PAID,
        valid_until=datetime(2026, 11, 1, tzinfo=timezone.utc),
        source="telegram_stars",
    )))
    service = FeatureAccessService(repository, entitlement_resolver=resolver)

    decision = await service.reserve_feature_usage(
        feature=AiFeature.LLM_ADMIN,
        chat_id=-100500,
        actor_user_id=None,
        trigger="telegram_message",
        timezone_name="UTC",
        idempotency_key="llm_admin:paid-free-policy",
        now=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )

    assert repository.reserve.await_args.kwargs["policy"].limit == 10
    assert repository.reserve.await_args.kwargs["access_tier"] == AccessTier.PAID
    assert decision.access_tier == AccessTier.PAID


@pytest.mark.asyncio
async def test_expired_paid_entitlement_and_resolver_errors_fail_closed():
    resolver = SimpleNamespace(
        resolve=AsyncMock(return_value=FeatureEntitlement(
            access_tier=AccessTier.PAID,
            valid_until=datetime(2026, 10, 4, tzinfo=timezone.utc),
            source="test_entitlement",
        )),
    )
    service = FeatureAccessService(SimpleNamespace(), entitlement_resolver=resolver)
    expired = await service.resolve_feature_access(
        feature=AiFeature.DAILY_SUMMARY,
        chat_id=-100500,
        trigger="scheduled",
        now=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )
    resolver.resolve.side_effect = RuntimeError("entitlement backend unavailable")
    unavailable = await service.resolve_feature_access(
        feature=AiFeature.DAILY_SUMMARY,
        chat_id=-100500,
        trigger="scheduled",
    )

    assert not expired.allowed and expired.reason == AccessReason.ACCESS_REQUIRED
    assert not unavailable.allowed and unavailable.reason == AccessReason.ACCESS_UNAVAILABLE


@pytest.mark.asyncio
async def test_owner_internal_access_bypasses_entitlement_source():
    resolver = SimpleNamespace(resolve=AsyncMock(side_effect=AssertionError("must not resolve")))
    service = FeatureAccessService(SimpleNamespace(), entitlement_resolver=resolver)

    decision = await service.resolve_feature_access(
        feature=AiFeature.DAILY_SUMMARY,
        chat_id=-100500,
        trigger="scheduled",
        owner_exempt=True,
    )

    assert decision.allowed
    assert decision.access_tier == AccessTier.OWNER_INTERNAL
    assert decision.owner_exempt
    assert decision.entitlement_source == "owner_admin"
    resolver.resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_summary_status_exposes_automatic_access_and_manual_quota():
    period_end = datetime(2026, 11, 1, tzinfo=timezone.utc)
    usage = FeatureUsageSummary(
        feature=AiFeature.DAILY_SUMMARY,
        scope_type="chat",
        scope_id="-100500",
        access_tier=AccessTier.FREE,
        quota_limit=10,
        quota_used=4,
        quota_remaining=6,
        period_start=datetime(2026, 10, 1, tzinfo=timezone.utc),
        period_end=period_end,
        reset_at=period_end,
        unlimited=False,
        owner_exempt=False,
        policy_key="daily_summary_manual_free_monthly_v1",
    )
    repository = SimpleNamespace(usage_summary=AsyncMock(return_value=usage))
    service = FeatureAccessService(repository)

    status = await service.get_daily_summary_access_status(
        chat_id=-100500,
        automatic_enabled=True,
        owner_exempt=False,
        timezone_name="UTC",
        now=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )

    assert status.automatic_enabled
    assert not status.automatic_access_allowed
    assert status.manual_used == 4
    assert status.manual_limit == 10
    assert status.manual_remaining == 6
    assert status.reset_at == period_end
    assert status.access_tier == AccessTier.FREE
    assert status.automatic_reason == AccessReason.ACCESS_REQUIRED


def test_daily_period_uses_bot_timezone_midnight():
    policy = resolve_feature_policy(feature=AiFeature.LLM_ADMIN, trigger="telegram_message")
    start, end = quota_period_bounds(
        policy=policy,
        now=datetime(2026, 10, 5, 16, 30, tzinfo=timezone.utc),
        timezone_name="Asia/Barnaul",
    )

    assert start == datetime(2026, 10, 4, 17, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 5, 17, 0, tzinfo=timezone.utc)


def test_monthly_period_uses_calendar_month_in_bot_timezone():
    policy = resolve_feature_policy(feature=AiFeature.DAILY_SUMMARY, trigger="manual")
    start, end = quota_period_bounds(
        policy=policy,
        now=datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc),
        timezone_name="Asia/Barnaul",
    )

    assert start == datetime(2026, 9, 30, 17, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc)


def test_daily_period_accounts_for_daylight_saving_boundary():
    policy = resolve_feature_policy(feature=AiFeature.LLM_ADMIN, trigger="telegram_message")
    start, end = quota_period_bounds(
        policy=policy,
        now=datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc),
        timezone_name="America/New_York",
    )

    assert start == datetime(2026, 3, 8, 5, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 3, 9, 4, 0, tzinfo=timezone.utc)
    assert end - start == timedelta(hours=23)


def test_owner_admin_detection_uses_telegram_status_values():
    assert is_telegram_chat_admin(SimpleNamespace(status="creator"))
    assert is_telegram_chat_admin(SimpleNamespace(status="administrator"))
    assert not is_telegram_chat_admin(SimpleNamespace(status="member"))
    assert not is_telegram_chat_admin(SimpleNamespace(status="left"))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["creator", "administrator"])
async def test_owner_admin_exemption_requires_live_telegram_admin_status(status):
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status=status)))

    assert await resolve_owner_admin_exemption(bot=bot, chat_id=-100500, admin_user_id=123)
    bot.get_chat_member.assert_awaited_once_with(chat_id=-100500, user_id=123)


@pytest.mark.asyncio
async def test_owner_non_admin_and_telegram_failure_do_not_get_exemption(caplog):
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))
    assert not await resolve_owner_admin_exemption(bot=bot, chat_id=-100500, admin_user_id=123)

    bot.get_chat_member.side_effect = RuntimeError("Telegram temporarily unavailable")
    assert not await resolve_owner_admin_exemption(bot=bot, chat_id=-100500, admin_user_id=123)
    assert "applying normal feature access policy" in caplog.text


@pytest.mark.asyncio
async def test_known_no_quota_feature_returns_typed_unlimited_decision_without_repository_call():
    repository = SimpleNamespace(reserve=AsyncMock(side_effect=AssertionError("must not reserve")))
    service = FeatureAccessService(repository)

    decision = await service.reserve_feature_usage(
        feature=AiFeature.AUTOCONFIG,
        chat_id=-100500,
        actor_user_id=123,
        trigger="telegram_message",
        timezone_name="UTC",
        idempotency_key="autoconfig:-100500:91",
    )

    assert decision.allowed
    assert decision.reason == AccessReason.NO_COMMERCIAL_QUOTA
    assert decision.unlimited
    assert decision.access_tier == AccessTier.FREE
    repository.reserve.assert_not_awaited()
