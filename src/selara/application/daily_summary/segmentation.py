from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

DEFAULT_GAP_MINUTES = 25
DEFAULT_MAX_ESTIMATED_TOKENS = 6000
DEFAULT_MAX_MESSAGES = 400
DEFAULT_OVERLAP_MESSAGES = 15


@dataclass(frozen=True)
class SegmentableMessage:
    message_id: int
    sent_at: datetime
    estimated_tokens: int


@dataclass(frozen=True)
class Segment:
    messages: tuple[SegmentableMessage, ...]
    # ids at the head of `messages` that are duplicated from the tail of the previous
    # segment for context only -- not new content, must not be double-counted downstream.
    overlap_message_ids: tuple[int, ...]

    @property
    def start_message_id(self) -> int:
        return self.messages[0].message_id

    @property
    def end_message_id(self) -> int:
        return self.messages[-1].message_id


def segment_messages(
    messages: list[SegmentableMessage],
    *,
    gap_minutes: int = DEFAULT_GAP_MINUTES,
    max_estimated_tokens: int = DEFAULT_MAX_ESTIMATED_TOKENS,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    overlap_messages: int = DEFAULT_OVERLAP_MESSAGES,
) -> list[Segment]:
    """Split a chat's messages into logical "conversations".

    A segment boundary happens either because of a real pause between messages
    (>= gap_minutes) or because a single uninterrupted conversation grew past the
    token/message budget. A pause boundary is a clean cut with no overlap. A
    budget-forced boundary carries `overlap_messages` messages duplicated from the
    tail of the previous segment, purely as context for the LLM -- overlap never
    counts against either limit and must be excluded from message-level stats by
    downstream consumers keying off `overlap_message_ids`.
    """
    if not messages:
        return []

    ordered = sorted(messages, key=lambda item: (item.sent_at, item.message_id))
    gap_delta = timedelta(minutes=gap_minutes)

    segments: list[Segment] = []
    previous_full: tuple[SegmentableMessage, ...] | None = None
    previous_split_forced = False
    index = 0
    total = len(ordered)

    while index < total:
        overlap: tuple[SegmentableMessage, ...] = ()
        if previous_split_forced and previous_full and overlap_messages > 0:
            overlap = previous_full[-overlap_messages:]

        new_messages: list[SegmentableMessage] = []
        cumulative_tokens = 0
        split_forced = False

        while index < total:
            candidate = ordered[index]

            if new_messages and candidate.sent_at - new_messages[-1].sent_at >= gap_delta:
                break

            if len(new_messages) >= max_messages:
                split_forced = True
                break

            if new_messages and cumulative_tokens + candidate.estimated_tokens > max_estimated_tokens:
                split_forced = True
                break

            new_messages.append(candidate)
            cumulative_tokens += candidate.estimated_tokens
            index += 1

        full_messages = overlap + tuple(new_messages)
        segments.append(
            Segment(
                messages=full_messages,
                overlap_message_ids=tuple(item.message_id for item in overlap),
            )
        )

        previous_full = full_messages
        previous_split_forced = split_forced

    return segments
