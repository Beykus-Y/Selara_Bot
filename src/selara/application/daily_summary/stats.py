from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class TopicCardRange:
    start_at: datetime
    end_at: datetime


def compute_episode_count(cards: list[TopicCardRange], *, gap_minutes: int) -> int:
    """Count real, time-separated episodes among the cards a merged theme absorbed.

    This runs AFTER LLM #2 (merge), on the set of original per-segment cards that
    got grouped into one final theme -- never before it, since only the merge stage
    can tell "XHTTP in the morning" and "DPI in the evening" are the same topic.

    Two cards whose time ranges overlap or sit within `gap_minutes` of each other
    are the same episode: this is exactly what a forced segmentation split with
    message-id overlap produces (the topic never actually stopped), so it must not
    be miscounted as the topic "coming back". Only a real silence longer than the
    gap threshold between two cards counts as a new episode.
    """
    if not cards:
        return 0

    ordered = sorted(cards, key=lambda card: card.start_at)
    gap = timedelta(minutes=gap_minutes)

    episodes = 1
    current_end = ordered[0].end_at
    for card in ordered[1:]:
        if card.start_at <= current_end + gap:
            current_end = max(current_end, card.end_at)
        else:
            episodes += 1
            current_end = card.end_at

    return episodes
