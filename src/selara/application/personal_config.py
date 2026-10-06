"""Selara Personal tunables: base values from settings (.env), optional overrides from the DB.

Priority per field: a value stored in the database wins, otherwise the setting from
``.env``/``core/config.py``. The provider caches briefly, so an override saved through an
admin interface applies without restarting the bot.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Awaitable, Callable, Protocol

from selara.application.feature_access import PersonalQuotaLimits

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TTL_SECONDS = 15.0

# Upper bounds shared by the API and .env validation (Telegram caps an invoice well below these).
MAX_PRICE_STARS = 10_000
MAX_DURATION_DAYS = 365
MAX_DAILY_LIMIT = 10_000


@dataclass(frozen=True, slots=True)
class PersonalConfigOverride:
    """Values an admin saved in the database; ``None`` falls back to settings."""

    price_stars: int | None = None
    duration_days: int | None = None
    free_daily_limit: int | None = None
    paid_daily_limit: int | None = None

    def __post_init__(self) -> None:
        for name, upper in (
            ("price_stars", MAX_PRICE_STARS),
            ("duration_days", MAX_DURATION_DAYS),
            ("free_daily_limit", MAX_DAILY_LIMIT),
            ("paid_daily_limit", MAX_DAILY_LIMIT),
        ):
            value = getattr(self, name)
            if value is not None and not 0 < value <= upper:
                raise ValueError(f"{name} must be between 1 and {upper}")


@dataclass(frozen=True, slots=True)
class PersonalConfig:
    """Effective values after merging settings and the database override."""

    price_stars: int | None
    duration_days: int
    limits: PersonalQuotaLimits


def config_from_settings(settings) -> PersonalConfig:
    return PersonalConfig(
        price_stars=settings.selara_personal_price_stars,
        duration_days=settings.selara_personal_duration_days,
        limits=PersonalQuotaLimits.from_settings(settings),
    )


def merge_config(base: PersonalConfig, override: PersonalConfigOverride | None) -> PersonalConfig:
    """Override beats base per field; raises ValueError if the result is inconsistent."""
    if override is None:
        return base
    limits = PersonalQuotaLimits(
        free_daily=override.free_daily_limit or base.limits.free_daily,
        paid_daily=override.paid_daily_limit or base.limits.paid_daily,
    )
    return replace(
        base,
        price_stars=override.price_stars if override.price_stars is not None else base.price_stars,
        duration_days=override.duration_days or base.duration_days,
        limits=limits,
    )


class PersonalConfigProvider(Protocol):
    async def get(self) -> PersonalConfig: ...


class StaticPersonalConfigProvider:
    """Fixed configuration (settings only, or tests)."""

    def __init__(self, config: PersonalConfig) -> None:
        self._config = config

    async def get(self) -> PersonalConfig:
        return self._config


class CachedPersonalConfigProvider:
    """Settings base + DB override, cached for a short TTL and dropped on ``invalidate``."""

    def __init__(
        self,
        base: PersonalConfig,
        load_override: Callable[[], Awaitable[PersonalConfigOverride | None]],
        *,
        ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = base
        self._load_override = load_override
        self._ttl = ttl_seconds
        self._clock = clock
        self._cached: PersonalConfig | None = None
        self._expires_at = 0.0

    def invalidate(self) -> None:
        self._cached = None
        self._expires_at = 0.0

    async def get(self) -> PersonalConfig:
        now = self._clock()
        if self._cached is not None and now < self._expires_at:
            return self._cached
        try:
            override = await self._load_override()
            config = merge_config(self._base, override)
        except Exception:
            # A DB outage or an inconsistent override must not switch the product off or
            # make limits unbounded: keep the last good value, else the .env base.
            logger.exception("Personal config override unavailable; using last known values")
            config = self._cached or self._base
        self._cached = config
        self._expires_at = now + self._ttl
        return config
