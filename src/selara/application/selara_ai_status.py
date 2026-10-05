"""Chat-level Selara AI status for the Mini App, derived from ``FeatureAccessService``.

The service stays the single source of truth for tier, quota and reset
boundaries; this module only reshapes its answers into a frontend-friendly
payload and keeps "could not check" distinct from "free".
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from selara.application.feature_access import (
    AccessReason,
    AccessTier,
    FeatureAccessService,
    FeatureUsageSummary,
)
from selara.infrastructure.llm.features import AiFeature

logger = logging.getLogger(__name__)
EXPIRING_SOON = timedelta(days=7)


def checkout_ready(settings: Any) -> bool:
    """Same prerequisites as the /premium checkout handler: provider and price."""
    return bool(
        settings.llm_enabled
        and (settings.llm_api_key or "").strip()
        and settings.selara_ai_price_stars is not None
    )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _quota_payload(summary: FeatureUsageSummary | None) -> dict[str, Any]:
    if summary is None:
        return {"status": "unavailable", "used": None, "limit": None, "remaining": None, "reset_at": None}
    if summary.quota_limit is None:
        return {
            "status": "unlimited",
            "used": None,
            "limit": None,
            "remaining": None,
            "reset_at": None,
            "exhausted": False,
        }
    remaining = summary.quota_remaining
    return {
        "status": "ok",
        "used": summary.quota_used,
        "limit": summary.quota_limit,
        "remaining": remaining,
        "reset_at": _iso(summary.reset_at),
        "exhausted": remaining is not None and remaining <= 0,
    }


def _automatic_state(*, enabled: bool, allowed: bool, available: bool) -> str:
    if not available:
        return "unknown"
    if allowed:
        return "active" if enabled else "available_disabled"
    return "requires_access_enabled" if enabled else "requires_access"


async def build_chat_ai_access_status(
    *,
    access_service: FeatureAccessService,
    chat_id: int,
    automatic_enabled: bool,
    owner_exempt: bool,
    timezone_name: str,
    can_manage_purchase: bool,
    checkout_configured: bool,
    bot_dm_url: str,
    display_timezone: str = "UTC",
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    base: dict[str, Any] = {
        "chat_id": chat_id,
        "checked_at": _iso(current),
        "timezone": display_timezone,
        "can_manage_purchase": can_manage_purchase,
        "checkout_configured": checkout_configured,
        "purchase": {"command": "/premium", "bot_dm_url": bot_dm_url},
    }
    try:
        automatic = await access_service.resolve_feature_access(
            feature=AiFeature.DAILY_SUMMARY,
            chat_id=chat_id,
            trigger="scheduled",
            owner_exempt=owner_exempt,
            now=current,
        )
    except Exception:
        logger.warning("Selara AI access status failed chat_id=%s", chat_id, exc_info=True)
        automatic = None

    if automatic is None or automatic.reason == AccessReason.ACCESS_UNAVAILABLE:
        return {
            **base,
            "state": "unavailable",
            "tier": None,
            "entitlement": None,
            "llm": _quota_payload(None),
            "manual_summary": _quota_payload(None),
            "automatic_summary": {
                "enabled": automatic_enabled,
                "access_allowed": None,
                "state": "unknown",
            },
        }

    async def usage(feature: AiFeature, trigger: str) -> FeatureUsageSummary | None:
        try:
            return await access_service.get_usage_summary(
                feature=feature,
                chat_id=chat_id,
                trigger=trigger,
                timezone_name=timezone_name,
                owner_exempt=owner_exempt,
                now=current,
            )
        except Exception:
            logger.warning(
                "Selara AI quota status failed chat_id=%s feature=%s", chat_id, feature.value, exc_info=True
            )
            return None

    llm = await usage(AiFeature.LLM_ADMIN, "telegram_message")
    manual = await usage(AiFeature.DAILY_SUMMARY, "manual")

    valid_until = automatic.entitlement_valid_until
    expired = (
        automatic.access_tier == AccessTier.FREE
        and automatic.reason == AccessReason.ACCESS_REQUIRED
        and valid_until is not None
    )
    paid = automatic.access_tier == AccessTier.PAID and automatic.allowed
    remaining_seconds = None
    if paid and valid_until is not None:
        expiry = valid_until if valid_until.tzinfo else valid_until.replace(tzinfo=timezone.utc)
        remaining_seconds = (expiry - current).total_seconds()
    entitlement = {
        "active": paid,
        "valid_until": _iso(valid_until) if paid else None,
        "expired_at": _iso(valid_until) if expired else None,
        "expiring_soon": remaining_seconds is not None and remaining_seconds <= EXPIRING_SOON.total_seconds(),
        "days_left": (
            max(0, -(-int(remaining_seconds) // 86_400)) if remaining_seconds is not None else None
        ),
        "source": automatic.entitlement_source,
    }
    return {
        **base,
        "state": "available",
        "tier": automatic.access_tier.value,
        "entitlement": entitlement,
        "llm": _quota_payload(llm),
        "manual_summary": _quota_payload(manual),
        "automatic_summary": {
            "enabled": automatic_enabled,
            "access_allowed": automatic.allowed,
            "state": _automatic_state(enabled=automatic_enabled, allowed=automatic.allowed, available=True),
        },
    }
