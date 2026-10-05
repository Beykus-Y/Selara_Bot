from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from selara.infrastructure.llm.features import AiFeature

logger = logging.getLogger(__name__)


class AccessTier(StrEnum):
    FREE = "free"
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


class FeatureQuotaRepository(Protocol):
    async def reserve(
        self,
        *,
        policy: FeatureQuotaPolicy,
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

    def __init__(self, repository: FeatureQuotaRepository) -> None:
        self._repository = repository

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
        return await self._repository.reserve(
            policy=policy,
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
        return await self._repository.usage_summary(
            policy=policy,
            chat_id=chat_id,
            owner_exempt=owner_exempt,
            period_start=start,
            period_end=end,
        )

    async def release_if_no_provider_attempts(self, *, invocation_id: int, reason: str) -> bool:
        return await self._repository.release_if_no_provider_attempts(invocation_id=invocation_id, reason=reason)
