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
    )
    assert config.price_stars == 69 and config.duration_days == 30
    assert config.limits == PersonalQuotaLimits(5, 150)


def test_database_override_wins_per_field_and_the_rest_falls_back_to_env(monkeypatch):
    base = _base(monkeypatch, SELARA_PERSONAL_PRICE_STARS="69")
    merged = merge_config(base, PersonalConfigOverride(price_stars=99, paid_daily_limit=300))
    assert merged.price_stars == 99
    assert merged.limits == PersonalQuotaLimits(5, 300)  # free limit still from env
    assert merged.duration_days == 30
    assert merge_config(base, None) == base


def test_override_can_enable_a_product_that_env_left_unpriced(monkeypatch):
    base = _base(monkeypatch)
    assert base.price_stars is None
    assert merge_config(base, PersonalConfigOverride(price_stars=50)).price_stars == 50


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
async def test_service_uses_the_hot_reloaded_limits_and_always_one_unit(monkeypatch):
    base = _base(monkeypatch)
    clock = _Clock()
    stored = {"value": PersonalConfigOverride(free_daily_limit=2, paid_daily_limit=20)}
    provider = CachedPersonalConfigProvider(base, AsyncMock(side_effect=lambda: stored["value"]), clock=clock)
    scope = QuotaScope.user(42)
    repository = SimpleNamespace(reserve=AsyncMock(return_value=_decision(scope)))
    service = FeatureAccessService(repository, personal_config=provider)

    await _reserve_personal(service)
    kwargs = repository.reserve.await_args.kwargs
    assert kwargs["policy"].limit == 2 and kwargs["cost"].units == Decimal("1")

    stored["value"] = PersonalConfigOverride(free_daily_limit=3, paid_daily_limit=30)
    clock.now = 100
    await _reserve_personal(service)
    kwargs = repository.reserve.await_args.kwargs
    assert kwargs["policy"].limit == 3 and kwargs["cost"].units == Decimal("1")


# ----- hostile or out-of-range values are validation errors, not 500s -----


@pytest.mark.parametrize(
    "kwargs",
    [
        {"price_stars": 0},
        {"price_stars": 10_001},
        {"duration_days": 0},
        {"duration_days": 366},
        {"free_daily_limit": 0},
        {"free_daily_limit": 10_001},
        {"paid_daily_limit": -1},
        {"paid_daily_limit": 10_001},
    ],
)
def test_out_of_range_values_are_rejected(kwargs):
    with pytest.raises(ValueError):
        PersonalConfigOverride(**kwargs)


def test_override_has_no_unit_weight_fields_so_ail_cannot_leak_into_personal_quota():
    with pytest.raises(TypeError):
        PersonalConfigOverride(default_units=Decimal("2"))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        PersonalConfigOverride(unit_weights={"personal_chat": Decimal("2")})  # type: ignore[call-arg]
    assert not hasattr(Settings, "ai_quota_unit_weights") and not hasattr(Settings, "ai_quota_default_units")


# ----- L5: a bad .env combination is reported by Settings, not by a late crash -----


def test_settings_reject_free_limit_not_below_paid(monkeypatch):
    with pytest.raises(ValueError, match="PERSONAL_FREE_DAILY_LIMIT"):
        _env(monkeypatch, PERSONAL_FREE_DAILY_LIMIT="200", PERSONAL_PAID_DAILY_LIMIT="150")


# ----- personal memory tunables: base in .env, override in the database -----


def test_memory_base_values_come_from_env_with_safe_defaults(monkeypatch):
    default = _base(monkeypatch)
    assert (default.memory_free_limit, default.memory_paid_limit) == (20, 200)
    assert default.memory_auto_extract is False and default.memory_extract_every == 10

    custom = _base(
        monkeypatch,
        PERSONAL_MEMORY_FREE_LIMIT="7",
        PERSONAL_MEMORY_PAID_LIMIT="70",
        PERSONAL_MEMORY_AUTO_EXTRACT="true",
        PERSONAL_MEMORY_EXTRACT_EVERY="4",
    )
    assert (custom.memory_free_limit, custom.memory_paid_limit) == (7, 70)
    assert custom.memory_auto_extract is True and custom.memory_extract_every == 4


def test_memory_override_wins_per_field_and_false_is_a_real_override(monkeypatch):
    base = _base(monkeypatch, PERSONAL_MEMORY_AUTO_EXTRACT="true")

    merged = merge_config(base, PersonalConfigOverride(memory_paid_limit=500, memory_auto_extract=False))

    assert merged.memory_paid_limit == 500 and merged.memory_free_limit == 20
    assert merged.memory_auto_extract is False  # False must not fall back to the .env True
    assert merge_config(base, PersonalConfigOverride()).memory_auto_extract is True


def test_memory_override_cannot_make_free_exceed_paid(monkeypatch):
    base = _base(monkeypatch)  # 20 / 200
    with pytest.raises(ValueError):
        merge_config(base, PersonalConfigOverride(memory_free_limit=300))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"memory_free_limit": 0},
        {"memory_paid_limit": -1},
        {"memory_free_limit": 100_000},
        {"memory_extract_every": 1},
        {"memory_extract_every": 41},  # larger than one extraction batch could ever satisfy (review M2)
        {"memory_extract_every": 100_000},
    ],
)
def test_memory_override_values_are_bounded(kwargs):
    with pytest.raises(ValueError):
        PersonalConfigOverride(**kwargs)


def test_settings_reject_memory_free_limit_above_paid(monkeypatch):
    with pytest.raises(ValueError, match="PERSONAL_MEMORY_FREE_LIMIT"):
        _env(monkeypatch, PERSONAL_MEMORY_FREE_LIMIT="300", PERSONAL_MEMORY_PAID_LIMIT="200")


def test_settings_reject_extract_interval_above_the_batch_size(monkeypatch):
    with pytest.raises(ValueError):
        _env(monkeypatch, PERSONAL_MEMORY_EXTRACT_EVERY="41")
    assert _base(monkeypatch, PERSONAL_MEMORY_EXTRACT_EVERY="40").memory_extract_every == 40
