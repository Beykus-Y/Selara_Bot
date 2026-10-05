from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from selara.infrastructure.llm.features import AiFeature

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


@dataclass(frozen=True, slots=True)
class FeatureQuotaPolicy:
    feature: AiFeature
    policy_key: str
    limit: int
    period: QuotaPeriod


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

    This is a typed hand-off for a future entitlement store. PR3 ships only a
    resolver that returns FREE; owner-internal access is derived separately
    from a live Telegram administrator check.
    """

    access_tier: AccessTier
    valid_until: datetime | None = None
    source: str | None = None
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


class ChatEntitlementResolver(Protocol):
    async def resolve(
        self,
        *,
        chat_id: int,
        feature: AiFeature,
        trigger: str,
    ) -> FeatureEntitlement: ...


class NoPaidChatEntitlementResolver:
    """Fail closed until PR4 connects a real entitlement source."""

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
        owner_exempt: bool,
        period_start: datetime,
        period_end: datetime,
    ) -> FeatureUsageSummary: ...

    async def release_if_no_provider_attempts(self, *, invocation_id: int, reason: str) -> bool: ...


def resolve_feature_policy(*, feature: AiFeature, trigger: str) -> FeatureQuotaPolicy | None:
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
    if feature in (AiFeature.AUTOCONFIG, AiFeature.LLM_CONTEXT_COMPRESSION):
        return None
    raise ValueError(f"No explicit feature access policy for {feature!r}")


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
    ) -> None:
        self._repository = repository
        self._entitlement_resolver = entitlement_resolver or NoPaidChatEntitlementResolver()

    async def resolve_feature_access(
        self,
        *,
        feature: AiFeature,
        chat_id: int,
        trigger: str,
        owner_exempt: bool = False,
        now: datetime | None = None,
    ) -> FeatureAccessDecision:
        """Resolve feature entitlement without reserving or consuming quota."""
        if owner_exempt:
            return FeatureAccessDecision(
                allowed=True,
                feature=feature,
                scope_type="chat",
                scope_id=str(chat_id),
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
            entitlement = await self._entitlement_resolver.resolve(
                chat_id=chat_id,
                feature=feature,
                trigger=trigger,
            )
            if not isinstance(entitlement, FeatureEntitlement):
                raise TypeError("Entitlement resolver returned an unsupported result")
            if entitlement.access_tier == AccessTier.OWNER_INTERNAL:
                raise ValueError("Owner-internal access requires a verified Telegram admin check")
            if entitlement.access_tier not in (AccessTier.FREE, AccessTier.PAID):
                raise ValueError("Entitlement resolver returned an unsupported access tier")
        except Exception:
            logger.debug(
                "Feature entitlement resolver failed feature=%s trigger=%s chat_id=%s",
                feature.value,
                trigger,
                chat_id,
                exc_info=True,
            )
            return FeatureAccessDecision(
                allowed=False,
                feature=feature,
                scope_type="chat",
                scope_id=str(chat_id),
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
                    scope_type="chat",
                    scope_id=str(chat_id),
                    access_tier=AccessTier.FREE,
                    quota_limit=None,
                    quota_used=None,
                    quota_remaining=None,
                    period_start=None,
                    period_end=None,
                    reason=AccessReason.ACCESS_REQUIRED,
                    entitlement_valid_until=entitlement_valid_until,
                    entitlement_source=entitlement.source,
                )

        paid = entitlement.access_tier == AccessTier.PAID
        return FeatureAccessDecision(
            allowed=paid,
            feature=feature,
            scope_type="chat",
            scope_id=str(chat_id),
            access_tier=entitlement.access_tier,
            quota_limit=None,
            quota_used=None,
            quota_remaining=None,
            period_start=None,
            period_end=None,
            reason=None if paid else AccessReason.ACCESS_REQUIRED,
            entitlement_valid_until=entitlement_valid_until,
            entitlement_source=entitlement.source,
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
        chat_type: str = "supergroup",
        chat_title: str | None = None,
        actor_is_bot: bool = False,
        source_message_id: int | None = None,
        summary_run_id: int | None = None,
        mode: str | None = None,
        owner_exempt: bool = False,
        now: datetime | None = None,
    ) -> FeatureAccessDecision:
        tier = AccessTier.OWNER_INTERNAL if owner_exempt else AccessTier.FREE
        policy = resolve_feature_policy(feature=feature, trigger=trigger)
        entitlement = None
        if policy is not None and not owner_exempt and feature == AiFeature.DAILY_SUMMARY and trigger == "manual":
            policy, tier, entitlement = await self._manual_summary_policy(
                policy=policy,
                chat_id=chat_id,
                now=now,
            )
        if policy is None:
            return FeatureAccessDecision(
                allowed=True,
                feature=feature,
                scope_type="chat",
                scope_id=str(chat_id),
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
        decision = await self._repository.reserve(
            policy=policy,
            access_tier=tier,
            chat_id=chat_id,
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
            )
        return decision

    async def get_usage_summary(
        self,
        *,
        feature: AiFeature,
        chat_id: int,
        trigger: str,
        timezone_name: str,
        owner_exempt: bool = False,
        now: datetime | None = None,
    ) -> FeatureUsageSummary:
        tier = AccessTier.OWNER_INTERNAL if owner_exempt else AccessTier.FREE
        policy = resolve_feature_policy(feature=feature, trigger=trigger)
        if policy is not None and not owner_exempt and feature == AiFeature.DAILY_SUMMARY and trigger == "manual":
            policy, tier, _ = await self._manual_summary_policy(
                policy=policy,
                chat_id=chat_id,
                now=now,
            )
        if policy is None:
            return FeatureUsageSummary(
                feature, "chat", str(chat_id), tier, None, None, None, None, None, None,
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
            owner_exempt=owner_exempt,
            period_start=start,
            period_end=end,
        )
        return replace(summary, access_tier=tier)

    async def _manual_summary_policy(
        self,
        *,
        policy: FeatureQuotaPolicy,
        chat_id: int,
        now: datetime | None,
    ) -> tuple[FeatureQuotaPolicy, AccessTier, FeatureEntitlement | None]:
        """Select a future paid manual quota without promising a paid limit today.

        Resolver errors and incomplete paid policies fall back to the current
        free 10/month policy. Owner-internal quota bypass remains a separate
        path based on a live Telegram admin check.
        """
        try:
            entitlement = await self._entitlement_resolver.resolve(
                chat_id=chat_id,
                feature=AiFeature.DAILY_SUMMARY,
                trigger="manual",
            )
            if not isinstance(entitlement, FeatureEntitlement):
                raise TypeError("Entitlement resolver returned an unsupported result")
            if entitlement.access_tier != AccessTier.PAID or entitlement.quota_policy is None:
                return policy, AccessTier.FREE, None
            if entitlement.quota_policy.feature != AiFeature.DAILY_SUMMARY:
                raise ValueError("Manual Daily Summary entitlement supplied a policy for another feature")
            if entitlement.quota_policy.period != QuotaPeriod.MONTH or entitlement.quota_policy.limit <= policy.limit:
                raise ValueError("Paid manual Daily Summary policy must raise the monthly free quota")
            if entitlement.valid_until is not None:
                expiry = entitlement.valid_until
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                current = now or datetime.now(timezone.utc)
                if current.tzinfo is None:
                    current = current.replace(tzinfo=timezone.utc)
                if expiry.astimezone(timezone.utc) <= current.astimezone(timezone.utc):
                    return policy, AccessTier.FREE, None
            return entitlement.quota_policy, AccessTier.PAID, entitlement
        except Exception:
            logger.debug(
                "Manual Daily Summary quota policy resolution failed; applying free policy chat_id=%s",
                chat_id,
                exc_info=True,
            )
            return policy, AccessTier.FREE, None

    async def release_if_no_provider_attempts(self, *, invocation_id: int, reason: str) -> bool:
        return await self._repository.release_if_no_provider_attempts(invocation_id=invocation_id, reason=reason)
