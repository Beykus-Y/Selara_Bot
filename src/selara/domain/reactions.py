from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

TelegramReactionType = Literal["emoji", "custom_emoji", "paid"]


def normalize_telegram_reaction_emoji(value: str) -> str:
    """Return the stable Bot API identity for visually equivalent standard emoji."""
    normalized = unicodedata.normalize("NFC", str(value or "").strip())
    return normalized.replace("\ufe0e", "").replace("\ufe0f", "")


@dataclass(frozen=True, slots=True)
class TelegramReactionValue:
    reaction_type: TelegramReactionType
    value: str
    display: str


@dataclass(frozen=True, slots=True)
class TelegramReactionTotal:
    reaction: TelegramReactionValue
    count: int


def canonicalize_telegram_reaction(
    reaction: TelegramReactionValue,
) -> TelegramReactionValue | None:
    reaction_type = reaction.reaction_type
    if reaction_type == "emoji":
        value = normalize_telegram_reaction_emoji(reaction.value or reaction.display)
        display = str(reaction.display or reaction.value).strip()
        return TelegramReactionValue("emoji", value, display) if value and display else None
    if reaction_type == "custom_emoji":
        value = str(reaction.value or "").strip()
        if not value or len(value) > 128:
            return None
        return TelegramReactionValue("custom_emoji", value, str(reaction.display or "✨")[:64])
    if reaction_type == "paid":
        return TelegramReactionValue("paid", "paid", str(reaction.display or "⭐")[:64])
    return None
