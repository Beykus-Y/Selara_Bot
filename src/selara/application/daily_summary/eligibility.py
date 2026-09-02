from __future__ import annotations

from dataclasses import dataclass

from selara.core.chat_settings import ChatSettings


@dataclass(frozen=True)
class DailySummaryEligibility:
    eligible: bool
    reason: str


def evaluate_daily_summary_eligibility(
    *,
    settings: ChatSettings,
    message_count_in_window: int,
    already_run_today: bool,
) -> DailySummaryEligibility:
    """Pure gate check for whether a chat may get a daily summary run right now.

    Gate order matters: it defines which single reason is reported when several
    gates would fail at once, so callers/tests get a deterministic answer.
    """
    if not settings.daily_summary_enabled:
        return DailySummaryEligibility(False, "disabled")

    if not settings.save_message:
        return DailySummaryEligibility(False, "save_message_disabled")

    # Gate on the actually-active write lock, not settings.antiraid_enabled: that field
    # is just "the antiraid feature is turned on", and gating on it would mean an admin
    # who enables raid protection permanently loses daily summaries.
    if settings.chat_write_locked:
        return DailySummaryEligibility(False, "chat_write_locked")

    if already_run_today:
        return DailySummaryEligibility(False, "already_run_today")

    if message_count_in_window < settings.daily_summary_min_messages:
        return DailySummaryEligibility(False, "not_enough_messages")

    return DailySummaryEligibility(True, "eligible")
