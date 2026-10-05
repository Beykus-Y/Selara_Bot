from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.application.selara_ai_product import (
    SELARA_AI_CURRENCY,
    SELARA_AI_DURATION,
    SELARA_AI_PRODUCT_KEY,
    SelaraAiProductUnavailable,
    UnsupportedSelaraAiProduct,
    get_selara_ai_product,
    invoice_payload_for_intent,
    parse_invoice_payload,
)
from selara.core.config import Settings
from selara.infrastructure.db.telegram_stars import PurchaseIntent, PreCheckoutResult
from selara.presentation.handlers import premium
from selara.presentation.handlers import private_panel
from selara.presentation.payment_safe_dispatcher import _requires_durable_processing


def test_selara_ai_price_and_duration_come_from_one_catalog_entry():
    product = get_selara_ai_product(product_key=SELARA_AI_PRODUCT_KEY, price_stars=137)

    assert product.price_stars == 137
    assert product.currency == SELARA_AI_CURRENCY == "XTR"
    assert product.duration == SELARA_AI_DURATION == timedelta(days=30)


def test_checkout_reads_the_authoritative_price_setting(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/selara_test")
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "test-provider-key")
    monkeypatch.setenv("SELARA_AI_PRICE_STARS", "137")

    settings = Settings(_env_file=None)

    assert premium._product_for_settings(settings).price_stars == 137


def test_product_is_unavailable_without_an_owner_configured_price():
    with pytest.raises(SelaraAiProductUnavailable):
        get_selara_ai_product(product_key=SELARA_AI_PRODUCT_KEY, price_stars=None)


def test_unsupported_product_is_rejected_and_nonpositive_prices_are_invalid():
    with pytest.raises(UnsupportedSelaraAiProduct):
        get_selara_ai_product(product_key="other", price_stars=10)
    with pytest.raises(SelaraAiProductUnavailable):
        get_selara_ai_product(product_key=SELARA_AI_PRODUCT_KEY, price_stars=0)


def test_invoice_payload_contains_only_versioned_uuid_intent_reference():
    intent_id = "5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e"
    payload = invoice_payload_for_intent(intent_id)

    assert payload == f"selara_ai:v1:{intent_id}"
    assert parse_invoice_payload(payload) == intent_id
    assert parse_invoice_payload("selara_ai:v2:" + intent_id) is None
    assert parse_invoice_payload("selara_ai:v1:-100123:137:30") is None
    assert parse_invoice_payload("selara_ai:v1:NOT-A-UUID") is None


@pytest.mark.asyncio
async def test_purchase_authority_requires_admin_bot_membership_and_fails_closed():
    bot = SimpleNamespace(
        get_chat_member=AsyncMock(side_effect=[
            SimpleNamespace(status="administrator"),
            SimpleNamespace(status="member"),
        ]),
        get_me=AsyncMock(return_value=SimpleNamespace(id=900)),
    )
    authorized = await premium._is_purchase_authorized(bot=bot, buyer_user_id=123, chat_id=-100)
    assert authorized == (True, None)

    member_bot = SimpleNamespace(
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
        get_me=AsyncMock(return_value=SimpleNamespace(id=900)),
    )
    denied = await premium._is_purchase_authorized(bot=member_bot, buyer_user_id=123, chat_id=-100)
    assert denied == (False, "buyer_not_admin")
    assert member_bot.get_me.await_count == 0

    failing_bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=RuntimeError("Telegram offline")))
    failed = await premium._is_purchase_authorized(bot=failing_bot, buyer_user_id=123, chat_id=-100)
    assert failed == (False, "telegram_unavailable")

    creator_bot = SimpleNamespace(
        get_chat_member=AsyncMock(side_effect=[
            SimpleNamespace(status="creator"),
            SimpleNamespace(status="administrator"),
        ]),
        get_me=AsyncMock(return_value=SimpleNamespace(id=900)),
    )
    creator_authorized = await premium._is_purchase_authorized(
        bot=creator_bot,
        buyer_user_id=123,
        chat_id=-100,
    )
    assert creator_authorized == (True, None)


def test_payment_updates_are_processed_before_polling_acknowledges_them():
    assert _requires_durable_processing(SimpleNamespace(pre_checkout_query=object(), message=None))
    assert _requires_durable_processing(
        SimpleNamespace(pre_checkout_query=None, message=SimpleNamespace(successful_payment=object()))
    )
    assert not _requires_durable_processing(
        SimpleNamespace(pre_checkout_query=None, message=SimpleNamespace(successful_payment=None))
    )


@pytest.mark.asyncio
async def test_pre_checkout_answers_exactly_once_after_persisting_acceptance(monkeypatch):
    intent = PurchaseIntent(
        id="5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
        buyer_user_id=123,
        source_chat_id=-100,
        chat_id=-100,
        chat_title="Test",
        product_key=SELARA_AI_PRODUCT_KEY,
        amount_stars=137,
        currency="XTR",
        duration_seconds=2_592_000,
        invoice_payload="selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
        status="open",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        pre_checkout_query_id=None,
        pre_checkout_accepted_at=None,
        consumed_at=None,
    )
    repository = SimpleNamespace(
        get_purchase_intent=AsyncMock(return_value=intent),
        accept_pre_checkout=AsyncMock(return_value=PreCheckoutResult(True, chat_id=-100)),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    monkeypatch.setattr(premium, "_is_purchase_authorized", AsyncMock(return_value=(True, None)))
    query = SimpleNamespace(
        id="query-1",
        invoice_payload=intent.invoice_payload,
        from_user=SimpleNamespace(id=123),
        total_amount=137,
        currency="XTR",
        answer=AsyncMock(),
    )

    await premium.selara_ai_pre_checkout(query, bot=object(), session_factory=object())

    repository.accept_pre_checkout.assert_awaited_once()
    query.answer.assert_awaited_once_with(ok=True, error_message=None)


@pytest.mark.asyncio
async def test_pre_checkout_rejects_wrong_buyer_without_authority_or_acceptance(monkeypatch):
    intent = PurchaseIntent(
        id="5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
        buyer_user_id=321,
        source_chat_id=-100,
        chat_id=-100,
        chat_title="Test",
        product_key=SELARA_AI_PRODUCT_KEY,
        amount_stars=137,
        currency="XTR",
        duration_seconds=2_592_000,
        invoice_payload="selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
        status="open",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        pre_checkout_query_id=None,
        pre_checkout_accepted_at=None,
        consumed_at=None,
    )
    repository = SimpleNamespace(
        get_purchase_intent=AsyncMock(return_value=intent),
        accept_pre_checkout=AsyncMock(),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    authority = AsyncMock()
    monkeypatch.setattr(premium, "_is_purchase_authorized", authority)
    query = SimpleNamespace(
        id="query-1",
        invoice_payload=intent.invoice_payload,
        from_user=SimpleNamespace(id=123),
        total_amount=137,
        currency="XTR",
        answer=AsyncMock(),
    )

    await premium.selara_ai_pre_checkout(query, bot=object(), session_factory=object())

    repository.accept_pre_checkout.assert_not_awaited()
    authority.assert_not_awaited()
    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_pre_checkout_rejects_unknown_payload_and_answers_once(monkeypatch):
    repository = SimpleNamespace(get_purchase_intent=AsyncMock(return_value=None))
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    query = SimpleNamespace(
        id="query-unknown",
        invoice_payload="selara_ai:v1:ffffffff-ffff-4fff-8fff-ffffffffffff",
        from_user=SimpleNamespace(id=123),
        total_amount=137,
        currency="XTR",
        answer=AsyncMock(),
    )

    await premium.selara_ai_pre_checkout(query, bot=object(), session_factory=object())

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_private_panel_pending_inputs_do_not_consume_successful_payment(monkeypatch):
    monkeypatch.setattr(private_panel, "_get_pending_cfg_input", lambda _user_id: object())
    monkeypatch.setattr(private_panel, "_get_pending_admin_input", lambda _user_id: object())
    filters = (
        private_panel.PendingCfgInputFilter(),
        private_panel.PendingAdminInputFilter(),
    )
    paid_message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(id=123),
        successful_payment=object(),
    )
    normal_message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(id=123),
        successful_payment=None,
    )

    for pending_filter in filters:
        assert await pending_filter(paid_message) is False
        assert await pending_filter(normal_message) is True
