"""Read model of the "Моя Selara" Mini App page: subscription, daily quota and memory limits of one user.

``FeatureAccessService`` stays the single source of truth for tier and quota; this module only reshapes its
answers. It never reserves quota: looking at the page is free, and an answer that could not be checked is
reported as "unavailable" instead of being passed off as the free tier.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from selara.application.feature_access import (
    AccessReason,
    AccessTier,
    FeatureAccessDecision,
    FeatureAccessService,
    FeatureUsageSummary,
    QuotaScope,
)
from selara.application.personal_config import PersonalConfig
from selara.application.selara_ai_status import EXPIRING_SOON, iso_utc, quota_payload
from selara.infrastructure.llm.features import AiFeature

logger = logging.getLogger(__name__)

PAID_TIERS = (AccessTier.PAID, AccessTier.OWNER_INTERNAL)


def memory_limit_for(tier: AccessTier | None, config: PersonalConfig) -> int | None:
    """Fact limit of a tier; ``None`` (access unknown) must be treated as "do not write"."""
    if tier is None:
        return None
    return config.memory_paid_limit if tier in PAID_TIERS else config.memory_free_limit


async def resolve_personal_decision(
    access_service: FeatureAccessService, *, user_id: int, owner_exempt: bool, now: datetime | None = None
) -> FeatureAccessDecision | None:
    """Tier of the user for the personal chat, or ``None`` when it could not be checked (fail closed)."""
    try:
        decision = await access_service.resolve_feature_access(
            feature=AiFeature.PERSONAL_CHAT,
            chat_id=user_id,
            trigger="telegram_message",
            scope=QuotaScope.user(user_id),
            owner_exempt=owner_exempt,
            now=now,
        )
    except Exception:
        logger.warning("personal overview: access resolution failed user_id=%s", user_id, exc_info=True)
        return None
    if decision.reason == AccessReason.ACCESS_UNAVAILABLE:
        return None
    return decision


async def build_personal_status(
    *,
    access_service: FeatureAccessService,
    decision: FeatureAccessDecision | None,
    user_id: int,
    owner_exempt: bool,
    timezone_name: str,
    config: PersonalConfig,
    offer_available: bool,
    bot_dm_url: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    purchase = {"command": "/premium", "bot_dm_url": bot_dm_url}
    offer = {
        "offer_available": offer_available,
        "price_stars": config.price_stars if offer_available else None,
        "duration_days": config.duration_days,
        "purchase": purchase,
    }
    if decision is None:
        return {
            "subscription": {
                "state": "unavailable",
                "tier": None,
                "active": False,
                "owner_exempt": owner_exempt,
                "valid_until": None,
                "days_left": None,
                "expiring_soon": False,
                **offer,
            },
            "quota": quota_payload(None),
        }

    usage: FeatureUsageSummary | None
    try:
        usage = await access_service.get_usage_summary(
            feature=AiFeature.PERSONAL_CHAT,
            chat_id=user_id,
            trigger="telegram_message",
            timezone_name=timezone_name,
            scope=QuotaScope.user(user_id),
            owner_exempt=owner_exempt,
            now=current,
        )
    except Exception:
        logger.warning("personal overview: quota summary failed user_id=%s", user_id, exc_info=True)
        usage = None

    paid = decision.access_tier == AccessTier.PAID
    valid_until = decision.entitlement_valid_until if paid else None
    remaining_seconds = None
    if valid_until is not None:
        expiry = valid_until if valid_until.tzinfo else valid_until.replace(tzinfo=timezone.utc)
        remaining_seconds = (expiry - current).total_seconds()
    return {
        "subscription": {
            "state": "available",
            "tier": decision.access_tier.value,
            "active": paid,
            "owner_exempt": decision.access_tier == AccessTier.OWNER_INTERNAL,
            "valid_until": iso_utc(valid_until),
            "days_left": (
                max(0, -(-int(remaining_seconds) // 86_400)) if remaining_seconds is not None else None
            ),
            "expiring_soon": remaining_seconds is not None and remaining_seconds <= EXPIRING_SOON.total_seconds(),
            **offer,
        },
        "quota": quota_payload(usage),
    }
