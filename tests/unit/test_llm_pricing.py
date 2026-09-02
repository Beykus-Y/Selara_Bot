from __future__ import annotations

from selara.infrastructure.llm.pricing import estimate_llm_cost_usd, estimate_stt_cost_usd


def test_estimate_llm_cost_known_model() -> None:
    cost = estimate_llm_cost_usd(model="gpt-4o-mini", prompt_tokens=1000, completion_tokens=1000)
    assert cost == round(0.00015 + 0.0006, 6)


def test_estimate_llm_cost_unknown_model_is_zero_not_an_error() -> None:
    cost = estimate_llm_cost_usd(model="some-future-model", prompt_tokens=1000, completion_tokens=1000)
    assert cost == 0.0


def test_estimate_llm_cost_handles_missing_token_counts() -> None:
    cost = estimate_llm_cost_usd(model="gpt-4o-mini", prompt_tokens=None, completion_tokens=None)
    assert cost == 0.0


def test_estimate_stt_cost_scales_with_minutes() -> None:
    cost = estimate_stt_cost_usd(audio_seconds=120)
    assert cost == round(2 * 0.006, 6)


def test_estimate_stt_cost_handles_missing_duration() -> None:
    assert estimate_stt_cost_usd(audio_seconds=None) == 0.0
