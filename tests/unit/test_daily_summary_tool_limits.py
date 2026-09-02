from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from selara.application.daily_summary.tool_limits import (
    GET_ACTIVITY_STATS_MAX_ROWS,
    GET_MESSAGE_CONTEXT_MAX_ROWS,
    GET_REPLY_THREAD_MAX_ROWS,
    SEARCH_MESSAGES_MAX_ROWS,
    ToolScope,
    ToolScopeError,
    clamp_row_limit,
    clamp_window_to_scope,
    enforce_chat_scope,
)

_WINDOW_FROM = datetime(2026, 9, 2, 7, 0, tzinfo=timezone.utc)
_WINDOW_TO = datetime(2026, 9, 3, 7, 0, tzinfo=timezone.utc)


def _scope() -> ToolScope:
    return ToolScope(chat_id=-100123, window_from=_WINDOW_FROM, window_to=_WINDOW_TO)


def test_enforce_chat_scope_accepts_matching_chat_id() -> None:
    enforce_chat_scope(_scope(), requested_chat_id=-100123)  # must not raise


def test_enforce_chat_scope_rejects_foreign_chat_id() -> None:
    # a tool call must never be able to read another chat's messages, even if the
    # model "asks nicely" for a different chat_id than the one this run is scoped to
    with pytest.raises(ToolScopeError):
        enforce_chat_scope(_scope(), requested_chat_id=-999999)


@pytest.mark.parametrize(
    ("max_rows", "requested", "expected"),
    [
        (GET_MESSAGE_CONTEXT_MAX_ROWS, None, GET_MESSAGE_CONTEXT_MAX_ROWS),
        (GET_MESSAGE_CONTEXT_MAX_ROWS, 10, 10),
        (GET_MESSAGE_CONTEXT_MAX_ROWS, 10_000, GET_MESSAGE_CONTEXT_MAX_ROWS),
        (GET_MESSAGE_CONTEXT_MAX_ROWS, 0, 1),
        (GET_MESSAGE_CONTEXT_MAX_ROWS, -5, 1),
    ],
)
def test_clamp_row_limit_stays_within_bounds(max_rows: int, requested: int | None, expected: int) -> None:
    assert clamp_row_limit(requested, max_rows=max_rows) == expected


def test_hard_caps_match_the_plan() -> None:
    # these are the exact caps agreed for v1 -- a regression here silently reopens
    # the door to an unbounded/expensive tool call
    assert GET_MESSAGE_CONTEXT_MAX_ROWS == 40
    assert GET_REPLY_THREAD_MAX_ROWS == 50
    assert SEARCH_MESSAGES_MAX_ROWS == 50
    assert GET_ACTIVITY_STATS_MAX_ROWS >= 1


def test_clamp_window_defaults_to_full_scope_when_unset() -> None:
    result = clamp_window_to_scope(scope=_scope(), requested_from=None, requested_to=None)
    assert result == (_WINDOW_FROM, _WINDOW_TO)


def test_clamp_window_pulls_in_out_of_range_bounds() -> None:
    # a tool call must never be able to read data from outside the day being
    # summarized, no matter what bounds it asks for
    requested_from = _WINDOW_FROM - timedelta(days=5)
    requested_to = _WINDOW_TO + timedelta(days=5)

    result = clamp_window_to_scope(scope=_scope(), requested_from=requested_from, requested_to=requested_to)

    assert result == (_WINDOW_FROM, _WINDOW_TO)


def test_clamp_window_keeps_a_valid_sub_window() -> None:
    requested_from = _WINDOW_FROM + timedelta(hours=1)
    requested_to = _WINDOW_TO - timedelta(hours=1)

    result = clamp_window_to_scope(scope=_scope(), requested_from=requested_from, requested_to=requested_to)

    assert result == (requested_from, requested_to)


def test_clamp_window_collapses_when_from_is_after_to() -> None:
    # rather than silently swapping an inverted range, collapse to zero-width
    requested_from = _WINDOW_TO
    requested_to = _WINDOW_FROM

    result = clamp_window_to_scope(scope=_scope(), requested_from=requested_from, requested_to=requested_to)

    assert result[0] == result[1]
