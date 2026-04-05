from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class CardRarity(StrEnum):
    common = "common"
    rare = "rare"
    epic = "epic"
    legendary = "legendary"
    mythic = "mythic"


RARITY_LABELS: dict[CardRarity, str] = {
    CardRarity.common: "⬜ Обычная",
    CardRarity.rare: "🟦 Редкая",
    CardRarity.epic: "🟪 Эпическая",
    CardRarity.legendary: "🟨 Легендарная",
    CardRarity.mythic: "🟥 Мифическая",
}

RARITY_SUMMARY_ORDER: tuple[CardRarity, ...] = (
    CardRarity.mythic,
    CardRarity.legendary,
    CardRarity.epic,
)

RARITY_SUMMARY_LABELS: dict[CardRarity, str] = {
    CardRarity.legendary: "Легендарных карт",
    CardRarity.epic: "Эпических карт",
    CardRarity.mythic: "Мифических карт",
}

REGION_LABELS: dict[str, str] = {
    "mondstadt": "Мондштадт",
    "liyue": "Ли Юэ",
    "inazuma": "Инадзума",
    "sumeru": "Сумеру",
    "fontaine": "Фонтейн",
    "natlan": "Натлан",
    "nod_krai": "Нод-Край",
    "snezhnaya": "Снежная",
    "khaenriah": "Каэнри'ах",
    "unknown": "Неизвестно",
}

ELEMENT_LABELS: dict[str, str] = {
    "hydro": "Гидро",
    "electro": "Электро",
    "pyro": "Пиро",
    "cryo": "Крио",
    "anemo": "Анемо",
    "dendro": "Дендро",
    "geo": "Гео",
    "unknown": "Неизвестно",
}

ELEMENT_ICONS: dict[str, str] = {
    "hydro": "💧",
    "electro": "⚡",
    "pyro": "🔥",
    "cryo": "❄️",
    "anemo": "🌪️",
    "dendro": "🌿",
    "geo": "🪨",
    "unknown": "❔",
}


def format_region_label(region_code: str | None) -> str:
    normalized = (region_code or "").strip().lower()
    if not normalized:
        return "Неизвестно"
    return REGION_LABELS.get(normalized, normalized.replace("_", " ").title())


def format_element_label(element_code: str | None) -> str:
    normalized = (element_code or "").strip().lower()
    if not normalized:
        return "Неизвестно"
    return ELEMENT_LABELS.get(normalized, normalized.replace("_", " ").title())


def format_element_icon(element_code: str | None) -> str:
    normalized = (element_code or "").strip().lower() or "unknown"
    return ELEMENT_ICONS.get(normalized, "❔")


def format_rarity_icon(rarity: CardRarity) -> str:
    return RARITY_LABELS[rarity].split(" ", 1)[0]


def format_rarity_summary_label(rarity: CardRarity) -> str:
    return RARITY_SUMMARY_LABELS[rarity]


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
    region_code: str | None = None
    element_code: str | None = None
    weight: float = 1


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
    pull_id: int | None = None
    sell_offer: "SellOffer" | None = None


@dataclass(slots=True, frozen=True)
class SellOffer:
    sale_price: int


@dataclass(slots=True, frozen=True)
class SellResult:
    status: str
    message: str
    player: PlayerState
    pull_id: int
    banner: str
    sale_price: int
    sold_at: datetime


@dataclass(slots=True, frozen=True)
class CurrencyGrantResult:
    status: str
    message: str
    player: PlayerState
    banner: str
    amount: int


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
