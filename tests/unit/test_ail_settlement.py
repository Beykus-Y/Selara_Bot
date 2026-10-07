"""Dynamic AIL billing: cost -> AIL, provider cost extraction and the handler's settlement step."""

from __future__ import annotations

import logging
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.application.feature_access import MAX_AIL_REQUEST_UNITS, ail_units_from_cost_usd
from selara.application.personal_config import (
    DEFAULT_AIL_USD_VALUE,
    PersonalConfig,
    config_from_settings,
)
from selara.application.feature_access import PersonalQuotaLimits
from selara.core.config import Settings
from selara.infrastructure.llm.client import LlmCallUsage, LlmClient, LlmConfig, _provider_reported_cost
from selara.infrastructure.llm.runtime import llm_runtime_problem
from selara.presentation.handlers.personal_ai import _settle_chat_turn, chat_turn_cost_usd, failed_turn_cost_usd

PER_AIL = Decimal("0.0005")


@pytest.mark.parametrize(
    ("cost", "expected"),
    [
        ("0.000273", "0.55"),   # GLM 3000/400
        ("0.0005", "1.00"),
        ("0.00475", "9.50"),
        ("0.01", "20.00"),
        ("0.000501", "1.01"),   # always rounded up
        ("0.0000001", "0.01"),  # never below the 0.01 quantum
        ("0", "0.01"),          # a free call still costs the minimum
        ("100000", str(MAX_AIL_REQUEST_UNITS) + ".00"),  # capped at what one request may weigh
    ],
)
def test_ail_units_are_the_cost_rounded_up_to_a_hundredth(cost, expected):
    assert ail_units_from_cost_usd(Decimal(cost), PER_AIL) == Decimal(expected)


@pytest.mark.parametrize("cost", [Decimal("-0.1"), Decimal("NaN"), Decimal("Infinity"), 0.1, None])
def test_ail_units_reject_unusable_costs(cost):
    with pytest.raises(ValueError):
        ail_units_from_cost_usd(cost, PER_AIL)


@pytest.mark.parametrize("rate", [Decimal("0"), Decimal("-1"), Decimal("NaN"), 0.0005])
def test_ail_units_reject_unusable_rates(rate):
    with pytest.raises(ValueError):
        ail_units_from_cost_usd(Decimal("1"), rate)


def _usage(*, cost, status="succeeded"):
    return LlmCallUsage("c", "m", 1, 1, 2, None if cost is None else Decimal(cost), "known", 1, status)


def test_turn_cost_sums_successful_calls_and_ignores_failed_attempts():
    usages = [_usage(cost=None, status="failed"), _usage(cost="0.0002"), _usage(cost="0.0001")]
    assert chat_turn_cost_usd(usages) == Decimal("0.0003")


@pytest.mark.parametrize("usages", [[], [_usage(cost=None, status="failed")], [_usage(cost="0.1"), _usage(cost=None)]])
def test_turn_cost_is_unknown_when_nothing_or_something_is_unpriced(usages):
    assert chat_turn_cost_usd(usages) is None


# --- provider cost -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0.000273, "0.000273000"), ("0.0005", "0.000500000"), (0, "0E-9"), (1, "1.000000000")],
)
def test_provider_cost_is_read_from_the_usage_object(raw, expected):
    assert _provider_reported_cost(SimpleNamespace(cost=raw)) == Decimal(expected)


def test_provider_cost_falls_back_to_pydantic_extras():
    assert _provider_reported_cost(SimpleNamespace(model_extra={"cost": 0.25})) == Decimal("0.25")


@pytest.mark.parametrize("raw", [None, True, "abc", -1, float("nan"), float("inf"), 10**9])
def test_unusable_provider_costs_are_ignored(raw):
    assert _provider_reported_cost(SimpleNamespace(cost=raw)) is None
    assert _provider_reported_cost(None) is None


def _response(**usage):
    return SimpleNamespace(model="x/y", usage=SimpleNamespace(prompt_tokens=3000, completion_tokens=400, total_tokens=3400, **usage))


def test_the_provider_cost_wins_over_the_estimate_and_is_recorded():
    usage = LlmClient._usage("chat_simple", _response(cost=0.0004), model="x/y", attempt=1)
    assert usage.provider_cost_usd == Decimal("0.000400000") and usage.estimated_cost_usd == usage.provider_cost_usd


def test_without_provider_cost_the_token_estimate_stays():
    usage = LlmClient._usage("chat_simple", _response(), model="gpt-4o-mini", attempt=1)
    assert usage.provider_cost_usd is None
    assert usage.estimated_cost_usd is not None  # legacy pricing table: 3000 in / 400 out


def _client(**config):
    return LlmClient(LlmConfig(api_key="k", model="m", **config))


def test_request_options_are_added_only_when_asked_for():
    plain = {"model": "m", "messages": []}
    assert _client()._with_provider_options(plain) is plain
    asked = _client(include_usage_cost=True)._with_provider_options(plain)
    assert asked["extra_body"] == {"usage": {"include": True}} and "extra_body" not in plain
    routed = _client(include_usage_cost=True, provider_preferences={"max_price": {"prompt": 1}})._with_provider_options(
        {**plain, "extra_body": {"custom": 1, "usage": {"include": False}}}
    )
    # A caller's own value is never overridden; the preferences ride along.
    assert routed["extra_body"] == {"custom": 1, "usage": {"include": False}, "provider": {"max_price": {"prompt": 1}}}


def test_group_provider_preferences_apply_only_to_group_features():
    plain = {"model": "m", "messages": []}
    client = _client(
        provider_preferences={"max_price": {"prompt": 1}},
        group_provider_preferences={"order": ["DeepInfra"], "allow_fallbacks": False},
    )
    group = client._with_provider_options(plain, route=False, group_route=True)
    assert group["extra_body"] == {"provider": {"order": ["DeepInfra"], "allow_fallbacks": False}}
    personal = client._with_provider_options(plain, route=True)
    assert personal["extra_body"] == {"provider": {"max_price": {"prompt": 1}}}
    assert client._with_provider_options(plain, route=False) is plain


def test_runtime_detects_openrouter_and_validates_preferences(monkeypatch):
    base = dict(_env_file=None, bot_token="1:x", database_url="sqlite:///", llm_enabled=True, llm_api_key="k")
    config, problem = llm_runtime_problem(Settings(**base, llm_base_url="https://openrouter.ai/api/v1"))
    assert problem is None and config.include_usage_cost is True and config.provider_preferences is None
    assert llm_runtime_problem(Settings(**base, llm_base_url="https://api.openai.com/v1"))[0].include_usage_cost is False
    forced = llm_runtime_problem(Settings(**base, llm_include_usage_cost=False, llm_base_url="https://openrouter.ai/api/v1"))
    assert forced[0].include_usage_cost is False
    prefs = llm_runtime_problem(Settings(**base, llm_provider_preferences_json='{"max_price": {"prompt": 0.5}}'))
    assert prefs[0].provider_preferences == {"max_price": {"prompt": 0.5}}
    for bad in ("not json", "[1]"):
        config, problem = llm_runtime_problem(Settings(**base, llm_provider_preferences_json=bad))
        assert config is None and "LLM_PROVIDER_PREFERENCES_JSON" in problem


# --- configuration -------------------------------------------------------------------------------


def test_billing_defaults_to_actual_cost_at_half_a_tenth_of_a_cent():
    settings = Settings(_env_file=None, bot_token="1:x", database_url="sqlite:///")
    config = config_from_settings(settings)
    assert config.ail_billing == "actual" and config.ail_usd_value == DEFAULT_AIL_USD_VALUE == PER_AIL
    ail = PersonalConfig(None, 30, config.limits, quota_mode="ail", ail_limits=PersonalQuotaLimits(6, 60, unit="ail"))
    assert ail.ail_settles_actual_cost
    assert not PersonalConfig(None, 30, config.limits).ail_settles_actual_cost  # requests mode never settles
    fixed = PersonalConfig(None, 30, config.limits, quota_mode="ail", ail_billing="fixed",
                           ail_limits=PersonalQuotaLimits(6, 60, unit="ail"))
    assert not fixed.ail_settles_actual_cost


def test_bad_billing_settings_are_rejected():
    limits = config_from_settings(Settings(_env_file=None, bot_token="1:x", database_url="sqlite:///")).limits
    with pytest.raises(ValueError):
        PersonalConfig(None, 30, limits, ail_billing="free")
    with pytest.raises(ValueError):
        PersonalConfig(None, 30, limits, ail_usd_value=Decimal("0"))
    with pytest.raises(Exception):
        Settings(_env_file=None, bot_token="1:x", database_url="sqlite:///", personal_ail_usd_value="0")


# --- the handler's settlement step ---------------------------------------------------------------


def _config():
    return SimpleNamespace(ail_usd_value=PER_AIL)


async def test_settlement_charges_the_rounded_up_actual_cost():
    access = SimpleNamespace(adjust=AsyncMock())
    await _settle_chat_turn(access, config=_config(), invocation_id=7, usages=[_usage(cost="0.000273")], user_id=1)
    access.adjust.assert_awaited_once_with(invocation_id=7, actual_units=Decimal("0.55"))


async def test_unknown_cost_keeps_the_reservation(caplog):
    access = SimpleNamespace(adjust=AsyncMock())
    with caplog.at_level(logging.WARNING):
        await _settle_chat_turn(access, config=_config(), invocation_id=7, usages=[_usage(cost=None)], user_id=1)
    access.adjust.assert_not_awaited()
    assert "keeping reservation" in caplog.text


async def test_a_failing_settlement_never_breaks_the_answer(caplog):
    access = SimpleNamespace(adjust=AsyncMock(side_effect=RuntimeError("db down")))
    await _settle_chat_turn(access, config=_config(), invocation_id=7, usages=[_usage(cost="0.001")], user_id=1)
    assert "settlement failed" in caplog.text


async def test_no_invocation_means_nothing_to_settle():
    access = SimpleNamespace(adjust=AsyncMock())
    await _settle_chat_turn(access, config=_config(), invocation_id=None, usages=[_usage(cost="0.001")], user_id=1)
    access.adjust.assert_not_awaited()


def test_failed_turn_cost_is_zero_for_provider_errors_and_the_price_of_priced_answers():
    assert failed_turn_cost_usd([_usage(cost=None, status="failed")]) == Decimal(0)
    assert failed_turn_cost_usd([_usage(cost=None, status="failed"), _usage(cost="0.002")]) == Decimal("0.002")
    assert failed_turn_cost_usd([_usage(cost=None, status="validation_failed")]) is None


async def test_a_failed_turn_settles_at_the_minimum_unit():
    access = SimpleNamespace(adjust=AsyncMock())
    await _settle_chat_turn(
        access, config=_config(), invocation_id=7, usages=[_usage(cost=None, status="failed")], user_id=1, failed=True
    )
    access.adjust.assert_awaited_once_with(invocation_id=7, actual_units=Decimal("0.01"))


def test_a_cost_above_the_request_cap_is_logged(caplog):
    with caplog.at_level(logging.WARNING):
        ail_units_from_cost_usd(Decimal("100000"), PER_AIL)
    assert "capped" in caplog.text


def test_provider_preferences_only_route_the_personal_chat_turn():
    client = _client(include_usage_cost=True, provider_preferences={"max_price": {"prompt": 1}})
    plain = {"model": "m"}
    assert "provider" in client._with_provider_options(plain)["extra_body"]
    other = client._with_provider_options(plain, route=False)["extra_body"]
    assert other == {"usage": {"include": True}}


async def test_a_tool_turn_is_never_charged_above_its_reservation():
    access = SimpleNamespace(adjust=AsyncMock())
    config = PersonalConfig(None, 30, PersonalQuotaLimits(5, 50), quota_mode="ail",
                            ail_limits=PersonalQuotaLimits(10, 100, unit="ail"))

    await _settle_chat_turn(access, config=config, invocation_id=1, usages=[_usage(cost="0.05")], user_id=5,
                            max_units=Decimal("3"))
    await _settle_chat_turn(access, config=config, invocation_id=1, usages=[_usage(cost="0.0005")], user_id=5,
                            max_units=Decimal("3"))

    assert [call.kwargs["actual_units"] for call in access.adjust.await_args_list] == [Decimal("3"), Decimal("1.00")]


async def test_a_provider_cost_above_the_tool_reservation_is_logged_as_our_loss(caplog):
    access = SimpleNamespace(adjust=AsyncMock())
    config = PersonalConfig(None, 30, PersonalQuotaLimits(5, 50), quota_mode="ail",
                            ail_limits=PersonalQuotaLimits(10, 100, unit="ail"))
    with caplog.at_level(logging.WARNING):
        await _settle_chat_turn(access, config=config, invocation_id=1, usages=[_usage(cost="0.05")], user_id=5,
                                max_units=Decimal("3"))
    assert "above its reservation" in caplog.text
