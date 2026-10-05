from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.feature_access import AccessTier, FeatureEntitlement
from selara.application.selara_ai_product import (
    PURCHASE_INTENT_TTL,
    SELARA_AI_CURRENCY,
    SELARA_AI_PRODUCT_KEY,
    SelaraAiProduct,
    invoice_payload_for_intent,
    parse_invoice_payload,
)
from selara.infrastructure.db.models import (
    ChatEntitlementModel,
    ChatModel,
    SelaraAiPaymentModel,
    SelaraAiPurchaseIntentModel,
    UserChatActivityModel,
)
from selara.infrastructure.llm.features import AiFeature

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PurchaseIntent:
    id: str
    buyer_user_id: int
    source_chat_id: int
    chat_id: int
    chat_title: str | None
    product_key: str
    amount_stars: int
    currency: str
    duration_seconds: int
    invoice_payload: str
    status: str
    expires_at: datetime
    pre_checkout_query_id: str | None
    pre_checkout_accepted_at: datetime | None
    consumed_at: datetime | None


@dataclass(frozen=True, slots=True)
class PreCheckoutResult:
    accepted: bool
    reason: str | None = None
    chat_id: int | None = None


@dataclass(frozen=True, slots=True)
class PaymentResult:
    state: Literal["applied", "duplicate", "rejected"]
    reason: str | None = None
    chat_id: int | None = None
    valid_until: datetime | None = None
    entitlement_action: Literal["created", "extended"] | None = None


@dataclass(frozen=True, slots=True)
class PaymentTotals:
    successful_payment_count: int
    stars_revenue: int


def entitlement_lock_key(*, chat_id: int, product_key: str) -> int:
    payload = f"selara-ai-entitlement\0{product_key}\0{chat_id}".encode("utf-8")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big", signed=True)


def _payment_lock_key(charge_id: str) -> int:
    payload = f"selara-ai-payment\0{charge_id}".encode("utf-8")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big", signed=True)


def _require_postgresql(session: AsyncSession) -> None:
    if session.bind is None or session.bind.dialect.name != "postgresql":
        raise RuntimeError("Telegram Stars purchase transactions require PostgreSQL")


async def _advisory_xact_lock(session: AsyncSession, lock_key: int) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})


def _snapshot(row: SelaraAiPurchaseIntentModel) -> PurchaseIntent:
    return PurchaseIntent(
        id=row.id,
        buyer_user_id=row.buyer_user_id,
        source_chat_id=row.source_chat_id,
        chat_id=row.chat_id,
        chat_title=row.chat_title,
        product_key=row.product_key,
        amount_stars=row.amount_stars,
        currency=row.currency,
        duration_seconds=row.duration_seconds,
        invoice_payload=row.invoice_payload,
        status=row.status,
        expires_at=row.expires_at,
        pre_checkout_query_id=row.pre_checkout_query_id,
        pre_checkout_accepted_at=row.pre_checkout_accepted_at,
        consumed_at=row.consumed_at,
    )


class SqlAlchemyChatEntitlementResolver:
    """PostgreSQL resolver wired into the existing FeatureAccessService seam."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def resolve(self, *, chat_id: int, feature: AiFeature, trigger: str) -> FeatureEntitlement:
        if feature not in {AiFeature.LLM_ADMIN, AiFeature.DAILY_SUMMARY}:
            return FeatureEntitlement(access_tier=AccessTier.FREE)
        try:
            async with self._session_factory() as session:
                row = await session.scalar(
                    select(ChatEntitlementModel).where(
                        ChatEntitlementModel.chat_id == chat_id,
                        ChatEntitlementModel.product_key == SELARA_AI_PRODUCT_KEY,
                        ChatEntitlementModel.status == "active",
                    )
                )
        except Exception:
            logger.warning(
                "Chat entitlement resolution failed chat_id=%s feature=%s trigger=%s",
                chat_id,
                feature.value,
                trigger,
            )
            raise
        if row is None:
            return FeatureEntitlement(access_tier=AccessTier.FREE)
        return FeatureEntitlement(
            access_tier=AccessTier.PAID,
            valid_until=row.valid_until,
            source="telegram_stars",
            product_key=row.product_key,
        )


class SqlAlchemyTelegramStarsRepository:
    """Intent, Stars payment, and entitlement transactions backed by PostgreSQL."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_purchasable_chats(self, *, user_id: int, limit: int = 25) -> list[ChatModel]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(ChatModel)
                .join(UserChatActivityModel, UserChatActivityModel.chat_id == ChatModel.telegram_chat_id)
                .where(
                    UserChatActivityModel.user_id == user_id,
                    UserChatActivityModel.is_active_member.is_(True),
                    ChatModel.is_bot_member.is_(True),
                    ChatModel.type.in_(("group", "supergroup")),
                )
                .order_by(UserChatActivityModel.last_seen_at.desc(), ChatModel.title.asc())
                .limit(min(max(limit, 1), 50))
            )
            return list(rows)

    async def user_has_known_chat(self, *, user_id: int, chat_id: int) -> bool:
        async with self._session_factory() as session:
            return bool(
                await session.scalar(
                    select(UserChatActivityModel.chat_id)
                    .join(ChatModel, ChatModel.telegram_chat_id == UserChatActivityModel.chat_id)
                    .where(
                        UserChatActivityModel.user_id == user_id,
                        UserChatActivityModel.chat_id == chat_id,
                        UserChatActivityModel.is_active_member.is_(True),
                        ChatModel.is_bot_member.is_(True),
                        ChatModel.type.in_(("group", "supergroup")),
                    )
                )
            )

    async def create_purchase_intent(
        self,
        *,
        buyer_user_id: int,
        source_chat_id: int,
        chat_id: int,
        chat_title: str | None,
        product: SelaraAiProduct,
        now: datetime | None = None,
    ) -> PurchaseIntent:
        current = _as_utc(now or datetime.now(timezone.utc))
        intent_id = str(uuid4())
        payload = invoice_payload_for_intent(intent_id)
        async with self._session_factory() as session:
            async with session.begin():
                _require_postgresql(session)
                chat = await session.scalar(
                    select(ChatModel).where(ChatModel.telegram_chat_id == chat_id).with_for_update()
                )
                if (
                    chat is None
                    or chat.type not in {"group", "supergroup"}
                    or not chat.is_bot_member
                ):
                    raise ValueError("Purchase target is not an available bot chat")
                row = SelaraAiPurchaseIntentModel(
                    id=intent_id,
                    buyer_user_id=buyer_user_id,
                    source_chat_id=source_chat_id,
                    chat_id=chat_id,
                    chat_title=chat_title,
                    product_key=product.key,
                    amount_stars=product.price_stars,
                    currency=product.currency,
                    duration_seconds=int(product.duration.total_seconds()),
                    invoice_payload=payload,
                    status="open",
                    created_at=current,
                    expires_at=current + PURCHASE_INTENT_TTL,
                )
                session.add(row)
                await session.flush()
                result = _snapshot(row)
        logger.info(
            "Selara AI purchase intent created chat_id=%s product=%s",
            chat_id,
            product.key,
        )
        return result

    async def get_purchase_intent(self, *, invoice_payload: str) -> PurchaseIntent | None:
        intent_id = parse_invoice_payload(invoice_payload)
        if intent_id is None:
            return None
        async with self._session_factory() as session:
            row = await session.get(SelaraAiPurchaseIntentModel, intent_id)
            if row is None or row.invoice_payload != invoice_payload:
                return None
            return _snapshot(row)

    async def mark_invoice_sent(self, *, intent_id: str, now: datetime | None = None) -> bool:
        async with self._session_factory() as session:
            async with session.begin():
                _require_postgresql(session)
                row = await session.scalar(
                    select(SelaraAiPurchaseIntentModel)
                    .where(SelaraAiPurchaseIntentModel.id == intent_id)
                    .with_for_update()
                )
                if row is None:
                    return False
                row.invoice_sent_at = _as_utc(now or datetime.now(timezone.utc))
                return True

    async def accept_pre_checkout(
        self,
        *,
        invoice_payload: str,
        buyer_user_id: int,
        amount_stars: int,
        currency: str,
        query_id: str,
        checked_chat_id: int,
        now: datetime | None = None,
    ) -> PreCheckoutResult:
        intent_id = parse_invoice_payload(invoice_payload)
        if intent_id is None:
            logger.warning("Telegram Stars pre-checkout rejected reason=invalid_payload")
            return PreCheckoutResult(False, "invalid_payload")
        current = _as_utc(now or datetime.now(timezone.utc))
        async with self._session_factory() as session:
            async with session.begin():
                _require_postgresql(session)
                row = await session.scalar(
                    select(SelaraAiPurchaseIntentModel)
                    .where(SelaraAiPurchaseIntentModel.id == intent_id)
                    .with_for_update()
                )
                if row is None or row.invoice_payload != invoice_payload:
                    result = PreCheckoutResult(False, "unknown_intent")
                elif (
                    row.pre_checkout_query_id == query_id
                    and row.pre_checkout_accepted_at is not None
                    and row.status in {"checkout_accepted", "consumed"}
                ):
                    result = PreCheckoutResult(True, chat_id=row.chat_id)
                elif row.buyer_user_id != buyer_user_id:
                    result = PreCheckoutResult(False, "wrong_buyer")
                elif row.product_key != SELARA_AI_PRODUCT_KEY:
                    result = PreCheckoutResult(False, "unsupported_product")
                elif row.amount_stars != amount_stars:
                    result = PreCheckoutResult(False, "wrong_amount")
                elif row.currency != SELARA_AI_CURRENCY or currency != row.currency:
                    result = PreCheckoutResult(False, "wrong_currency")
                elif row.expires_at <= current:
                    result = PreCheckoutResult(False, "expired_intent")
                elif row.status == "consumed" or row.pre_checkout_accepted_at is not None:
                    result = PreCheckoutResult(False, "intent_already_used")
                elif row.chat_id != checked_chat_id:
                    result = PreCheckoutResult(False, "target_changed", chat_id=row.chat_id)
                else:
                    chat = await session.scalar(
                        select(ChatModel).where(ChatModel.telegram_chat_id == row.chat_id)
                    )
                    if chat is None or chat.type not in {"group", "supergroup"} or not chat.is_bot_member:
                        result = PreCheckoutResult(False, "target_unavailable", chat_id=row.chat_id)
                    else:
                        row.pre_checkout_query_id = query_id
                        row.pre_checkout_accepted_at = current
                        row.status = "checkout_accepted"
                        result = PreCheckoutResult(True, chat_id=row.chat_id)
        if result.accepted:
            logger.info("Telegram Stars pre-checkout accepted")
        else:
            logger.warning("Telegram Stars pre-checkout rejected reason=%s", result.reason)
        return result

    async def process_successful_payment(
        self,
        *,
        buyer_user_id: int,
        invoice_payload: str,
        telegram_payment_charge_id: str,
        provider_payment_charge_id: str | None,
        amount_stars: int,
        currency: str,
        payment_at: datetime,
    ) -> PaymentResult:
        """Persist payment, intent consumption, and entitlement extension atomically."""
        if not telegram_payment_charge_id:
            raise ValueError("Telegram payment charge identifier is required for idempotency")
        paid_at = _as_utc(payment_at)
        intent_id = parse_invoice_payload(invoice_payload)
        async with self._session_factory() as session:
            async with session.begin():
                _require_postgresql(session)
                await _advisory_xact_lock(session, _payment_lock_key(telegram_payment_charge_id))
                existing_payment = await session.scalar(
                    select(SelaraAiPaymentModel)
                    .where(SelaraAiPaymentModel.telegram_payment_charge_id == telegram_payment_charge_id)
                    .with_for_update()
                )
                if existing_payment is not None:
                    if not _payment_matches(
                        existing_payment,
                        buyer_user_id=buyer_user_id,
                        invoice_payload=invoice_payload,
                        amount_stars=amount_stars,
                        currency=currency,
                        provider_payment_charge_id=provider_payment_charge_id,
                    ):
                        logger.error("Telegram Stars charge identifier conflict; duplicate was not applied")
                        result = PaymentResult("rejected", "charge_conflict")
                    elif existing_payment.processing_state == "rejected":
                        result = PaymentResult("rejected", existing_payment.processing_reason)
                    else:
                        result = await self._duplicate_result(session, existing_payment)
                else:
                    intent = None
                    if intent_id is not None:
                        intent = await session.scalar(
                            select(SelaraAiPurchaseIntentModel)
                            .where(SelaraAiPurchaseIntentModel.id == intent_id)
                            .with_for_update()
                        )
                        if intent is not None and intent.invoice_payload != invoice_payload:
                            intent = None
                    reason = _payment_rejection_reason(
                        intent,
                        intent_id=intent_id,
                        buyer_user_id=buyer_user_id,
                        amount_stars=amount_stars,
                        currency=currency,
                    )
                    payment = SelaraAiPaymentModel(
                        telegram_payment_charge_id=telegram_payment_charge_id,
                        provider_payment_charge_id=provider_payment_charge_id,
                        invoice_payload=invoice_payload,
                        purchase_intent_id=intent.id if intent is not None else None,
                        buyer_user_id=buyer_user_id,
                        source_chat_id=intent.source_chat_id if intent is not None else None,
                        target_chat_id=intent.chat_id if intent is not None else None,
                        product_key=intent.product_key if intent is not None else None,
                        amount_stars=amount_stars,
                        currency=currency,
                        payment_at=paid_at,
                        processing_state="rejected" if reason else "applied",
                        processing_reason=reason,
                    )
                    session.add(payment)
                    await session.flush()
                    if reason is not None:
                        result = PaymentResult("rejected", reason, chat_id=intent.chat_id if intent else None)
                    else:
                        assert intent is not None
                        await _advisory_xact_lock(
                            session,
                            entitlement_lock_key(chat_id=intent.chat_id, product_key=intent.product_key),
                        )
                        entitlement = await session.scalar(
                            select(ChatEntitlementModel)
                            .where(
                                ChatEntitlementModel.chat_id == intent.chat_id,
                                ChatEntitlementModel.product_key == intent.product_key,
                            )
                            .with_for_update()
                        )
                        duration = timedelta(seconds=intent.duration_seconds)
                        entitlement_action: Literal["created", "extended"]
                        if entitlement is None:
                            entitlement_action = "created"
                            entitlement = ChatEntitlementModel(
                                chat_id=intent.chat_id,
                                product_key=intent.product_key,
                                status="active",
                                valid_from=paid_at,
                                valid_until=paid_at + duration,
                            )
                            session.add(entitlement)
                        else:
                            entitlement_action = "extended"
                            if entitlement.status == "active" and entitlement.valid_until > paid_at:
                                base = entitlement.valid_until
                            else:
                                base = paid_at
                                entitlement.valid_from = paid_at
                            entitlement.valid_until = base + duration
                            entitlement.status = "active"
                            entitlement.updated_at = func.now()
                        intent.status = "consumed"
                        intent.consumed_at = intent.consumed_at or paid_at
                        await session.flush()
                        result = PaymentResult(
                            "applied",
                            chat_id=intent.chat_id,
                            valid_until=entitlement.valid_until,
                            entitlement_action=entitlement_action,
                        )

        if result.state == "applied":
            logger.info(
                "Telegram Stars payment applied chat_id=%s product=%s entitlement_action=%s",
                result.chat_id,
                SELARA_AI_PRODUCT_KEY,
                result.entitlement_action,
            )
        elif result.state == "duplicate":
            logger.info("Duplicate Telegram Stars payment ignored")
        else:
            logger.error("Telegram Stars payment rejected reason=%s", result.reason)
        return result

    async def _duplicate_result(
        self,
        session: AsyncSession,
        payment: SelaraAiPaymentModel,
    ) -> PaymentResult:
        if payment.purchase_intent_id is None or payment.product_key is None:
            return PaymentResult("duplicate", "duplicate_rejected")
        intent = await session.scalar(
            select(SelaraAiPurchaseIntentModel).where(
                SelaraAiPurchaseIntentModel.id == payment.purchase_intent_id
            )
        )
        chat_id = intent.chat_id if intent is not None else payment.target_chat_id
        valid_until = None
        if chat_id is not None:
            valid_until = await session.scalar(
                select(ChatEntitlementModel.valid_until).where(
                    ChatEntitlementModel.chat_id == chat_id,
                    ChatEntitlementModel.product_key == payment.product_key,
                )
            )
        return PaymentResult("duplicate", chat_id=chat_id, valid_until=valid_until)

    async def get_entitlement(self, *, chat_id: int, product_key: str = SELARA_AI_PRODUCT_KEY) -> ChatEntitlementModel | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ChatEntitlementModel).where(
                    ChatEntitlementModel.chat_id == chat_id,
                    ChatEntitlementModel.product_key == product_key,
                )
            )

    async def get_chat_title(self, *, chat_id: int) -> str | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ChatModel.title).where(ChatModel.telegram_chat_id == chat_id)
            )

    async def active_paid_chat_count(self, *, now: datetime | None = None) -> int:
        current = _as_utc(now or datetime.now(timezone.utc))
        async with self._session_factory() as session:
            return int(
                await session.scalar(
                    select(func.count(ChatEntitlementModel.id)).where(
                        ChatEntitlementModel.product_key == SELARA_AI_PRODUCT_KEY,
                        ChatEntitlementModel.status == "active",
                        ChatEntitlementModel.valid_until > current,
                    )
                )
                or 0
            )

    async def payment_totals(self) -> PaymentTotals:
        async with self._session_factory() as session:
            count, revenue = (
                await session.execute(
                    select(
                        func.count(SelaraAiPaymentModel.id),
                        func.coalesce(func.sum(SelaraAiPaymentModel.amount_stars), 0),
                    ).where(SelaraAiPaymentModel.processing_state == "applied")
                )
            ).one()
            return PaymentTotals(int(count), int(revenue))

    async def list_payment_history(
        self,
        *,
        chat_id: int | None = None,
        buyer_user_id: int | None = None,
        limit: int = 100,
    ) -> list[SelaraAiPaymentModel]:
        statement = select(SelaraAiPaymentModel)
        if chat_id is not None:
            statement = statement.where(
                (SelaraAiPaymentModel.target_chat_id == chat_id)
                | (SelaraAiPaymentModel.source_chat_id == chat_id)
            )
        if buyer_user_id is not None:
            statement = statement.where(SelaraAiPaymentModel.buyer_user_id == buyer_user_id)
        statement = statement.order_by(SelaraAiPaymentModel.payment_at.desc()).limit(min(max(limit, 1), 500))
        async with self._session_factory() as session:
            return list(await session.scalars(statement))


def _payment_rejection_reason(
    intent: SelaraAiPurchaseIntentModel | None,
    *,
    intent_id: str | None,
    buyer_user_id: int,
    amount_stars: int,
    currency: str,
) -> str | None:
    if intent_id is None or intent is None:
        return "unknown_intent"
    if intent.buyer_user_id != buyer_user_id:
        return "wrong_buyer"
    if intent.product_key != SELARA_AI_PRODUCT_KEY:
        return "unsupported_product"
    if amount_stars != intent.amount_stars:
        return "wrong_amount"
    if currency != intent.currency or currency != SELARA_AI_CURRENCY:
        return "wrong_currency"
    # Expiry and live admin/bot checks apply before payment. Once Telegram has
    # confirmed payment, they must not cancel the economic effect.
    return None


def _payment_matches(
    payment: SelaraAiPaymentModel,
    *,
    buyer_user_id: int,
    invoice_payload: str,
    amount_stars: int,
    currency: str,
    provider_payment_charge_id: str | None,
) -> bool:
    return (
        payment.buyer_user_id == buyer_user_id
        and payment.invoice_payload == invoice_payload
        and payment.amount_stars == amount_stars
        and payment.currency == currency
        and payment.provider_payment_charge_id == provider_payment_charge_id
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
