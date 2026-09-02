from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Hard caps for the 4 read-only tools the analyst stage (LLM #3) may call. These are
# backend-enforced ceilings, not suggestions the model can negotiate past -- the model
# says what it wants, Selara clamps it.
GET_MESSAGE_CONTEXT_MAX_ROWS = 40
GET_REPLY_THREAD_MAX_ROWS = 50
SEARCH_MESSAGES_MAX_ROWS = 50
GET_ACTIVITY_STATS_MAX_ROWS = 1  # a single aggregate row, not a row-limited listing


class ToolScopeError(Exception):
    """Raised when a tool call tries to escape the chat/window it was scoped to."""


@dataclass(frozen=True)
class ToolScope:
    chat_id: int
    window_from: datetime
    window_to: datetime


def enforce_chat_scope(scope: ToolScope, *, requested_chat_id: int) -> None:
    """Reject any tool call that isn't for the chat this summary run is scoped to.

    The 4 analyst tools never take a caller-supplied chat_id as truth -- this exists
    so a compromised/confused prompt can't be used to pull data from another chat.
    """
    if requested_chat_id != scope.chat_id:
        raise ToolScopeError(f"tool call requested chat_id={requested_chat_id}, run is scoped to {scope.chat_id}")


def clamp_row_limit(requested: int | None, *, max_rows: int) -> int:
    """Clamp a model-requested row limit into [1, max_rows], defaulting to max_rows."""
    if requested is None:
        return max_rows
    return max(1, min(requested, max_rows))


def clamp_window_to_scope(
    *,
    scope: ToolScope,
    requested_from: datetime | None,
    requested_to: datetime | None,
) -> tuple[datetime, datetime]:
    """Clamp a model-requested sub-window into the run's own [window_from, window_to).

    A tool call is never allowed to read data from outside the day being
    summarized, no matter what bounds it asks for -- missing bounds default to
    the full run window, out-of-range bounds are pulled back in, and a
    requested_from past requested_to collapses to an empty (zero-width) window
    rather than silently swapping the two.
    """
    effective_from = requested_from if requested_from is not None else scope.window_from
    effective_to = requested_to if requested_to is not None else scope.window_to

    effective_from = max(effective_from, scope.window_from)
    effective_to = min(effective_to, scope.window_to)

    if effective_from > effective_to:
        effective_from = effective_to

    return effective_from, effective_to
