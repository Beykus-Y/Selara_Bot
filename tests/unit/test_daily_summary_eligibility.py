from __future__ import annotations

from dataclasses import replace

import pytest

from selara.application.daily_summary.eligibility import evaluate_daily_summary_eligibility
from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
        }
    )


def _enabled_chat_settings(**overrides):
    base = replace(
        default_chat_settings(_settings()),
        daily_summary_enabled=True,
        save_message=True,
        daily_summary_min_messages=50,
    )
    return replace(base, **overrides)


def test_eligible_when_all_gates_pass() -> None:
    result = evaluate_daily_summary_eligibility(
        settings=_enabled_chat_settings(),
        message_count_in_window=120,
        already_run_today=False,
    )

    assert result.eligible is True
    assert result.reason == "eligible"


def test_not_eligible_when_feature_disabled() -> None:
    result = evaluate_daily_summary_eligibility(
        settings=_enabled_chat_settings(daily_summary_enabled=False),
        message_count_in_window=120,
        already_run_today=False,
    )

    assert result.eligible is False
    assert result.reason == "disabled"


def test_not_eligible_when_save_message_disabled() -> None:
    # the pipeline has no data to work with at all without message archiving
    result = evaluate_daily_summary_eligibility(
        settings=_enabled_chat_settings(save_message=False),
        message_count_in_window=120,
        already_run_today=False,
    )

    assert result.eligible is False
    assert result.reason == "save_message_disabled"


def test_not_eligible_when_chat_write_locked() -> None:
    # gates on the actually-active lock, not the antiraid feature toggle itself --
    # an admin enabling antiraid protection must not permanently lose daily summaries
    result = evaluate_daily_summary_eligibility(
        settings=_enabled_chat_settings(chat_write_locked=True, antiraid_enabled=False),
        message_count_in_window=120,
        already_run_today=False,
    )

    assert result.eligible is False
    assert result.reason == "chat_write_locked"


def test_eligible_when_antiraid_feature_enabled_but_not_currently_locked() -> None:
    # antiraid_enabled=True alone must never block the summary -- only an active lock does
    result = evaluate_daily_summary_eligibility(
        settings=_enabled_chat_settings(antiraid_enabled=True, chat_write_locked=False),
        message_count_in_window=120,
        already_run_today=False,
    )

    assert result.eligible is True


def test_not_eligible_when_already_run_today() -> None:
    result = evaluate_daily_summary_eligibility(
        settings=_enabled_chat_settings(),
        message_count_in_window=120,
        already_run_today=True,
    )

    assert result.eligible is False
    assert result.reason == "already_run_today"


def test_not_eligible_below_message_threshold() -> None:
    result = evaluate_daily_summary_eligibility(
        settings=_enabled_chat_settings(daily_summary_min_messages=50),
        message_count_in_window=49,
        already_run_today=False,
    )

    assert result.eligible is False
    assert result.reason == "not_enough_messages"


def test_eligible_exactly_at_message_threshold() -> None:
    result = evaluate_daily_summary_eligibility(
        settings=_enabled_chat_settings(daily_summary_min_messages=50),
        message_count_in_window=50,
        already_run_today=False,
    )

    assert result.eligible is True


@pytest.mark.parametrize("gate", ["disabled", "save_message_disabled", "chat_write_locked", "already_run_today"])
def test_gate_precedence_reports_first_blocking_reason(gate: str) -> None:
    # several gates can be true at once (e.g. feature disabled AND below threshold);
    # the reported reason must be deterministic, not whichever check happened to run last.
    kwargs = {
        "disabled": {"daily_summary_enabled": False},
        "save_message_disabled": {"save_message": False},
        "chat_write_locked": {"chat_write_locked": True},
        "already_run_today": {},
    }[gate]
    already_run_today = gate == "already_run_today"

    result = evaluate_daily_summary_eligibility(
        settings=_enabled_chat_settings(**kwargs),
        message_count_in_window=0,  # also fails the threshold gate
        already_run_today=already_run_today,
    )

    assert result.eligible is False
    assert result.reason == gate
