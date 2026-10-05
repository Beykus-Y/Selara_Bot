from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

SELARA_AI_PRODUCT_KEY = "selara_ai_monthly"
SELARA_AI_CURRENCY = "XTR"
SELARA_AI_DURATION = timedelta(days=30)
PURCHASE_INTENT_TTL = timedelta(minutes=15)
_INVOICE_PAYLOAD_PREFIX = "selara_ai:v1:"


class UnsupportedSelaraAiProduct(ValueError):
    pass


class SelaraAiProductUnavailable(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SelaraAiProduct:
    key: str
    title: str
    description: str
    price_stars: int
    currency: str
    duration: timedelta

    @property
    def duration_label(self) -> str:
        return f"{int(self.duration.total_seconds() // 86_400)} дней"


def get_selara_ai_product(*, product_key: str, price_stars: int | None) -> SelaraAiProduct:
    """Resolve the sole supported product using its one configured Stars price."""
    if product_key != SELARA_AI_PRODUCT_KEY:
        raise UnsupportedSelaraAiProduct(product_key)
    if price_stars is None or price_stars <= 0:
        raise SelaraAiProductUnavailable("SELARA_AI_PRICE_STARS must be set to a positive integer")
    duration = SELARA_AI_DURATION
    duration_label = f"{int(duration.total_seconds() // 86_400)} дней"
    return SelaraAiProduct(
        key=SELARA_AI_PRODUCT_KEY,
        title=f"Selara AI на {duration_label}",
        description=f"Доступ к AI-функциям Selara для выбранного чата на {duration_label}.",
        price_stars=price_stars,
        currency=SELARA_AI_CURRENCY,
        duration=duration,
    )


def invoice_payload_for_intent(intent_id: str) -> str:
    """Build a versioned payload that carries only an unguessable intent UUID."""
    canonical_id = str(UUID(intent_id))
    return f"{_INVOICE_PAYLOAD_PREFIX}{canonical_id}"


def parse_invoice_payload(payload: str | None) -> str | None:
    if not isinstance(payload, str) or not payload.startswith(_INVOICE_PAYLOAD_PREFIX):
        return None
    raw_id = payload[len(_INVOICE_PAYLOAD_PREFIX):]
    try:
        canonical_id = str(UUID(raw_id))
    except (ValueError, AttributeError):
        return None
    if canonical_id != raw_id:
        return None
    return canonical_id
