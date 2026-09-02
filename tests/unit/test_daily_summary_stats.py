from __future__ import annotations

from datetime import datetime, timedelta, timezone

from selara.application.daily_summary.stats import TopicCardRange, compute_episode_count

_BASE = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _range(start_minute: int, end_minute: int) -> TopicCardRange:
    return TopicCardRange(start_at=_BASE + timedelta(minutes=start_minute), end_at=_BASE + timedelta(minutes=end_minute))


def test_no_cards_means_no_episodes() -> None:
    assert compute_episode_count([], gap_minutes=25) == 0


def test_single_card_is_one_episode() -> None:
    assert compute_episode_count([_range(0, 30)], gap_minutes=25) == 1


def test_overlapping_cards_from_segmentation_overlap_collapse_to_one_episode() -> None:
    # this is exactly the case a forced segment split with message-id overlap produces:
    # two cards whose time ranges overlap because they share the same overlap messages --
    # this must NOT be counted as the topic "coming back", it's the same conversation.
    cards = [_range(0, 60), _range(55, 120)]

    assert compute_episode_count(cards, gap_minutes=25) == 1


def test_cards_close_together_within_gap_are_one_episode() -> None:
    cards = [_range(0, 30), _range(40, 70)]  # 10 minute gap, well under the 25 minute threshold

    assert compute_episode_count(cards, gap_minutes=25) == 1


def test_cards_separated_by_a_real_gap_are_distinct_episodes() -> None:
    # discussed in the morning, silent for hours, came back in the evening
    cards = [_range(0, 30), _range(300, 330)]

    assert compute_episode_count(cards, gap_minutes=25) == 2


def test_three_cards_two_real_gaps_gives_three_episodes() -> None:
    cards = [_range(0, 10), _range(200, 210), _range(500, 510)]

    assert compute_episode_count(cards, gap_minutes=25) == 3


def test_unordered_input_is_sorted_before_counting() -> None:
    cards = [_range(500, 510), _range(0, 10), _range(200, 210)]

    assert compute_episode_count(cards, gap_minutes=25) == 3
