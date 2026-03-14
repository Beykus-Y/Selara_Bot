from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class CardRarity(StrEnum):
    common = "common"
    rare = "rare"
    epic = "epic"
    legendary = "legendary"


RARITY_LABELS: dict[CardRarity, str] = {
    CardRarity.common: "⬜ Обычная",
    CardRarity.rare: "🟦 Редкая",
    CardRarity.epic: "🟪 Эпическая",
    CardRarity.legendary: "🟨 Легендарная",
}


@dataclass(slots=True, frozen=True)
class GachaCard:
    code: str
    banner: str
    name: str
    rarity: CardRarity
    points: int
    primogems: int
    adventure_xp: int
    image_url: str
    weight: int = 1


@dataclass(slots=True, frozen=True)
class PlayerState:
    user_id: int
    username: str | None
    adventure_rank: int
    adventure_xp: int
    total_points: int
    total_primogems: int
    next_pull_at: datetime | None


@dataclass(slots=True, frozen=True)
class PullResult:
    status: str
    message: str
    card: GachaCard | None
    player: PlayerState
    cooldown_until: datetime
    seconds_remaining: int
    is_new: bool = False
    copies_owned: int = 0
    adventure_xp_gained: int = 0


def xp_for_rank(rank: int) -> int:
    normalized_rank = max(1, rank)
    return 300 + (normalized_rank - 1) * 150


def resolve_rank(total_xp: int) -> tuple[int, int, int]:
    rank = 1
    remaining = max(0, total_xp)
    current_requirement = xp_for_rank(rank)
    while remaining >= current_requirement:
        remaining -= current_requirement
        rank += 1
        current_requirement = xp_for_rank(rank)
    return rank, remaining, current_requirement
