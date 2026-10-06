from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

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


class FlatUsagePricer:
    """Every operation costs exactly one unit, which keeps today's per-request limits."""

    _ONE = QuotaCost(Decimal("1"))

    def price(
        self,
        *,
        feature: AiFeature,
        model_key: str | None = None,
        operation: str = "request",
    ) -> QuotaCost:
        return self._ONE
