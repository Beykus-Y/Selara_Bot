from __future__ import annotations

from typing import Protocol

from selara.application.feature_access import AccessTier
from selara.infrastructure.llm.features import AiFeature


class ModelRouter(Protocol):
    """Chooses the model for a feature and tier; real routing is a later stage."""

    def resolve(self, *, feature: AiFeature, tier: AccessTier) -> str: ...


class DefaultModelRouter:
    """Trivial router: every feature and tier uses the one configured ``LLM_MODEL``."""

    def __init__(self, default_model: str) -> None:
        self._default_model = default_model

    def resolve(self, *, feature: AiFeature, tier: AccessTier) -> str:
        return self._default_model
