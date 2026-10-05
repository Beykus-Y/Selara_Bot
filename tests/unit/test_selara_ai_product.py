from __future__ import annotations

import asyncio
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
        id=900,
        get_chat_member=AsyncMock(side_effect=[
            SimpleNamespace(status="administrator"),
            SimpleNamespace(status="member"),
        ]),
        get_me=AsyncMock(),
    )
    authorized = await premium._is_purchase_authorized(bot=bot, buyer_user_id=123, chat_id=-100)
    assert authorized == (True, None)
    bot.get_me.assert_not_awaited()

    member_bot = SimpleNamespace(
        id=900,
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
        get_me=AsyncMock(),
    )
    denied = await premium._is_purchase_authorized(bot=member_bot, buyer_user_id=123, chat_id=-100)
    assert denied == (False, "buyer_not_admin")
    member_bot.get_me.assert_not_awaited()

    failing_bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=RuntimeError("Telegram offline")))
    failed = await premium._is_purchase_authorized(bot=failing_bot, buyer_user_id=123, chat_id=-100)
    assert failed == (False, "telegram_unavailable")

    creator_bot = SimpleNamespace(
        id=900,
        get_chat_member=AsyncMock(side_effect=[
            SimpleNamespace(status="creator"),
            SimpleNamespace(status="administrator"),
        ]),
        get_me=AsyncMock(),
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
        terms_version="v1",
        terms_accepted_at=datetime.now(timezone.utc),
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
async def test_pre_checkout_retry_with_new_query_id_survives_lost_answer(monkeypatch):
    state = {"accepted": False}
    intent = SimpleNamespace(
        id="5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
        buyer_user_id=123,
        source_chat_id=-100,
        chat_id=-100,
        product_key=SELARA_AI_PRODUCT_KEY,
        amount_stars=137,
        currency="XTR",
        status="open",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        terms_version="v1",
        terms_accepted_at=datetime.now(timezone.utc),
    )

    async def get_intent(*, invoice_payload):
        assert invoice_payload == "selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e"
        intent.status = "checkout_accepted" if state["accepted"] else "open"
        return intent

    async def accept(**kwargs):
        state["accepted"] = True
        return PreCheckoutResult(True, chat_id=-100)

    repository = SimpleNamespace(
        get_purchase_intent=AsyncMock(side_effect=get_intent),
        accept_pre_checkout=AsyncMock(side_effect=accept),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    monkeypatch.setattr(premium, "_is_purchase_authorized", AsyncMock(return_value=(True, None)))
    answer = AsyncMock(side_effect=[TimeoutError("network failure"), None])

    def make_query(query_id):
        return SimpleNamespace(
            id=query_id,
            invoice_payload=intent.invoice_payload,
            from_user=SimpleNamespace(id=123),
            total_amount=137,
            currency="XTR",
            answer=answer,
        )

    await premium.selara_ai_pre_checkout(make_query("query-1"), bot=object(), session_factory=object())
    await premium.selara_ai_pre_checkout(make_query("query-2"), bot=object(), session_factory=object())

    assert repository.accept_pre_checkout.await_count == 2
    assert answer.await_count == 2
    assert answer.await_args_list[0].kwargs["ok"] is True
    assert answer.await_args_list[1].kwargs == {"ok": True, "error_message": None}


@pytest.mark.asyncio
async def test_pre_checkout_validation_has_a_bounded_deadline_and_answers_once(monkeypatch):
    monkeypatch.setattr(premium, "_PRECHECKOUT_VALIDATION_DEADLINE_SECONDS", 0.01)
    monkeypatch.setattr(premium, "_PRECHECKOUT_ANSWER_DEADLINE_SECONDS", 1.0)

    async def slow_lookup(**_kwargs):
        await asyncio.sleep(0.05)

    repository = SimpleNamespace(get_purchase_intent=AsyncMock(side_effect=slow_lookup))
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    query = SimpleNamespace(
        id="query-slow",
        invoice_payload="selara_ai:v1:ffffffff-ffff-4fff-8fff-ffffffffffff",
        from_user=SimpleNamespace(id=123),
        total_amount=137,
        currency="XTR",
        answer=AsyncMock(),
    )

    await premium.selara_ai_pre_checkout(query, bot=object(), session_factory=object())

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs["ok"] is False


def test_purchase_keyboard_requires_explicit_terms_acceptance():
    keyboard = premium._purchase_keyboard(chat_id=-100, price_stars=137)
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert buttons[0].callback_data == "premium:accept:-100"
    assert "принимаю условия" in buttons[0].text.lower()
    assert buttons[1].callback_data == "premium:terms:-100"
    assert "/paysupport" in premium._terms_text()


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
