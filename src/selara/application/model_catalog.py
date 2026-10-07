"""Validated, immutable model configuration; no ORM objects escape the store."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime
from types import MappingProxyType
from typing import Awaitable, Callable, Mapping, Protocol

logger = logging.getLogger(__name__)
PROFILE_NAMES = {
    "basic": "Базовая", "analytics": "Аналитик", "freeform": "Свободная",
    "creative": "Творческая", "fast": "Быстрая",
}
# Stable user-facing presentation of the profile keys; display names stay editable per profile.
PROFILE_ORDER = ("basic", "analytics", "freeform", "creative", "fast")
PROFILE_EMOJI = {"basic": "⚪", "analytics": "🧠", "freeform": "🎭", "creative": "🎨", "fast": "⚡"}
PROFILE_DESCRIPTIONS = {
    "basic": "Для обычных разговоров",
    "analytics": "Для сложного анализа",
    "freeform": "Для более свободного и ролевого общения",
    "creative": "Для сложных творческих задач",
    "fast": "Для минимальной задержки",
}
MAX_TOKEN_PRICE = Decimal("1000000")


def validate_key(key: str) -> None:
    if not isinstance(key, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is None:
        raise ValueError("model key must be 1..64 lowercase letters, digits or underscores")


def validate_text(value: str, name: str, maximum: int = 255) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be nonempty, trimmed and at most {maximum} characters")


def validate_decimal(value: Decimal | None, name: str, *, maximum: Decimal, positive: bool = False) -> None:
    if value is None and not positive:
        return
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if value < 0 or (positive and value == 0) or value > maximum:
        raise ValueError(f"{name} outside allowed range")
    if value != value.quantize(Decimal("0.000000001")):
        raise ValueError(f"{name} supports at most 9 decimal places")


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    supports_tools: bool = False
    supports_structured_output: bool = False
    supports_vision: bool = False

    def __post_init__(self) -> None:
        if any(type(value) is not bool for value in (
            self.supports_tools, self.supports_structured_output, self.supports_vision,
        )):
            raise ValueError("capabilities must be booleans")

    def satisfies(self, required: ModelCapabilities) -> bool:
        return all(not getattr(required, name) or getattr(self, name) for name in (
            "supports_tools", "supports_structured_output", "supports_vision",
        ))


@dataclass(frozen=True, slots=True)
class CatalogModel:
    key: str
    model_id: str
    display_name: str
    enabled: bool = True
    prompt_price_usd_per_million: Decimal | None = None
    completion_price_usd_per_million: Decimal | None = None
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    aliases: tuple[str, ...] = ()
    revision: int = 0
    updated_by: int | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_key(self.key)
        validate_text(self.model_id, "model_id")
        validate_text(self.display_name, "display_name")
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        if not isinstance(self.capabilities, ModelCapabilities):
            raise ValueError("capabilities must be ModelCapabilities")
        for name in ("prompt_price_usd_per_million", "completion_price_usd_per_million"):
            validate_decimal(getattr(self, name), name, maximum=MAX_TOKEN_PRICE)
        object.__setattr__(self, "aliases", tuple(self.aliases))
        for alias in self.aliases:
            validate_text(alias, "alias")
        if len(set((self.model_id, *self.aliases))) != len(self.aliases) + 1:
            raise ValueError("duplicate model identifiers")

    def estimate(self, prompt_tokens: int | None, completion_tokens: int | None) -> Decimal | None:
        if (self.prompt_price_usd_per_million is None or self.completion_price_usd_per_million is None
                or prompt_tokens is None or completion_tokens is None):
            return None
        return ((Decimal(prompt_tokens) * self.prompt_price_usd_per_million
                 + Decimal(completion_tokens) * self.completion_price_usd_per_million)
                / Decimal(1000000)).quantize(Decimal("0.000000001"))


@dataclass(frozen=True, slots=True)
class ModelProfile:
    profile_key: str
    display_name: str
    model_key: str | None = None
    ail_multiplier: Decimal = Decimal("1")
    enabled: bool = True
    revision: int = 0
    updated_by: int | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.profile_key not in PROFILE_NAMES:
            raise ValueError("unknown model profile")
        validate_text(self.display_name, "display_name")
        if self.model_key is not None:
            validate_key(self.model_key)
        validate_decimal(self.ail_multiplier, "ail_multiplier", maximum=Decimal("1000"), positive=True)
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean")


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    models: tuple[CatalogModel, ...] = ()
    profiles: tuple[ModelProfile, ...] = ()
    models_by_key: Mapping[str, CatalogModel] = field(init=False, repr=False)
    models_by_id: Mapping[str, CatalogModel] = field(init=False, repr=False)
    profiles_by_key: Mapping[str, ModelProfile] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "models", tuple(self.models))
        object.__setattr__(self, "profiles", tuple(self.profiles))
        by_key = {model.key: model for model in self.models}
        by_id = {name: model for model in self.models for name in (model.model_id, *model.aliases)}
        profiles = {profile.profile_key: profile for profile in self.profiles}
        if len(by_key) != len(self.models) or len(by_id) != sum(1 + len(m.aliases) for m in self.models):
            raise ValueError("catalog identifiers must be globally unique")
        if len(profiles) != len(self.profiles):
            raise ValueError("duplicate profile key")
        if any(p.model_key is not None and p.model_key not in by_key for p in self.profiles):
            raise ValueError("profile references missing catalog model")
        object.__setattr__(self, "models_by_key", MappingProxyType(by_key))
        object.__setattr__(self, "models_by_id", MappingProxyType(by_id))
        object.__setattr__(self, "profiles_by_key", MappingProxyType(profiles))


class CatalogProvider(Protocol):
    async def get(self) -> CatalogSnapshot: ...


class ModelConfigurationConflict(ValueError):
    """The configuration was changed after the editor loaded it."""


class ModelCatalogStore(Protocol):
    async def load(self) -> CatalogSnapshot: ...
    async def save_model(self, model: CatalogModel, *, expected_revision: int | None = None,
                         updated_by: int | None = None, confirm_disable: bool = False) -> None: ...
    async def save_profile(self, profile: ModelProfile, *, expected_revision: int | None = None,
                           updated_by: int | None = None) -> None: ...


class CachedModelCatalogProvider:
    """One atomic snapshot per TTL; invalidate retains last-known-good on failures."""

    def __init__(self, load: Callable[[], Awaitable[CatalogSnapshot]], *, ttl_seconds: float = 15,
                 clock: Callable[[], float] = time.monotonic, load_timeout_seconds: float = 2.0) -> None:
        if ttl_seconds <= 0 or load_timeout_seconds <= 0:
            raise ValueError("cache TTL must be positive")
        self._load = load
        self._load_timeout = load_timeout_seconds
        self._ttl = ttl_seconds
        self._clock = clock
        self._cached: CatalogSnapshot | None = None
        self._expires_at = 0.0
        self._generation = 0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._generation += 1
        self._expires_at = 0.0

    async def get(self) -> CatalogSnapshot:
        if self._clock() < self._expires_at and self._cached is not None:
            return self._cached
        async with self._lock:
            if self._clock() < self._expires_at and self._cached is not None:
                return self._cached
            generation = self._generation
            try:
                snapshot = await asyncio.wait_for(self._load(), timeout=self._load_timeout)
                if not isinstance(snapshot, CatalogSnapshot):
                    raise ValueError("invalid catalog snapshot")
            except Exception:
                logger.exception("Model catalog unavailable; retaining last known configuration")
                snapshot = self._cached or CatalogSnapshot()
            self._cached = snapshot
            self._expires_at = self._clock() + self._ttl if generation == self._generation else 0.0
            return snapshot
