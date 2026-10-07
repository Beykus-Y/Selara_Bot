from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Dispatcher
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, StatementError

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
from selara.infrastructure.db.telegram_stars import (
    PaymentRefundClaim,
    PaymentResult,
    PurchaseIntent,
    PreCheckoutResult,
    SqlAlchemyTelegramStarsRepository,
)
from selara.presentation.handlers import premium
from selara.presentation.handlers import private_panel
from selara.presentation.payment_safe_dispatcher import PaymentSafeDispatcher, _requires_durable_processing


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


def test_payment_polling_override_tracks_the_aiogram_private_hook_signature():
    upstream = inspect.signature(Dispatcher._polling)
    override = inspect.signature(PaymentSafeDispatcher._polling)

    assert tuple(override.parameters) == tuple(upstream.parameters)


def test_payment_retry_backoff_is_exponential_and_capped():
    assert [premium._payment_retry_delay(attempt) for attempt in range(1, 9)] == [
        1,
        2,
        4,
        8,
        16,
        32,
        60,
        60,
    ]


@pytest.mark.asyncio
async def test_owner_can_refund_a_durably_claimed_rejected_payment(monkeypatch):
    repository = SimpleNamespace(
        claim_rejected_payment_refund=AsyncMock(
            return_value=PaymentRefundClaim(
                "claimed",
                buyer_user_id=456,
                telegram_payment_charge_id="charge-refund",
                amount_stars=137,
            )
        ),
        finish_rejected_payment_refund=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    bot = SimpleNamespace(refund_star_payment=AsyncMock(return_value=True))
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(id=123),
        text="/stars_refund 17",
        answer=AsyncMock(),
    )

    await premium.refund_rejected_stars_payment(
        message,
        bot=bot,
        session_factory=object(),
        settings=SimpleNamespace(admin_user_id=123),
    )

    repository.claim_rejected_payment_refund.assert_awaited_once_with(
        payment_id=17,
        requested_by_user_id=123,
    )
    bot.refund_star_payment.assert_awaited_once_with(
        user_id=456,
        telegram_payment_charge_id="charge-refund",
    )
    repository.finish_rejected_payment_refund.assert_awaited_once_with(
        payment_id=17,
        succeeded=True,
        result_code="refunded",
    )


@pytest.mark.asyncio
async def test_already_refunded_charge_is_recorded_as_refunded(monkeypatch):
    repository = SimpleNamespace(
        claim_rejected_payment_refund=AsyncMock(
            return_value=PaymentRefundClaim(
                "claimed",
                buyer_user_id=456,
                telegram_payment_charge_id="charge-refund",
                amount_stars=137,
            )
        ),
        finish_rejected_payment_refund=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    bot = SimpleNamespace(
        refund_star_payment=AsyncMock(
            side_effect=TelegramBadRequest(
                method=SimpleNamespace(),
                message="Bad Request: CHARGE_ALREADY_REFUNDED",
            )
        )
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(id=123),
        text="/stars_refund 17",
        answer=AsyncMock(),
    )

    await premium.refund_rejected_stars_payment(
        message,
        bot=bot,
        session_factory=object(),
        settings=SimpleNamespace(admin_user_id=123),
    )

    repository.finish_rejected_payment_refund.assert_awaited_once_with(
        payment_id=17,
        succeeded=True,
        result_code="already_refunded",
    )
    assert "уже был выполнен" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_refund_claim_database_error_answers_owner(monkeypatch):
    repository = SimpleNamespace(
        claim_rejected_payment_refund=AsyncMock(side_effect=RuntimeError("db down")),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    bot = SimpleNamespace(refund_star_payment=AsyncMock())
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(id=123),
        text="/stars_refund 17",
        answer=AsyncMock(),
    )

    await premium.refund_rejected_stars_payment(
        message,
        bot=bot,
        session_factory=object(),
        settings=SimpleNamespace(admin_user_id=123),
    )

    bot.refund_star_payment.assert_not_awaited()
    message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_payment_persistence_retry_alerts_owner_after_three_failures(monkeypatch):
    now = datetime.now(timezone.utc)
    repository = SimpleNamespace(
        process_successful_payment=AsyncMock(
            side_effect=[RuntimeError("temporary failure")] * 3
            + [PaymentResult("applied", chat_id=-100, valid_until=now + timedelta(days=30))]
        ),
        record_unprocessable_payment=AsyncMock(return_value=1),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    monkeypatch.setattr(premium.asyncio, "sleep", AsyncMock())
    message = SimpleNamespace(
        successful_payment=SimpleNamespace(
            invoice_payload="selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
            telegram_payment_charge_id="charge-retry",
            provider_payment_charge_id="",
            total_amount=137,
            currency="XTR",
        ),
        from_user=SimpleNamespace(id=123),
        date=now,
        answer=AsyncMock(),
    )
    bot = SimpleNamespace(send_message=AsyncMock())

    await premium.selara_ai_successful_payment(
        message,
        session_factory=object(),
        settings=SimpleNamespace(admin_user_id=900, bot_timezone="UTC"),
        bot=bot,
    )

    assert repository.process_successful_payment.await_count == 4
    # A transient failure that recovers on retry must never dead-letter the payment.
    repository.record_unprocessable_payment.assert_not_awaited()
    bot.send_message.assert_awaited_once()
    assert "Charge: charge-retry" in bot.send_message.await_args.kwargs["text"]
    message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_poison_payment_is_dead_lettered_and_polling_can_resume(monkeypatch):
    now = datetime.now(timezone.utc)
    repository = SimpleNamespace(
        process_successful_payment=AsyncMock(side_effect=ValueError("poison payload")),
        record_unprocessable_payment=AsyncMock(return_value=77),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    monkeypatch.setattr(premium.asyncio, "sleep", AsyncMock())
    message = SimpleNamespace(
        successful_payment=SimpleNamespace(
            invoice_payload="selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
            telegram_payment_charge_id="charge-poison",
            provider_payment_charge_id="",
            total_amount=137,
            currency="XTR",
        ),
        from_user=SimpleNamespace(id=123),
        date=now,
        answer=AsyncMock(),
    )
    bot = SimpleNamespace(send_message=AsyncMock())

    await premium.selara_ai_successful_payment(
        message,
        session_factory=object(),
        settings=SimpleNamespace(admin_user_id=900, bot_timezone="UTC"),
        bot=bot,
    )

    assert repository.process_successful_payment.await_count == premium._PAYMENT_RETRY_DEADLETTER_ATTEMPT
    repository.record_unprocessable_payment.assert_awaited_once()
    assert (
        repository.record_unprocessable_payment.await_args.kwargs["telegram_payment_charge_id"] == "charge-poison"
    )
    alert_texts = [call.kwargs["text"] for call in bot.send_message.await_args_list]
    assert any("processing_failed" in text for text in alert_texts)
    assert any("/stars_refund 77" in text for text in alert_texts)
    message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_payment_stays_unacknowledged_while_even_the_dead_letter_write_fails(monkeypatch):
    now = datetime.now(timezone.utc)
    attempts = {"count": 0}

    async def permanent_but_unrecordable(*_args, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] > premium._PAYMENT_RETRY_DEADLETTER_ATTEMPT + 2:
            raise asyncio.CancelledError()
        raise ValueError("poison payload")

    repository = SimpleNamespace(
        process_successful_payment=AsyncMock(side_effect=permanent_but_unrecordable),
        record_unprocessable_payment=AsyncMock(side_effect=RuntimeError("db down")),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    monkeypatch.setattr(premium.asyncio, "sleep", AsyncMock())
    message = SimpleNamespace(
        successful_payment=SimpleNamespace(
            invoice_payload="selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
            telegram_payment_charge_id="charge-outage",
            provider_payment_charge_id="",
            total_amount=137,
            currency="XTR",
        ),
        from_user=SimpleNamespace(id=123),
        date=now,
        answer=AsyncMock(),
    )
    bot = SimpleNamespace(send_message=AsyncMock())

    with pytest.raises(asyncio.CancelledError):
        await premium.selara_ai_successful_payment(
            message,
            session_factory=object(),
            settings=SimpleNamespace(admin_user_id=900, bot_timezone="UTC"),
            bot=bot,
        )

    assert repository.record_unprocessable_payment.await_count >= 2
    assert bot.send_message.await_count == 1
    assert "приостановлены" in bot.send_message.await_args.kwargs["text"]
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_transient_payment_failure_never_enters_the_dead_letter_path(monkeypatch):
    """A contended or unavailable database must keep the update retrying instead of
    dead-lettering an already-charged, valid payment as rejected."""
    now = datetime.now(timezone.utc)
    attempts = {"count": 0}

    async def lock_timeout_then_cancel(*_args, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] > premium._PAYMENT_RETRY_DEADLETTER_ATTEMPT + 2:
            raise asyncio.CancelledError()
        raise OperationalError("SELECT ... FOR UPDATE", {}, RuntimeError("55P03 lock timeout"))

    repository = SimpleNamespace(
        process_successful_payment=AsyncMock(side_effect=lock_timeout_then_cancel),
        record_unprocessable_payment=AsyncMock(return_value=1),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    monkeypatch.setattr(premium.asyncio, "sleep", AsyncMock())
    message = SimpleNamespace(
        successful_payment=SimpleNamespace(
            invoice_payload="selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
            telegram_payment_charge_id="charge-contention",
            provider_payment_charge_id="",
            total_amount=137,
            currency="XTR",
        ),
        from_user=SimpleNamespace(id=123),
        date=now,
        answer=AsyncMock(),
    )
    bot = SimpleNamespace(send_message=AsyncMock())

    with pytest.raises(asyncio.CancelledError):
        await premium.selara_ai_successful_payment(
            message,
            session_factory=object(),
            settings=SimpleNamespace(admin_user_id=900, bot_timezone="UTC"),
            bot=bot,
        )

    assert repository.process_successful_payment.await_count > premium._PAYMENT_RETRY_DEADLETTER_ATTEMPT
    repository.record_unprocessable_payment.assert_not_awaited()
    message.answer.assert_not_awaited()


def test_permanent_payment_failure_classification():
    assert premium._is_permanent_payment_failure(ValueError("charge id is required"))
    assert premium._is_permanent_payment_failure(TypeError("unexpected payload shape"))
    assert premium._is_permanent_payment_failure(
        IntegrityError("INSERT INTO selara_ai_payments ...", {}, Exception("check violation"))
    )
    # SQLAlchemy wraps the driver error; unwrap orig/cause chains when classifying.
    assert premium._is_permanent_payment_failure(
        StatementError("statement raised ValueError", "stmt", {}, orig=ValueError("poison"))
    )
    assert not premium._is_permanent_payment_failure(
        OperationalError("SELECT ... FOR UPDATE", {}, RuntimeError("lock timeout"))
    )
    # Deadlock, serialization and lock waits surface as the DBAPIError base.
    assert not premium._is_permanent_payment_failure(DBAPIError("stmt", {}, RuntimeError("40P01 deadlock")))
    assert not premium._is_permanent_payment_failure(RuntimeError("Telegram Stars purchase transactions require PostgreSQL"))


@pytest.mark.asyncio
async def test_dead_letter_refuses_charge_ids_it_cannot_store_verbatim():
    repository = SqlAlchemyTelegramStarsRepository(SimpleNamespace())

    recorded = await repository.record_unprocessable_payment(
        buyer_user_id=123,
        invoice_payload="selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
        telegram_payment_charge_id="c" * 256,
        provider_payment_charge_id="",
        amount_stars=137,
        currency="XTR",
        payment_at=datetime.now(timezone.utc),
    )

    assert recorded is None


@pytest.mark.asyncio
async def test_confirmation_timeout_does_not_repeat_the_committed_payment(monkeypatch):
    monkeypatch.setattr(premium, "_PAYMENT_CONFIRMATION_TIMEOUT_SECONDS", 0.01)
    now = datetime.now(timezone.utc)

    async def slow_answer(*_args, **_kwargs):
        await asyncio.sleep(0.05)

    repository = SimpleNamespace(
        process_successful_payment=AsyncMock(
            return_value=PaymentResult("applied", chat_id=-100, valid_until=now + timedelta(days=30))
        ),
        get_chat_title=AsyncMock(return_value="Test group"),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    message = SimpleNamespace(
        successful_payment=SimpleNamespace(
            invoice_payload="selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
            telegram_payment_charge_id="charge-confirmation-timeout",
            provider_payment_charge_id="",
            total_amount=137,
            currency="XTR",
        ),
        from_user=SimpleNamespace(id=123),
        date=now,
        answer=slow_answer,
    )

    await premium.selara_ai_successful_payment(
        message,
        session_factory=object(),
        settings=SimpleNamespace(admin_user_id=None, bot_timezone="UTC"),
        bot=SimpleNamespace(send_message=AsyncMock()),
    )

    repository.process_successful_payment.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_owner_cannot_start_stars_refund(monkeypatch):
    repository_factory = lambda _factory: SimpleNamespace(
        claim_rejected_payment_refund=AsyncMock()
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", repository_factory)
    bot = SimpleNamespace(refund_star_payment=AsyncMock())
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(id=456),
        text="/stars_refund 17",
        answer=AsyncMock(),
    )

    await premium.refund_rejected_stars_payment(
        message,
        bot=bot,
        session_factory=object(),
        settings=SimpleNamespace(admin_user_id=123),
    )

    bot.refund_star_payment.assert_not_awaited()
    message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_payment_polling_waits_for_durable_handler_before_advancing_updates(monkeypatch):
    dispatcher = PaymentSafeDispatcher()
    payment_update = SimpleNamespace(pre_checkout_query=object(), message=None)
    handler_started = asyncio.Event()
    allow_commit = asyncio.Event()
    durable_commit_finished = asyncio.Event()
    next_update_requested = asyncio.Event()

    async def process_payment(**_kwargs):
        handler_started.set()
        await allow_commit.wait()
        durable_commit_finished.set()

    async def listen_updates(*_args, **_kwargs):
        yield payment_update
        assert durable_commit_finished.is_set()
        next_update_requested.set()

    bot = SimpleNamespace(
        id=1,
        me=AsyncMock(return_value=SimpleNamespace(username="test-bot")),
    )
    monkeypatch.setattr(dispatcher, "_listen_updates", listen_updates)
    monkeypatch.setattr(dispatcher, "_process_update", process_payment)

    polling = asyncio.create_task(dispatcher._polling(bot=bot))
    await handler_started.wait()
    assert not next_update_requested.is_set()
    allow_commit.set()
    await polling

    assert durable_commit_finished.is_set()
    assert next_update_requested.is_set()


@pytest.mark.asyncio
async def test_poison_payment_update_does_not_stall_processing_of_other_updates(monkeypatch):
    dispatcher = PaymentSafeDispatcher()
    now = datetime.now(timezone.utc)
    repository = SimpleNamespace(
        process_successful_payment=AsyncMock(side_effect=ValueError("poison payload")),
        record_unprocessable_payment=AsyncMock(return_value=31),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    monkeypatch.setattr(premium.asyncio, "sleep", AsyncMock())

    def poison_payment_update():
        return SimpleNamespace(
            pre_checkout_query=None,
            message=SimpleNamespace(
                successful_payment=SimpleNamespace(
                    invoice_payload="selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
                    telegram_payment_charge_id="charge-poison-polling",
                    provider_payment_charge_id="",
                    total_amount=137,
                    currency="XTR",
                ),
                from_user=SimpleNamespace(id=123),
                date=now,
                answer=AsyncMock(),
            ),
        )

    plain_update = SimpleNamespace(
        pre_checkout_query=None,
        message=SimpleNamespace(successful_payment=None),
    )
    processed: list[str] = []

    async def process_update(*, bot, update, **_kwargs):
        if _requires_durable_processing(update):
            await premium.selara_ai_successful_payment(
                update.message,
                session_factory=object(),
                settings=SimpleNamespace(admin_user_id=900, bot_timezone="UTC"),
                bot=SimpleNamespace(send_message=AsyncMock()),
            )
            processed.append("payment")
        else:
            processed.append("other")

    async def listen_updates(*_args, **_kwargs):
        yield poison_payment_update()
        yield plain_update

    monkeypatch.setattr(dispatcher, "_listen_updates", listen_updates)
    monkeypatch.setattr(dispatcher, "_process_update", process_update)
    bot = SimpleNamespace(
        id=1,
        me=AsyncMock(return_value=SimpleNamespace(username="test-bot")),
    )

    await asyncio.wait_for(dispatcher._polling(bot=bot, handle_as_tasks=False), timeout=5)

    assert processed == ["payment", "other"]
    repository.record_unprocessable_payment.assert_awaited_once()


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
        invoice_payload="selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
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


def _provider_settings(monkeypatch, *, llm_enabled: bool) -> Settings:
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/selara_test")
    monkeypatch.setenv("LLM_ENABLED", "true" if llm_enabled else "false")
    monkeypatch.setenv("LLM_API_KEY", "test-provider-key")
    monkeypatch.setenv("SELARA_AI_PRICE_STARS", "137")
    return Settings(_env_file=None)


def _open_intent() -> PurchaseIntent:
    return PurchaseIntent(
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


def _pre_checkout_query(intent: PurchaseIntent) -> SimpleNamespace:
    return SimpleNamespace(
        id="query-1",
        invoice_payload=intent.invoice_payload,
        from_user=SimpleNamespace(id=123),
        total_amount=137,
        currency="XTR",
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("llm_enabled,expected_ok", [(False, False), (True, True)])
async def test_pre_checkout_rechecks_the_llm_provider_before_accepting(monkeypatch, llm_enabled, expected_ok):
    intent = _open_intent()
    repository = SimpleNamespace(
        get_purchase_intent=AsyncMock(return_value=intent),
        accept_pre_checkout=AsyncMock(return_value=PreCheckoutResult(True, chat_id=-100)),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    authority = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(premium, "_is_purchase_authorized", authority)
    settings = _provider_settings(monkeypatch, llm_enabled=llm_enabled)
    query = _pre_checkout_query(intent)

    await premium.selara_ai_pre_checkout(query, bot=object(), session_factory=object(), settings=settings)

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs["ok"] is expected_ok
    if expected_ok:
        repository.accept_pre_checkout.assert_awaited_once()
    else:
        repository.accept_pre_checkout.assert_not_awaited()
        authority.assert_not_awaited()


def _payment_message(now: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        successful_payment=SimpleNamespace(
            invoice_payload="selara_ai:v1:5f22bc5f-3cae-4ecf-a136-7b1a5d20a74e",
            telegram_payment_charge_id="charge-confirmation-text",
            provider_payment_charge_id="",
            total_amount=137,
            currency="XTR",
        ),
        from_user=SimpleNamespace(id=123),
        date=now,
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "summary_enabled,expected,forbidden",
    [
        (True, "уже включены", "выключены"),
        (False, "выключены: включите", "уже включены"),
        (None, "убедитесь", "уже включены"),
    ],
)
async def test_confirmation_does_not_promise_automatic_summaries_when_toggle_is_off(
    monkeypatch, summary_enabled, expected, forbidden
):
    now = datetime.now(timezone.utc)
    repository = SimpleNamespace(
        process_successful_payment=AsyncMock(
            return_value=PaymentResult("applied", chat_id=-100, valid_until=now + timedelta(days=30))
        ),
        get_chat_title=AsyncMock(return_value="Test group"),
        get_chat_daily_summary_enabled=AsyncMock(
            return_value=summary_enabled,
            side_effect=RuntimeError("db down") if summary_enabled is None else None,
        ),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    message = _payment_message(now)

    await premium.selara_ai_successful_payment(
        message,
        session_factory=object(),
        settings=SimpleNamespace(admin_user_id=None, bot_timezone="UTC"),
        bot=SimpleNamespace(send_message=AsyncMock()),
    )

    text = message.answer.await_args.args[0]
    assert expected in text
    assert forbidden not in text
    assert "AI-функции чата" not in text


@pytest.mark.asyncio
async def test_charge_conflict_alert_names_existing_payment_and_offers_no_refund(monkeypatch):
    now = datetime.now(timezone.utc)
    repository = SimpleNamespace(
        process_successful_payment=AsyncMock(
            return_value=PaymentResult("rejected", "charge_conflict", conflicting_payment_id=41)
        ),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    message = _payment_message(now)
    bot = SimpleNamespace(send_message=AsyncMock())

    await premium.selara_ai_successful_payment(
        message,
        session_factory=object(),
        settings=SimpleNamespace(admin_user_id=900, bot_timezone="UTC"),
        bot=bot,
    )

    bot.send_message.assert_awaited_once()
    text = bot.send_message.await_args.kwargs["text"]
    assert "payment record: 41" in text
    assert "/stars_refund" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,expected",
    [
        (
            TelegramBadRequest(method=SimpleNamespace(), message="Bad Request: CHARGE_ALREADY_REFUNDED"),
            "уже был выполнен",
        ),
        (TelegramBadRequest(method=SimpleNamespace(), message="Bad Request: other"), "отклонил возврат"),
    ],
)
async def test_refund_reply_is_sent_even_when_saving_the_outcome_fails(monkeypatch, error, expected):
    repository = SimpleNamespace(
        claim_rejected_payment_refund=AsyncMock(
            return_value=PaymentRefundClaim(
                "claimed", buyer_user_id=456, telegram_payment_charge_id="charge-refund", amount_stars=137
            )
        ),
        finish_rejected_payment_refund=AsyncMock(side_effect=RuntimeError("db down")),
    )
    monkeypatch.setattr(premium, "SqlAlchemyTelegramStarsRepository", lambda _factory: repository)
    bot = SimpleNamespace(refund_star_payment=AsyncMock(side_effect=error))
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(id=123),
        text="/stars_refund 17",
        answer=AsyncMock(),
    )

    await premium.refund_rejected_stars_payment(
        message, bot=bot, session_factory=object(), settings=SimpleNamespace(admin_user_id=123)
    )

    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    assert expected in text
    assert "Не удалось записать результат" in text
