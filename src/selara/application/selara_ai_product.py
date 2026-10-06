from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

SELARA_AI_PRODUCT_KEY = "selara_ai_monthly"
SELARA_AI_TERMS_VERSION = "v2"
SELARA_PERSONAL_PRODUCT_KEY = "selara_personal_monthly"
SELARA_PERSONAL_TERMS_VERSION = "personal-v1"
SELARA_AI_CURRENCY = "XTR"
SELARA_AI_DURATION = timedelta(days=30)
PRODUCT_SCOPE_CHAT = "chat"
PRODUCT_SCOPE_USER = "user"
PURCHASE_INTENT_TTL = timedelta(minutes=15)
_INVOICE_PAYLOAD_PREFIX = "selara_ai:v1:"


class UnsupportedSelaraAiProduct(ValueError):
    pass


class SelaraAiProductUnavailable(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProductSpec:
    """Static facts about a sellable product; the price is owner configuration, not catalog."""

    key: str
    scope: str
    # None: the duration is owner configuration (SELARA_PERSONAL_DURATION_DAYS), not catalog.
    duration: timedelta | None
    terms_version: str


PRODUCT_SPECS: dict[str, ProductSpec] = {
    SELARA_AI_PRODUCT_KEY: ProductSpec(
        key=SELARA_AI_PRODUCT_KEY,
        scope=PRODUCT_SCOPE_CHAT,
        duration=SELARA_AI_DURATION,
        terms_version=SELARA_AI_TERMS_VERSION,
    ),
    SELARA_PERSONAL_PRODUCT_KEY: ProductSpec(
        key=SELARA_PERSONAL_PRODUCT_KEY,
        scope=PRODUCT_SCOPE_USER,
        duration=None,
        terms_version=SELARA_PERSONAL_TERMS_VERSION,
    ),
}


def get_product_spec(product_key: str | None) -> ProductSpec | None:
    return PRODUCT_SPECS.get(product_key) if product_key else None


@dataclass(frozen=True, slots=True)
class SelaraAiProduct:
    key: str
    title: str
    description: str
    price_stars: int
    currency: str
    duration: timedelta
    scope: str = PRODUCT_SCOPE_CHAT
    terms_version: str = SELARA_AI_TERMS_VERSION

    @property
    def duration_label(self) -> str:
        return f"{int(self.duration.total_seconds() // 86_400)} дней"


def get_selara_ai_product(
    *, product_key: str, price_stars: int | None, duration: timedelta | None = None
) -> SelaraAiProduct:
    """Resolve a supported product using its configured Stars price (and duration, if configurable)."""
    spec = get_product_spec(product_key)
    if spec is None:
        raise UnsupportedSelaraAiProduct(product_key)
    if price_stars is None or price_stars <= 0:
        price_env = "SELARA_PERSONAL_PRICE_STARS" if spec.scope == PRODUCT_SCOPE_USER else "SELARA_AI_PRICE_STARS"
        raise SelaraAiProductUnavailable(f"{price_env} must be set to a positive integer")
    resolved_duration = spec.duration if spec.duration is not None else duration
    if resolved_duration is None or resolved_duration <= timedelta(0):
        raise SelaraAiProductUnavailable("SELARA_PERSONAL_DURATION_DAYS must be a positive number of days")
    duration_label = f"{int(resolved_duration.total_seconds() // 86_400)} дней"
    if spec.scope == PRODUCT_SCOPE_USER:
        title = f"Selara Personal на {duration_label}"
        description = f"Личный доступ к Selara AI для вашего аккаунта на {duration_label}."
    else:
        title = f"Selara AI на {duration_label}"
        description = f"Доступ к AI-функциям Selara для выбранного чата на {duration_label}."
    return SelaraAiProduct(
        key=spec.key,
        title=title,
        description=description,
        price_stars=price_stars,
        currency=SELARA_AI_CURRENCY,
        duration=resolved_duration,
        scope=spec.scope,
        terms_version=spec.terms_version,
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
