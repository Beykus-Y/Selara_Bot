from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import logging
from typing import Protocol, TYPE_CHECKING

from selara.application.model_catalog import CatalogProvider, ModelCapabilities
if TYPE_CHECKING:
    from selara.application.feature_access import AccessTier
    from selara.infrastructure.llm.features import AiFeature


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    model_id: str
    catalog_key: str | None = None
    profile_key: str | None = None
    ail_multiplier: Decimal = Decimal("1")
    # None means legacy capabilities are unknown, not explicitly unsupported.
    capabilities: ModelCapabilities | None = None
    is_fallback: bool = True


class ModelRouter(Protocol):
    async def resolve(self, *, profile_key: str | None = None, legacy_model: str | None = None,
                      required: ModelCapabilities = ModelCapabilities(),
                      feature: AiFeature | None = None, tier: AccessTier | None = None) -> ResolvedModel: ...


logger = logging.getLogger(__name__)


class DefaultModelRouter:
    """Profile configuration over the existing default, with a per-operation legacy fallback."""

    def __init__(self, default_model: str, catalog: CatalogProvider | None = None) -> None:
        self._default_model = default_model
        self._catalog = catalog

    async def resolve(self, *, profile_key: str | None = None, legacy_model: str | None = None,
                      required: ModelCapabilities = ModelCapabilities(),
                      feature: AiFeature | None = None, tier: AccessTier | None = None) -> ResolvedModel:
        fallback = ResolvedModel(model_id=legacy_model or self._default_model, profile_key=profile_key)
        if self._catalog is None or profile_key is None:
            return fallback
        try:
            snapshot = await self._catalog.get()
        except Exception:
            logger.exception("Model routing unavailable; using legacy configuration")
            return fallback
        profile = snapshot.profiles_by_key.get(profile_key)
        if profile is None or not profile.enabled:
            return fallback
        fallback = ResolvedModel(model_id=fallback.model_id, profile_key=profile_key,
                                 ail_multiplier=profile.ail_multiplier)
        model = snapshot.models_by_key.get(profile.model_key)
        if model is None or not model.enabled or not model.capabilities.satisfies(required):
            return fallback
        return ResolvedModel(model_id=model.model_id, catalog_key=model.key, profile_key=profile_key,
                             ail_multiplier=profile.ail_multiplier, capabilities=model.capabilities,
                             is_fallback=False)
