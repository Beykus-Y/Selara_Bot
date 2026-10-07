"""How a pet's personality forms: traits from behaviour, attitude to people and the chat, mood of the day.

Nobody sets a pet's traits: they are computed from what happened to the pet (its journal), so a pet that is
played with a lot becomes playful, a pet that is teased becomes grumpy. No I/O and no model here.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping, Sequence

from selara.application.ai_pets import mechanics as m

# A pet needs a bit of life behind it before it has traits at all.
TRAITS_MIN_EVENTS = 10
# A trait needs this share of the journal and at least this many matching events.
TRAIT_MIN_SHARE = 0.30
TRAIT_MIN_COUNT = 3
# Event types that count as "something happened to the pet" when forming traits.
BEHAVIOR_EVENT_TYPES = (
    "pat", "play", "feed", "toy", "tease", "hurt", "talk",
    "custom_care", "custom_feed", "custom_play", "custom_teach", "custom_social", "custom_prank",
)

_TRAIT_SOURCES: dict[str, tuple[str, ...]] = {
    "playful": ("play", "toy", "custom_play"),
    "affectionate": ("pat", "custom_care", "custom_social"),
    "greedy": ("feed", "custom_feed"),
    "curious": ("talk", "custom_teach"),
    "mischievous": ("custom_prank",),
    "grumpy": ("tease",),
    "shy": ("hurt",),
}
_LOYAL_AFFINITY = 60


def derive_traits(counts: Mapping[str, int], *, top_affinity: int = 0) -> list[str]:
    """Up to ``MAX_TRAITS`` traits from ``event_type -> count`` and the pet's strongest bond (stable order)."""
    total = sum(max(0, int(counts.get(key, 0))) for key in BEHAVIOR_EVENT_TYPES)
    if total < TRAITS_MIN_EVENTS:
        return []
    scored: list[tuple[float, str]] = []
    for trait, sources in _TRAIT_SOURCES.items():
        matching = sum(max(0, int(counts.get(key, 0))) for key in sources)
        share = matching / total
        if matching >= TRAIT_MIN_COUNT and share >= TRAIT_MIN_SHARE:
            scored.append((share, trait))
    negative = sum(max(0, int(counts.get(key, 0))) for key in ("tease", "hurt", "custom_prank"))
    active = sum(max(0, int(counts.get(key, 0))) for key in ("play", "toy", "custom_play", "custom_teach"))
    if total >= 2 * TRAITS_MIN_EVENTS and negative == 0 and active == 0:
        scored.append((0.5, "lazy"))
    elif total >= 2 * TRAITS_MIN_EVENTS and negative == 0 and active / total < 0.2:
        scored.append((0.25, "calm"))
    if top_affinity >= _LOYAL_AFFINITY:
        scored.append((0.28, "loyal"))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [trait for _, trait in scored[: m.MAX_TRAITS]]


_MOOD_OF_DAY: dict[str, tuple[str, ...]] = {
    "high": (
        "Сегодня настроение отличное, хочется дурачиться.",
        "Сегодня всё нравится: хвост трубой, глаза горят.",
        "С самого утра в приподнятом настроении.",
    ),
    "good": (
        "Сегодня спокойный и приятный день.",
        "Сегодня настроение ровное, можно и поболтать, и поиграть.",
        "Сегодня доволен(на) жизнью, но без фанатизма.",
    ),
    "low": (
        "Сегодня немного скучно, хочется внимания.",
        "Сегодня не в духе: лучше не торопить.",
        "Сегодня тянет помолчать и погреться в уголке.",
    ),
    "bad": (
        "Сегодня совсем грустно, нужна забота.",
        "Сегодня всё валится из лап, нужно ласковое слово.",
        "Сегодня хочется спрятаться и чтобы пожалели.",
    ),
}
_TRAIT_FLAVOR: dict[str, str] = {
    "playful": "Тянет на игры.",
    "lazy": "Лень даже шевелиться.",
    "curious": "Всюду суёт нос.",
    "shy": "Держится поближе к стене.",
    "grumpy": "Ворчит по любому поводу.",
    "affectionate": "Просится на ручки.",
    "mischievous": "Явно что-то замышляет.",
    "calm": "Невозмутим(а) как всегда.",
    "greedy": "Мысли только о еде.",
    "loyal": "Ищет глазами хозяина.",
}


def _mood_bucket(mood: int) -> str:
    if mood >= 75:
        return "high"
    if mood >= 50:
        return "good"
    if mood >= 25:
        return "low"
    return "bad"


def mood_of_the_day(*, pet_id: int, day: date, mood: int, traits: Sequence[str] = ()) -> str:
    """One short phrase per pet per local day: the same all day, changing tomorrow; follows the current mood."""
    seed = int(pet_id) * 31 + day.toordinal()
    phrases = _MOOD_OF_DAY[_mood_bucket(mood)]
    text = phrases[seed % len(phrases)]
    flavored = [trait for trait in traits if trait in _TRAIT_FLAVOR]
    if flavored and seed % 3 != 0:
        text += " " + _TRAIT_FLAVOR[flavored[seed % len(flavored)]]
    return text


def group_attitude(relations: Iterable[tuple[int, int]]) -> str:
    """The pet's feeling about the chat as a whole from ``(affinity, interactions)`` of everyone it met here."""
    weight = 0
    score = 0
    for affinity, interactions in relations:
        w = max(1, int(interactions))
        weight += w
        score += int(affinity) * w
    if weight < 5:
        return "ещё присматривается к чату"
    average = score / weight
    if average >= 30:
        return "тепло относится к чату"
    if average >= 10:
        return "в целом дружелюбно относится к чату"
    if average > -10:
        return "нейтрально относится к чату"
    return "настороженно относится к чату"
