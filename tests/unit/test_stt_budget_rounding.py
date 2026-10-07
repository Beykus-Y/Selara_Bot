import pytest

from selara.infrastructure.db.stt_budget_repository import reservation_milliseconds


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf"), float("-inf"), 31])
def test_invalid_or_over_cap_audio_cannot_bypass_admission(duration):
    assert reservation_milliseconds(duration, 30) is None


@pytest.mark.parametrize("duration,milliseconds", [(25, 25000), (25.0001, 25001), (0.0001, 1), (4.999, 4999), (30, 30000)])
def test_audio_is_charged_with_conservative_millisecond_rounding(duration, milliseconds):
    assert reservation_milliseconds(duration, 30) == milliseconds


def test_disabled_budget_never_admits_positive_audio():
    assert reservation_milliseconds(1, 0) is None
