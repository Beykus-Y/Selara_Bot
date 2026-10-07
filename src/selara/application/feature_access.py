from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
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
    # The pool still has room, but this actor spent their own share of it.
    ACTOR_QUOTA_EXHAUSTED = "actor_quota_exhausted"


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
# AI Limits mode spends a separate pool: request rows and AIL rows never add up together.
PERSONAL_AIL_POOL_KEY = "personal_ail_daily"
REQUEST_UNIT = "request"
AIL_UNIT = "ail"
# AIL units are stored in ai_feature_quota_usage.units NUMERIC(10,2).
AIL_UNITS_QUANTUM = Decimal("0.01")
MAX_AIL_REQUEST_UNITS = Decimal("1000")
# A pet's talk is paid by its owner: one pool per owner (one pet per owner today).
PET_POOL_KEY = "pet_daily"
DEFAULT_PET_TALK_DAILY_LIMIT = 60
# Member mode in a group (calling Selara by a chat call name) is paid by the chat.
GROUP_MEMBER_POOL_KEY = "group_member_daily"
# Features the personal config may price; group features never read its weights.
PERSONAL_FEATURES = frozenset({AiFeature.PERSONAL_CHAT, AiFeature.PERSONAL_MEMORY_EXTRACT})
# What a personal request draws from the pool in requests mode: 5/150 are requests, not weighted units.
PERSONAL_REQUEST_COST = QuotaCost(Decimal("1"))


def validate_ail_units(units: Decimal) -> Decimal:
    """A positive, finite AIL cost with at most two decimals (what the units column stores exactly)."""
    if not isinstance(units, Decimal) or not units.is_finite() or units <= 0 or units > MAX_AIL_REQUEST_UNITS:
        raise ValueError("AIL cost must be a positive finite Decimal")
    if units != units.quantize(AIL_UNITS_QUANTUM):
        raise ValueError("AIL cost supports at most 2 decimal places")
    return units


def ail_units_from_cost_usd(cost_usd: Decimal, usd_per_ail: Decimal) -> Decimal:
    """What a request that really cost ``cost_usd`` weighs in AIL: rounded up to 0.01, at least 0.01."""
    if not isinstance(cost_usd, Decimal) or not cost_usd.is_finite() or cost_usd < 0:
        raise ValueError("Request cost must be a non-negative finite Decimal")
    if not isinstance(usd_per_ail, Decimal) or not usd_per_ail.is_finite() or usd_per_ail <= 0:
        raise ValueError("The USD value of one AIL must be a positive finite Decimal")
    units = (cost_usd / usd_per_ail).quantize(AIL_UNITS_QUANTUM, rounding=ROUND_CEILING)
    if units > MAX_AIL_REQUEST_UNITS:
        logger.warning(
            "AIL request cost capped at %s AIL (real cost %s USD = %s AIL)", MAX_AIL_REQUEST_UNITS, cost_usd, units
        )
    return min(max(units, AIL_UNITS_QUANTUM), MAX_AIL_REQUEST_UNITS)


@dataclass(frozen=True, slots=True)
class AilSettlement:
    """Outcome of settling one reservation at its actual cost."""

    settled: bool
    # ``units`` is what the request now counts for; ``reserved_units`` what it counted for before.
    units: Decimal
    reserved_units: Decimal
    already_settled: bool = False


@dataclass(frozen=True, slots=True)
class PersonalQuotaLimits:
    """Daily limits of the personal pool: requests (from settings) or AI Limits (owner-configured)."""

    free_daily: int
    paid_daily: int
    unit: str = REQUEST_UNIT

    def __post_init__(self) -> None:
        if self.free_daily <= 0 or self.paid_daily <= self.free_daily:
            raise ValueError("Personal limits must satisfy 0 < free < paid")
        if self.unit not in (REQUEST_UNIT, AIL_UNIT):
            raise ValueError("Personal limits unit must be 'request' or 'ail'")

    @property
    def pool_key(self) -> str:
        return PERSONAL_AIL_POOL_KEY if self.unit == AIL_UNIT else PERSONAL_POOL_KEY

    @classmethod
    def from_settings(cls, settings) -> "PersonalQuotaLimits":
        return cls(
            free_daily=settings.personal_free_daily_limit,
            paid_daily=settings.personal_paid_daily_limit,
        )


@dataclass(frozen=True, slots=True)
class GroupMemberQuotaLimits:
    """Daily member-mode limits of one chat (total and per member), free and with Selara AI."""

    free_daily: int
    free_per_actor: int
    paid_daily: int
    paid_per_actor: int

    def __post_init__(self) -> None:
        if not 0 < self.free_per_actor <= self.free_daily:
            raise ValueError("Group member limits must satisfy 0 < free per member <= free daily")
        if not 0 < self.paid_per_actor <= self.paid_daily:
            raise ValueError("Group member limits must satisfy 0 < paid per member <= paid daily")
        if self.paid_daily <= self.free_daily or self.paid_per_actor < self.free_per_actor:
            raise ValueError("Paid group member limits must raise the free ones")

    @classmethod
    def from_settings(cls, settings) -> "GroupMemberQuotaLimits":
        return cls(
            free_daily=settings.group_member_free_daily_limit,
            free_per_actor=settings.group_member_free_per_user_daily_limit,
            paid_daily=settings.group_member_paid_daily_limit,
            paid_per_actor=settings.group_member_paid_per_user_daily_limit,
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
    unit: str = REQUEST_UNIT
    # Optional share of the pool one actor may spend in a period, counted under the same lock.
    per_actor_limit: int | None = None

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
    # What the pool counted for this decision: "request" or "ail" (None: no quota policy).
    quota_unit: str | None = None

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
    quota_unit: str | None = None


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

    async def adjust_units(self, *, invocation_id: int, actual_units: Decimal) -> AilSettlement | None: ...

    async def last_settled_units(self, *, scope: QuotaScope, pool_key: str) -> Decimal | None: ...


def resolve_feature_policy(
    *,
    feature: AiFeature,
    trigger: str,
    personal_limits: PersonalQuotaLimits | None = None,
    group_member_limits: GroupMemberQuotaLimits | None = None,
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
            "personal_chat_free_daily_ail_v1" if personal_limits.unit == AIL_UNIT else "personal_chat_free_daily_v1",
            personal_limits.free_daily,
            QuotaPeriod.DAY,
            pool_key=personal_limits.pool_key,
            unit=personal_limits.unit,
        )
    if feature == AiFeature.GROUP_MEMBER:
        if group_member_limits is None:
            # Fail closed: member mode is free for every chat, so it must never become unlimited.
            raise ValueError("Group member quota limits are not configured")
        return FeatureQuotaPolicy(
            feature,
            "group_member_free_daily_v1",
            group_member_limits.free_daily,
            QuotaPeriod.DAY,
            pool_key=GROUP_MEMBER_POOL_KEY,
            per_actor_limit=group_member_limits.free_per_actor,
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
        "personal_chat_paid_daily_ail_v1" if limits.unit == AIL_UNIT else "personal_chat_paid_daily_v1",
        limits.paid_daily,
        QuotaPeriod.DAY,
        pool_key=limits.pool_key,
        unit=limits.unit,
    )


def paid_group_member_policy(limits: GroupMemberQuotaLimits) -> FeatureQuotaPolicy:
    """Selara AI raises the chat's member-mode pool and each member's share of it."""
    return FeatureQuotaPolicy(
        AiFeature.GROUP_MEMBER,
        "group_member_paid_daily_v1",
        limits.paid_daily,
        QuotaPeriod.DAY,
        pool_key=GROUP_MEMBER_POOL_KEY,
        per_actor_limit=limits.paid_per_actor,
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
        group_member_limits: GroupMemberQuotaLimits | None = None,
    ) -> None:
        self._group_member_limits = group_member_limits
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
        # Requests (5/150) or, once the owner switched it on, AI Limits budgets.
        return (await self._personal_config.get()).active_limits

    def _cost_of(
        self, feature: AiFeature, trigger: str, policy: FeatureQuotaPolicy, units: Decimal | None
    ) -> QuotaCost:
        """A personal message costs one request, or in AIL mode its model profile's multiplier."""
        if feature in PERSONAL_FEATURES:
            if policy.unit != AIL_UNIT:
                # Requests mode: the multiplier never changes what a message costs.
                return PERSONAL_REQUEST_COST
            if units is None:
                # Fail closed: an AIL reservation without a resolved model cost must not run.
                raise ValueError("AIL reservations need the resolved model profile cost")
            return QuotaCost(validate_ail_units(units))
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
        units: Decimal | None = None,
        model_profile: str | None = None,
    ) -> FeatureAccessDecision:
        """Reserve quota for ``scope`` (default: the chat); ``chat_id`` is where the request happened.

        ``units`` is the AIL cost taken from the request's resolved model snapshot; it is used only
        when the pool counts AI Limits. ``model_profile`` is recorded on the reservation for analytics.
        """
        scope = scope or QuotaScope.chat(chat_id)
        tier = AccessTier.OWNER_INTERNAL if owner_exempt else AccessTier.FREE
        personal_limits = await self._personal_limits_now()
        policy = resolve_feature_policy(
            feature=feature,
            trigger=trigger,
            personal_limits=personal_limits,
            group_member_limits=self._group_member_limits,
        )
        entitlement = None
        if policy is not None and owner_exempt and policy.limit < 1:
            # Free pet policies have a zero limit; the usage row needs a positive one, and an exempt owner is not capped by it.
            policy = replace(policy, limit=1)
        if policy is not None and not owner_exempt:
            policy, tier, entitlement = await self._paid_feature_policy(
                policy=policy,
                scope=scope,
                feature=feature,
                trigger=trigger,
                now=now,
                personal_limits=personal_limits,
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
        cost = self._cost_of(feature, trigger, policy, units)
        # The profile is recorded only where it priced the reservation (AIL); request rows stay as before.
        extra = {"model_profile": model_profile} if model_profile is not None and policy.unit == AIL_UNIT else {}
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
            **extra,
        )
        decision = replace(decision, quota_unit=policy.unit)
        if entitlement is not None:
            return replace(
                decision,
                access_tier=tier,
                entitlement_valid_until=entitlement.valid_until,
                entitlement_source=entitlement.source,
                entitlement_product=entitlement.product_key,
            )
        return decision

    async def adjust(self, *, invocation_id: int, actual_units: Decimal) -> AilSettlement | None:
        """Settle a consumed AIL reservation at the actual cost of the request.

        The reservation is made from the profile multiplier before the provider call; once the
        real cost is known the row's units become ``actual_units``. Cheaper than reserved gives
        AIL back; dearer is charged in full even past the daily limit (the provider already
        billed it), so the next reservation is the one that is refused. A reservation is settled
        once: repeating the call changes nothing. ``None``: nothing to settle (released, free of
        quota, or not an AIL pool).
        """
        return await self._repository.adjust_units(
            invocation_id=invocation_id, actual_units=validate_ail_units(actual_units)
        )

    async def last_ail_charge(self, user_id: int) -> Decimal | None:
        """What the user's latest settled Personal request cost in AIL (``None`` before the first)."""
        return await self._repository.last_settled_units(
            scope=QuotaScope.user(user_id), pool_key=PERSONAL_AIL_POOL_KEY
        )

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
            feature=feature,
            trigger=trigger,
            personal_limits=personal_limits,
            group_member_limits=self._group_member_limits,
        )
        if policy is not None and not owner_exempt:
            policy, tier, _ = await self._paid_feature_policy(
                policy=policy,
                scope=scope,
                feature=feature,
                trigger=trigger,
                now=now,
                personal_limits=personal_limits,
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
        return replace(summary, access_tier=tier, quota_unit=policy.unit)

    async def _paid_feature_policy(
        self,
        *,
        policy: FeatureQuotaPolicy,
        scope: QuotaScope,
        feature: AiFeature,
        trigger: str,
        now: datetime | None,
        personal_limits: PersonalQuotaLimits | None = None,
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
            if (
                feature == AiFeature.PERSONAL_CHAT
                and personal_limits is not None
                and (paid_policy.unit != policy.unit or policy.unit == AIL_UNIT)
            ):
                # The AIL budget is not sold per subscription: take it from the same config snapshot
                # as the free policy, so a mode switch between two reads cannot demote a subscriber.
                paid_policy = paid_personal_policy(personal_limits)
            if paid_policy.feature != feature:
                raise ValueError("Paid entitlement supplied a quota policy for another feature")
            if paid_policy.pool != policy.pool or paid_policy.unit != policy.unit:
                raise ValueError("Paid entitlement supplied a quota policy for another pool")
            if paid_policy.period != policy.period or paid_policy.limit <= policy.limit:
                raise ValueError("Paid feature quota policy must raise the existing period limit")
            if policy.per_actor_limit is not None and (
                paid_policy.per_actor_limit is None or paid_policy.per_actor_limit < policy.per_actor_limit
            ):
                raise ValueError("Paid feature quota policy must not lower the per-actor limit")
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
