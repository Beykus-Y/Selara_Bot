from __future__ import annotations

from datetime import datetime, timedelta, timezone

from selara.application.daily_summary.segmentation import SegmentableMessage, segment_messages


def _msg(message_id: int, minute: int, *, tokens: int = 5) -> SegmentableMessage:
    return SegmentableMessage(
        message_id=message_id,
        sent_at=datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute),
        estimated_tokens=tokens,
    )


def test_segment_messages_empty_input_returns_no_segments() -> None:
    assert segment_messages([]) == []


def test_segment_messages_single_continuous_stream_stays_one_segment() -> None:
    messages = [_msg(i, i) for i in range(1, 21)]

    segments = segment_messages(messages, gap_minutes=25, max_estimated_tokens=10_000, max_messages=400)

    assert len(segments) == 1
    assert segments[0].start_message_id == 1
    assert segments[0].end_message_id == 20
    assert [m.message_id for m in segments[0].messages] == list(range(1, 21))


def test_segment_messages_splits_on_long_pause() -> None:
    messages = [_msg(1, 0), _msg(2, 5), _msg(3, 5 + 40), _msg(4, 5 + 40 + 3)]

    segments = segment_messages(messages, gap_minutes=25, max_estimated_tokens=10_000, max_messages=400)

    assert len(segments) == 2
    assert [m.message_id for m in segments[0].messages] == [1, 2]
    assert [m.message_id for m in segments[1].messages] == [3, 4]
    # a pause-based split carries no artificial overlap
    assert segments[1].overlap_message_ids == ()


def test_segment_messages_splits_on_token_budget_with_overlap() -> None:
    # 30 messages, no pauses, each ~100 tokens -> budget of 250 forces multiple splits
    messages = [_msg(i, i, tokens=100) for i in range(1, 31)]

    segments = segment_messages(
        messages,
        gap_minutes=25,
        max_estimated_tokens=250,
        max_messages=400,
        overlap_messages=2,
    )

    assert len(segments) > 1
    # a forced (non-pause) split carries the configured overlap from the tail of the
    # previous segment, duplicated at the head of the next one -- so no theme is cut
    # exactly in half with zero shared context.
    for previous, current in zip(segments, segments[1:]):
        expected_overlap = tuple(m.message_id for m in previous.messages[-2:])
        assert current.overlap_message_ids == expected_overlap
        assert [m.message_id for m in current.messages[: len(expected_overlap)]] == list(expected_overlap)


def test_segment_messages_splits_on_max_messages_for_short_texts() -> None:
    # many one-word messages: token budget never trips, max_messages is the real limit.
    # max_messages/token budget cap only the NEW content of a segment -- overlap is
    # bonus context on top, it never itself counts against either limit.
    messages = [_msg(i, i, tokens=1) for i in range(1, 11)]

    segments = segment_messages(
        messages,
        gap_minutes=25,
        max_estimated_tokens=10_000,
        max_messages=4,
        overlap_messages=1,
    )

    assert len(segments) == 3
    assert [m.message_id for m in segments[0].messages] == [1, 2, 3, 4]
    assert segments[0].overlap_message_ids == ()
    # overlap head (last message of the previous segment) plus fresh new content
    assert [m.message_id for m in segments[1].messages] == [4, 5, 6, 7, 8]
    assert segments[1].overlap_message_ids == (4,)
    assert [m.message_id for m in segments[2].messages] == [8, 9, 10]
    assert segments[2].overlap_message_ids == (8,)
