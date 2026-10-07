from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sqlalchemy.dialects.postgresql import insert as pg_insert

from selara.application.feature_access import (
    AIL_UNIT,
    PET_FEATURES,
    AccessTier,
    FeatureEntitlement,
    GroupMemberQuotaLimits,
    PersonalQuotaLimits,
    DEFAULT_PET_TALK_DAILY_LIMIT,
    paid_group_member_policy,
    paid_personal_policy,
    paid_pet_policy,
)
from selara.application.personal_config import PersonalConfigProvider
from selara.application.selara_ai_product import (
    PRODUCT_SCOPE_CHAT,
    PRODUCT_SCOPE_USER,
    PURCHASE_INTENT_TTL,
    SELARA_AI_CURRENCY,
    SELARA_AI_PRODUCT_KEY,
    SELARA_PERSONAL_PRODUCT_KEY,
    SelaraAiProduct,
    get_product_spec,
    invoice_payload_for_intent,
    parse_invoice_payload,
)
from selara.infrastructure.db.models import (
    ChatEntitlementModel,
    ChatModel,
    ChatSettingsModel,
    SelaraAiPaymentModel,
    SelaraAiPurchaseIntentModel,
    UserChatActivityModel,
    UserEntitlementModel,
    UserModel,
)
from selara.infrastructure.db.selara_ai_payment_refund import SelaraAiPaymentRefundModel
from selara.infrastructure.llm.features import AiFeature

logger = logging.getLogger(__name__)
PURCHASE_INTENT_CREATE_COOLDOWN = timedelta(minutes=1)


class PurchaseIntentRateLimited(Exception):
    """Raised when the same buyer requests another invoice too soon for a chat."""


@dataclass(frozen=True, slots=True)
class PurchaseIntent:
    id: str
    buyer_user_id: int
    source_chat_id: int | None
    chat_id: int | None
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
    terms_version: str | None = None
    terms_accepted_at: datetime | None = None
    target_scope: str = PRODUCT_SCOPE_CHAT
    target_user_id: int | None = None


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
    payment_id: int | None = None
    # Set only for charge_conflict: the already stored payment that owns the charge id.
    # The conflicting update itself is not persisted, so it has no refundable payment_id.
    conflicting_payment_id: int | None = None
    # "chat" payments activate chat_id; "user" payments (Selara Personal) activate user_id.
    target_scope: str = PRODUCT_SCOPE_CHAT
    user_id: int | None = None


@dataclass(frozen=True, slots=True)
class PaymentRefundClaim:
    state: Literal["claimed", "pending", "refunded", "failed", "not_found", "not_rejected"]
    buyer_user_id: int | None = None
    telegram_payment_charge_id: str | None = None
    amount_stars: int | None = None


@dataclass(frozen=True, slots=True)
class PaymentTotals:
    successful_payment_count: int
    stars_revenue: int


def entitlement_lock_key(*, chat_id: int, product_key: str) -> int:
    payload = f"selara-ai-entitlement\0{product_key}\0{chat_id}".encode("utf-8")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big", signed=True)


def user_entitlement_lock_key(*, user_id: int, product_key: str) -> int:
    payload = f"selara-ai-user-entitlement\0{product_key}\0{user_id}".encode("utf-8")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big", signed=True)


def _personal_intent_lock_key(buyer_user_id: int) -> int:
    payload = f"selara-personal-intent\0{buyer_user_id}".encode("utf-8")
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
        terms_version=row.terms_version,
        terms_accepted_at=row.terms_accepted_at,
        target_scope=row.target_scope,
        target_user_id=row.target_user_id,
    )


def _snapshot_limits(limits: PersonalQuotaLimits, sold_paid_limit: int | None) -> PersonalQuotaLimits:
    """The limit a subscriber bought, never below the current free limit (and the config for old rows)."""
    paid = sold_paid_limit if sold_paid_limit is not None else limits.paid_daily
    return PersonalQuotaLimits(free_daily=limits.free_daily, paid_daily=max(paid, limits.free_daily + 1))


class SqlAlchemyUserEntitlementResolver:
    """PostgreSQL resolver for the personal (user-scoped) entitlement."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        config: PersonalConfigProvider,
        *,
        pet_daily_limit: int = DEFAULT_PET_TALK_DAILY_LIMIT,
    ) -> None:
        self._session_factory = session_factory
        self._config = config
        self._pet_daily_limit = pet_daily_limit

    async def resolve(self, *, user_id: int, feature: AiFeature, trigger: str) -> FeatureEntitlement:
        if feature != AiFeature.PERSONAL_CHAT and feature not in PET_FEATURES:
            return FeatureEntitlement(access_tier=AccessTier.FREE)
        limits = (await self._config.get()).active_limits
        try:
            async with self._session_factory() as session:
                row = await session.scalar(
                    select(UserEntitlementModel).where(
                        UserEntitlementModel.user_id == user_id,
                        UserEntitlementModel.product_key == SELARA_PERSONAL_PRODUCT_KEY,
                        UserEntitlementModel.status == "active",
                    )
                )
        except Exception:
            logger.warning(
                "User entitlement resolution failed feature=%s trigger=%s",
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
            quota_policy=(
                paid_pet_policy(self._pet_daily_limit, feature, limits if limits.unit == AIL_UNIT else None)
                if feature in PET_FEATURES
                # The sold request limit is a requests-mode promise; AIL budgets come from the config.
                else paid_personal_policy(
                    limits if limits.unit == AIL_UNIT else _snapshot_limits(limits, row.paid_daily_limit)
                )
            ),
        )


class SqlAlchemyChatEntitlementResolver:
    """PostgreSQL resolver wired into the existing FeatureAccessService seam."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        group_member_limits: GroupMemberQuotaLimits | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._group_member_limits = group_member_limits

    async def resolve(self, *, chat_id: int, feature: AiFeature, trigger: str) -> FeatureEntitlement:
        if feature not in {AiFeature.LLM_ADMIN, AiFeature.DAILY_SUMMARY, AiFeature.GROUP_MEMBER}:
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
        quota_policy = None
        if feature == AiFeature.GROUP_MEMBER:
            if self._group_member_limits is None:
                # Without configured limits the paid tier keeps the free policy rather than going unlimited.
                return FeatureEntitlement(access_tier=AccessTier.FREE)
            quota_policy = paid_group_member_policy(self._group_member_limits)
        return FeatureEntitlement(
            access_tier=AccessTier.PAID,
            valid_until=row.valid_until,
            source="telegram_stars",
            product_key=row.product_key,
            quota_policy=quota_policy,
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
        terms_version: str,
        terms_accepted_at: datetime,
        now: datetime | None = None,
    ) -> PurchaseIntent:
        if not terms_version.strip():
            raise ValueError("Terms acceptance version is required")
        if product.scope != PRODUCT_SCOPE_CHAT:
            raise ValueError("Chat purchase intents require a chat-scoped product")
        current = _as_utc(now or datetime.now(timezone.utc))
        accepted_at = _as_utc(terms_accepted_at)
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
                recent_intent_id = await session.scalar(
                    select(SelaraAiPurchaseIntentModel.id)
                    .where(
                        SelaraAiPurchaseIntentModel.buyer_user_id == buyer_user_id,
                        SelaraAiPurchaseIntentModel.chat_id == chat_id,
                        SelaraAiPurchaseIntentModel.created_at
                        >= current - PURCHASE_INTENT_CREATE_COOLDOWN,
                    )
                    .limit(1)
                )
                if recent_intent_id is not None:
                    raise PurchaseIntentRateLimited
                row = SelaraAiPurchaseIntentModel(
                    id=intent_id,
                    buyer_user_id=buyer_user_id,
                    source_chat_id=source_chat_id,
                    chat_id=chat_id,
                    chat_title=chat_title,
                    product_key=product.key,
                    target_scope=PRODUCT_SCOPE_CHAT,
                    amount_stars=product.price_stars,
                    currency=product.currency,
                    duration_seconds=int(product.duration.total_seconds()),
                    invoice_payload=payload,
                    terms_version=terms_version,
                    terms_accepted_at=accepted_at,
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

    async def create_personal_purchase_intent(
        self,
        *,
        buyer_user_id: int,
        product: SelaraAiProduct,
        terms_version: str,
        terms_accepted_at: datetime,
        now: datetime | None = None,
    ) -> PurchaseIntent:
        """Create an intent for the buyer's own personal subscription (gifts are off in the MVP)."""
        if not terms_version.strip():
            raise ValueError("Terms acceptance version is required")
        if product.scope != PRODUCT_SCOPE_USER:
            raise ValueError("Personal purchase intents require a user-scoped product")
        if product.paid_daily_limit is None or product.paid_daily_limit <= 0:
            raise ValueError("Personal purchase intents must carry the daily limit they sell")
        current = _as_utc(now or datetime.now(timezone.utc))
        accepted_at = _as_utc(terms_accepted_at)
        intent_id = str(uuid4())
        payload = invoice_payload_for_intent(intent_id)
        async with self._session_factory() as session:
            async with session.begin():
                _require_postgresql(session)
                # Serialize the cooldown check per buyer so concurrent taps cannot both pass it.
                await _advisory_xact_lock(session, _personal_intent_lock_key(buyer_user_id))
                recent_intent_id = await session.scalar(
                    select(SelaraAiPurchaseIntentModel.id)
                    .where(
                        SelaraAiPurchaseIntentModel.buyer_user_id == buyer_user_id,
                        SelaraAiPurchaseIntentModel.target_scope == PRODUCT_SCOPE_USER,
                        SelaraAiPurchaseIntentModel.created_at
                        >= current - PURCHASE_INTENT_CREATE_COOLDOWN,
                    )
                    .limit(1)
                )
                if recent_intent_id is not None:
                    raise PurchaseIntentRateLimited
                row = SelaraAiPurchaseIntentModel(
                    id=intent_id,
                    buyer_user_id=buyer_user_id,
                    source_chat_id=None,
                    chat_id=None,
                    chat_title=None,
                    target_scope=PRODUCT_SCOPE_USER,
                    target_user_id=buyer_user_id,
                    paid_daily_limit=product.paid_daily_limit,
                    product_key=product.key,
                    amount_stars=product.price_stars,
                    currency=product.currency,
                    duration_seconds=int(product.duration.total_seconds()),
                    invoice_payload=payload,
                    terms_version=terms_version,
                    terms_accepted_at=accepted_at,
                    status="open",
                    created_at=current,
                    expires_at=current + PURCHASE_INTENT_TTL,
                )
                session.add(row)
                await session.flush()
                result = _snapshot(row)
        logger.info("Selara Personal purchase intent created product=%s", product.key)
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
        checked_chat_id: int | None = None,
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
                elif row.buyer_user_id != buyer_user_id:
                    result = PreCheckoutResult(False, "wrong_buyer")
                elif not _product_matches_scope(row.product_key, row.target_scope):
                    result = PreCheckoutResult(False, "unsupported_product")
                elif row.target_scope == PRODUCT_SCOPE_USER and row.target_user_id != buyer_user_id:
                    result = PreCheckoutResult(False, "wrong_buyer")
                elif row.amount_stars != amount_stars:
                    result = PreCheckoutResult(False, "wrong_amount")
                elif row.currency != SELARA_AI_CURRENCY or currency != row.currency:
                    result = PreCheckoutResult(False, "wrong_currency")
                elif row.expires_at <= current:
                    result = PreCheckoutResult(False, "expired_intent")
                elif row.status == "consumed":
                    result = PreCheckoutResult(False, "intent_already_used")
                elif row.terms_version is None or row.terms_accepted_at is None:
                    result = PreCheckoutResult(False, "terms_not_accepted")
                elif row.target_scope == PRODUCT_SCOPE_USER:
                    # A personal purchase targets the buyer: there is no chat to check.
                    row.pre_checkout_query_id = query_id
                    row.pre_checkout_accepted_at = current
                    row.status = "checkout_accepted"
                    result = PreCheckoutResult(True)
                elif row.chat_id != checked_chat_id:
                    result = PreCheckoutResult(False, "target_changed", chat_id=row.chat_id)
                else:
                    chat = await session.scalar(
                        select(ChatModel).where(ChatModel.telegram_chat_id == row.chat_id)
                    )
                    if chat is None or chat.type not in {"group", "supergroup"} or not chat.is_bot_member:
                        result = PreCheckoutResult(False, "target_unavailable", chat_id=row.chat_id)
                    else:
                        # Telegram may retry with a new pre_checkout_query id if its
                        # previous answer was lost. Replacing this audit marker is safe:
                        # only successful_payment consumes the intent economically.
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
                        result = PaymentResult(
                            "rejected", "charge_conflict", conflicting_payment_id=existing_payment.id
                        )
                    elif existing_payment.processing_state == "rejected":
                        result = PaymentResult(
                            "rejected",
                            existing_payment.processing_reason,
                            payment_id=existing_payment.id,
                        )
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
                        target_scope=intent.target_scope if intent is not None else PRODUCT_SCOPE_CHAT,
                        target_user_id=intent.target_user_id if intent is not None else None,
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
                        result = PaymentResult(
                            "rejected",
                            reason,
                            chat_id=intent.chat_id if intent else None,
                            payment_id=payment.id,
                            target_scope=intent.target_scope if intent else PRODUCT_SCOPE_CHAT,
                            user_id=intent.target_user_id if intent else None,
                        )
                    elif intent is not None and intent.target_scope == PRODUCT_SCOPE_USER:
                        result = await self._apply_user_entitlement(
                            session, intent=intent, payment_id=payment.id, paid_at=paid_at
                        )
                    else:
                        assert intent is not None and intent.chat_id is not None
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
                            _extend_entitlement(entitlement, paid_at=paid_at, duration=duration)
                        intent.status = "consumed"
                        intent.consumed_at = intent.consumed_at or paid_at
                        await session.flush()
                        result = PaymentResult(
                            "applied",
                            chat_id=intent.chat_id,
                            valid_until=entitlement.valid_until,
                            entitlement_action=entitlement_action,
                            payment_id=payment.id,
                        )

        if result.state == "applied":
            logger.info(
                "Telegram Stars payment applied scope=%s chat_id=%s product=%s entitlement_action=%s",
                result.target_scope,
                result.chat_id,
                SELARA_PERSONAL_PRODUCT_KEY if result.target_scope == PRODUCT_SCOPE_USER else SELARA_AI_PRODUCT_KEY,
                result.entitlement_action,
            )
        elif result.state == "duplicate":
            logger.info("Duplicate Telegram Stars payment ignored")
        else:
            logger.error("Telegram Stars payment rejected reason=%s", result.reason)
        return result

    async def _apply_user_entitlement(
        self,
        session: AsyncSession,
        *,
        intent: SelaraAiPurchaseIntentModel,
        payment_id: int,
        paid_at: datetime,
    ) -> PaymentResult:
        """Grant or extend the buyer's personal entitlement inside the payment transaction."""
        assert intent.target_user_id is not None
        user_id = intent.target_user_id
        # The paying user normally exists already; never let a missing row lose a confirmed payment.
        await session.execute(
            pg_insert(UserModel)
            .values(telegram_user_id=user_id, is_bot=False)
            .on_conflict_do_nothing(index_elements=[UserModel.telegram_user_id])
        )
        await _advisory_xact_lock(
            session,
            user_entitlement_lock_key(user_id=user_id, product_key=intent.product_key),
        )
        entitlement = await session.scalar(
            select(UserEntitlementModel)
            .where(
                UserEntitlementModel.user_id == user_id,
                UserEntitlementModel.product_key == intent.product_key,
            )
            .with_for_update()
        )
        duration = timedelta(seconds=intent.duration_seconds)
        entitlement_action: Literal["created", "extended"]
        if entitlement is None:
            entitlement_action = "created"
            entitlement = UserEntitlementModel(
                user_id=user_id,
                product_key=intent.product_key,
                status="active",
                valid_from=paid_at,
                valid_until=paid_at + duration,
                paid_daily_limit=intent.paid_daily_limit,
            )
            session.add(entitlement)
        else:
            entitlement_action = "extended"
            still_active = entitlement.status == "active" and entitlement.valid_until > paid_at
            previous_limit = entitlement.paid_daily_limit
            _extend_entitlement(entitlement, paid_at=paid_at, duration=duration)
            # While the subscription is active the larger limit wins, so days that were
            # already paid for are never cut by a later, lower offer; after expiry the
            # new purchase simply sets its own limit. Config edits never touch this value.
            if intent.paid_daily_limit is not None:
                if still_active and previous_limit is not None:
                    entitlement.paid_daily_limit = max(previous_limit, intent.paid_daily_limit)
                else:
                    entitlement.paid_daily_limit = intent.paid_daily_limit
        intent.status = "consumed"
        intent.consumed_at = intent.consumed_at or paid_at
        await session.flush()
        return PaymentResult(
            "applied",
            valid_until=entitlement.valid_until,
            entitlement_action=entitlement_action,
            payment_id=payment_id,
            target_scope=PRODUCT_SCOPE_USER,
            user_id=user_id,
        )

    async def record_unprocessable_payment(
        self,
        *,
        buyer_user_id: int,
        invoice_payload: str,
        telegram_payment_charge_id: str,
        provider_payment_charge_id: str | None,
        amount_stars: int,
        currency: str,
        payment_at: datetime,
    ) -> int | None:
        """Durably dead-letter a confirmed payment whose processing keeps failing.

        The row reuses the rejected-payment audit (state ``rejected`` with reason
        ``processing_failed``) so the existing owner alerts and ``/stars_refund``
        stay usable even though the economic effect was never applied. Idempotent
        on the charge id; returns the stored payment id, or ``None`` when the row
        could not be written (the caller must keep the update unacknowledged).

        The charge id is the key Telegram accepts for refunds, so an id longer
        than the audit column is refused instead of truncated: a truncated value
        could both collide with another charge on the unique index and fail every
        later ``/stars_refund``. A missing or empty charge id is refused as well:
        an empty value cannot key a unique audit row, so distinct poison payments
        would collapse onto a single dead-letter record and every payment after
        the first would be silently lost. Scope, target, and product are copied
        from the resolvable intent so a failed personal purchase stays visible in
        the user-scoped payment views instead of being filed as a chat purchase.
        """
        charge_id = telegram_payment_charge_id
        if not charge_id or not charge_id.strip():
            # The audit index keys on the charge id, so an empty value would make
            # ON CONFLICT DO NOTHING file every such payment onto one dead-letter
            # row and silently lose all but the first. Refuse the write instead:
            # the caller keeps the update unacknowledged and retrying.
            logger.error(
                "Telegram Stars dead-letter refused: charge id is missing, so it "
                "cannot key a unique dead-letter row; keeping the update "
                "unacknowledged",
            )
            return None
        if len(charge_id) > 255:
            logger.error(
                "Telegram Stars dead-letter refused: charge id exceeds the audit "
                "column limit and cannot be stored verbatim length=%s",
                len(charge_id),
            )
            return None
        paid_at = _as_utc(payment_at)
        async with self._session_factory() as session:
            async with session.begin():
                _require_postgresql(session)
                await _advisory_xact_lock(session, _payment_lock_key(charge_id))
                intent = None
                intent_id = parse_invoice_payload(invoice_payload)
                if intent_id is not None:
                    intent = await session.scalar(
                        select(SelaraAiPurchaseIntentModel).where(
                            SelaraAiPurchaseIntentModel.id == intent_id
                        )
                    )
                    if intent is not None and intent.invoice_payload != invoice_payload:
                        intent = None
                # Copy the intent reference only when it satisfies the audit
                # constraints; a broken intent row falls back to the scope-less
                # defaults instead of blocking the dead-letter write.
                target_scope = PRODUCT_SCOPE_CHAT
                target_user_id = None
                target_chat_id = None
                source_chat_id = None
                product_key = None
                if intent is not None and _product_matches_scope(intent.product_key, intent.target_scope):
                    if intent.target_scope == PRODUCT_SCOPE_USER and intent.target_user_id is not None:
                        target_scope = PRODUCT_SCOPE_USER
                        target_user_id = intent.target_user_id
                        product_key = intent.product_key
                    else:
                        target_chat_id = intent.chat_id
                        source_chat_id = intent.source_chat_id
                        product_key = intent.product_key
                stored_id = await session.scalar(
                    pg_insert(SelaraAiPaymentModel)
                    .values(
                        telegram_payment_charge_id=charge_id,
                        provider_payment_charge_id=(
                            provider_payment_charge_id[:255] if provider_payment_charge_id is not None else None
                        ),
                        invoice_payload=invoice_payload,
                        purchase_intent_id=intent.id if intent is not None else None,
                        buyer_user_id=buyer_user_id,
                        source_chat_id=source_chat_id,
                        target_chat_id=target_chat_id,
                        target_scope=target_scope,
                        target_user_id=target_user_id,
                        product_key=product_key,
                        # Defensive bounds only: this row must survive a poison
                        # payload, and rejected rows never feed revenue totals.
                        amount_stars=max(int(amount_stars), 0),
                        currency=currency[:3],
                        payment_at=paid_at,
                        processing_state="rejected",
                        processing_reason="processing_failed",
                    )
                    .on_conflict_do_nothing(index_elements=[SelaraAiPaymentModel.telegram_payment_charge_id])
                    .returning(SelaraAiPaymentModel.id)
                )
                if stored_id is not None:
                    return int(stored_id)
                return await session.scalar(
                    select(SelaraAiPaymentModel.id).where(
                        SelaraAiPaymentModel.telegram_payment_charge_id == charge_id
                    )
                )

    async def _duplicate_result(
        self,
        session: AsyncSession,
        payment: SelaraAiPaymentModel,
    ) -> PaymentResult:
        if payment.purchase_intent_id is None or payment.product_key is None:
            return PaymentResult("duplicate", "duplicate_rejected")
        if payment.target_scope == PRODUCT_SCOPE_USER:
            valid_until = None
            if payment.target_user_id is not None:
                valid_until = await session.scalar(
                    select(UserEntitlementModel.valid_until).where(
                        UserEntitlementModel.user_id == payment.target_user_id,
                        UserEntitlementModel.product_key == payment.product_key,
                    )
                )
            return PaymentResult(
                "duplicate",
                valid_until=valid_until,
                target_scope=PRODUCT_SCOPE_USER,
                user_id=payment.target_user_id,
            )
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

    async def claim_rejected_payment_refund(
        self,
        *,
        payment_id: int,
        requested_by_user_id: int,
    ) -> PaymentRefundClaim:
        """Durably claim one refund attempt for a rejected, unapplied payment."""
        async with self._session_factory() as session:
            async with session.begin():
                _require_postgresql(session)
                payment = await session.scalar(
                    select(SelaraAiPaymentModel)
                    .where(SelaraAiPaymentModel.id == payment_id)
                    .with_for_update()
                )
                if payment is None:
                    return PaymentRefundClaim("not_found")
                if payment.processing_state != "rejected":
                    return PaymentRefundClaim("not_rejected")

                refund = await session.get(
                    SelaraAiPaymentRefundModel,
                    payment_id,
                    with_for_update=True,
                )
                if refund is not None:
                    return PaymentRefundClaim(refund.status)

                session.add(
                    SelaraAiPaymentRefundModel(
                        payment_id=payment_id,
                        requested_by_user_id=requested_by_user_id,
                        status="pending",
                    )
                )
                return PaymentRefundClaim(
                    "claimed",
                    buyer_user_id=payment.buyer_user_id,
                    telegram_payment_charge_id=payment.telegram_payment_charge_id,
                    amount_stars=payment.amount_stars,
                )

    async def finish_rejected_payment_refund(
        self,
        *,
        payment_id: int,
        succeeded: bool,
        result_code: str | None = None,
    ) -> bool:
        """Finish a claimed refund; a pending row is retained if delivery was ambiguous."""
        async with self._session_factory() as session:
            async with session.begin():
                _require_postgresql(session)
                refund = await session.get(
                    SelaraAiPaymentRefundModel,
                    payment_id,
                    with_for_update=True,
                )
                if refund is None or refund.status != "pending":
                    return False
                refund.status = "refunded" if succeeded else "failed"
                refund.completed_at = datetime.now(timezone.utc)
                refund.result_code = result_code
                return True

    async def get_entitlement(self, *, chat_id: int, product_key: str = SELARA_AI_PRODUCT_KEY) -> ChatEntitlementModel | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ChatEntitlementModel).where(
                    ChatEntitlementModel.chat_id == chat_id,
                    ChatEntitlementModel.product_key == product_key,
                )
            )

    async def get_user_entitlement(
        self, *, user_id: int, product_key: str = SELARA_PERSONAL_PRODUCT_KEY
    ) -> UserEntitlementModel | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(UserEntitlementModel).where(
                    UserEntitlementModel.user_id == user_id,
                    UserEntitlementModel.product_key == product_key,
                )
            )

    async def get_chat_title(self, *, chat_id: int) -> str | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ChatModel.title).where(ChatModel.telegram_chat_id == chat_id)
            )

    async def get_chat_daily_summary_enabled(self, *, chat_id: int) -> bool | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ChatSettingsModel.daily_summary_enabled).where(ChatSettingsModel.chat_id == chat_id)
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

    async def active_paid_user_count(self, *, now: datetime | None = None) -> int:
        current = _as_utc(now or datetime.now(timezone.utc))
        async with self._session_factory() as session:
            return int(
                await session.scalar(
                    select(func.count(UserEntitlementModel.id)).where(
                        UserEntitlementModel.product_key == SELARA_PERSONAL_PRODUCT_KEY,
                        UserEntitlementModel.status == "active",
                        UserEntitlementModel.valid_until > current,
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
            statement = (
                statement.outerjoin(
                    SelaraAiPurchaseIntentModel,
                    SelaraAiPurchaseIntentModel.id == SelaraAiPaymentModel.purchase_intent_id,
                )
                .where(
                    (SelaraAiPaymentModel.target_chat_id == chat_id)
                    | (SelaraAiPaymentModel.source_chat_id == chat_id)
                    | (SelaraAiPurchaseIntentModel.chat_id == chat_id)
                )
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
    if not _product_matches_scope(intent.product_key, intent.target_scope):
        return "unsupported_product"
    if intent.target_scope == PRODUCT_SCOPE_USER and intent.target_user_id != buyer_user_id:
        return "wrong_buyer"
    if amount_stars != intent.amount_stars:
        return "wrong_amount"
    if currency != intent.currency or currency != SELARA_AI_CURRENCY:
        return "wrong_currency"
    # Expiry and live admin/bot checks apply before payment. Once Telegram has
    # confirmed payment, they must not cancel the economic effect.
    return None


def _product_matches_scope(product_key: str | None, target_scope: str | None) -> bool:
    """A product may only be sold into the scope its catalog entry declares."""
    spec = get_product_spec(product_key)
    return spec is not None and spec.scope == target_scope


def _extend_entitlement(entitlement, *, paid_at: datetime, duration: timedelta) -> None:
    """Add ``duration`` after the remaining active time, or restart from the payment moment."""
    if entitlement.status == "active" and entitlement.valid_until > paid_at:
        base = entitlement.valid_until
    else:
        base = paid_at
        entitlement.valid_from = paid_at
    entitlement.valid_until = base + duration
    entitlement.status = "active"
    entitlement.updated_at = func.now()


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
