"""Deterministic AI-pet mechanics: no I/O, no LLM.

Parameters, cooldowns, affinity and XP are computed here so the DB layer only
locks rows and stores results, and every rule is unit-testable in isolation.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Literal, Mapping

STAT_MIN = 0
STAT_MAX = 100
AFFINITY_MIN = -100
AFFINITY_MAX = 100

NAME_MAX_LEN = 32
SPECIES_CUSTOM_MAX_LEN = 40
MAX_TRAITS = 3
MAX_LEVEL = 50
TRAVEL_UNLOCK_LEVEL = 10

# Lazy tick: parameters drift per whole elapsed hour, applied on the next access.
TICK_SATIETY_PER_HOUR = 4
TICK_MOOD_PER_HOUR = 2
TICK_HUNGRY_MOOD_PER_HOUR = 2  # extra mood loss while satiety is below HUNGRY_BELOW
TICK_ENERGY_REGEN_PER_HOUR = 8
HUNGRY_BELOW = 30
MAX_TICK_HOURS = 24 * 30

# Positive affinity one person can earn with a pet per day; losses are not capped.
AFFINITY_DAILY_GAIN_CAP = 15
# XP from one person per day: full rate, then half rate, then nothing.
XP_FULL_PER_DAY = 20
XP_HALF_PER_DAY = 40

FOOD_FULL_AT = 95
TOY_MIN_ENERGY = 10
TOY_ENERGY_COST = 10

SpeciesKey = Literal["dog", "cat", "spider", "dragon", "human", "custom"]
ActionKey = Literal["pat", "play", "tease", "hurt"]
ItemKind = Literal["food", "toy"]


@dataclass(frozen=True, slots=True)
class Species:
    key: str
    title: str
    emoji: str
    sound: str


SPECIES: dict[str, Species] = {
    "dog": Species("dog", "собака", "🐶", "Гав!"),
    "cat": Species("cat", "кошка", "🐱", "Мур!"),
    "spider": Species("spider", "паук", "🕷", "*шуршит лапками*"),
    "dragon": Species("dragon", "дракон", "🐉", "*выпускает облачко дыма*"),
    "human": Species("human", "человек", "🧑", "Хе-хе."),
    "custom": Species("custom", "своё", "✨", "*радостно подпрыгивает*"),
}

SPECIES_ALIASES: dict[str, str] = {
    "собака": "dog",
    "пёс": "dog",
    "пес": "dog",
    "щенок": "dog",
    "кот": "cat",
    "кошка": "cat",
    "котик": "cat",
    "паук": "spider",
    "дракон": "dragon",
    "человек": "human",
}

TRAITS: dict[str, str] = {
    "playful": "игривый",
    "lazy": "ленивый",
    "curious": "любопытный",
    "shy": "застенчивый",
    "brave": "смелый",
    "grumpy": "ворчливый",
    "affectionate": "ласковый",
    "mischievous": "озорной",
    "proud": "гордый",
    "calm": "спокойный",
    "greedy": "прожорливый",
    "loyal": "верный",
}
_TRAIT_BY_TITLE = {title: key for key, title in TRAITS.items()}


@dataclass(frozen=True, slots=True)
class ActionRule:
    key: str
    title: str
    cooldown: timedelta
    mood: int = 0
    satiety: int = 0
    energy: int = 0
    affinity: int = 0
    xp: int = 0
    min_energy: int = 0


ACTIONS: dict[str, ActionRule] = {
    "pat": ActionRule("pat", "погладить", timedelta(minutes=10), mood=6, affinity=2, xp=2),
    "play": ActionRule(
        "play", "поиграть", timedelta(minutes=30), mood=10, satiety=-6, energy=-15, affinity=3, xp=5, min_energy=15
    ),
    "tease": ActionRule("tease", "подразнить", timedelta(minutes=10), mood=-8, affinity=-4),
    "hurt": ActionRule("hurt", "обидеть", timedelta(minutes=60), mood=-20, affinity=-12),
}
ACTION_ALIASES: dict[str, str] = {
    "погладить": "pat",
    "гладить": "pat",
    "поиграть": "play",
    "играть": "play",
    "дразнить": "tease",
    "подразнить": "tease",
    "обидеть": "hurt",
}
ITEM_COOLDOWNS: dict[str, timedelta] = {"food": timedelta(minutes=15), "toy": timedelta(minutes=30)}
# Cosmetics are worn, not used: one item per slot, no effect on the pet's stats.
COSMETIC_SLOTS: dict[str, str] = {"head": "голова", "neck": "шея", "back": "спина"}
# How many of one food or toy a pet can keep in its bag.
BAG_STACK_LIMIT = 20
ITEM_EVENT_TYPES: dict[str, str] = {"food": "feed", "toy": "toy"}

_EFFECT_BOUNDS: dict[str, tuple[int, int]] = {
    "satiety": (-100, 100),
    "mood": (-100, 100),
    "energy": (-100, 100),
    "affinity": (-20, 20),
    "xp": (0, 100),
}

# Letters/digits at both ends; inside also space, hyphen and apostrophe (no underscore).
_NAME_RE = re.compile(r"^[^\W_](?:(?:[^\W_]|[ '\-])*[^\W_])?$", re.UNICODE)
_LINK_MARKERS = ("http", "www.", "t.me", ".ru", ".com", "@", "/", "#")
# Short root list for obviously abusive names; the admin can still put a pet to sleep.
_DENYLIST_ROOTS = ("хуй", "хуе", "пизд", "ебан", "ебат", "ёба", "бляд", "сука", "мудак", "пидор", "шлюх", "fuck", "shit")


class PetValidationError(ValueError):
    """User-facing validation problem; ``str(exc)`` is shown as is."""


@dataclass(frozen=True, slots=True)
class PetStats:
    level: int
    xp: int
    mood: int
    satiety: int
    energy: int
    last_tick_at: datetime


@dataclass(frozen=True, slots=True)
class RelationState:
    affinity: int
    affinity_gained_today: int
    xp_gained_today: int
    gained_day: date | None

    def for_day(self, today: date) -> "RelationState":
        if self.gained_day == today:
            return self
        return replace(self, affinity_gained_today=0, xp_gained_today=0, gained_day=today)


@dataclass(frozen=True, slots=True)
class ItemEffects:
    satiety: int = 0
    mood: int = 0
    energy: int = 0
    affinity: int = 0
    xp: int = 0


@dataclass(frozen=True, slots=True)
class Effect:
    """Raw deltas an action wants to apply before caps and bounds."""

    mood: int = 0
    satiety: int = 0
    energy: int = 0
    affinity: int = 0
    xp: int = 0


@dataclass(frozen=True, slots=True)
class Outcome:
    stats: PetStats
    relation: RelationState
    applied: dict[str, int]
    leveled_up_to: int | None


def clamp(value: int, low: int = STAT_MIN, high: int = STAT_MAX) -> int:
    return max(low, min(high, int(value)))


def normalize_name(value: str) -> str:
    return " ".join((value or "").split()).casefold().replace("ё", "е")


def _check_free_text(value: str, *, field: str, max_len: int) -> str:
    cleaned = " ".join((value or "").split())
    if not cleaned:
        raise PetValidationError(f"{field}: нужно хотя бы одно слово.")
    if len(cleaned) > max_len:
        raise PetValidationError(f"{field}: не длиннее {max_len} символов.")
    lowered = cleaned.casefold()
    if any(marker in lowered for marker in _LINK_MARKERS):
        raise PetValidationError(f"{field}: без ссылок, упоминаний и спецсимволов.")
    if not _NAME_RE.match(cleaned):
        raise PetValidationError(f"{field}: только буквы, цифры, пробел, дефис и апостроф.")
    folded = lowered.replace("ё", "е")
    if any(root in folded or root in lowered for root in _DENYLIST_ROOTS):
        raise PetValidationError(f"{field}: выберите другое слово.")
    return cleaned


def validate_name(value: str) -> str:
    return _check_free_text(value, field="Имя", max_len=NAME_MAX_LEN)


def resolve_species(value: str) -> tuple[str, str | None]:
    """Return ``(species_key, species_custom)`` for a user-entered species word."""
    cleaned = " ".join((value or "").split())
    key = SPECIES_ALIASES.get(cleaned.casefold())
    if key is not None:
        return key, None
    return "custom", _check_free_text(cleaned, field="Вид", max_len=SPECIES_CUSTOM_MAX_LEN)


def parse_traits(value: str) -> list[str]:
    """Parse up to three traits from a comma/space separated list of titles."""
    tokens = [token.strip().casefold() for token in re.split(r"[,\s]+", value or "") if token.strip()]
    if not tokens:
        raise PetValidationError("Перечислите черты через запятую.")
    keys: list[str] = []
    for token in tokens:
        key = _TRAIT_BY_TITLE.get(token) or (token if token in TRAITS else None)
        if key is None:
            raise PetValidationError(f"Неизвестная черта «{token}». Доступны: {', '.join(TRAITS.values())}.")
        if key not in keys:
            keys.append(key)
    if len(keys) > MAX_TRAITS:
        raise PetValidationError(f"Можно выбрать не больше {MAX_TRAITS} черт.")
    return keys


def parse_item_effects(raw: object, *, kind: str) -> ItemEffects | None:
    """Validate catalog effects edited by the owner; ``None`` disables a broken item."""
    if kind == "cosmetic":
        # Cosmetics change nothing but looks; any effects mean a mis-edited row.
        return ItemEffects() if isinstance(raw, Mapping) and not raw else None
    if not isinstance(raw, Mapping) or kind not in ITEM_EVENT_TYPES:
        return None
    values: dict[str, int] = {}
    for key, amount in raw.items():
        bounds = _EFFECT_BOUNDS.get(str(key))
        if bounds is None or isinstance(amount, bool) or not isinstance(amount, int):
            return None
        if not bounds[0] <= amount <= bounds[1]:
            return None
        values[str(key)] = amount
    effects = ItemEffects(**values)
    if kind == "food" and effects.satiety <= 0:
        return None
    if kind == "toy" and effects.mood <= 0:
        return None
    return effects


def xp_to_next_level(level: int) -> int:
    return 50 * max(1, level)


def level_for_xp(xp: int) -> int:
    level = 1
    remaining = max(0, int(xp))
    while level < MAX_LEVEL and remaining >= xp_to_next_level(level):
        remaining -= xp_to_next_level(level)
        level += 1
    return level


def level_progress(xp: int) -> tuple[int, int, int]:
    """Return ``(level, xp_into_level, xp_needed_for_next)``."""
    level = 1
    remaining = max(0, int(xp))
    while level < MAX_LEVEL and remaining >= xp_to_next_level(level):
        remaining -= xp_to_next_level(level)
        level += 1
    return level, remaining, xp_to_next_level(level)


def apply_tick(stats: PetStats, now: datetime) -> PetStats:
    """Apply whole elapsed hours since ``last_tick_at``; the remainder carries over."""
    elapsed = now - stats.last_tick_at
    hours = int(elapsed.total_seconds() // 3600)
    if hours <= 0:
        return stats
    applied_hours = min(hours, MAX_TICK_HOURS)
    satiety = clamp(stats.satiety - TICK_SATIETY_PER_HOUR * applied_hours)
    hungry_hours = 0
    if stats.satiety - TICK_SATIETY_PER_HOUR * applied_hours < HUNGRY_BELOW:
        # Hours spent below the threshold, counted from when satiety crossed it.
        above = max(0, stats.satiety - HUNGRY_BELOW)
        hungry_hours = max(0, applied_hours - above // TICK_SATIETY_PER_HOUR)
    mood = clamp(stats.mood - TICK_MOOD_PER_HOUR * applied_hours - TICK_HUNGRY_MOOD_PER_HOUR * hungry_hours)
    energy = clamp(stats.energy + TICK_ENERGY_REGEN_PER_HOUR * applied_hours)
    return replace(
        stats,
        satiety=satiety,
        mood=mood,
        energy=energy,
        last_tick_at=stats.last_tick_at + timedelta(hours=hours),
    )


def _awarded_xp(raw_points: int) -> int:
    """XP awarded for ``raw_points`` submitted in one day: full rate, then half, then none."""
    raw = max(0, raw_points)
    return min(raw, XP_FULL_PER_DAY) + max(0, min(raw, XP_HALF_PER_DAY) - XP_FULL_PER_DAY) // 2


def apply_effect(
    stats: PetStats,
    relation: RelationState,
    effect: Effect,
    *,
    today: date,
) -> Outcome:
    relation = relation.for_day(today)

    affinity_delta = effect.affinity
    if affinity_delta > 0:
        affinity_delta = min(affinity_delta, max(0, AFFINITY_DAILY_GAIN_CAP - relation.affinity_gained_today))
    new_affinity = clamp(relation.affinity + affinity_delta, AFFINITY_MIN, AFFINITY_MAX)
    affinity_applied = new_affinity - relation.affinity

    # ``xp_gained_today`` stores raw points, so the award stays exact across many small actions.
    raw_after = relation.xp_gained_today + max(0, effect.xp)
    xp_gain = _awarded_xp(raw_after) - _awarded_xp(relation.xp_gained_today)
    new_xp = stats.xp + xp_gain
    old_level = level_for_xp(stats.xp)
    new_level = level_for_xp(new_xp)

    new_stats = replace(
        stats,
        mood=clamp(stats.mood + effect.mood),
        satiety=clamp(stats.satiety + effect.satiety),
        energy=clamp(stats.energy + effect.energy),
        xp=new_xp,
        level=new_level,
    )
    new_relation = replace(
        relation,
        affinity=new_affinity,
        affinity_gained_today=relation.affinity_gained_today + max(0, affinity_applied),
        xp_gained_today=raw_after,
    )
    applied = {
        "mood": new_stats.mood - stats.mood,
        "satiety": new_stats.satiety - stats.satiety,
        "energy": new_stats.energy - stats.energy,
        "affinity": affinity_applied,
        "xp": xp_gain,
    }
    return Outcome(
        stats=new_stats,
        relation=new_relation,
        applied=applied,
        leveled_up_to=new_level if new_level > old_level else None,
    )


def action_effect(action: ActionRule) -> Effect:
    return Effect(
        mood=action.mood,
        satiety=action.satiety,
        energy=action.energy,
        affinity=action.affinity,
        xp=action.xp,
    )


def item_effect(kind: str, effects: ItemEffects) -> Effect:
    energy = effects.energy - (TOY_ENERGY_COST if kind == "toy" else 0)
    return Effect(
        mood=effects.mood,
        satiety=effects.satiety,
        energy=energy,
        affinity=effects.affinity,
        xp=effects.xp,
    )


def action_block_reason(action: ActionRule, stats: PetStats) -> str | None:
    if stats.energy < action.min_energy:
        return "слишком устал(а) — пусть отдохнёт"
    return None


def item_block_reason(kind: str, stats: PetStats) -> str | None:
    if kind == "food" and stats.satiety >= FOOD_FULL_AT:
        return "не голоден(на)"
    if kind == "toy" and stats.energy < TOY_MIN_ENERGY:
        return "слишком устал(а) для игр"
    return None


def cooldown_left(last_at: datetime | None, cooldown: timedelta, now: datetime) -> timedelta | None:
    if last_at is None:
        return None
    left = last_at + cooldown - now
    return left if left.total_seconds() > 0 else None


def format_duration(value: timedelta) -> str:
    total = max(1, int(value.total_seconds()))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин"
    return f"{seconds} сек"


def affinity_label(affinity: int) -> str:
    if affinity >= 60:
        return "обожает"
    if affinity >= 25:
        return "доверяет"
    if affinity > -25:
        return "нейтрален"
    if affinity > -60:
        return "настороже"
    return "злится"


def mood_label(mood: int) -> str:
    if mood >= 80:
        return "счастлив(а)"
    if mood >= 55:
        return "в хорошем настроении"
    if mood >= 30:
        return "скучает"
    return "грустит"


def species_for(species_key: str) -> Species:
    return SPECIES.get(species_key, SPECIES["custom"])


def species_title(species_key: str, species_custom: str | None) -> str:
    if species_key == "custom" and species_custom:
        return species_custom
    return species_for(species_key).title


_REPLIES: dict[str, tuple[str, ...]] = {
    "pat": (
        "{emoji} {name} довольно жмурится. {sound}",
        "{emoji} {name} подставляет голову под руку. {sound}",
        "{emoji} {name} тихо урчит от удовольствия.",
    ),
    "play": (
        "{emoji} {name} носится кругами и явно доволен(на) игрой!",
        "{emoji} {name} азартно играет. {sound}",
    ),
    "tease": (
        "{emoji} {name} фыркает и отворачивается.",
        "{emoji} {name} недовольно косится. Кажется, это было лишним.",
    ),
    "hurt": (
        "{emoji} {name} обиженно прячется в угол.",
        "{emoji} {name} вздрагивает и смотрит с упрёком.",
    ),
    "feed": (
        "{emoji} {name} с аппетитом съедает {item}. {sound}",
        "{emoji} {name} хрумкает {item} и облизывается.",
    ),
    "toy": (
        "{emoji} {name} в восторге от новой игрушки: {item}! {sound}",
        "{emoji} {name} гоняет {item} по всему чату.",
    ),
}


def reply_text(event_type: str, *, name: str, species_key: str, item: str | None = None, rng: random.Random | None = None) -> str:
    species = species_for(species_key)
    template = (rng or random).choice(_REPLIES[event_type])
    return template.format(emoji=species.emoji, name=name, sound=species.sound, item=item or "")
