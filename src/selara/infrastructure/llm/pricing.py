"""Static provider pricing registry. Missing prices remain explicitly unknown."""

from __future__ import annotations

from decimal import Decimal

# model_name -> (price per 1K prompt tokens USD, price per 1K completion tokens USD)
MODEL_PRICING_USD_PER_1K_TOKENS: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-4o-mini": (Decimal("0.00015"), Decimal("0.0006")),
    "gpt-4o": (Decimal("0.0025"), Decimal("0.01")),
}

# Exact provider snapshot identifiers whose token rates follow a registered
# pricing family. Keep this explicit so unknown future variants remain unknown.
MODEL_PRICING_SNAPSHOT_FAMILIES: dict[str, str] = {
    "gpt-4o-mini-2024-07-18": "gpt-4o-mini",
    "gpt-4o-2024-08-06": "gpt-4o",
    "gpt-4o-2024-11-20": "gpt-4o",
}

# USD per minute of transcribed audio (Whisper-style STT pricing).
STT_PRICE_USD_PER_MINUTE = 0.006


def estimate_llm_cost_usd(
    *, model: str, prompt_tokens: int | None, completion_tokens: int | None
) -> Decimal | None:
    """Return a precise known estimate, or ``None`` when this model is unpriced."""
    pricing_model = MODEL_PRICING_SNAPSHOT_FAMILIES.get(model, model)
    pricing = MODEL_PRICING_USD_PER_1K_TOKENS.get(pricing_model)
    if pricing is None or prompt_tokens is None or completion_tokens is None:
        return None
    prompt_price, completion_price = pricing
    return (
        Decimal(prompt_tokens) * prompt_price / Decimal(1000)
        + Decimal(completion_tokens) * completion_price / Decimal(1000)
    ).quantize(Decimal("0.000000001"))


def estimate_stt_cost_usd(*, audio_seconds: float | None) -> float:
    minutes = (audio_seconds or 0) / 60
    return round(minutes * STT_PRICE_USD_PER_MINUTE, 6)
