"""Chat-level Selara AI status for the Mini App, derived from ``FeatureAccessService``.

The service stays the single source of truth for tier, quota and reset
boundaries; this module only reshapes its answers into a frontend-friendly
payload and keeps "could not check" distinct from "free".
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from selara.application.feature_access import (
    AccessReason,
    AccessTier,
    FeatureAccessService,
    FeatureUsageSummary,
)
from selara.infrastructure.llm.features import AiFeature
from selara.infrastructure.llm.runtime import llm_runtime_config

logger = logging.getLogger(__name__)
EXPIRING_SOON = timedelta(days=7)


def checkout_ready(settings: Any) -> bool:
    """Same prerequisites as the /premium checkout handler: provider and price."""
    return llm_runtime_config(settings) is not None and settings.selara_ai_price_stars is not None


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def quota_payload(summary: FeatureUsageSummary | None) -> dict[str, Any]:
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
        # "request" or "ail" (AI Limits): what used/limit/remaining count.
        "unit": summary.quota_unit or "request",
        "used": summary.quota_used,
        "limit": summary.quota_limit,
        "remaining": remaining,
        "reset_at": iso_utc(summary.reset_at),
        "exhausted": remaining is not None and remaining <= 0,
    }


def _automatic_state(*, enabled: bool, allowed: bool, available: bool, provider_available: bool = True) -> str:
    if not available:
        return "unknown"
    if allowed:
        if not enabled:
            return "available_disabled"
        return "active" if provider_available else "provider_unavailable"
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
    provider_available: bool = True,
    display_timezone: str = "UTC",
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    base: dict[str, Any] = {
        "chat_id": chat_id,
        "checked_at": iso_utc(current),
        "timezone": display_timezone,
        "can_manage_purchase": can_manage_purchase,
        "checkout_configured": checkout_configured,
        "provider_available": provider_available,
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
            "llm": quota_payload(None),
            "manual_summary": quota_payload(None),
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
        "valid_until": iso_utc(valid_until) if paid else None,
        "expired_at": iso_utc(valid_until) if expired else None,
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
        "llm": quota_payload(llm),
        "manual_summary": quota_payload(manual),
        "automatic_summary": {
            "enabled": automatic_enabled,
            "access_allowed": automatic.allowed,
            "state": _automatic_state(
                enabled=automatic_enabled,
                allowed=automatic.allowed,
                available=True,
                provider_available=provider_available,
            ),
        },
    }


class TelegramFlagCache:
    """Short TTL cache for live Telegram answers; only successful lookups are stored."""

    def __init__(self, *, ttl_seconds: float = 45.0, max_entries: int = 2048, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._items: dict[tuple, tuple[float, bool]] = {}
        # In-flight lookups by (chat_id, user_id), so concurrent cache misses share one Telegram call.
        self.inflight: dict[tuple[int, int], asyncio.Future[bool | None]] = {}

    def get(self, key: tuple) -> bool | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        if entry[0] <= self._clock():
            self._items.pop(key, None)
            return None
        return entry[1]

    def put(self, key: tuple, value: bool) -> None:
        if len(self._items) >= self._max:
            now = self._clock()
            self._items = {k: v for k, v in self._items.items() if v[0] > now}
            if len(self._items) >= self._max:
                self._items.clear()
        self._items[key] = (self._clock() + self._ttl, value)


async def resolve_telegram_flags(
    *,
    bot: Any,
    chat_id: int,
    user_id: int,
    admin_user_id: int | None,
    is_admin: Callable[[Any], bool],
    cache: TelegramFlagCache,
    timeout: float = 4.0,
) -> tuple[bool, bool]:
    """Return ``(owner_exempt, can_manage_purchase)`` from live Telegram admin status.

    Missing answers are fetched concurrently. Failures and timeouts are treated as
    "not admin" for this response but never cached.
    """
    owner_key = ("owner", chat_id)
    user_key = ("user", chat_id, user_id)
    owner_value = cache.get(owner_key) if admin_user_id is not None else False
    user_value = cache.get(user_key)

    async def fetch(target_id: int) -> bool | None:
        try:
            member = await asyncio.wait_for(bot.get_chat_member(chat_id=chat_id, user_id=target_id), timeout=timeout)
            return bool(is_admin(member))
        except Exception:
            logger.warning("Telegram admin lookup failed chat_id=%s", chat_id, exc_info=True)
            return None

    async def lookup(target_id: int) -> bool | None:
        flight_key = (chat_id, target_id)
        task = cache.inflight.get(flight_key)
        if task is None:
            task = asyncio.ensure_future(fetch(target_id))
            cache.inflight[flight_key] = task
            task.add_done_callback(lambda _task, key=flight_key: cache.inflight.pop(key, None))
        # shield: one caller being cancelled must not cancel the lookup other callers wait on.
        return await asyncio.shield(task)

    wanted: list[int] = []
    if owner_value is None and admin_user_id is not None:
        wanted.append(admin_user_id)
    if user_value is None and user_id not in wanted:
        wanted.append(user_id)
    answers = dict(zip(wanted, await asyncio.gather(*(lookup(target) for target in wanted))))

    if owner_value is None and admin_user_id is not None:
        owner_value = answers.get(admin_user_id)
        if owner_value is not None:
            cache.put(owner_key, owner_value)
    if user_value is None:
        user_value = answers.get(user_id)
        if user_value is not None:
            cache.put(user_key, user_value)
    owner_exempt = bool(owner_value)
    return owner_exempt, (False if owner_exempt else bool(user_value))
