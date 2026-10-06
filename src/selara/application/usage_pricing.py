from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Protocol

from selara.infrastructure.llm.features import AiFeature


@dataclass(frozen=True, slots=True)
class QuotaCost:
    """Units one operation draws from a quota pool (a request today, AI Limits later)."""

    units: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if self.units < 0:
            raise ValueError("Quota cost cannot be negative")


class UsagePricer(Protocol):
    """Maps ``(feature, model, operation)`` to pool units; feature code never knows the numbers."""

    def price(
        self,
        *,
        feature: AiFeature,
        model_key: str | None = None,
        operation: str = "request",
    ) -> QuotaCost: ...


# A unit is defined as one plain request; weights express every other cost relative to it.
BASELINE_REQUEST_UNITS = Decimal("1")


class ConfiguredUsagePricer:
    """Prices from configuration: a default for every operation plus per-feature overrides.

    Feature code only calls ``price``. This is the AI Limits foundation: Selara Personal
    features are deliberately NOT priced through it yet (they cost one request, see
    ``feature_access.PERSONAL_REQUEST_COST``); a later PR switches that on explicitly.
    """

    def __init__(
        self,
        default_units: Decimal = BASELINE_REQUEST_UNITS,
        overrides: Mapping[str, Decimal] | None = None,
    ) -> None:
        self._default = QuotaCost(Decimal(default_units))
        self._overrides = {key: QuotaCost(Decimal(value)) for key, value in (overrides or {}).items()}

    def price(
        self,
        *,
        feature: AiFeature,
        model_key: str | None = None,
        operation: str = "request",
    ) -> QuotaCost:
        return self._overrides.get(feature.value, self._default)
