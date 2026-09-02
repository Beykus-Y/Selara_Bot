"""Static per-model pricing for daily summary cost accounting (docs/DAILY_SUMMARY_TODO.md).

Deliberately a plain module-level dict, not a DB table or a Settings field --
prices change rarely and this is meant to be edited by hand when they do (per the
approved plan). An unrecognized model costs 0 rather than raising, so a pricing gap
never breaks the pipeline -- it just under-reports cost for that model until this
table is updated.
"""

from __future__ import annotations

# model_name -> (price per 1K prompt tokens USD, price per 1K completion tokens USD)
MODEL_PRICING_USD_PER_1K_TOKENS: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
}

_UNKNOWN_MODEL_PRICING = (0.0, 0.0)

# USD per minute of transcribed audio (Whisper-style STT pricing).
STT_PRICE_USD_PER_MINUTE = 0.006


def estimate_llm_cost_usd(*, model: str, prompt_tokens: int | None, completion_tokens: int | None) -> float:
    prompt_price, completion_price = MODEL_PRICING_USD_PER_1K_TOKENS.get(model, _UNKNOWN_MODEL_PRICING)
    prompt_cost = (prompt_tokens or 0) / 1000 * prompt_price
    completion_cost = (completion_tokens or 0) / 1000 * completion_price
    return round(prompt_cost + completion_cost, 6)


def estimate_stt_cost_usd(*, audio_seconds: float | None) -> float:
    minutes = (audio_seconds or 0) / 60
    return round(minutes * STT_PRICE_USD_PER_MINUTE, 6)
