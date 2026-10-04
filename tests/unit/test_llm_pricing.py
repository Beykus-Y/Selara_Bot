from __future__ import annotations

from selara.infrastructure.llm.pricing import estimate_llm_cost_usd, estimate_stt_cost_usd


def test_estimate_llm_cost_known_model() -> None:
    cost = estimate_llm_cost_usd(model="gpt-4o-mini", prompt_tokens=1000, completion_tokens=1000)
    assert str(cost) == "0.000750000"


def test_estimate_llm_cost_resolves_explicit_provider_snapshot() -> None:
    cost = estimate_llm_cost_usd(
        model="gpt-4o-mini-2024-07-18", prompt_tokens=1000, completion_tokens=1000
    )
    assert str(cost) == "0.000750000"


@pytest.mark.parametrize("snapshot", ["gpt-4o-2024-08-06", "gpt-4o-2024-11-20"])
def test_estimate_llm_cost_resolves_known_gpt_4o_snapshots(snapshot: str) -> None:
    cost = estimate_llm_cost_usd(model=snapshot, prompt_tokens=1000, completion_tokens=1000)
    assert str(cost) == "0.012500000"


def test_estimate_llm_cost_unknown_model_is_unknown_not_an_error() -> None:
    cost = estimate_llm_cost_usd(model="some-future-model", prompt_tokens=1000, completion_tokens=1000)
    assert cost is None


def test_estimate_llm_cost_handles_missing_token_counts() -> None:
    cost = estimate_llm_cost_usd(model="gpt-4o-mini", prompt_tokens=None, completion_tokens=None)
    assert cost is None


def test_estimate_stt_cost_scales_with_minutes() -> None:
    cost = estimate_stt_cost_usd(audio_seconds=120)
    assert cost == round(2 * 0.006, 6)


def test_estimate_stt_cost_handles_missing_duration() -> None:
    assert estimate_stt_cost_usd(audio_seconds=None) == 0.0
