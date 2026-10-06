from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.application.feature_access import (
    PERSONAL_POOL_KEY,
    AccessReason,
    AccessTier,
    FeatureAccessDecision,
    FeatureAccessService,
    FeatureEntitlement,
    FeatureQuotaPolicy,
    NoPaidUserEntitlementResolver,
    PersonalQuotaLimits,
    QuotaPeriod,
    QuotaScope,
    paid_personal_policy,
    resolve_feature_policy,
)
from selara.application.model_router import DefaultModelRouter
from selara.application.personal_config import PersonalConfig, StaticPersonalConfigProvider, config_from_settings
from selara.application.selara_ai_product import (
    PRODUCT_SPECS,
    SELARA_AI_PRODUCT_KEY,
    SELARA_PERSONAL_PRODUCT_KEY,
    SELARA_PERSONAL_TERMS_VERSION,
    SelaraAiProductUnavailable,
    UnsupportedSelaraAiProduct,
    get_selara_ai_product,
    invoice_payload_for_intent,
    parse_invoice_payload,
)
from selara.application.usage_pricing import ConfiguredUsagePricer, QuotaCost
from selara.core.config import Settings
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.feature_quota import feature_quota_lock_key
from selara.infrastructure.db.models import (
    AiFeatureQuotaUsageModel,
    ChatEntitlementModel,
    SelaraAiPaymentModel,
    SelaraAiPurchaseIntentModel,
    UserEntitlementModel,
)
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.auth import resolve_owner_private_exemption

_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
# Test configuration; production values come from settings (PERSONAL_*_DAILY_LIMIT).
_LIMITS = PersonalQuotaLimits(free_daily=5, paid_daily=150)
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _decision(scope: QuotaScope, *, allowed: bool = True) -> FeatureAccessDecision:
    return FeatureAccessDecision(
        allowed=allowed,
        feature=AiFeature.PERSONAL_CHAT,
        scope_type=scope.scope_type,
        scope_id=str(scope.scope_id),
        access_tier=AccessTier.FREE,
        quota_limit=5,
        quota_used=1,
        quota_remaining=4,
        period_start=None,
        period_end=None,
    )


async def _reserve_personal(service: FeatureAccessService, *, user_id: int = 42, owner_exempt: bool = False):
    return await service.reserve_feature_usage(
        feature=AiFeature.PERSONAL_CHAT,
        chat_id=user_id,
        scope=QuotaScope.user(user_id),
        actor_user_id=user_id,
        trigger="telegram_message",
        timezone_name="UTC",
        idempotency_key=f"personal_chat:{user_id}:1",
        chat_type="private",
        owner_exempt=owner_exempt,
        now=_NOW,
    )


# ----- products ---------------------------------------------------------------


def test_personal_product_is_a_user_scoped_thirty_day_catalog_entry():
    product = get_selara_ai_product(
        product_key=SELARA_PERSONAL_PRODUCT_KEY, price_stars=69, duration=timedelta(days=30)
    )

    assert product.key == SELARA_PERSONAL_PRODUCT_KEY == "selara_personal_monthly"
    assert product.scope == "user"
    assert product.price_stars == 69
    assert product.currency == "XTR"
    assert product.duration == timedelta(days=30)
    assert product.terms_version == SELARA_PERSONAL_TERMS_VERSION == "personal-v1"
    assert "Personal" in product.title


def test_group_product_stays_chat_scoped():
    product = get_selara_ai_product(product_key=SELARA_AI_PRODUCT_KEY, price_stars=100)

    assert product.scope == "chat"
    assert PRODUCT_SPECS[SELARA_AI_PRODUCT_KEY].scope == "chat"
    assert PRODUCT_SPECS[SELARA_PERSONAL_PRODUCT_KEY].scope == "user"


@pytest.mark.parametrize("price", [None, 0, -1])
def test_personal_product_is_hidden_without_a_positive_price(price):
    with pytest.raises(SelaraAiProductUnavailable):
        get_selara_ai_product(product_key=SELARA_PERSONAL_PRODUCT_KEY, price_stars=price, duration=timedelta(days=30))


def test_unknown_product_is_still_rejected():
    with pytest.raises(UnsupportedSelaraAiProduct):
        get_selara_ai_product(product_key="selara_pets_monthly", price_stars=10)


def test_invoice_payload_for_personal_intent_is_the_same_opaque_uuid_format():
    intent_id = "5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e"
    payload = invoice_payload_for_intent(intent_id)
    assert parse_invoice_payload(payload) == intent_id


def test_personal_price_comes_from_its_own_env_and_is_unset_by_default(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/selara_test")
    monkeypatch.delenv("SELARA_PERSONAL_PRICE_STARS", raising=False)
    assert Settings(_env_file=None).selara_personal_price_stars is None

    monkeypatch.setenv("SELARA_PERSONAL_PRICE_STARS", "69")
    assert Settings(_env_file=None).selara_personal_price_stars == 69


# ----- policies / pools -------------------------------------------------------


def test_personal_chat_policy_is_five_free_requests_per_day_in_the_personal_pool():
    policy = resolve_feature_policy(
        feature=AiFeature.PERSONAL_CHAT, trigger="telegram_message", personal_limits=_LIMITS
    )

    assert policy is not None
    assert policy.limit == 5
    assert policy.period == QuotaPeriod.DAY
    assert policy.pool == PERSONAL_POOL_KEY == "personal_daily"
    assert policy.unit == "request"


def test_paid_personal_policy_is_one_hundred_fifty_per_day_in_the_same_pool():
    free = resolve_feature_policy(
        feature=AiFeature.PERSONAL_CHAT, trigger="telegram_message", personal_limits=_LIMITS
    )
    paid = paid_personal_policy(_LIMITS)

    assert paid.limit == 150
    assert paid.feature == AiFeature.PERSONAL_CHAT
    assert paid.period == free.period
    assert paid.pool == free.pool
    assert paid.policy_key != free.policy_key


@pytest.mark.parametrize(
    "feature",
    [AiFeature.AUTOCONFIG, AiFeature.PERSONAL_MEMORY_EXTRACT, AiFeature.LLM_CONTEXT_COMPRESSION],
)
def test_internal_operations_and_autocfg_never_draw_from_a_quota_pool(feature):
    assert resolve_feature_policy(feature=feature, trigger="internal") is None


def test_existing_chat_features_keep_the_feature_as_their_pool():
    policy = resolve_feature_policy(feature=AiFeature.LLM_ADMIN, trigger="telegram_message")
    assert policy.pool == "llm_admin"


def test_quota_scope_helpers_and_lock_keys_do_not_collide_between_scopes():
    chat, user = QuotaScope.chat(77), QuotaScope.user(77)
    assert (chat.scope_type, chat.scope_id) == ("chat", 77)
    assert (user.scope_type, user.scope_id) == ("user", 77)

    period = _NOW
    chat_key = feature_quota_lock_key(pool_key="llm_admin", scope_type="chat", scope_id=77, period_start=period)
    user_key = feature_quota_lock_key(pool_key="llm_admin", scope_type="user", scope_id=77, period_start=period)
    other_user = feature_quota_lock_key(pool_key="llm_admin", scope_type="user", scope_id=78, period_start=period)
    assert len({chat_key, user_key, other_user}) == 3


# ----- pricer / router / adjust stubs ----------------------------------------


def test_flat_pricer_charges_one_unit_for_everything_today():
    pricer = ConfiguredUsagePricer()

    for feature in AiFeature:
        cost = pricer.price(feature=feature, model_key="any-model", operation="telegram_message")
        assert cost == QuotaCost(Decimal("1"))
    assert pricer.price(feature=AiFeature.PERSONAL_CHAT).units == Decimal("1")


def test_quota_cost_rejects_negative_units():
    with pytest.raises(ValueError):
        QuotaCost(Decimal("-1"))


def test_model_router_stub_always_returns_the_configured_model():
    router = DefaultModelRouter("gpt-4o-mini")

    assert router.resolve(feature=AiFeature.PERSONAL_CHAT, tier=AccessTier.PAID) == "gpt-4o-mini"
    assert router.resolve(feature=AiFeature.LLM_ADMIN, tier=AccessTier.FREE) == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_adjust_is_an_interface_only_noop():
    repository = SimpleNamespace()
    service = FeatureAccessService(repository)

    assert await service.adjust(invocation_id=1, actual_units=Decimal("3")) is None


# ----- FeatureAccessService with user scope ----------------------------------


@pytest.mark.asyncio
async def test_user_scope_reserve_passes_scope_pool_and_unit_cost_to_repository():
    scope = QuotaScope.user(42)
    repository = SimpleNamespace(reserve=AsyncMock(return_value=_decision(scope)))
    service = FeatureAccessService(repository, personal_limits=_LIMITS)

    decision = await _reserve_personal(service)

    kwargs = repository.reserve.await_args.kwargs
    assert kwargs["scope"] == scope
    assert kwargs["chat_id"] == 42
    assert kwargs["policy"].limit == 5
    assert kwargs["policy"].pool == "personal_daily"
    assert kwargs["cost"] == QuotaCost(Decimal("1"))
    assert kwargs["access_tier"] == AccessTier.FREE
    assert decision.scope_type == "user" and decision.scope_id == "42"


@pytest.mark.asyncio
async def test_default_user_entitlement_resolver_fails_closed_to_the_free_limit():
    scope = QuotaScope.user(42)
    repository = SimpleNamespace(reserve=AsyncMock(return_value=_decision(scope)))
    service = FeatureAccessService(
        repository, user_entitlement_resolver=NoPaidUserEntitlementResolver(), personal_limits=_LIMITS
    )

    await _reserve_personal(service)

    assert repository.reserve.await_args.kwargs["policy"].limit == 5
    assert repository.reserve.await_args.kwargs["access_tier"] == AccessTier.FREE


@pytest.mark.asyncio
async def test_active_personal_entitlement_raises_the_pool_limit_to_one_fifty():
    scope = QuotaScope.user(42)
    repository = SimpleNamespace(reserve=AsyncMock(return_value=_decision(scope)))
    resolver = SimpleNamespace(
        resolve=AsyncMock(
            return_value=FeatureEntitlement(
                access_tier=AccessTier.PAID,
                valid_until=_NOW + timedelta(days=3),
                source="telegram_stars",
                product_key=SELARA_PERSONAL_PRODUCT_KEY,
                quota_policy=paid_personal_policy(_LIMITS),
            )
        )
    )
    service = FeatureAccessService(repository, user_entitlement_resolver=resolver, personal_limits=_LIMITS)

    decision = await _reserve_personal(service)

    resolver.resolve.assert_awaited_once_with(
        user_id=42, feature=AiFeature.PERSONAL_CHAT, trigger="telegram_message"
    )
    assert repository.reserve.await_args.kwargs["policy"].limit == 150
    assert repository.reserve.await_args.kwargs["access_tier"] == AccessTier.PAID
    assert decision.access_tier == AccessTier.PAID
    assert decision.entitlement_product == SELARA_PERSONAL_PRODUCT_KEY


@pytest.mark.asyncio
async def test_expired_or_failing_personal_entitlement_falls_back_to_five():
    scope = QuotaScope.user(42)
    expired = SimpleNamespace(
        resolve=AsyncMock(
            return_value=FeatureEntitlement(
                access_tier=AccessTier.PAID,
                valid_until=_NOW - timedelta(seconds=1),
                quota_policy=paid_personal_policy(_LIMITS),
            )
        )
    )
    failing = SimpleNamespace(resolve=AsyncMock(side_effect=RuntimeError("db down")))
    for resolver in (expired, failing):
        repository = SimpleNamespace(reserve=AsyncMock(return_value=_decision(scope)))
        service = FeatureAccessService(repository, user_entitlement_resolver=resolver, personal_limits=_LIMITS)

        await _reserve_personal(service)

        assert repository.reserve.await_args.kwargs["policy"].limit == 5
        assert repository.reserve.await_args.kwargs["access_tier"] == AccessTier.FREE


@pytest.mark.asyncio
async def test_paid_policy_for_another_pool_is_rejected_and_free_limit_applies():
    scope = QuotaScope.user(42)
    foreign_pool = FeatureQuotaPolicy(
        AiFeature.PERSONAL_CHAT, "x", 150, QuotaPeriod.DAY, pool_key="some_other_pool"
    )
    resolver = SimpleNamespace(
        resolve=AsyncMock(
            return_value=FeatureEntitlement(access_tier=AccessTier.PAID, quota_policy=foreign_pool)
        )
    )
    repository = SimpleNamespace(reserve=AsyncMock(return_value=_decision(scope)))
    service = FeatureAccessService(repository, user_entitlement_resolver=resolver, personal_limits=_LIMITS)

    await _reserve_personal(service)

    assert repository.reserve.await_args.kwargs["policy"].limit == 5


@pytest.mark.asyncio
async def test_owner_exempt_dm_skips_entitlement_lookup_and_is_owner_internal():
    scope = QuotaScope.user(42)
    repository = SimpleNamespace(
        reserve=AsyncMock(
            return_value=FeatureAccessDecision(
                allowed=True,
                feature=AiFeature.PERSONAL_CHAT,
                scope_type="user",
                scope_id="42",
                access_tier=AccessTier.OWNER_INTERNAL,
                quota_limit=None,
                quota_used=None,
                quota_remaining=None,
                period_start=None,
                period_end=None,
                owner_exempt=True,
            )
        )
    )
    resolver = SimpleNamespace(resolve=AsyncMock(side_effect=AssertionError("owner must not hit the resolver")))
    service = FeatureAccessService(repository, user_entitlement_resolver=resolver, personal_limits=_LIMITS)

    decision = await _reserve_personal(service, owner_exempt=True)

    assert decision.owner_exempt and decision.access_tier == AccessTier.OWNER_INTERNAL
    assert repository.reserve.await_args.kwargs["owner_exempt"] is True
    assert repository.reserve.await_args.kwargs["scope"] == scope


@pytest.mark.asyncio
@pytest.mark.parametrize("feature", [AiFeature.AUTOCONFIG, AiFeature.PERSONAL_MEMORY_EXTRACT])
async def test_unmetered_features_in_a_dm_do_not_touch_the_repository(feature):
    repository = SimpleNamespace(reserve=AsyncMock(side_effect=AssertionError("must not reserve")))
    service = FeatureAccessService(repository)

    decision = await service.reserve_feature_usage(
        feature=feature,
        chat_id=42,
        scope=QuotaScope.user(42),
        actor_user_id=42,
        trigger="internal",
        timezone_name="UTC",
        idempotency_key="unmetered:42:1",
        chat_type="private",
        now=_NOW,
    )

    assert decision.allowed
    assert decision.reason == AccessReason.NO_COMMERCIAL_QUOTA
    assert decision.scope_type == "user" and decision.scope_id == "42"
    repository.reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_personal_requests_always_cost_one_unit_whatever_the_pricer_says():
    """5/150 are requests: an AI Limits pricer must not change what a Personal request costs."""
    scope = QuotaScope.user(42)
    pricer = ConfiguredUsagePricer(Decimal("7"), {"personal_chat": Decimal("4"), "personal_memory_extract": Decimal("9")})
    repository = SimpleNamespace(reserve=AsyncMock(return_value=_decision(scope)))
    service = FeatureAccessService(repository, pricer=pricer, personal_limits=_LIMITS)

    await _reserve_personal(service)

    assert repository.reserve.await_args.kwargs["cost"] == QuotaCost(Decimal("1"))
    # With 150 paid requests a user still gets 150 requests, not 150 // 4.
    assert repository.reserve.await_args.kwargs["policy"].limit == 5


@pytest.mark.asyncio
async def test_the_pricer_foundation_still_prices_non_personal_features():
    scope = QuotaScope.chat(-100)
    repository = SimpleNamespace(reserve=AsyncMock(return_value=_decision(scope)))
    pricer = SimpleNamespace(price=lambda **_: QuotaCost(Decimal("2.5")))
    service = FeatureAccessService(repository, pricer=pricer)

    await service.reserve_feature_usage(
        feature=AiFeature.LLM_ADMIN, chat_id=-100, actor_user_id=1, actor_is_bot=False,
        trigger="telegram_message", timezone_name="UTC", idempotency_key="g:1", chat_type="supergroup",
    )

    assert repository.reserve.await_args.kwargs["cost"].units == Decimal("2.5")


def test_owner_private_exemption_is_identity_based_and_needs_no_chat_admin_lookup():
    assert resolve_owner_private_exemption(user_id=7, admin_user_id=7) is True
    assert resolve_owner_private_exemption(user_id=8, admin_user_id=7) is False
    assert resolve_owner_private_exemption(user_id=7, admin_user_id=None) is False
    assert resolve_owner_private_exemption(user_id=None, admin_user_id=7) is False


# ----- schema contract --------------------------------------------------------


def _load_migration(filename: str):
    spec = importlib.util.spec_from_file_location(filename[:-3], _VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new_migrations_extend_the_single_alembic_chain():
    personal = _load_migration("0079_personal_entitlements.py")
    quota = _load_migration("0080_quota_user_scope.py")
    config = _load_migration("0081_selara_personal_config.py")
    personal_ai = _load_migration("0082_personal_ai.py")

    assert personal.down_revision == "0078_family_pet_command_key"
    assert quota.down_revision == personal.revision
    assert config.down_revision == quota.revision
    assert personal_ai.down_revision == config.revision
    revisions, parents = set(), set()
    for path in _VERSIONS.glob("[0-9]*.py"):
        module = _load_migration(path.name)
        revisions.add(module.revision)
        down = module.down_revision
        if isinstance(down, (tuple, list)):
            parents.update(down)
        elif down:
            parents.add(down)
    assert revisions - parents == {personal_ai.revision}
    assert max(len(personal.revision), len(quota.revision)) <= 32


def _check_text(table, name: str) -> str:
    for constraint in table.constraints:
        if getattr(constraint, "name", None) == name:
            return str(constraint.sqltext)
    raise AssertionError(f"constraint {name} is missing on {table.name}")


def test_chat_entitlements_stay_closed_to_the_personal_product():
    text = _check_text(ChatEntitlementModel.__table__, "ck_chat_entitlements_product")
    assert "selara_ai_monthly" in text
    assert "selara_personal_monthly" not in text


def test_user_entitlements_accept_only_the_personal_product():
    table = UserEntitlementModel.__table__
    assert table.name == "user_entitlements"
    text = _check_text(table, "ck_user_entitlements_product")
    assert "selara_personal_monthly" in text and "selara_ai_monthly" not in text


def test_purchase_audit_tables_know_both_products_and_enforce_self_only_gifts_off():
    intents = SelaraAiPurchaseIntentModel.__table__
    payments = SelaraAiPaymentModel.__table__
    for table, name in (
        (intents, "ck_selara_ai_purchase_intents_product"),
        (payments, "ck_selara_ai_payments_product"),
    ):
        text = _check_text(table, name)
        assert "selara_ai_monthly" in text and "selara_personal_monthly" in text
    self_only = _check_text(intents, "ck_selara_ai_purchase_intents_personal_self_only")
    assert "target_user_id" in self_only and "buyer_user_id" in self_only
    assert intents.c.chat_id.nullable and intents.c.source_chat_id.nullable
    assert intents.c.target_user_id.nullable
    assert not payments.c.target_scope.nullable and not intents.c.target_scope.nullable


def test_quota_usage_table_carries_scope_units_and_pool():
    table = AiFeatureQuotaUsageModel.__table__
    for column in ("quota_scope_type", "quota_scope_id", "units", "pool_key"):
        assert column in table.c
    assert not table.c.units.nullable and not table.c.pool_key.nullable
    assert "legacy_orphan" in _check_text(table, "ck_ai_feature_quota_scope_type")
    assert "legacy_orphan" in _check_text(table, "ck_ai_feature_quota_scope_id")


def test_metadata_registers_user_entitlements():
    assert "user_entitlements" in Base.metadata.tables


# ----- /premium handler -------------------------------------------------------


def _settings(monkeypatch, *, personal_price: str | None):
    from selara.presentation.handlers import premium  # noqa: F401  (import side effects only)

    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/selara_test")
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "test-provider-key")
    monkeypatch.setenv("SELARA_AI_PRICE_STARS", "137")
    if personal_price is None:
        monkeypatch.delenv("SELARA_PERSONAL_PRICE_STARS", raising=False)
    else:
        monkeypatch.setenv("SELARA_PERSONAL_PRICE_STARS", personal_price)
    return Settings(_env_file=None)


def _private_message(user_id: int = 900):
    return SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
    )


def _buttons(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_premium_hides_selara_personal_until_its_price_is_configured(monkeypatch):
    from selara.presentation.handlers import premium

    repository = SimpleNamespace(list_purchasable_chats=AsyncMock(return_value=[
        SimpleNamespace(telegram_chat_id=-100, title="Group"),
    ]))
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    message = _private_message()

    settings = _settings(monkeypatch, personal_price=None)
    await premium.premium_command(
        message,
        bot=object(),
        session_factory=object(),
        settings=settings,
        personal_config=StaticPersonalConfigProvider(config_from_settings(settings)),
    )

    markup = message.answer.await_args.kwargs["reply_markup"]
    assert _buttons(markup) == ["premium:select:-100"]


@pytest.mark.asyncio
async def test_premium_offers_group_or_self_when_personal_price_is_configured(monkeypatch):
    from selara.presentation.handlers import premium

    message = _private_message()

    settings = _settings(monkeypatch, personal_price="69")
    await premium.premium_command(
        message,
        bot=object(),
        session_factory=object(),
        settings=settings,
        personal_config=StaticPersonalConfigProvider(config_from_settings(settings)),
    )

    assert _buttons(message.answer.await_args.kwargs["reply_markup"]) == ["premium:group", "premium:self"]


@pytest.mark.asyncio
async def test_personal_pre_checkout_skips_the_chat_admin_check_and_passes_no_chat(monkeypatch):
    from selara.application.selara_ai_product import SELARA_PERSONAL_PRODUCT_KEY
    from selara.infrastructure.db.telegram_stars import PreCheckoutResult, PurchaseIntent
    from selara.presentation.handlers import premium

    intent = PurchaseIntent(
        id="5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
        buyer_user_id=123,
        source_chat_id=None,
        chat_id=None,
        chat_title=None,
        product_key=SELARA_PERSONAL_PRODUCT_KEY,
        amount_stars=69,
        currency="XTR",
        duration_seconds=2_592_000,
        invoice_payload="selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
        status="open",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        pre_checkout_query_id=None,
        pre_checkout_accepted_at=None,
        consumed_at=None,
        terms_version="personal-v1",
        terms_accepted_at=datetime.now(timezone.utc),
        target_scope="user",
        target_user_id=123,
    )
    repository = SimpleNamespace(
        get_purchase_intent=AsyncMock(return_value=intent),
        accept_pre_checkout=AsyncMock(return_value=PreCheckoutResult(True)),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    authority = AsyncMock(side_effect=AssertionError("a personal purchase has no chat to authorize"))
    monkeypatch.setattr(premium, "_is_purchase_authorized", authority)
    query = SimpleNamespace(
        id="query-personal",
        invoice_payload=intent.invoice_payload,
        from_user=SimpleNamespace(id=123),
        total_amount=69,
        currency="XTR",
        answer=AsyncMock(),
    )

    await premium.selara_ai_pre_checkout(query, bot=object(), session_factory=object())

    authority.assert_not_awaited()
    assert repository.accept_pre_checkout.await_args.kwargs["checked_chat_id"] is None
    query.answer.assert_awaited_once_with(ok=True, error_message=None)


@pytest.mark.asyncio
async def test_successful_personal_payment_confirms_the_subscription_not_a_chat(monkeypatch):
    from selara.infrastructure.db.telegram_stars import PaymentResult
    from selara.presentation.handlers import premium

    repository = SimpleNamespace(
        process_successful_payment=AsyncMock(
            return_value=PaymentResult(
                "applied",
                valid_until=_NOW + timedelta(days=30),
                entitlement_action="created",
                payment_id=1,
                target_scope="user",
                user_id=900,
            )
        ),
        get_chat_title=AsyncMock(side_effect=AssertionError("personal payments have no chat")),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=900),
        date=_NOW,
        answer=AsyncMock(),
        successful_payment=SimpleNamespace(
            invoice_payload="selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
            telegram_payment_charge_id="personal-confirm",
            provider_payment_charge_id="",
            total_amount=69,
            currency="XTR",
        ),
    )

    await premium.selara_ai_successful_payment(
        message,
        session_factory=object(),
        settings=SimpleNamespace(admin_user_id=None, bot_timezone="UTC"),
        bot=SimpleNamespace(),
    )

    text = message.answer.await_args.args[0]
    assert "Selara Personal" in text and "активна" in text


# ----- nothing about prices, durations or limits is hardcoded ----


def _env(monkeypatch, **values: str):
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/selara_test")
    for key in (
        "SELARA_PERSONAL_PRICE_STARS",
        "SELARA_PERSONAL_DURATION_DAYS",
        "PERSONAL_FREE_DAILY_LIMIT",
        "PERSONAL_PAID_DAILY_LIMIT",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_settings_defaults_live_in_config_and_can_all_be_overridden(monkeypatch):
    default = _env(monkeypatch)
    assert default.selara_personal_price_stars is None
    assert default.selara_personal_duration_days == 30
    assert (default.personal_free_daily_limit, default.personal_paid_daily_limit) == (5, 150)

    custom = _env(
        monkeypatch,
        SELARA_PERSONAL_PRICE_STARS="99",
        SELARA_PERSONAL_DURATION_DAYS="7",
        PERSONAL_FREE_DAILY_LIMIT="3",
        PERSONAL_PAID_DAILY_LIMIT="40",
    )
    assert custom.selara_personal_price_stars == 99
    assert custom.selara_personal_duration_days == 7
    assert PersonalQuotaLimits.from_settings(custom) == PersonalQuotaLimits(3, 40)


def test_personal_product_duration_and_price_follow_configuration():
    product = get_selara_ai_product(
        product_key=SELARA_PERSONAL_PRODUCT_KEY, price_stars=99, duration=timedelta(days=7)
    )
    assert product.price_stars == 99 and product.duration == timedelta(days=7)
    assert "7 дней" in product.title
    with pytest.raises(SelaraAiProductUnavailable):
        get_selara_ai_product(product_key=SELARA_PERSONAL_PRODUCT_KEY, price_stars=99)


def test_premium_texts_use_configured_limits_and_duration(monkeypatch):
    from selara.presentation.handlers import premium

    settings = _env(
        monkeypatch,
        SELARA_PERSONAL_DURATION_DAYS="7",
        PERSONAL_FREE_DAILY_LIMIT="3",
        PERSONAL_PAID_DAILY_LIMIT="40",
    )
    text = premium._personal_terms_text(config_from_settings(settings))
    assert "40 запросов" in text and "3 бесплатных" in text and "7 дней" in text
    assert "150" not in text and "30 дней" not in text


def test_personal_limits_override_policies_and_are_validated():
    limits = PersonalQuotaLimits(free_daily=2, paid_daily=9)
    free = resolve_feature_policy(
        feature=AiFeature.PERSONAL_CHAT, trigger="telegram_message", personal_limits=limits
    )
    assert free.limit == 2 and paid_personal_policy(limits).limit == 9
    for free_limit, paid_limit in ((0, 5), (5, 5), (5, 3)):
        with pytest.raises(ValueError):
            PersonalQuotaLimits(free_daily=free_limit, paid_daily=paid_limit)


def test_unconfigured_personal_limits_fail_closed_instead_of_unlimited():
    with pytest.raises(ValueError, match="not configured"):
        resolve_feature_policy(feature=AiFeature.PERSONAL_CHAT, trigger="telegram_message")


def test_configured_pricer_uses_default_and_per_feature_weights():
    pricer = ConfiguredUsagePricer(Decimal("2"), {"personal_chat": Decimal("0.5")})

    assert pricer.price(feature=AiFeature.PERSONAL_CHAT).units == Decimal("0.5")
    assert pricer.price(feature=AiFeature.LLM_ADMIN).units == Decimal("2")


@pytest.mark.asyncio
async def test_service_reserves_with_the_configured_limit():
    scope = QuotaScope.user(42)
    repository = SimpleNamespace(reserve=AsyncMock(return_value=_decision(scope)))
    service = FeatureAccessService(repository, personal_limits=PersonalQuotaLimits(free_daily=8, paid_daily=80))

    await _reserve_personal(service)

    assert repository.reserve.await_args.kwargs["policy"].limit == 8
    assert repository.reserve.await_args.kwargs["cost"].units == Decimal("1")


# ----- renewal limit wording, invoice description, active-subscriber screen -----


def test_personal_invoice_description_states_the_daily_limit_being_sold():
    product = get_selara_ai_product(
        product_key=SELARA_PERSONAL_PRODUCT_KEY, price_stars=69, duration=timedelta(days=30), paid_daily_limit=150
    )
    assert "150" in product.description and len(product.description) <= 255
    chat = get_selara_ai_product(product_key=SELARA_AI_PRODUCT_KEY, price_stars=69)
    assert "запрос" not in chat.description


def test_terms_describe_one_consistent_renewal_rule(monkeypatch):
    from selara.presentation.handlers import premium

    text = premium._personal_terms_text(config_from_settings(_env(monkeypatch)))
    assert "больший" in text
    assert "повторная покупка закрепляет" not in text


@pytest.mark.asyncio
async def test_active_subscriber_sees_current_limit_and_what_renewal_will_give(monkeypatch):
    from selara.presentation.handlers import premium

    settings = _settings(monkeypatch, personal_price="69")
    entitlement = SimpleNamespace(
        status="active", valid_until=datetime.now(timezone.utc) + timedelta(days=10), paid_daily_limit=150
    )
    repository = SimpleNamespace(get_user_entitlement=AsyncMock(return_value=entitlement))
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    config = StaticPersonalConfigProvider(
        PersonalConfig(69, 30, PersonalQuotaLimits(free_daily=5, paid_daily=80))  # lowered since the purchase
    )
    message = SimpleNamespace(chat=SimpleNamespace(type="private"), edit_text=AsyncMock())
    query = SimpleNamespace(message=message, from_user=SimpleNamespace(id=900), answer=AsyncMock())

    await premium.show_personal_offer(query, session_factory=object(), settings=settings, personal_config=config)

    text = message.edit_text.await_args.args[0]
    assert "150" in text  # what the subscriber has now
    assert "не урезаются" in text and "80" in text  # offered limit and the paid-days guarantee
    assert "будет 150" in text  # the larger of current and offered
