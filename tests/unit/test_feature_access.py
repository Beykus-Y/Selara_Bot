from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.application.feature_access import (
    AccessReason,
    AccessTier,
    FeatureAccessService,
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
    assert "applying normal feature quota" in caplog.text


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
