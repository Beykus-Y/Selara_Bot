from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import logging
from typing import Protocol, TYPE_CHECKING

from selara.application.model_catalog import CatalogProvider, CatalogSnapshot, ModelCapabilities
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
    # The catalog snapshot this resolution was made from: the pricing reference for the call, so
    # cost, model and multiplier of one request all come from the same configuration revision.
    catalog: CatalogSnapshot | None = field(default=None, compare=False, repr=False)


def resolve_from_snapshot(snapshot: CatalogSnapshot | None, *, profile_key: str | None, legacy_model: str,
                          required: ModelCapabilities = ModelCapabilities()) -> ResolvedModel:
    """Resolve one profile against one immutable snapshot; unusable profiles fall back to legacy."""
    fallback = ResolvedModel(model_id=legacy_model, profile_key=profile_key, catalog=snapshot)
    if snapshot is None or profile_key is None:
        return fallback
    profile = snapshot.profiles_by_key.get(profile_key)
    if profile is None or not profile.enabled:
        return fallback
    fallback = ResolvedModel(model_id=legacy_model, profile_key=profile_key,
                             ail_multiplier=profile.ail_multiplier, catalog=snapshot)
    model = snapshot.models_by_key.get(profile.model_key)
    if model is None or not model.enabled or not model.capabilities.satisfies(required):
        return fallback
    return ResolvedModel(model_id=model.model_id, catalog_key=model.key, profile_key=profile_key,
                         ail_multiplier=profile.ail_multiplier, capabilities=model.capabilities,
                         is_fallback=False, catalog=snapshot)


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
        legacy = legacy_model or self._default_model
        if self._catalog is None or profile_key is None:
            return ResolvedModel(model_id=legacy, profile_key=profile_key)
        try:
            snapshot = await self._catalog.get()
        except Exception:
            logger.exception("Model routing unavailable; using legacy configuration")
            return ResolvedModel(model_id=legacy, profile_key=profile_key)
        return resolve_from_snapshot(snapshot, profile_key=profile_key, legacy_model=legacy, required=required)
