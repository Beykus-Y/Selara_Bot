from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from selara.application.feature_access import (
    AccessTier,
    FeatureAccessService,
    FeatureEntitlement,
    FeatureUsageSummary,
    quota_period_bounds,
)
from selara.application.selara_ai_status import TelegramFlagCache, build_chat_ai_access_status, resolve_telegram_flags

_NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


class _Repository:
    def __init__(self, *, used: dict[str, int] | None = None, fail: bool = False) -> None:
        self.used = used or {}
        self.fail = fail

    async def usage_summary(self, *, policy, chat_id, owner_exempt, period_start, period_end, scope=None):
        if self.fail:
            raise RuntimeError("database unavailable")
        if owner_exempt:
            return FeatureUsageSummary(
                policy.feature, "chat", str(chat_id), AccessTier.OWNER_INTERNAL,
                None, None, None, period_start, period_end, period_end, True, True, policy.policy_key,
            )
        used = self.used.get(policy.policy_key, 0)
        return FeatureUsageSummary(
            policy.feature, "chat", str(chat_id), AccessTier.FREE, policy.limit, used,
            max(0, policy.limit - used), period_start, period_end, period_end, False, False, policy.policy_key,
        )


class _Resolver:
    def __init__(self, entitlement: FeatureEntitlement | Exception) -> None:
        self.entitlement = entitlement

    async def resolve(self, *, chat_id, feature, trigger):
        if isinstance(self.entitlement, Exception):
            raise self.entitlement
        return self.entitlement


async def _status(entitlement, *, enabled=False, owner_exempt=False, repository=None, tz="UTC", can_manage=True):
    service = FeatureAccessService(repository or _Repository(), entitlement_resolver=_Resolver(entitlement))
    return await build_chat_ai_access_status(
        access_service=service,
        chat_id=-1001,
        automatic_enabled=enabled,
        owner_exempt=owner_exempt,
        timezone_name=tz,
        can_manage_purchase=can_manage,
        checkout_configured=True,
        bot_dm_url="https://t.me/selara_test_bot",
        display_timezone=tz,
        now=_NOW,
    )


@pytest.mark.asyncio
async def test_free_chat_reports_real_quotas_and_denied_automatic_summary():
    status = await _status(
        FeatureEntitlement(access_tier=AccessTier.FREE),
        repository=_Repository(used={"llm_admin_free_daily_v1": 3, "daily_summary_manual_free_monthly_v1": 2}),
    )
    assert status["state"] == "available" and status["tier"] == "free"
    assert status["entitlement"]["active"] is False and status["entitlement"]["expired_at"] is None
    assert (status["llm"]["used"], status["llm"]["limit"], status["llm"]["remaining"]) == (3, 10, 7)
    assert (status["manual_summary"]["used"], status["manual_summary"]["limit"]) == (2, 10)
    assert status["automatic_summary"] == {"enabled": False, "access_allowed": False, "state": "requires_access"}


@pytest.mark.asyncio
async def test_free_chat_with_toggle_on_still_requires_selara_ai():
    status = await _status(FeatureEntitlement(access_tier=AccessTier.FREE), enabled=True)
    assert status["automatic_summary"]["state"] == "requires_access_enabled"


@pytest.mark.asyncio
async def test_paid_chat_shows_expiry_and_separates_access_from_toggle():
    until = _NOW + timedelta(days=25)
    paid = FeatureEntitlement(access_tier=AccessTier.PAID, valid_until=until, source="telegram_stars")
    off = await _status(paid, enabled=False)
    on = await _status(paid, enabled=True)
    assert off["tier"] == "paid" and off["entitlement"]["active"] is True
    assert off["entitlement"]["valid_until"] == until.isoformat()
    assert off["entitlement"]["days_left"] == 25 and off["entitlement"]["expiring_soon"] is False
    assert off["automatic_summary"]["state"] == "available_disabled"
    assert on["automatic_summary"]["state"] == "active"
    # No paid quota exists yet: the real free limits are reported unchanged.
    assert off["llm"]["limit"] == 10 and off["manual_summary"]["limit"] == 10


@pytest.mark.asyncio
async def test_paid_chat_expiring_within_seven_days_is_flagged():
    status = await _status(
        FeatureEntitlement(access_tier=AccessTier.PAID, valid_until=_NOW + timedelta(days=2, hours=1))
    )
    assert status["entitlement"]["expiring_soon"] is True and status["entitlement"]["days_left"] == 3


@pytest.mark.asyncio
async def test_expired_entitlement_is_free_with_expired_date_even_a_millisecond_late():
    expired = _NOW - timedelta(milliseconds=1)
    status = await _status(FeatureEntitlement(access_tier=AccessTier.PAID, valid_until=expired), enabled=True)
    assert status["tier"] == "free"
    assert status["entitlement"]["active"] is False
    assert status["entitlement"]["expired_at"] == expired.isoformat()
    assert status["automatic_summary"]["state"] == "requires_access_enabled"
    assert status["llm"]["limit"] == 10


@pytest.mark.asyncio
async def test_owner_internal_has_no_commercial_limit_and_no_entitlement():
    status = await _status(FeatureEntitlement(access_tier=AccessTier.FREE), owner_exempt=True, enabled=True)
    assert status["tier"] == "owner_internal"
    assert status["llm"]["status"] == "unlimited" and status["llm"]["limit"] is None
    assert status["manual_summary"]["status"] == "unlimited"
    assert status["entitlement"]["active"] is False and status["entitlement"]["valid_until"] is None
    assert status["entitlement"]["source"] == "owner_admin"
    assert status["automatic_summary"]["state"] == "active"


@pytest.mark.asyncio
async def test_resolver_failure_is_unavailable_not_free():
    status = await _status(RuntimeError("entitlements down"))
    assert status["state"] == "unavailable"
    assert status["tier"] is None
    assert status["llm"]["status"] == "unavailable" and status["llm"]["used"] is None
    assert status["automatic_summary"]["state"] == "unknown"


@pytest.mark.asyncio
async def test_quota_backend_failure_does_not_become_zero_used():
    status = await _status(FeatureEntitlement(access_tier=AccessTier.FREE), repository=_Repository(fail=True))
    assert status["state"] == "available"
    assert status["llm"]["status"] == "unavailable" and status["llm"]["used"] is None
    assert status["manual_summary"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_exhausted_quota_is_flagged():
    status = await _status(
        FeatureEntitlement(access_tier=AccessTier.FREE),
        repository=_Repository(used={"llm_admin_free_daily_v1": 10}),
    )
    assert status["llm"]["remaining"] == 0 and status["llm"]["exhausted"] is True


@pytest.mark.asyncio
async def test_quota_reset_follows_bot_timezone_day_and_month_boundaries():
    # 2026-10-05 12:00 UTC is 15:00 in Moscow, so the day resets at 2026-10-05 21:00 UTC.
    status = await _status(FeatureEntitlement(access_tier=AccessTier.FREE), tz="Europe/Moscow")
    assert status["llm"]["reset_at"] == datetime(2026, 10, 5, 21, 0, tzinfo=timezone.utc).isoformat()
    assert status["manual_summary"]["reset_at"] == datetime(2026, 10, 31, 21, 0, tzinfo=timezone.utc).isoformat()
    assert status["timezone"] == "Europe/Moscow"


def test_quota_period_bounds_December_rolls_to_next_year():
    from selara.application.feature_access import FeatureQuotaPolicy, QuotaPeriod
    from selara.infrastructure.llm.features import AiFeature

    policy = FeatureQuotaPolicy(AiFeature.DAILY_SUMMARY, "k", 10, QuotaPeriod.MONTH)
    start, end = quota_period_bounds(policy=policy, now=datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc), timezone_name="UTC")
    assert (start, end) == (datetime(2026, 12, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc))


class _Bot:
    def __init__(self, admins: set[int], *, fail: bool = False) -> None:
        self.admins = admins
        self.fail = fail
        self.calls: list[int] = []

    async def get_chat_member(self, *, chat_id, user_id):
        self.calls.append(user_id)
        if self.fail:
            raise RuntimeError("telegram down")
        return user_id


async def _flags(bot, cache, *, user_id=77, admin_user_id=900):
    return await resolve_telegram_flags(
        bot=bot, chat_id=-1001, user_id=user_id, admin_user_id=admin_user_id,
        is_admin=lambda member: member in bot.admins, cache=cache,
    )


@pytest.mark.asyncio
async def test_telegram_flags_are_cached_per_chat_and_user_until_ttl():
    now = [0.0]
    cache = TelegramFlagCache(ttl_seconds=45, clock=lambda: now[0])
    bot = _Bot({77})
    assert await _flags(bot, cache) == (False, True)
    assert await _flags(bot, cache) == (False, True)
    assert sorted(bot.calls) == [77, 900]
    assert await _flags(bot, cache, user_id=78) == (False, False)  # other user: one new lookup
    assert sorted(bot.calls) == [77, 78, 900]
    now[0] = 46.0
    await _flags(bot, cache)
    assert len(bot.calls) == 5  # expired entries are looked up again


@pytest.mark.asyncio
async def test_telegram_flag_failures_are_not_cached_and_not_treated_as_admin():
    cache = TelegramFlagCache()
    broken = _Bot({77, 900}, fail=True)
    assert await _flags(broken, cache) == (False, False)
    healthy = _Bot({77})
    assert await _flags(healthy, cache) == (False, True)  # the failure was not cached
    assert len(healthy.calls) == 2


@pytest.mark.asyncio
async def test_owner_exempt_chat_hides_purchase_and_owner_viewer_needs_one_lookup():
    cache = TelegramFlagCache()
    bot = _Bot({900, 77})
    assert await _flags(bot, cache) == (True, False)
    owner_bot = _Bot({900})
    assert await _flags(owner_bot, TelegramFlagCache(), user_id=900) == (True, False)
    assert owner_bot.calls == [900]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_exempt,tier", [(False, AccessTier.PAID), (True, AccessTier.FREE)])
async def test_provider_unavailable_replaces_active_state(owner_exempt, tier):
    service = FeatureAccessService(
        _Repository(), entitlement_resolver=_Resolver(FeatureEntitlement(access_tier=tier, valid_until=_NOW + timedelta(days=30)))
    )
    kwargs = dict(
        access_service=service, chat_id=-1001, automatic_enabled=True, owner_exempt=owner_exempt,
        timezone_name="UTC", can_manage_purchase=True, checkout_configured=True,
        bot_dm_url="https://t.me/x", display_timezone="UTC", now=_NOW,
    )
    down = await build_chat_ai_access_status(**kwargs, provider_available=False)
    assert down["provider_available"] is False
    assert down["automatic_summary"]["state"] == "provider_unavailable"
    up = await build_chat_ai_access_status(**kwargs, provider_available=True)
    assert up["automatic_summary"]["state"] == "active"


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_model": ""},
        {"llm_summary_model": ""},
        {"llm_summary_model": "  "},
        {"llm_timeout_seconds": 0},
        {"llm_timeout_seconds": -1},
        {"llm_api_key": "   "},
        {"llm_enabled": False},
    ],
)
def test_llm_runtime_config_rejects_unusable_settings_consistently(overrides):
    from types import SimpleNamespace

    from selara.application.selara_ai_status import checkout_ready
    from selara.infrastructure.llm.runtime import llm_runtime_config
    from selara.presentation.handlers.premium import SelaraAiProductUnavailable, _product_for_settings

    good = dict(
        llm_enabled=True, llm_api_key="key", llm_model="m", llm_base_url="", llm_timeout_seconds=30.0,
        llm_summary_model="s", llm_supports_structured_output=False, selara_ai_price_stars=100,
    )
    assert llm_runtime_config(SimpleNamespace(**good)) is not None
    assert checkout_ready(SimpleNamespace(**good)) is True
    _product_for_settings(SimpleNamespace(**good))

    bad = SimpleNamespace(**{**good, **overrides})
    assert llm_runtime_config(bad) is None
    assert checkout_ready(bad) is False
    with pytest.raises(SelaraAiProductUnavailable):
        _product_for_settings(bad)


@pytest.mark.asyncio
async def test_concurrent_cache_misses_share_one_telegram_lookup():
    import asyncio

    calls: list[int] = []
    release = asyncio.Event()

    class SlowBot:
        async def get_chat_member(self, *, chat_id, user_id):
            calls.append(user_id)
            await release.wait()
            return user_id

        admins = {77, 900}

    bot = SlowBot()
    cache = TelegramFlagCache()
    tasks = [asyncio.create_task(_flags(bot, cache)) for _ in range(5)]
    await asyncio.sleep(0.01)
    release.set()
    results = await asyncio.gather(*tasks)

    assert all(result == (True, False) for result in results)
    assert sorted(calls) == [77, 900]  # one lookup per target, not one per concurrent request
    assert cache.inflight == {}
