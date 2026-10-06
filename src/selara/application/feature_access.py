from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from selara.application.usage_pricing import ConfiguredUsagePricer, QuotaCost, UsagePricer
from selara.infrastructure.llm.features import AiFeature

if TYPE_CHECKING:
    from selara.application.personal_config import PersonalConfigProvider

logger = logging.getLogger(__name__)


class AccessTier(StrEnum):
    FREE = "free"
    PAID = "paid"
    OWNER_INTERNAL = "owner_internal"


class AccessReason(StrEnum):
    QUOTA_EXHAUSTED = "quota_exhausted"
    DUPLICATE_REQUEST = "duplicate_request"
    NO_COMMERCIAL_QUOTA = "no_commercial_quota"
    FEATURE_DISABLED = "feature_disabled"
    ACCESS_REQUIRED = "access_required"
    ACCESS_UNAVAILABLE = "access_unavailable"


class QuotaPeriod(StrEnum):
    DAY = "day"
    MONTH = "month"


class QuotaScopeType(StrEnum):
    CHAT = "chat"
    USER = "user"


@dataclass(frozen=True, slots=True)
class QuotaScope:
    """Who pays for a request. ``chat_id`` elsewhere only says where it happened."""

    scope_type: QuotaScopeType
    scope_id: int

    @classmethod
    def chat(cls, chat_id: int) -> "QuotaScope":
        return cls(QuotaScopeType.CHAT, chat_id)

    @classmethod
    def user(cls, user_id: int) -> "QuotaScope":
        return cls(QuotaScopeType.USER, user_id)


PERSONAL_POOL_KEY = "personal_daily"
# A pet's talk is paid by its owner: one pool per owner (one pet per owner today).
PET_POOL_KEY = "pet_daily"
DEFAULT_PET_TALK_DAILY_LIMIT = 60
# Features the personal config may price; group features never read its weights.
PERSONAL_FEATURES = frozenset({AiFeature.PERSONAL_CHAT, AiFeature.PERSONAL_MEMORY_EXTRACT})
# What a personal request draws from the pool: 5/150 are requests, not weighted units.
PERSONAL_REQUEST_COST = QuotaCost(Decimal("1"))


@dataclass(frozen=True, slots=True)
class PersonalQuotaLimits:
    """Daily limits of the personal pool, in quota units; values come from settings."""

    free_daily: int
    paid_daily: int

    def __post_init__(self) -> None:
        if self.free_daily <= 0 or self.paid_daily <= self.free_daily:
            raise ValueError("Personal limits must satisfy 0 < free < paid")

    @classmethod
    def from_settings(cls, settings) -> "PersonalQuotaLimits":
        return cls(
            free_daily=settings.personal_free_daily_limit,
            paid_daily=settings.personal_paid_daily_limit,
        )


@dataclass(frozen=True, slots=True)
class FeatureQuotaPolicy:
    feature: AiFeature
    policy_key: str
    limit: int
    period: QuotaPeriod
    # Policies spend a pool, not a feature, so several features can share one
    # budget later. ``None`` keeps today's behaviour: a feature is its own pool.
    pool_key: str | None = None
    # What ``limit`` counts: plain requests now, AI Limits later.
    unit: str = "request"

    @property
    def pool(self) -> str:
        return self.pool_key or self.feature.value


@dataclass(frozen=True, slots=True)
class FeatureAccessDecision:
    allowed: bool
    feature: AiFeature
    scope_type: str
    scope_id: str
    access_tier: AccessTier
    quota_limit: int | None
    quota_used: int | None
    quota_remaining: int | None
    period_start: datetime | None
    period_end: datetime | None
    reason: AccessReason | None = None
    owner_exempt: bool = False
    invocation_id: int | None = None
    quota_usage_id: int | None = None
    policy_key: str | None = None
    reused: bool = False
    entitlement_valid_until: datetime | None = None
    entitlement_source: str | None = None
    entitlement_product: str | None = None

    @property
    def unlimited(self) -> bool:
        return self.quota_limit is None


@dataclass(frozen=True, slots=True)
class FeatureUsageSummary:
    feature: AiFeature
    scope_type: str
    scope_id: str
    access_tier: AccessTier
    quota_limit: int | None
    quota_used: int | None
    quota_remaining: int | None
    period_start: datetime | None
    period_end: datetime | None
    reset_at: datetime | None
    unlimited: bool
    owner_exempt: bool
    policy_key: str | None


@dataclass(frozen=True, slots=True)
class FeatureEntitlement:
    """Resolved non-owner entitlement for one chat/feature/trigger.

    This is the typed hand-off from entitlement storage. Owner-internal access
    is derived separately from a live Telegram administrator check.
    """

    access_tier: AccessTier
    valid_until: datetime | None = None
    source: str | None = None
    product_key: str | None = None
    quota_policy: FeatureQuotaPolicy | None = None


@dataclass(frozen=True, slots=True)
class DailySummaryAccessStatus:
    automatic_enabled: bool
    automatic_access_allowed: bool
    manual_used: int | None
    manual_limit: int | None
    manual_remaining: int | None
    reset_at: datetime | None
    access_tier: AccessTier
    owner_exempt: bool
    automatic_reason: AccessReason | None = None
    entitlement_valid_until: datetime | None = None
    entitlement_source: str | None = None
    entitlement_product: str | None = None


class ChatEntitlementResolver(Protocol):
    async def resolve(
        self,
        *,
        chat_id: int,
        feature: AiFeature,
        trigger: str,
    ) -> FeatureEntitlement: ...


class UserEntitlementResolver(Protocol):
    async def resolve(
        self,
        *,
        user_id: int,
        feature: AiFeature,
        trigger: str,
    ) -> FeatureEntitlement: ...


class NoPaidUserEntitlementResolver:
    """Fail closed to the free tier when no personal entitlement source is wired."""

    async def resolve(
        self,
        *,
        user_id: int,
        feature: AiFeature,
        trigger: str,
    ) -> FeatureEntitlement:
        return FeatureEntitlement(access_tier=AccessTier.FREE)


class NoPaidChatEntitlementResolver:
    """Fail closed when a caller has not been wired to an entitlement source."""

    async def resolve(
        self,
        *,
        chat_id: int,
        feature: AiFeature,
        trigger: str,
    ) -> FeatureEntitlement:
        return FeatureEntitlement(access_tier=AccessTier.FREE)


class FeatureQuotaRepository(Protocol):
    async def reserve(
        self,
        *,
        policy: FeatureQuotaPolicy,
        access_tier: AccessTier,
        chat_id: int,
        scope: QuotaScope,
        cost: QuotaCost,
        chat_type: str,
        chat_title: str | None,
        actor_user_id: int | None,
        actor_is_bot: bool,
        trigger: str,
        mode: str | None,
        source_message_id: int | None,
        summary_run_id: int | None,
        idempotency_key: str,
        owner_exempt: bool,
        period_start: datetime,
        period_end: datetime,
    ) -> FeatureAccessDecision: ...

    async def usage_summary(
        self,
        *,
        policy: FeatureQuotaPolicy,
        chat_id: int,
        scope: QuotaScope,
        owner_exempt: bool,
        period_start: datetime,
        period_end: datetime,
    ) -> FeatureUsageSummary: ...

    async def release_if_no_provider_attempts(self, *, invocation_id: int, reason: str) -> bool: ...


def resolve_feature_policy(
    *, feature: AiFeature, trigger: str, personal_limits: PersonalQuotaLimits | None = None
) -> FeatureQuotaPolicy | None:
    """Return the explicit commercial policy for a user-facing feature.

    ``None`` means the known feature intentionally has no commercial quota in
    this release. Unknown features and unsupported Daily Summary triggers raise
    so a newly added feature cannot silently become unlimited.
    """
    if feature == AiFeature.LLM_ADMIN:
        return FeatureQuotaPolicy(feature, "llm_admin_free_daily_v1", 10, QuotaPeriod.DAY)
    if feature == AiFeature.DAILY_SUMMARY:
        if trigger == "manual":
            return FeatureQuotaPolicy(feature, "daily_summary_manual_free_monthly_v1", 10, QuotaPeriod.MONTH)
        if trigger == "scheduled":
            return None
        raise ValueError(f"Unsupported Daily Summary trigger for access policy: {trigger!r}")
    if feature == AiFeature.PERSONAL_CHAT:
        if personal_limits is None:
            # Fail closed: without configured limits the feature must not become unlimited.
            raise ValueError("Personal quota limits are not configured")
        return FeatureQuotaPolicy(
            feature,
            "personal_chat_free_daily_v1",
            personal_limits.free_daily,
            QuotaPeriod.DAY,
            pool_key=PERSONAL_POOL_KEY,
        )
    if feature in (AiFeature.PET_TALK, AiFeature.PET_EVENT_TEXT):
        # Without the owner's Selara Personal a pet cannot talk or post events (its mechanics still work).
        return FeatureQuotaPolicy(feature, f"{feature.value}_free_daily_v1", 0, QuotaPeriod.DAY, pool_key=PET_POOL_KEY)
    # /autocfg and internal operations (memory extraction, context compression) are
    # accounted for cost but never spend a user's or chat's commercial quota.
    if feature in (
        AiFeature.AUTOCONFIG,
        AiFeature.LLM_CONTEXT_COMPRESSION,
        AiFeature.PERSONAL_MEMORY_EXTRACT,
        AiFeature.PET_MEMORY_EXTRACT,
    ):
        return None
    raise ValueError(f"No explicit feature access policy for {feature!r}")


def paid_personal_policy(limits: PersonalQuotaLimits) -> FeatureQuotaPolicy:
    """Selara Personal raises the same daily pool from the free to the paid limit."""
    return FeatureQuotaPolicy(
        AiFeature.PERSONAL_CHAT,
        "personal_chat_paid_daily_v1",
        limits.paid_daily,
        QuotaPeriod.DAY,
        pool_key=PERSONAL_POOL_KEY,
    )


def paid_pet_policy(
    daily_limit: int = DEFAULT_PET_TALK_DAILY_LIMIT, feature: AiFeature = AiFeature.PET_TALK
) -> FeatureQuotaPolicy:
    """Selara Personal gives the owner's pet ``daily_limit`` AI units a day, shared by talk and events."""
    return FeatureQuotaPolicy(
        feature,
        f"{feature.value}_paid_daily_v1",
        daily_limit,
        QuotaPeriod.DAY,
        pool_key=PET_POOL_KEY,
    )


def quota_period_bounds(
    *, policy: FeatureQuotaPolicy, now: datetime, timezone_name: str,
) -> tuple[datetime, datetime]:
    """Resolve calendar quota periods in BOT_TIMEZONE and return UTC bounds."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    try:
        local_tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown BOT_TIMEZONE=%s for feature quota; falling back to UTC", timezone_name)
        local_tz = ZoneInfo("UTC")

    local_now = now_utc.astimezone(local_tz)
    if policy.period == QuotaPeriod.DAY:
        local_start = datetime.combine(local_now.date(), time.min, tzinfo=local_tz)
        local_end = datetime.combine(local_now.date() + timedelta(days=1), time.min, tzinfo=local_tz)
    else:
        month_start = date(local_now.year, local_now.month, 1)
        if local_now.month == 12:
            next_month = date(local_now.year + 1, 1, 1)
        else:
            next_month = date(local_now.year, local_now.month + 1, 1)
        local_start = datetime.combine(month_start, time.min, tzinfo=local_tz)
        local_end = datetime.combine(next_month, time.min, tzinfo=local_tz)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def message_idempotency_key(*, feature: AiFeature, chat_id: int, source_message_id: int) -> str:
    """Namespaced stable key for replayed Telegram message updates."""
    return f"{feature.value}:{chat_id}:{source_message_id}"


class FeatureAccessService:
    """Single typed API for resolving and reserving user-facing feature access."""

    def __init__(
        self,
        repository: FeatureQuotaRepository,
        *,
        entitlement_resolver: ChatEntitlementResolver | None = None,
        user_entitlement_resolver: UserEntitlementResolver | None = None,
        pricer: UsagePricer | None = None,
        personal_limits: PersonalQuotaLimits | None = None,
        personal_config: PersonalConfigProvider | None = None,
    ) -> None:
        # ``personal_config`` (settings + DB override, hot-reloaded) wins over the static ``personal_limits``.
        self._personal_limits = personal_limits
        self._personal_config = personal_config
        self._repository = repository
        self._entitlement_resolver = entitlement_resolver or NoPaidChatEntitlementResolver()
        self._user_entitlement_resolver = user_entitlement_resolver or NoPaidUserEntitlementResolver()
        self._pricer: UsagePricer = pricer or ConfiguredUsagePricer()

    async def _personal_limits_now(self) -> PersonalQuotaLimits | None:
        if self._personal_config is None:
            return self._personal_limits
        return (await self._personal_config.get()).limits

    def _cost_of(self, feature: AiFeature, trigger: str) -> QuotaCost:
        """Personal features cost exactly one request: AI Limits weights are a later, deliberate switch."""
        if feature in PERSONAL_FEATURES:
            return PERSONAL_REQUEST_COST
        return self._pricer.price(feature=feature, model_key=None, operation=trigger)

    async def _resolve_entitlement(
        self,
        *,
        scope: QuotaScope,
        feature: AiFeature,
        trigger: str,
    ) -> FeatureEntitlement:
        if scope.scope_type == QuotaScopeType.USER:
            return await self._user_entitlement_resolver.resolve(
                user_id=scope.scope_id,
                feature=feature,
                trigger=trigger,
            )
        return await self._entitlement_resolver.resolve(
            chat_id=scope.scope_id,
            feature=feature,
            trigger=trigger,
        )

    async def resolve_feature_access(
        self,
        *,
        feature: AiFeature,
        chat_id: int,
        trigger: str,
        scope: QuotaScope | None = None,
        owner_exempt: bool = False,
        now: datetime | None = None,
    ) -> FeatureAccessDecision:
        """Resolve feature entitlement without reserving or consuming quota."""
        scope = scope or QuotaScope.chat(chat_id)
        scope_type, scope_id = scope.scope_type.value, str(scope.scope_id)
        if owner_exempt:
            return FeatureAccessDecision(
                allowed=True,
                feature=feature,
                scope_type=scope_type,
                scope_id=scope_id,
                access_tier=AccessTier.OWNER_INTERNAL,
                quota_limit=None,
                quota_used=None,
                quota_remaining=None,
                period_start=None,
                period_end=None,
                owner_exempt=True,
                entitlement_source="owner_admin",
            )

        try:
            entitlement = await self._resolve_entitlement(scope=scope, feature=feature, trigger=trigger)
            if not isinstance(entitlement, FeatureEntitlement):
                raise TypeError("Entitlement resolver returned an unsupported result")
            if entitlement.access_tier == AccessTier.OWNER_INTERNAL:
                raise ValueError("Owner-internal access requires a verified Telegram admin check")
            if entitlement.access_tier not in (AccessTier.FREE, AccessTier.PAID):
                raise ValueError("Entitlement resolver returned an unsupported access tier")
        except Exception:
            logger.debug(
                "Feature entitlement resolver failed feature=%s trigger=%s scope=%s:%s",
                feature.value,
                trigger,
                scope_type,
                scope_id,
                exc_info=True,
            )
            return FeatureAccessDecision(
                allowed=False,
                feature=feature,
                scope_type=scope_type,
                scope_id=scope_id,
                access_tier=AccessTier.FREE,
                quota_limit=None,
                quota_used=None,
                quota_remaining=None,
                period_start=None,
                period_end=None,
                reason=AccessReason.ACCESS_UNAVAILABLE,
            )

        entitlement_valid_until = entitlement.valid_until
        if entitlement_valid_until is not None:
            expiry_utc = entitlement_valid_until
            if expiry_utc.tzinfo is None:
                expiry_utc = expiry_utc.replace(tzinfo=timezone.utc)
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            if expiry_utc.astimezone(timezone.utc) <= current.astimezone(timezone.utc):
                return FeatureAccessDecision(
                    allowed=False,
                    feature=feature,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    access_tier=AccessTier.FREE,
                    quota_limit=None,
                    quota_used=None,
                    quota_remaining=None,
                    period_start=None,
                    period_end=None,
                    reason=AccessReason.ACCESS_REQUIRED,
                    entitlement_valid_until=entitlement_valid_until,
                    entitlement_source=entitlement.source,
                    entitlement_product=entitlement.product_key,
                )

        paid = entitlement.access_tier == AccessTier.PAID
        return FeatureAccessDecision(
            allowed=paid,
            feature=feature,
            scope_type=scope_type,
            scope_id=scope_id,
            access_tier=entitlement.access_tier,
            quota_limit=None,
            quota_used=None,
            quota_remaining=None,
            period_start=None,
            period_end=None,
            reason=None if paid else AccessReason.ACCESS_REQUIRED,
            entitlement_valid_until=entitlement_valid_until,
            entitlement_source=entitlement.source,
            entitlement_product=entitlement.product_key,
        )

    async def get_daily_summary_access_status(
        self,
        *,
        chat_id: int,
        automatic_enabled: bool,
        owner_exempt: bool,
        timezone_name: str,
        now: datetime | None = None,
    ) -> DailySummaryAccessStatus:
        """Return the manual quota and scheduled entitlement state for service/admin use."""
        manual_usage = await self.get_usage_summary(
            feature=AiFeature.DAILY_SUMMARY,
            chat_id=chat_id,
            trigger="manual",
            timezone_name=timezone_name,
            owner_exempt=owner_exempt,
            now=now,
        )
        automatic_access = await self.resolve_feature_access(
            feature=AiFeature.DAILY_SUMMARY,
            chat_id=chat_id,
            trigger="scheduled",
            owner_exempt=owner_exempt,
            now=now,
        )
        return DailySummaryAccessStatus(
            automatic_enabled=automatic_enabled,
            automatic_access_allowed=automatic_access.allowed,
            manual_used=manual_usage.quota_used,
            manual_limit=manual_usage.quota_limit,
            manual_remaining=manual_usage.quota_remaining,
            reset_at=manual_usage.reset_at,
            access_tier=automatic_access.access_tier,
            owner_exempt=owner_exempt,
            automatic_reason=automatic_access.reason,
            entitlement_valid_until=automatic_access.entitlement_valid_until,
            entitlement_source=automatic_access.entitlement_source,
            entitlement_product=automatic_access.entitlement_product,
        )

    async def reserve_feature_usage(
        self,
        *,
        feature: AiFeature,
        chat_id: int,
        actor_user_id: int | None,
        trigger: str,
        timezone_name: str,
        idempotency_key: str,
        scope: QuotaScope | None = None,
        chat_type: str = "supergroup",
        chat_title: str | None = None,
        actor_is_bot: bool = False,
        source_message_id: int | None = None,
        summary_run_id: int | None = None,
        mode: str | None = None,
        owner_exempt: bool = False,
        now: datetime | None = None,
    ) -> FeatureAccessDecision:
        """Reserve quota for ``scope`` (default: the chat); ``chat_id`` is where the request happened."""
        scope = scope or QuotaScope.chat(chat_id)
        tier = AccessTier.OWNER_INTERNAL if owner_exempt else AccessTier.FREE
        personal_limits = await self._personal_limits_now()
        policy = resolve_feature_policy(
            feature=feature, trigger=trigger, personal_limits=personal_limits
        )
        entitlement = None
        if policy is not None and not owner_exempt:
            policy, tier, entitlement = await self._paid_feature_policy(
                policy=policy,
                scope=scope,
                feature=feature,
                trigger=trigger,
                now=now,
            )
        if policy is None:
            return FeatureAccessDecision(
                allowed=True,
                feature=feature,
                scope_type=scope.scope_type.value,
                scope_id=str(scope.scope_id),
                access_tier=tier,
                quota_limit=None,
                quota_used=None,
                quota_remaining=None,
                period_start=None,
                period_end=None,
                reason=AccessReason.NO_COMMERCIAL_QUOTA,
                owner_exempt=owner_exempt,
            )
        if not idempotency_key:
            raise ValueError("A stable idempotency key is required for quota reservations")
        start, end = quota_period_bounds(
            policy=policy,
            now=now or datetime.now(timezone.utc),
            timezone_name=timezone_name,
        )
        cost = self._cost_of(feature, trigger)
        decision = await self._repository.reserve(
            policy=policy,
            access_tier=tier,
            chat_id=chat_id,
            scope=scope,
            cost=cost,
            chat_type=chat_type,
            chat_title=chat_title,
            actor_user_id=actor_user_id,
            actor_is_bot=actor_is_bot,
            trigger=trigger,
            mode=mode,
            source_message_id=source_message_id,
            summary_run_id=summary_run_id,
            idempotency_key=idempotency_key,
            owner_exempt=owner_exempt,
            period_start=start,
            period_end=end,
        )
        if entitlement is not None:
            return replace(
                decision,
                access_tier=tier,
                entitlement_valid_until=entitlement.valid_until,
                entitlement_source=entitlement.source,
                entitlement_product=entitlement.product_key,
            )
        return decision

    async def adjust(self, *, invocation_id: int, actual_units: Decimal) -> None:
        """Interface only: correcting a reservation by actual cost arrives with AI Limits.

        Reservations are made from an estimate before the provider call. A later stage
        will implement this to settle the difference; until then it deliberately does
        nothing, so callers can already be written against the final shape.
        """
        return None

    async def get_usage_summary(
        self,
        *,
        feature: AiFeature,
        chat_id: int,
        trigger: str,
        timezone_name: str,
        scope: QuotaScope | None = None,
        owner_exempt: bool = False,
        now: datetime | None = None,
    ) -> FeatureUsageSummary:
        scope = scope or QuotaScope.chat(chat_id)
        tier = AccessTier.OWNER_INTERNAL if owner_exempt else AccessTier.FREE
        personal_limits = await self._personal_limits_now()
        policy = resolve_feature_policy(
            feature=feature, trigger=trigger, personal_limits=personal_limits
        )
        if policy is not None and not owner_exempt:
            policy, tier, _ = await self._paid_feature_policy(
                policy=policy,
                scope=scope,
                feature=feature,
                trigger=trigger,
                now=now,
            )
        if policy is None:
            return FeatureUsageSummary(
                feature, scope.scope_type.value, str(scope.scope_id), tier, None, None, None, None, None, None,
                True, owner_exempt, None,
            )
        start, end = quota_period_bounds(
            policy=policy,
            now=now or datetime.now(timezone.utc),
            timezone_name=timezone_name,
        )
        summary = await self._repository.usage_summary(
            policy=policy,
            chat_id=chat_id,
            scope=scope,
            owner_exempt=owner_exempt,
            period_start=start,
            period_end=end,
        )
        return replace(summary, access_tier=tier)

    async def _paid_feature_policy(
        self,
        *,
        policy: FeatureQuotaPolicy,
        scope: QuotaScope,
        feature: AiFeature,
        trigger: str,
        now: datetime | None,
    ) -> tuple[FeatureQuotaPolicy, AccessTier, FeatureEntitlement | None]:
        """Keep paid tier visible while preserving free quota until a paid limit exists."""
        try:
            entitlement = await self._resolve_entitlement(scope=scope, feature=feature, trigger=trigger)
            if not isinstance(entitlement, FeatureEntitlement):
                raise TypeError("Entitlement resolver returned an unsupported result")
            if entitlement.access_tier != AccessTier.PAID:
                return policy, AccessTier.FREE, None
            if entitlement.valid_until is not None:
                expiry = entitlement.valid_until
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                current = now or datetime.now(timezone.utc)
                if current.tzinfo is None:
                    current = current.replace(tzinfo=timezone.utc)
                if expiry.astimezone(timezone.utc) <= current.astimezone(timezone.utc):
                    return policy, AccessTier.FREE, None
            if entitlement.quota_policy is None:
                return policy, AccessTier.PAID, entitlement
            paid_policy = entitlement.quota_policy
            if paid_policy.feature != feature:
                raise ValueError("Paid entitlement supplied a quota policy for another feature")
            if paid_policy.pool != policy.pool or paid_policy.unit != policy.unit:
                raise ValueError("Paid entitlement supplied a quota policy for another pool")
            if paid_policy.period != policy.period or paid_policy.limit <= policy.limit:
                raise ValueError("Paid feature quota policy must raise the existing period limit")
            return paid_policy, AccessTier.PAID, entitlement
        except Exception:
            logger.debug(
                "Paid feature quota policy resolution failed; applying free policy scope=%s:%s feature=%s",
                scope.scope_type.value,
                scope.scope_id,
                feature.value,
                exc_info=True,
            )
            return policy, AccessTier.FREE, None

    async def release_if_no_provider_attempts(self, *, invocation_id: int, reason: str) -> bool:
        return await self._repository.release_if_no_provider_attempts(invocation_id=invocation_id, reason=reason)
