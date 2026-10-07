"""Which model profile group-wide AI features use, edited by the owner at runtime.

A route maps a feature group to a profile of the model catalog. No route (or an unusable
profile) keeps the feature on the legacy ``LLM_MODEL``; the cache follows the same short TTL as
the catalog, so a change applies without restarting the bot.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Mapping

from selara.application.model_catalog import PROFILE_NAMES
from selara.infrastructure.llm.features import AiFeature

logger = logging.getLogger(__name__)

DEFAULT_ROUTE_TTL_SECONDS = 15.0

ROUTE_GROUP_ASK = "group_ask"
ROUTE_GROUP_MEMBER = "group_member"
ROUTE_PETS = "pets"

ROUTE_TITLES: Mapping[str, str] = {
    ROUTE_GROUP_ASK: "Вопросы в группах (? и ??)",
    ROUTE_GROUP_MEMBER: "Обращения к Selara по кличке",
    ROUTE_PETS: "AI-питомцы (разговор и реплики)",
}
ROUTE_KEYS = tuple(ROUTE_TITLES)
# Routes whose calls offer tools: a model without tool support falls back to LLM_MODEL at runtime.
TOOL_ROUTES = frozenset({ROUTE_GROUP_ASK, ROUTE_GROUP_MEMBER})

# Features whose model follows a route. Summaries, autoconfig, Personal and the internal
# extraction/compression operations keep their own routing on purpose.
FEATURE_ROUTES: Mapping[str, str] = {
    AiFeature.LLM_ADMIN.value: ROUTE_GROUP_ASK,
    AiFeature.GROUP_MEMBER.value: ROUTE_GROUP_MEMBER,
    AiFeature.PET_TALK.value: ROUTE_PETS,
    AiFeature.PET_EVENT_TEXT.value: ROUTE_PETS,
    AiFeature.PET_ACTION.value: ROUTE_PETS,
}


def validate_route(route_key: str, profile_key: str | None) -> None:
    if route_key not in ROUTE_TITLES:
        raise ValueError("Неизвестная настройка.")
    if profile_key is not None and profile_key not in PROFILE_NAMES:
        raise ValueError("Неизвестный профиль модели.")


class CachedFeatureRoutes:
    """``route_key -> profile_key`` with a TTL cache and last-known-good on load failures."""

    def __init__(
        self,
        load: Callable[[], Awaitable[Mapping[str, str | None]]],
        *,
        ttl_seconds: float = DEFAULT_ROUTE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._load = load
        self._ttl = ttl_seconds
        self._clock = clock
        self._cached: Mapping[str, str | None] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._expires_at = 0.0

    async def get(self) -> Mapping[str, str | None]:
        if self._clock() < self._expires_at:
            return self._cached
        async with self._lock:  # single flight: one load per expiry, the rest reuse it
            now = self._clock()
            if now < self._expires_at:
                return self._cached
            try:
                self._cached = dict(await self._load())
            except Exception:
                logger.exception("Feature model routes unavailable; keeping the last known values")
            self._expires_at = now + self._ttl
            return self._cached

    async def profile_for_feature(self, feature: str | None) -> str | None:
        route = FEATURE_ROUTES.get(feature or "")
        return (await self.get()).get(route) if route else None
