"""PR 13: user model profile selection and the AI Limits (AIL) quota mode."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.application.feature_access import (
    AIL_UNIT,
    QuotaScope,
    PERSONAL_AIL_POOL_KEY,
    PERSONAL_POOL_KEY,
    AccessTier,
    FeatureAccessDecision,
    FeatureAccessService,
    FeatureEntitlement,
    PersonalQuotaLimits,
    paid_personal_policy,
    resolve_feature_policy,
)
from selara.application.model_catalog import CatalogModel, CatalogSnapshot, ModelCapabilities, ModelProfile
from selara.application.personal_config import (
    CachedPersonalConfigProvider,
    PersonalConfig,
    PersonalConfigOverride,
    StaticPersonalConfigProvider,
    ail_limits_from,
    merge_config,
)
from selara.application.personal_models import (
    ail_activation_problems,
    choose_from_snapshot,
    format_ail,
    profile_options,
)
from selara.application.selara_ai_product import (
    SELARA_PERSONAL_AIL_TERMS_VERSION,
    SELARA_PERSONAL_TERMS_VERSION,
    personal_terms_version,
)
from selara.infrastructure.llm.client import LlmClient, LlmConfig
from selara.infrastructure.llm.features import AiFeature

REQUESTS = PersonalQuotaLimits(free_daily=5, paid_daily=150)
AIL = PersonalQuotaLimits(free_daily=10, paid_daily=100, unit=AIL_UNIT)
LEGACY = "legacy-model"


def _model(key: str, model_id: str, *, enabled: bool = True) -> CatalogModel:
    return CatalogModel(
        key, model_id, key.title(), enabled=enabled,
        prompt_price_usd_per_million=Decimal("1"), completion_price_usd_per_million=Decimal("2"),
        capabilities=ModelCapabilities(),
    )


def _catalog(*, analytics_model: str = "model-a", analytics_x: str = "2", creative_enabled: bool = True,
             basic_assigned: bool = True, extra: tuple[ModelProfile, ...] = ()) -> CatalogSnapshot:
    models = (_model("base", "model-basic"), _model("a", "model-a"), _model("b", "model-b"), _model("c", "model-c"))
    by_id = {m.model_id: m.key for m in models}
    profiles = (
        ModelProfile("basic", "Базовая", "base" if basic_assigned else None, Decimal("1")),
        ModelProfile("analytics", "Аналитик", by_id[analytics_model], Decimal(analytics_x)),
        ModelProfile("creative", "Творческая", "c", Decimal("5"), enabled=creative_enabled),
        ModelProfile("fast", "Быстрая", None, Decimal("3")),
        *extra,
    )
    return CatalogSnapshot(models, profiles)


# --- configuration ----------------------------------------------------------------


def test_requests_mode_is_the_default_and_ail_needs_both_budgets() -> None:
    base = PersonalConfig(None, 30, REQUESTS)
    assert base.quota_mode == "requests" and base.active_limits is REQUESTS
    with pytest.raises(ValueError):
        PersonalConfig(None, 30, REQUESTS, quota_mode="ail")  # no budgets: fail closed
    with pytest.raises(ValueError):
        merge_config(base, PersonalConfigOverride(quota_mode="ail", free_daily_ail=10))
    with pytest.raises(ValueError):
        ail_limits_from(100, 10)  # paid must exceed free
    with pytest.raises(ValueError):
        PersonalConfigOverride(quota_mode="unlimited")
    with pytest.raises(ValueError):
        PersonalConfigOverride(free_daily_ail=0)
    config = merge_config(base, PersonalConfigOverride(quota_mode="ail", free_daily_ail=10, paid_daily_ail=100))
    assert config.ail_enabled and config.active_limits == AIL
    # Budgets alone (owner preparing the switch) keep requests mode.
    prepared = merge_config(base, PersonalConfigOverride(free_daily_ail=10, paid_daily_ail=100))
    assert not prepared.ail_enabled and prepared.active_limits == REQUESTS


async def test_broken_override_never_fails_open() -> None:
    base = PersonalConfig(None, 30, REQUESTS)
    good = PersonalConfigOverride(quota_mode="ail", free_daily_ail=10, paid_daily_ail=100)
    state = {"value": good}

    async def load():
        if isinstance(state["value"], Exception):
            raise state["value"]
        return state["value"]

    provider = CachedPersonalConfigProvider(base, load, ttl_seconds=0.001)
    assert (await provider.get()).ail_enabled
    state["value"] = RuntimeError("db down")
    provider.invalidate()
    assert (await provider.get()).ail_enabled  # last known good
    cold = CachedPersonalConfigProvider(base, load)
    assert (await cold.get()).quota_mode == "requests"  # no last good: requests 5/150, never unlimited


def test_pools_and_policies_differ_per_mode() -> None:
    requests = resolve_feature_policy(feature=AiFeature.PERSONAL_CHAT, trigger="m", personal_limits=REQUESTS)
    ail = resolve_feature_policy(feature=AiFeature.PERSONAL_CHAT, trigger="m", personal_limits=AIL)
    assert (requests.pool, requests.unit, requests.limit) == (PERSONAL_POOL_KEY, "request", 5)
    assert (ail.pool, ail.unit, ail.limit) == (PERSONAL_AIL_POOL_KEY, "ail", 10)
    assert paid_personal_policy(AIL).pool == PERSONAL_AIL_POOL_KEY
    assert requests.policy_key != ail.policy_key


# --- quota service ----------------------------------------------------------------------


class _Repo:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def reserve(self, **kwargs):
        self.calls.append(kwargs)
        policy = kwargs["policy"]
        return FeatureAccessDecision(
            allowed=True, feature=policy.feature, scope_type="user", scope_id="1", access_tier=kwargs["access_tier"],
            quota_limit=policy.limit, quota_used=kwargs["cost"].units, quota_remaining=0,
            period_start=None, period_end=None, policy_key=policy.policy_key,
        )


class _Resolver:
    def __init__(self, tier: AccessTier, policy=None) -> None:
        self.tier, self.policy = tier, policy

    async def resolve(self, **_):
        return FeatureEntitlement(access_tier=self.tier, quota_policy=self.policy)


async def _reserve(service, **extra):
    return await service.reserve_feature_usage(
        feature=AiFeature.PERSONAL_CHAT, chat_id=1, actor_user_id=1, trigger="telegram_message",
        timezone_name="UTC", idempotency_key="personal_chat:1:1", scope=QuotaScope.user(1), **extra,
    )


async def test_requests_mode_ignores_the_multiplier() -> None:
    repo = _Repo()
    service = FeatureAccessService(repo, personal_config=StaticPersonalConfigProvider(PersonalConfig(None, 30, REQUESTS)))
    decision = await _reserve(service, units=Decimal("5"), model_profile="creative")
    assert repo.calls[0]["cost"].units == Decimal("1")  # creative ×5 is still one request
    assert "model_profile" not in repo.calls[0]
    assert repo.calls[0]["policy"].limit == 5 and decision.quota_unit == "request"


async def test_ail_mode_charges_the_snapshot_multiplier_and_records_the_profile() -> None:
    repo = _Repo()
    config = PersonalConfig(None, 30, REQUESTS, quota_mode="ail", ail_limits=AIL)
    service = FeatureAccessService(repo, personal_config=StaticPersonalConfigProvider(config))
    for units in ("1", "2", "5", "2.5"):
        await _reserve(service, units=Decimal(units), model_profile="analytics")
    assert [call["cost"].units for call in repo.calls] == [Decimal("1"), Decimal("2"), Decimal("5"), Decimal("2.5")]
    assert {call["policy"].pool for call in repo.calls} == {PERSONAL_AIL_POOL_KEY}
    assert repo.calls[0]["model_profile"] == "analytics"
    with pytest.raises(ValueError):
        await _reserve(service)  # AIL without a resolved cost fails closed
    with pytest.raises(ValueError):
        await _reserve(service, units=Decimal("0.333"))  # not storable in NUMERIC(10,2)


async def test_paid_ail_budget_comes_from_the_same_config_snapshot() -> None:
    repo = _Repo()
    config = PersonalConfig(None, 30, REQUESTS, quota_mode="ail", ail_limits=AIL)
    # The resolver still hands out a requests-mode policy (read before the switch).
    resolver = _Resolver(AccessTier.PAID, paid_personal_policy(REQUESTS))
    service = FeatureAccessService(
        repo, personal_config=StaticPersonalConfigProvider(config), user_entitlement_resolver=resolver
    )
    decision = await _reserve(service, units=Decimal("2"))
    assert decision.access_tier == AccessTier.PAID
    assert (repo.calls[0]["policy"].limit, repo.calls[0]["policy"].pool) == (100, PERSONAL_AIL_POOL_KEY)


async def test_owner_reserves_without_consuming_but_keeps_the_profile() -> None:
    repo = _Repo()
    config = PersonalConfig(None, 30, REQUESTS, quota_mode="ail", ail_limits=AIL)
    service = FeatureAccessService(repo, personal_config=StaticPersonalConfigProvider(config))
    await _reserve(service, units=Decimal("5"), model_profile="creative", owner_exempt=True)
    assert repo.calls[0]["owner_exempt"] is True and repo.calls[0]["access_tier"] == AccessTier.OWNER_INTERNAL


async def test_internal_operations_never_spend_the_budget() -> None:
    repo = _Repo()
    config = PersonalConfig(None, 30, REQUESTS, quota_mode="ail", ail_limits=AIL)
    service = FeatureAccessService(repo, personal_config=StaticPersonalConfigProvider(config))
    for feature in (AiFeature.PERSONAL_MEMORY_EXTRACT, AiFeature.LLM_CONTEXT_COMPRESSION, AiFeature.AUTOCONFIG):
        decision = await service.reserve_feature_usage(
            feature=feature, chat_id=1, actor_user_id=1, trigger="internal", timezone_name="UTC",
            idempotency_key=f"{feature.value}:1", units=Decimal("5"),
        )
        assert decision.allowed and decision.quota_limit is None
    assert repo.calls == []


# --- model selection and the single snapshot -----------------------------------------------


def test_new_user_gets_basic_and_a_choice_is_kept() -> None:
    snapshot = _catalog()
    assert choose_from_snapshot(snapshot, selected_key=None, legacy_model=LEGACY).profile_key == "basic"
    choice = choose_from_snapshot(snapshot, selected_key="analytics", legacy_model=LEGACY)
    assert (choice.profile_key, choice.effective.model_id, choice.ail_cost, choice.fell_back) == (
        "analytics", "model-a", Decimal("2"), False
    )


@pytest.mark.parametrize("selected", ["creative", "fast", "whatever", "openrouter/vendor/model"])
def test_unusable_or_forged_profiles_run_and_are_charged_as_basic(selected) -> None:
    choice = choose_from_snapshot(_catalog(creative_enabled=False), selected_key=selected, legacy_model=LEGACY)
    assert (choice.profile_key, choice.effective.model_id, choice.ail_cost) == ("basic", "model-basic", Decimal("1"))
    assert choice.fell_back is (selected in ("creative", "fast"))


def test_basic_without_a_model_uses_the_legacy_fallback_at_one_ail() -> None:
    choice = choose_from_snapshot(_catalog(basic_assigned=False), selected_key="basic", legacy_model=LEGACY)
    assert (choice.effective.model_id, choice.ail_cost, choice.effective.is_fallback) == (LEGACY, Decimal("1"), True)
    assert choose_from_snapshot(None, selected_key="analytics", legacy_model=LEGACY).effective.model_id == LEGACY


def test_physical_model_change_keeps_the_users_profile() -> None:
    before = choose_from_snapshot(_catalog(), selected_key="analytics", legacy_model=LEGACY)
    after = choose_from_snapshot(_catalog(analytics_model="model-b"), selected_key="analytics", legacy_model=LEGACY)
    assert before.selected_key == after.selected_key == "analytics"
    assert (before.effective.model_id, after.effective.model_id) == ("model-a", "model-b")


def test_selector_options_hide_broken_profiles() -> None:
    options = {o.profile_key: o for o in profile_options(_catalog(creative_enabled=False), legacy_model=LEGACY)}
    assert options["basic"].available and options["analytics"].available
    assert not options["creative"].available and not options["fast"].available
    assert options["analytics"].ail_multiplier == Decimal("2") and options["analytics"].display_name == "Аналитик"
    odd = _catalog(analytics_x="2.345")
    assert not {o.profile_key: o for o in profile_options(odd, legacy_model=LEGACY)}["analytics"].available


def test_ail_activation_needs_a_usable_basic_and_storable_multipliers() -> None:
    assert ail_activation_problems(_catalog()) == []
    assert ail_activation_problems(_catalog(basic_assigned=False))
    assert ail_activation_problems(_catalog(analytics_x="0.125"))
    assert ail_activation_problems(None)


def test_ail_amounts_are_printed_without_trailing_zeros() -> None:
    assert [format_ail(v) for v in (Decimal("2.500000"), Decimal("5.00"), 3, 73.5, None)] == ["2.5", "5", "3", "73.5", "—"]


async def test_config_change_after_resolution_does_not_split_model_and_price() -> None:
    """Critical: quota and provider call use one resolution even if the admin edits in between."""
    resolved_from = _catalog()  # analytics -> model-a ×2
    choice = choose_from_snapshot(resolved_from, selected_key="analytics", legacy_model=LEGACY)
    changed = _catalog(analytics_model="model-b", analytics_x="5")  # admin edit after resolution
    provider = SimpleNamespace(get=AsyncMock(return_value=changed))
    llm = LlmClient(LlmConfig(api_key="test", model=LEGACY, summary_model="summary"), model_catalog=provider)
    llm._client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
        model="model-a", usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
    ))
    result = await llm.chat_simple([{"role": "user", "content": "hi"}], resolved_model=choice.effective)
    assert choice.ail_cost == Decimal("2")
    assert llm._client.chat.completions.create.await_args.kwargs["model"] == "model-a"
    assert result.usages[0].model_profile == "analytics"
    assert result.usages[0].estimated_cost_usd is not None  # priced from the resolution's snapshot
    provider.get.assert_not_awaited()  # never re-resolved
    nxt = choose_from_snapshot(changed, selected_key="analytics", legacy_model=LEGACY)
    assert (nxt.effective.model_id, nxt.ail_cost) == ("model-b", Decimal("5"))
    with pytest.raises(ValueError):
        await llm.chat_simple([], resolved_model=choice.effective, model_profile="analytics")


def test_terms_version_follows_the_quota_mode() -> None:
    assert personal_terms_version(ail_enabled=False) == SELARA_PERSONAL_TERMS_VERSION == "personal-v1"
    assert personal_terms_version(ail_enabled=True) == SELARA_PERSONAL_AIL_TERMS_VERSION
    assert len(SELARA_PERSONAL_AIL_TERMS_VERSION) <= 32


def test_premium_texts_do_not_contradict_each_other() -> None:
    from selara.presentation.handlers import premium

    requests_terms = premium._personal_terms_text(PersonalConfig(None, 30, REQUESTS))
    assert "150 запросов" in requests_terms and "AIL" not in requests_terms
    ail_config = PersonalConfig(None, 30, REQUESTS, quota_mode="ail", ail_limits=AIL)
    ail_terms = premium._personal_terms_text(ail_config)
    assert "100 AIL" in ail_terms and "10 бесплатных" in ail_terms
    assert "запросов в сутки" not in ail_terms and "150" not in ail_terms
    assert "не гарантированное количество сообщений" in ail_terms


def test_profile_key_is_validated_by_the_repository_model() -> None:
    from selara.application.personal_models import is_profile_key

    assert is_profile_key("creative") and not is_profile_key("gpt-4o") and not is_profile_key(None)
    assert replace(_catalog().profiles_by_key["analytics"], ail_multiplier=Decimal("3")).ail_multiplier == 3


# --- review fixes: invoice wording, terms binding ----------------------------------------------


def _premium_settings(monkeypatch):
    from selara.core.config import Settings

    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/selara_test")
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "test-provider-key")
    monkeypatch.setenv("SELARA_PERSONAL_PRICE_STARS", "69")
    return Settings(_env_file=None)


def test_ail_invoice_never_promises_a_request_count(monkeypatch) -> None:
    from selara.presentation.handlers import premium

    settings = _premium_settings(monkeypatch)
    ail = premium._personal_product_for_settings(
        settings, PersonalConfig(69, 30, REQUESTS, quota_mode="ail", ail_limits=AIL)
    )
    assert "100 AI Limits" in ail.description and "запросов" not in ail.description
    assert ail.paid_daily_limit == 150 and len(ail.description) <= 255  # still snapshotted for requests mode
    requests = premium._personal_product_for_settings(settings, PersonalConfig(69, 30, REQUESTS))
    assert "150 запросов" in requests.description


@pytest.mark.parametrize(("data", "ail", "creates"), [
    ("premium:self_accept:personal-v1", True, False),       # saw request terms, AIL got enabled since
    ("premium:self_accept", True, False),                   # pre-release button: personal-v1
    ("premium:self_accept:personal-v2-ail", False, False),  # saw AIL terms, switched back since
    ("premium:self_accept:personal-v2-ail", True, True),
    ("premium:self_accept", False, True),
])
async def test_acceptance_is_bound_to_the_terms_version_shown(monkeypatch, data, ail, creates) -> None:
    from selara.presentation.handlers import premium

    settings = _premium_settings(monkeypatch)
    repository = SimpleNamespace(create_personal_purchase_intent=AsyncMock(side_effect=RuntimeError("stop here")))
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    config = PersonalConfig(69, 30, REQUESTS, quota_mode="ail", ail_limits=AIL) if ail else PersonalConfig(69, 30, REQUESTS)
    message = SimpleNamespace(chat=SimpleNamespace(type="private"), edit_text=AsyncMock())
    query = SimpleNamespace(data=data, message=message, from_user=SimpleNamespace(id=900), answer=AsyncMock())

    await premium.accept_terms_and_buy_selara_personal(
        query, bot=AsyncMock(), session_factory=object(), settings=settings,
        personal_config=StaticPersonalConfigProvider(config),
    )

    assert repository.create_personal_purchase_intent.await_count == (1 if creates else 0)
    if creates:
        kwargs = repository.create_personal_purchase_intent.await_args.kwargs
        assert kwargs["terms_version"] == personal_terms_version(ail_enabled=ail)
    else:
        assert "изменились" in message.edit_text.await_args.args[0]
