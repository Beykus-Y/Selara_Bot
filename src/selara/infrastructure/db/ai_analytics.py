"""Read-only owner analytics for Selara AI cost and Telegram Stars monetization.

Nothing here writes. AI cost semantics mirror ``AiAccountingService.aggregate_window``:
rows are attributed to the window by the *invocation* ``started_at`` using
``[window_from, window_to)``, usage rows linked to an invocation only, and a
provider-attempt marker without a usage row is one provider call with unknown
cost. Stars revenue (XTR) is never converted to or combined with USD cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Date, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from selara.application.selara_ai_product import SELARA_AI_PRODUCT_KEY, SELARA_PERSONAL_PRODUCT_KEY
from selara.infrastructure.db.models import (
    AiFeatureInvocationModel,
    ChatEntitlementModel,
    UserEntitlementModel,
    ChatModel,
    LlmUsageLogModel,
    SelaraAiPaymentModel,
    SelaraAiPurchaseIntentModel,
)
from selara.infrastructure.db.selara_ai_payment_refund import SelaraAiPaymentRefundModel

EXPIRING_SOON = timedelta(days=7)
MAX_BREAKDOWN_ROWS = 20
MAX_PAYMENT_PAGE = 50
MAX_ENTITLEMENT_ROWS = 50
REFUND_STATES = ("pending", "refunded", "failed")


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _unknown_usage_expr():
    return (LlmUsageLogModel.pricing_status == "unknown") | (LlmUsageLogModel.status == "failed")


def _day_bucket(session: AsyncSession, column, timezone_name: str):
    """Local calendar day of a timestamp; PostgreSQL honours BOT_TIMEZONE."""
    bind = getattr(session, "bind", None)
    dialect = bind.dialect.name if bind is not None else ""
    if dialect == "postgresql":
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            timezone_name = "UTC"
        return cast(func.timezone(timezone_name, column), Date)
    return func.date(column)


def _day_key(value) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    return str(value)[:10]


@dataclass(frozen=True, slots=True)
class PaymentFilters:
    state: str = "all"  # all | applied | rejected
    refund: str = "all"  # all | none | pending | refunded | failed
    chat_id: int | None = None
    buyer_user_id: int | None = None
    since: datetime | None = None
    target_scope: str | None = None  # None = both; chat | user (Selara Personal)


class AdminAiAnalyticsRepository:
    """Bounded aggregate queries over existing accounting and billing tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----- AI accounting ---------------------------------------------------

    @staticmethod
    def _in_window(window_from: datetime, window_to: datetime):
        return (
            AiFeatureInvocationModel.started_at >= window_from,
            AiFeatureInvocationModel.started_at < window_to,
        )

    async def feature_breakdown(self, *, window_from: datetime, window_to: datetime) -> list[dict]:
        in_window = self._in_window(window_from, window_to)
        invocations = await self._session.execute(
            select(
                AiFeatureInvocationModel.feature,
                func.count(AiFeatureInvocationModel.id),
                func.count(case((AiFeatureInvocationModel.status.in_(("failed", "partial")), 1))),
            )
            .where(*in_window)
            .group_by(AiFeatureInvocationModel.feature)
        )
        usage = await self._session.execute(
            select(
                AiFeatureInvocationModel.feature,
                func.count(LlmUsageLogModel.id),
                func.coalesce(func.sum(LlmUsageLogModel.estimated_cost_usd), 0),
                func.count(case((_unknown_usage_expr(), 1))),
            )
            .select_from(LlmUsageLogModel)
            .join(AiFeatureInvocationModel, LlmUsageLogModel.invocation_id == AiFeatureInvocationModel.id)
            .where(*in_window)
            .group_by(AiFeatureInvocationModel.feature)
        )
        has_usage = (
            select(LlmUsageLogModel.id)
            .where(LlmUsageLogModel.invocation_id == AiFeatureInvocationModel.id)
            .exists()
        )
        markers = await self._session.execute(
            select(AiFeatureInvocationModel.feature, func.count(AiFeatureInvocationModel.id))
            .where(*in_window, AiFeatureInvocationModel.provider_attempt_started_at.is_not(None), ~has_usage)
            .group_by(AiFeatureInvocationModel.feature)
        )
        rows: dict[str, dict] = {}

        def row(feature: str) -> dict:
            return rows.setdefault(
                feature,
                {
                    "feature": feature,
                    "invocations": 0,
                    "unsuccessful_invocations": 0,
                    "provider_calls": 0,
                    "known_cost_usd": Decimal(0),
                    "unknown_cost_calls": 0,
                },
            )

        for feature, count, unsuccessful in invocations.all():
            item = row(feature)
            item["invocations"] = int(count)
            item["unsuccessful_invocations"] = int(unsuccessful)
        for feature, calls, cost, unknown in usage.all():
            item = row(feature)
            item["provider_calls"] += int(calls)
            item["known_cost_usd"] = Decimal(cost)
            item["unknown_cost_calls"] += int(unknown)
        for feature, marker_count in markers.all():
            item = row(feature)
            item["provider_calls"] += int(marker_count)
            item["unknown_cost_calls"] += int(marker_count)
        ordered = sorted(
            rows.values(),
            key=lambda item: (item["known_cost_usd"], item["provider_calls"], item["feature"]),
            reverse=True,
        )
        return ordered[:MAX_BREAKDOWN_ROWS]

    async def model_breakdown(self, *, window_from: datetime, window_to: datetime) -> tuple[list[dict], int]:
        """Per-model usage plus provider attempts that never produced a usage row."""
        in_window = self._in_window(window_from, window_to)
        result = await self._session.execute(
            select(
                LlmUsageLogModel.model,
                func.count(LlmUsageLogModel.id),
                func.coalesce(func.sum(LlmUsageLogModel.prompt_tokens), 0),
                func.coalesce(func.sum(LlmUsageLogModel.completion_tokens), 0),
                func.coalesce(func.sum(LlmUsageLogModel.estimated_cost_usd), 0),
                func.count(case((_unknown_usage_expr(), 1))),
            )
            .select_from(LlmUsageLogModel)
            .join(AiFeatureInvocationModel, LlmUsageLogModel.invocation_id == AiFeatureInvocationModel.id)
            .where(*in_window)
            .group_by(LlmUsageLogModel.model)
        )
        models = [
            {
                "model": model,
                "provider_calls": int(calls),
                "prompt_tokens": int(prompt),
                "completion_tokens": int(completion),
                "known_cost_usd": Decimal(cost),
                "unknown_cost_calls": int(unknown),
            }
            for model, calls, prompt, completion, cost, unknown in result.all()
        ]
        models.sort(key=lambda item: (item["known_cost_usd"], item["provider_calls"], item["model"]), reverse=True)
        has_usage = (
            select(LlmUsageLogModel.id)
            .where(LlmUsageLogModel.invocation_id == AiFeatureInvocationModel.id)
            .exists()
        )
        marker_only = await self._session.scalar(
            select(func.count(AiFeatureInvocationModel.id)).where(
                *in_window, AiFeatureInvocationModel.provider_attempt_started_at.is_not(None), ~has_usage
            )
        )
        return models[:MAX_BREAKDOWN_ROWS], int(marker_only or 0)

    async def stage_breakdown(self, *, window_from: datetime, window_to: datetime, limit: int = 10) -> list[dict]:
        result = await self._session.execute(
            select(
                AiFeatureInvocationModel.feature,
                LlmUsageLogModel.stage,
                func.count(LlmUsageLogModel.id),
                func.coalesce(func.sum(LlmUsageLogModel.estimated_cost_usd), 0),
            )
            .select_from(LlmUsageLogModel)
            .join(AiFeatureInvocationModel, LlmUsageLogModel.invocation_id == AiFeatureInvocationModel.id)
            .where(*self._in_window(window_from, window_to))
            .group_by(AiFeatureInvocationModel.feature, LlmUsageLogModel.stage)
        )
        stages = [
            {"feature": feature, "stage": stage, "provider_calls": int(calls), "known_cost_usd": Decimal(cost)}
            for feature, stage, calls, cost in result.all()
        ]
        stages.sort(key=lambda item: (item["known_cost_usd"], item["provider_calls"]), reverse=True)
        return stages[:limit]

    async def daily_ai_series(
        self, *, window_from: datetime, window_to: datetime, timezone_name: str
    ) -> list[dict]:
        in_window = self._in_window(window_from, window_to)
        inv_day = _day_bucket(self._session, AiFeatureInvocationModel.started_at, timezone_name)
        invocations = await self._session.execute(
            select(inv_day, func.count(AiFeatureInvocationModel.id)).where(*in_window).group_by(inv_day)
        )
        usage = await self._session.execute(
            select(
                inv_day,
                func.count(LlmUsageLogModel.id),
                func.coalesce(func.sum(LlmUsageLogModel.estimated_cost_usd), 0),
            )
            .select_from(LlmUsageLogModel)
            .join(AiFeatureInvocationModel, LlmUsageLogModel.invocation_id == AiFeatureInvocationModel.id)
            .where(*in_window)
            .group_by(inv_day)
        )
        has_usage = (
            select(LlmUsageLogModel.id)
            .where(LlmUsageLogModel.invocation_id == AiFeatureInvocationModel.id)
            .exists()
        )
        markers = await self._session.execute(
            select(inv_day, func.count(AiFeatureInvocationModel.id))
            .where(*in_window, AiFeatureInvocationModel.provider_attempt_started_at.is_not(None), ~has_usage)
            .group_by(inv_day)
        )
        days: dict[str, dict] = {}

        def day(key: str) -> dict:
            return days.setdefault(
                key, {"date": key, "invocations": 0, "provider_calls": 0, "known_cost_usd": Decimal(0)}
            )

        for key, count in invocations.all():
            day(_day_key(key))["invocations"] = int(count)
        for key, calls, cost in usage.all():
            item = day(_day_key(key))
            item["provider_calls"] += int(calls)
            item["known_cost_usd"] = Decimal(cost)
        for key, marker_count in markers.all():
            # Marker-only attempts are provider calls with unknown cost, as in aggregate_window.
            day(_day_key(key))["provider_calls"] += int(marker_count)
        return [days[key] for key in sorted(days)]

    # ----- Monetization ------------------------------------------------------

    async def payment_summary(self, *, window_from: datetime, window_to: datetime) -> dict:
        payment = SelaraAiPaymentModel
        in_window = (payment.payment_at >= window_from, payment.payment_at < window_to)
        applied, revenue, rejected = (
            await self._session.execute(
                select(
                    func.count(case((payment.processing_state == "applied", 1))),
                    func.coalesce(func.sum(case((payment.processing_state == "applied", payment.amount_stars), else_=0)), 0),
                    func.count(case((payment.processing_state == "rejected", 1))),
                ).where(*in_window)
            )
        ).one()
        refunds = {state: 0 for state in REFUND_STATES}
        refund_rows = await self._session.execute(
            select(SelaraAiPaymentRefundModel.status, func.count(SelaraAiPaymentRefundModel.payment_id))
            .join(payment, payment.id == SelaraAiPaymentRefundModel.payment_id)
            .where(*in_window, payment.processing_state == "rejected")
            .group_by(SelaraAiPaymentRefundModel.status)
        )
        for state, count in refund_rows.all():
            if state in refunds:
                refunds[state] = int(count)
        all_count, all_revenue = (
            await self._session.execute(
                select(
                    func.count(payment.id),
                    func.coalesce(func.sum(payment.amount_stars), 0),
                ).where(payment.processing_state == "applied")
            )
        ).one()
        return {
            "successful_payments": int(applied),
            "stars_revenue": int(revenue),
            "rejected_payments": int(rejected),
            "refunds": refunds,
            "all_time": {"successful_payments": int(all_count), "stars_revenue": int(all_revenue)},
        }

    async def daily_stars_series(
        self, *, window_from: datetime, window_to: datetime, timezone_name: str
    ) -> list[dict]:
        payment = SelaraAiPaymentModel
        day = _day_bucket(self._session, payment.payment_at, timezone_name)
        result = await self._session.execute(
            select(day, func.count(payment.id), func.coalesce(func.sum(payment.amount_stars), 0))
            .where(
                payment.processing_state == "applied",
                payment.payment_at >= window_from,
                payment.payment_at < window_to,
            )
            .group_by(day)
            .order_by(day)
        )
        return [
            {"date": _day_key(key), "payments": int(count), "stars": int(stars)}
            for key, count, stars in result.all()
        ]

    async def entitlement_counts(self, *, now: datetime) -> dict:
        entitlement = ChatEntitlementModel
        active = (
            entitlement.product_key == SELARA_AI_PRODUCT_KEY,
            entitlement.status == "active",
            entitlement.valid_until > now,
        )
        total, expiring = (
            await self._session.execute(
                select(
                    func.count(entitlement.id),
                    func.count(case((entitlement.valid_until <= now + EXPIRING_SOON, 1))),
                ).where(*active)
            )
        ).one()
        return {"active_paid_chats": int(total), "expiring_within_7_days": int(expiring)}

    async def personal_entitlement_counts(self, *, now: datetime) -> dict:
        """Active Selara Personal subscriptions (user-scoped), kept apart from chat counts."""
        entitlement = UserEntitlementModel
        total, expiring = (
            await self._session.execute(
                select(
                    func.count(entitlement.id),
                    func.count(case((entitlement.valid_until <= now + EXPIRING_SOON, 1))),
                ).where(
                    entitlement.product_key == SELARA_PERSONAL_PRODUCT_KEY,
                    entitlement.status == "active",
                    entitlement.valid_until > now,
                )
            )
        ).one()
        return {
            "active_personal_subscriptions": int(total),
            "personal_expiring_within_7_days": int(expiring),
        }

    async def active_entitlements(self, *, now: datetime, limit: int = MAX_ENTITLEMENT_ROWS) -> list[dict]:
        entitlement = ChatEntitlementModel
        last_purchase = (
            select(func.max(SelaraAiPaymentModel.payment_at))
            .select_from(SelaraAiPaymentModel)
            .outerjoin(
                SelaraAiPurchaseIntentModel,
                SelaraAiPurchaseIntentModel.id == SelaraAiPaymentModel.purchase_intent_id,
            )
            .where(
                SelaraAiPaymentModel.processing_state == "applied",
                or_(
                    SelaraAiPurchaseIntentModel.chat_id == entitlement.chat_id,
                    and_(
                        SelaraAiPurchaseIntentModel.id.is_(None),
                        SelaraAiPaymentModel.target_chat_id == entitlement.chat_id,
                    ),
                ),
            )
            .correlate(entitlement)
            .scalar_subquery()
        )
        result = await self._session.execute(
            select(entitlement, ChatModel.title, last_purchase)
            .outerjoin(ChatModel, ChatModel.telegram_chat_id == entitlement.chat_id)
            .where(
                entitlement.product_key == SELARA_AI_PRODUCT_KEY,
                entitlement.status == "active",
                entitlement.valid_until > now,
            )
            .order_by(entitlement.valid_until.asc(), entitlement.id.asc())
            .limit(limit)
        )
        rows = []
        for row, title, purchased_at in result.all():
            valid_until = as_utc(row.valid_until)
            remaining = valid_until - now
            rows.append(
                {
                    "chat_id": int(row.chat_id),
                    "chat_title": title,
                    "product_key": row.product_key,
                    "valid_until": valid_until,
                    "days_left": max(0, -(-int(remaining.total_seconds()) // 86_400)),
                    "expiring_soon": remaining <= EXPIRING_SOON,
                    "last_purchase_at": as_utc(purchased_at) if purchased_at is not None else None,
                }
            )
        return rows

    # ----- Payment history ---------------------------------------------------

    @staticmethod
    def _canonical_chat_id():
        """Current chat of a payment; intents follow group -> supergroup migration."""
        return func.coalesce(SelaraAiPurchaseIntentModel.chat_id, SelaraAiPaymentModel.target_chat_id)

    def _payment_select(self):
        payment = SelaraAiPaymentModel
        canonical = self._canonical_chat_id()
        return (
            select(
                payment,
                canonical.label("canonical_chat_id"),
                func.coalesce(ChatModel.title, SelaraAiPurchaseIntentModel.chat_title).label("chat_title"),
                SelaraAiPaymentRefundModel.status.label("refund_status"),
                SelaraAiPaymentRefundModel.requested_at.label("refund_requested_at"),
                SelaraAiPaymentRefundModel.completed_at.label("refund_completed_at"),
                SelaraAiPaymentRefundModel.result_code.label("refund_result_code"),
            )
            .select_from(payment)
            .outerjoin(SelaraAiPurchaseIntentModel, SelaraAiPurchaseIntentModel.id == payment.purchase_intent_id)
            .outerjoin(ChatModel, ChatModel.telegram_chat_id == canonical)
            .outerjoin(SelaraAiPaymentRefundModel, SelaraAiPaymentRefundModel.payment_id == payment.id)
        )

    @staticmethod
    def _payment_row(record, *, detail: bool) -> dict:
        payment = record[0]
        refund = None
        if record.refund_status is not None:
            refund = {
                "status": record.refund_status,
                "requested_at": as_utc(record.refund_requested_at) if record.refund_requested_at else None,
                "completed_at": as_utc(record.refund_completed_at) if record.refund_completed_at else None,
                "result_code": record.refund_result_code,
            }
        item = {
            "id": int(payment.id),
            "payment_at": as_utc(payment.payment_at),
            "buyer_user_id": int(payment.buyer_user_id),
            "source_chat_id": payment.source_chat_id,
            "target_chat_id": payment.target_chat_id,
            "chat_id": record.canonical_chat_id,
            "chat_title": record.chat_title,
            "amount_stars": int(payment.amount_stars),
            "currency": payment.currency,
            "state": payment.processing_state,
            "reason": payment.processing_reason,
            "product_key": payment.product_key,
            "target_scope": payment.target_scope,
            "target_user_id": payment.target_user_id,
            "refund": refund,
        }
        if detail:
            item["telegram_payment_charge_id"] = payment.telegram_payment_charge_id
        return item

    async def list_payments(
        self, *, filters: PaymentFilters, cursor: tuple[datetime, int] | None, limit: int
    ) -> tuple[list[dict], tuple[datetime, int] | None]:
        payment = SelaraAiPaymentModel
        limit = max(1, min(limit, MAX_PAYMENT_PAGE))
        statement = self._payment_select()
        if filters.state in {"applied", "rejected"}:
            statement = statement.where(payment.processing_state == filters.state)
        if filters.refund == "none":
            statement = statement.where(SelaraAiPaymentRefundModel.payment_id.is_(None))
        elif filters.refund in REFUND_STATES:
            statement = statement.where(SelaraAiPaymentRefundModel.status == filters.refund)
        if filters.chat_id is not None:
            statement = statement.where(
                or_(
                    payment.target_chat_id == filters.chat_id,
                    payment.source_chat_id == filters.chat_id,
                    SelaraAiPurchaseIntentModel.chat_id == filters.chat_id,
                )
            )
        if filters.buyer_user_id is not None:
            statement = statement.where(payment.buyer_user_id == filters.buyer_user_id)
        if filters.target_scope in {"chat", "user"}:
            statement = statement.where(payment.target_scope == filters.target_scope)
        if filters.since is not None:
            statement = statement.where(payment.payment_at >= filters.since)
        if cursor is not None:
            cursor_at, cursor_id = cursor
            statement = statement.where(
                or_(payment.payment_at < cursor_at, and_(payment.payment_at == cursor_at, payment.id < cursor_id))
            )
        result = await self._session.execute(
            statement.order_by(payment.payment_at.desc(), payment.id.desc()).limit(limit + 1)
        )
        records = result.all()
        has_more = len(records) > limit
        records = records[:limit]
        items = [self._payment_row(record, detail=False) for record in records]
        next_cursor = None
        if has_more and records:
            last = records[-1][0]
            next_cursor = (as_utc(last.payment_at), int(last.id))
        return items, next_cursor

    async def payment_detail(self, *, payment_id: int, now: datetime) -> dict | None:
        result = await self._session.execute(
            self._payment_select().where(SelaraAiPaymentModel.id == payment_id)
        )
        record = result.first()
        if record is None:
            return None
        payment = record[0]
        item = self._payment_row(record, detail=True)
        intent = None
        if payment.purchase_intent_id is not None:
            row = await self._session.get(SelaraAiPurchaseIntentModel, payment.purchase_intent_id)
            if row is not None:
                intent = {
                    "id": row.id,
                    "status": row.status,
                    "created_at": as_utc(row.created_at),
                    "expires_at": as_utc(row.expires_at),
                    "amount_stars": int(row.amount_stars),
                    "source_chat_id": row.source_chat_id,
                    "chat_id": row.chat_id,
                    "terms_version": row.terms_version,
                    "terms_accepted_at": as_utc(row.terms_accepted_at) if row.terms_accepted_at else None,
                }
        item["intent"] = intent
        entitlement = None
        if item["chat_id"] is not None:
            row = await self._session.scalar(
                select(ChatEntitlementModel).where(
                    ChatEntitlementModel.chat_id == item["chat_id"],
                    ChatEntitlementModel.product_key == SELARA_AI_PRODUCT_KEY,
                )
            )
            if row is not None:
                valid_until = as_utc(row.valid_until)
                entitlement = {
                    "status": row.status,
                    "valid_from": as_utc(row.valid_from),
                    "valid_until": valid_until,
                    "active_now": row.status == "active" and valid_until > now,
                }
        item["entitlement"] = entitlement
        return item
