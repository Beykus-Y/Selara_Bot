from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.application.feature_access import (
    AIL_UNIT,
    PERSONAL_AIL_POOL_KEY,
    PET_POOL_KEY,
    PersonalQuotaLimits,
    paid_pet_policy,
    resolve_feature_policy,
)
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.handlers.pet_billing import pet_reserve_units, settle_pet_line

AIL_LIMITS = PersonalQuotaLimits(free_daily=5, paid_daily=50, unit=AIL_UNIT)
REQUEST_LIMITS = PersonalQuotaLimits(free_daily=10, paid_daily=100)


@pytest.mark.parametrize("feature", [AiFeature.PET_TALK, AiFeature.PET_EVENT_TEXT])
def test_pet_policies_follow_the_personal_mode(feature) -> None:
    ail = resolve_feature_policy(feature=feature, trigger="x", personal_limits=AIL_LIMITS)
    assert ail.pool_key == PERSONAL_AIL_POOL_KEY and ail.unit == AIL_UNIT and ail.limit == 0
    paid = paid_pet_policy(feature=feature, personal_limits=AIL_LIMITS)
    assert paid.pool_key == PERSONAL_AIL_POOL_KEY and paid.unit == AIL_UNIT and paid.limit == 50
    legacy = resolve_feature_policy(feature=feature, trigger="x", personal_limits=REQUEST_LIMITS)
    assert legacy.pool_key == PET_POOL_KEY and legacy.unit != AIL_UNIT
    assert paid_pet_policy(60, feature, REQUEST_LIMITS).pool_key == PET_POOL_KEY


def _usage(cost: str | None, status: str = "succeeded"):
    return SimpleNamespace(status=status, estimated_cost_usd=None if cost is None else Decimal(cost))


def _config(*, actual: bool = True):
    return SimpleNamespace(ail_settles_actual_cost=actual, ail_usd_value=Decimal("0.0005"))


async def test_a_pet_line_settles_at_its_real_cost_and_never_raises() -> None:
    access = SimpleNamespace(adjust=AsyncMock())
    decision = SimpleNamespace(invocation_id=7, quota_unit=AIL_UNIT)
    await settle_pet_line(access, config=_config(), decision=decision, usages=[_usage("0.001")])
    access.adjust.assert_awaited_once_with(invocation_id=7, actual_units=Decimal("2.00"))

    # Not an AIL reservation, fixed billing, unknown price: the reservation stays untouched.
    for decision_, config, usages in (
        (SimpleNamespace(invocation_id=7, quota_unit="request"), _config(), [_usage("0.001")]),
        (decision, _config(actual=False), [_usage("0.001")]),
        (decision, _config(), [_usage(None)]),
        (SimpleNamespace(invocation_id=None, quota_unit=AIL_UNIT), _config(), [_usage("0.001")]),
    ):
        access.adjust.reset_mock()
        await settle_pet_line(access, config=config, decision=decision_, usages=usages)
        access.adjust.assert_not_awaited()

    broken = SimpleNamespace(adjust=AsyncMock(side_effect=RuntimeError("db")))
    await settle_pet_line(broken, config=_config(), decision=decision, usages=[_usage("0.001")])


def test_the_per_request_cap_comes_from_settings() -> None:
    assert pet_reserve_units(SimpleNamespace(pet_request_ail_cap=Decimal("2.5"))) == Decimal("2.5")
    assert pet_reserve_units(SimpleNamespace()) == Decimal("3")
