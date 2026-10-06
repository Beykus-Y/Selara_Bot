from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.application.feature_access import FeatureAccessService, PersonalQuotaLimits, QuotaScope
from selara.application.personal_config import (
    CachedPersonalConfigProvider,
    PersonalConfig,
    PersonalConfigOverride,
    config_from_settings,
    merge_config,
)
from selara.core.config import Settings
from selara.infrastructure.llm.features import AiFeature

from tests.unit.test_personal_scope_foundation import _decision, _env, _reserve_personal


def _base(monkeypatch, **env: str) -> PersonalConfig:
    return config_from_settings(_env(monkeypatch, **env))


def test_base_config_comes_from_env(monkeypatch):
    config = _base(
        monkeypatch,
        SELARA_PERSONAL_PRICE_STARS="69",
        PERSONAL_FREE_DAILY_LIMIT="5",
        PERSONAL_PAID_DAILY_LIMIT="150",
        AI_QUOTA_UNIT_WEIGHTS='{"personal_chat": "2"}',
    )
    assert config.price_stars == 69 and config.duration_days == 30
    assert config.limits == PersonalQuotaLimits(5, 150)
    assert config.unit_weights == {"personal_chat": Decimal("2")}


def test_database_override_wins_per_field_and_the_rest_falls_back_to_env(monkeypatch):
    base = _base(monkeypatch, SELARA_PERSONAL_PRICE_STARS="69")
    merged = merge_config(
        base,
        PersonalConfigOverride(price_stars=99, paid_daily_limit=300, unit_weights={"personal_chat": Decimal("3")}),
    )
    assert merged.price_stars == 99
    assert merged.limits == PersonalQuotaLimits(5, 300)  # free limit still from env
    assert merged.duration_days == 30
    assert merged.pricer().price(feature=AiFeature.PERSONAL_CHAT).units == Decimal("3")
    assert merge_config(base, None) == base


def test_override_can_enable_a_product_that_env_left_unpriced(monkeypatch):
    base = _base(monkeypatch)
    assert base.price_stars is None
    assert merge_config(base, PersonalConfigOverride(price_stars=50)).price_stars == 50


@pytest.mark.parametrize(
    "kwargs",
    [{"price_stars": 0}, {"duration_days": -1}, {"free_daily_limit": 0}, {"default_units": Decimal("-1")},
     {"unit_weights": {"personal_chat": Decimal("-1")}}],
)
def test_invalid_override_values_are_rejected(kwargs):
    with pytest.raises(ValueError):
        PersonalConfigOverride(**kwargs)


def test_override_that_breaks_the_free_below_paid_invariant_is_rejected(monkeypatch):
    base = _base(monkeypatch)  # 5 / 150
    with pytest.raises(ValueError):
        merge_config(base, PersonalConfigOverride(free_daily_limit=200))


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_provider_caches_then_applies_a_changed_override_without_restart(monkeypatch):
    base = _base(monkeypatch)
    clock = _Clock()
    stored = {"value": None}
    load = AsyncMock(side_effect=lambda: stored["value"])
    provider = CachedPersonalConfigProvider(base, load, ttl_seconds=15, clock=clock)

    assert (await provider.get()).limits.paid_daily == 150
    stored["value"] = PersonalConfigOverride(paid_daily_limit=400)
    assert (await provider.get()).limits.paid_daily == 150  # still cached
    assert load.await_count == 1

    clock.now = 16  # TTL elapsed: picked up with no restart
    assert (await provider.get()).limits.paid_daily == 400

    stored["value"] = PersonalConfigOverride(paid_daily_limit=500)
    provider.invalidate()  # a save on this process applies at once
    assert (await provider.get()).limits.paid_daily == 500


@pytest.mark.asyncio
async def test_provider_survives_database_errors_with_last_good_then_env(monkeypatch):
    base = _base(monkeypatch)
    clock = _Clock()
    loader = AsyncMock(side_effect=[PersonalConfigOverride(price_stars=77), RuntimeError("db down"), RuntimeError("db down")])
    provider = CachedPersonalConfigProvider(base, loader, ttl_seconds=1, clock=clock)

    assert (await provider.get()).price_stars == 77
    clock.now = 2
    assert (await provider.get()).price_stars == 77  # last known good, not unlimited/disabled

    fresh = CachedPersonalConfigProvider(base, AsyncMock(side_effect=RuntimeError("db down")), clock=clock)
    assert (await fresh.get()) == base


@pytest.mark.asyncio
async def test_service_uses_limits_and_weights_from_the_hot_reloaded_config(monkeypatch):
    base = _base(monkeypatch)
    clock = _Clock()
    stored = {"value": PersonalConfigOverride(free_daily_limit=2, paid_daily_limit=20,
                                              unit_weights={"personal_chat": Decimal("4")})}
    provider = CachedPersonalConfigProvider(base, AsyncMock(side_effect=lambda: stored["value"]), clock=clock)
    scope = QuotaScope.user(42)
    repository = SimpleNamespace(reserve=AsyncMock(return_value=_decision(scope)))
    service = FeatureAccessService(repository, personal_config=provider)

    await _reserve_personal(service)
    kwargs = repository.reserve.await_args.kwargs
    assert kwargs["policy"].limit == 2 and kwargs["cost"].units == Decimal("4")

    stored["value"] = PersonalConfigOverride(free_daily_limit=3, paid_daily_limit=30)
    clock.now = 100
    await _reserve_personal(service)
    kwargs = repository.reserve.await_args.kwargs
    assert kwargs["policy"].limit == 3 and kwargs["cost"].units == Decimal("1")


# ----- M1: hostile or out-of-range values are validation errors, not 500s or silent zero weights -----


@pytest.mark.parametrize(
    "kwargs",
    [
        {"default_units": Decimal("NaN")},
        {"default_units": Decimal("Infinity")},
        {"default_units": Decimal("1001")},
        {"default_units": Decimal("0")},
        {"default_units": Decimal("0.001")},  # would be stored as 0.00
        {"unit_weights": {"personal_chat": Decimal("Infinity")}},
        {"unit_weights": {"personal_chat": Decimal("NaN")}},
        {"unit_weights": {"personal_chat": Decimal("0.004")}},
        {"unit_weights": {"personal_chat": Decimal("1e20")}},
        {"price_stars": 10_001},
        {"duration_days": 366},
        {"free_daily_limit": 10_001},
        {"paid_daily_limit": 10_001},
    ],
)
def test_out_of_range_or_non_finite_values_are_rejected(kwargs):
    with pytest.raises(ValueError):
        PersonalConfigOverride(**kwargs)


def test_weights_are_rounded_to_the_stored_precision():
    override = PersonalConfigOverride(default_units=Decimal("1.234"), unit_weights={"personal_chat": Decimal("0.005")})
    assert override.default_units == Decimal("1.23")
    assert override.unit_weights == {"personal_chat": Decimal("0.01")}


# ----- M2: the personal config can never price group features -----


def test_override_rejects_weights_for_non_personal_features():
    with pytest.raises(ValueError, match="personal"):
        PersonalConfigOverride(unit_weights={"llm_admin": Decimal("2")})


def test_env_weights_for_non_personal_features_fail_fast(monkeypatch):
    settings = _env(monkeypatch, AI_QUOTA_UNIT_WEIGHTS='{"daily_summary": "3"}')
    with pytest.raises(ValueError, match="personal"):
        config_from_settings(settings)


@pytest.mark.asyncio
async def test_personal_config_prices_only_personal_features():
    from selara.application.personal_config import StaticPersonalConfigProvider

    config = PersonalConfig(
        price_stars=None,
        duration_days=30,
        limits=PersonalQuotaLimits(5, 150),
        default_units=Decimal("7"),
        unit_weights={"personal_chat": Decimal("2")},
    )
    scope = QuotaScope.chat(-100)
    repository = SimpleNamespace(reserve=AsyncMock(return_value=_decision(scope)))
    service = FeatureAccessService(repository, personal_config=StaticPersonalConfigProvider(config))

    await service.reserve_feature_usage(
        feature=AiFeature.LLM_ADMIN,
        chat_id=-100,
        actor_user_id=1,
        actor_is_bot=False,
        trigger="telegram_message",
        timezone_name="UTC",
        idempotency_key="group:1",
        chat_type="supergroup",
    )

    assert repository.reserve.await_args.kwargs["cost"].units == Decimal("1")  # not 7

    await _reserve_personal(FeatureAccessService(
        repository := SimpleNamespace(reserve=AsyncMock(return_value=_decision(QuotaScope.user(42)))),
        personal_config=StaticPersonalConfigProvider(config),
    ))
    assert repository.reserve.await_args.kwargs["cost"].units == Decimal("2")


# ----- L5: a bad .env combination is reported by Settings, not by a late crash -----


def test_settings_reject_free_limit_not_below_paid(monkeypatch):
    with pytest.raises(ValueError, match="PERSONAL_FREE_DAILY_LIMIT"):
        _env(monkeypatch, PERSONAL_FREE_DAILY_LIMIT="200", PERSONAL_PAID_DAILY_LIMIT="150")
