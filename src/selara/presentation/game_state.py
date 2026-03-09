from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import re
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Literal, Protocol
from uuid import uuid4

try:
    from redis.exceptions import RedisError as _RedisError
except ModuleNotFoundError:  # pragma: no cover
    _RedisError = None

logger = logging.getLogger(__name__)

GameStatus = Literal["lobby", "started", "finished"]
GameKind = Literal["spy", "mafia", "dice", "number", "quiz", "bredovukha", "bunker", "whoami", "zlobcards"]
GamePhase = Literal[
    "lobby",
    "freeplay",
    "whoami_ask",
    "whoami_answer",
    "category_pick",
    "private_answers",
    "public_vote",
    "bunker_reveal",
    "bunker_vote",
    "night",
    "day_discussion",
    "day_vote",
    "day_execution_confirm",
    "finished",
]


@dataclass(frozen=True)
class GameDefinition:
    key: GameKind
    title: str
    short_description: str
    min_players: int
    secret_roles: bool


GAME_DEFINITIONS: dict[GameKind, GameDefinition] = {
    "spy": GameDefinition(
        key="spy",
        title="Найди шпиона",
        short_description="У игроков скрытые роли: шпион/мирный. Мирные знают локацию, шпион — нет.",
        min_players=3,
        secret_roles=True,
    ),
    "mafia": GameDefinition(
        key="mafia",
        title="Мини-мафия",
        short_description="Ночь/день/голосование, скрытые роли и легальная победа по правилам.",
        min_players=4,
        secret_roles=True,
    ),
    "dice": GameDefinition(
        key="dice",
        title="Дуэль кубиков",
        short_description="Все кидают кубик, побеждает максимальный результат.",
        min_players=2,
        secret_roles=False,
    ),
    "number": GameDefinition(
        key="number",
        title="Угадай число",
        short_description="Бот загадывает число 1..100, игроки пытаются угадать в чате.",
        min_players=2,
        secret_roles=False,
    ),
    "quiz": GameDefinition(
        key="quiz",
        title="Викторина",
        short_description="Раунды с вопросами и вариантами ответов. Побеждает участник с максимумом баллов.",
        min_players=2,
        secret_roles=False,
    ),
    "bredovukha": GameDefinition(
        key="bredovukha",
        title="Бредовуха",
        short_description="Заполняйте пропуск в факте, обманывайте друг друга и угадывайте правду.",
        min_players=3,
        secret_roles=False,
    ),
    "bunker": GameDefinition(
        key="bunker",
        title="Бункер",
        short_description="Катастрофа, скрытые характеристики и голосования за выбывание до лимита мест.",
        min_players=6,
        secret_roles=True,
    ),
    "whoami": GameDefinition(
        key="whoami",
        title="Кто я",
        short_description="Угадайте свою карточку через вопросы с ответами «да / нет / не знаю».",
        min_players=3,
        secret_roles=True,
    ),
    "zlobcards": GameDefinition(
        key="zlobcards",
        title="500 Злобных Карт",
        short_description="Чёрная карточка, приватный выбор белых карт и анонимное голосование за лучший панч.",
        min_players=3,
        secret_roles=False,
    ),
}

GAME_LAUNCHABLE_KINDS: tuple[GameKind, ...] = (
    "zlobcards",
    "spy",
    "whoami",
    "mafia",
    "dice",
    "quiz",
    "bredovukha",
    "bunker",
)

NUMBER_GUESS_MIN = 1
NUMBER_GUESS_MAX = 100
QUIZ_ROUNDS = 5
BRED_DEFAULT_ROUNDS = 5
BRED_MIN_ROUNDS = 1
BRED_MIN_LIE_LEN = 1
BRED_MAX_LIE_LEN = 120
BRED_CATEGORY_CHOICES = 5
WHOAMI_MIN_QUESTION_LEN = 3
WHOAMI_MAX_QUESTION_LEN = 180
WHOAMI_MAX_GUESS_LEN = 120
WHOAMI_HISTORY_LIMIT = 10
ZLOBCARDS_DEFAULT_ROUNDS = 8
ZLOBCARDS_MIN_ROUNDS = 1
ZLOBCARDS_MAX_ROUNDS = 30
ZLOBCARDS_DEFAULT_TARGET_SCORE = 7
ZLOBCARDS_MIN_TARGET_SCORE = 1
ZLOBCARDS_MAX_TARGET_SCORE = 20
ZLOBCARDS_HAND_SIZE = 5
ZLOBCARDS_MAX_CARD_TEXT_LEN = 180

BUNKER_CARD_FIELDS: tuple[str, ...] = (
    "profession",
    "age",
    "gender",
    "health_condition",
    "skill",
    "hobby",
    "phobia",
    "trait",
    "item",
)
BUNKER_DATA_KEYS_REQUIRED: tuple[str, ...] = (
    "catastrophes",
    "bunker_conditions",
    "professions",
    "ages",
    "genders",
    "health_conditions",
    "skills",
    "hobbies",
    "phobias",
    "traits",
    "items",
)
BUNKER_FIELD_TO_DATA_KEY: dict[str, str] = {
    "profession": "professions",
    "age": "ages",
    "gender": "genders",
    "health_condition": "health_conditions",
    "skill": "skills",
    "hobby": "hobbies",
    "phobia": "phobias",
    "trait": "traits",
    "item": "items",
}

MAFIA_ROLE_CIVILIAN = "Мирный житель"
MAFIA_ROLE_COMMISSIONER = "Комиссар"
MAFIA_ROLE_DOCTOR = "Доктор"
MAFIA_ROLE_ESCORT = "Красотка"
MAFIA_ROLE_BODYGUARD = "Телохранитель"
MAFIA_ROLE_JOURNALIST = "Журналист"
MAFIA_ROLE_INSPECTOR = "Инспектор"
MAFIA_ROLE_CHILD = "Ребёнок"
MAFIA_ROLE_PRIEST = "Священник"
MAFIA_ROLE_VETERAN = "Ветеран"
MAFIA_ROLE_REANIMATOR = "Реаниматор"
MAFIA_ROLE_PSYCHOLOGIST = "Психолог"
MAFIA_ROLE_DETECTIVE = "Детектив"

MAFIA_ROLE_MAFIA = "Рядовая мафия"
MAFIA_ROLE_DON = "Дон мафии"
MAFIA_ROLE_LAWYER = "Адвокат"
MAFIA_ROLE_WEREWOLF = "Оборотень"
MAFIA_ROLE_NINJA = "Ниндзя"
MAFIA_ROLE_POISONER = "Отравитель"
MAFIA_ROLE_TERRORIST = "Террорист"

MAFIA_ROLE_MANIAC = "Маньяк"
MAFIA_ROLE_JESTER = "Шут"
MAFIA_ROLE_WITCH = "Ведьма"
MAFIA_ROLE_SERIAL = "Серийный убийца"
MAFIA_ROLE_VAMPIRE = "Вампир"
MAFIA_ROLE_BOMBER = "Подрывник"
MAFIA_ROLE_VAMPIRE_THRALL = "Обращённый вампир"

MAFIA_TEAM_CIVILIAN = "civilian"
MAFIA_TEAM_MAFIA = "mafia"
MAFIA_TEAM_NEUTRAL = "neutral"
MAFIA_TEAM_VAMPIRE = "vampire"

MAFIA_CIVILIAN_SPECIAL_POOL: tuple[tuple[str, int], ...] = (
    (MAFIA_ROLE_COMMISSIONER, 5),
    (MAFIA_ROLE_DOCTOR, 5),
    (MAFIA_ROLE_ESCORT, 7),
    (MAFIA_ROLE_BODYGUARD, 8),
    (MAFIA_ROLE_DETECTIVE, 8),
    (MAFIA_ROLE_CHILD, 8),
    (MAFIA_ROLE_JOURNALIST, 9),
    (MAFIA_ROLE_INSPECTOR, 9),
    (MAFIA_ROLE_PSYCHOLOGIST, 9),
    (MAFIA_ROLE_PRIEST, 10),
    (MAFIA_ROLE_VETERAN, 10),
    (MAFIA_ROLE_REANIMATOR, 11),
)

MAFIA_SPECIAL_MAFIA_POOL: tuple[tuple[str, int], ...] = (
    (MAFIA_ROLE_DON, 8),
    (MAFIA_ROLE_LAWYER, 9),
    (MAFIA_ROLE_WEREWOLF, 9),
    (MAFIA_ROLE_NINJA, 10),
    (MAFIA_ROLE_POISONER, 10),
    (MAFIA_ROLE_TERRORIST, 8),
)

MAFIA_NEUTRAL_POOL: tuple[tuple[str, int], ...] = (
    (MAFIA_ROLE_MANIAC, 7),
    (MAFIA_ROLE_JESTER, 7),
    (MAFIA_ROLE_SERIAL, 8),
    (MAFIA_ROLE_WITCH, 10),
    (MAFIA_ROLE_BOMBER, 10),
    (MAFIA_ROLE_VAMPIRE, 11),
)

MAFIA_CIVILIAN_ROLES: set[str] = {
    MAFIA_ROLE_CIVILIAN,
    MAFIA_ROLE_COMMISSIONER,
    MAFIA_ROLE_DOCTOR,
    MAFIA_ROLE_ESCORT,
    MAFIA_ROLE_BODYGUARD,
    MAFIA_ROLE_JOURNALIST,
    MAFIA_ROLE_INSPECTOR,
    MAFIA_ROLE_CHILD,
    MAFIA_ROLE_PRIEST,
    MAFIA_ROLE_VETERAN,
    MAFIA_ROLE_REANIMATOR,
    MAFIA_ROLE_PSYCHOLOGIST,
    MAFIA_ROLE_DETECTIVE,
}

MAFIA_MAFIA_ROLES: set[str] = {
    MAFIA_ROLE_MAFIA,
    MAFIA_ROLE_DON,
    MAFIA_ROLE_LAWYER,
    MAFIA_ROLE_WEREWOLF,
    MAFIA_ROLE_NINJA,
    MAFIA_ROLE_POISONER,
    MAFIA_ROLE_TERRORIST,
}

MAFIA_NEUTRAL_ROLES: set[str] = {
    MAFIA_ROLE_MANIAC,
    MAFIA_ROLE_JESTER,
    MAFIA_ROLE_WITCH,
    MAFIA_ROLE_SERIAL,
    MAFIA_ROLE_VAMPIRE,
    MAFIA_ROLE_BOMBER,
}

MAFIA_ATTACKER_ROLES: set[str] = {
    MAFIA_ROLE_MAFIA,
    MAFIA_ROLE_DON,
    MAFIA_ROLE_WEREWOLF,
    MAFIA_ROLE_NINJA,
    MAFIA_ROLE_POISONER,
    MAFIA_ROLE_TERRORIST,
}

MAFIA_BLOCK_IMMUNE_ROLES: set[str] = {
    MAFIA_ROLE_SERIAL,
}

MAFIA_NIGHT_KILL_ROLES: set[str] = {
    MAFIA_ROLE_MAFIA,
    MAFIA_ROLE_DON,
    MAFIA_ROLE_WEREWOLF,
    MAFIA_ROLE_NINJA,
    MAFIA_ROLE_TERRORIST,
    MAFIA_ROLE_MANIAC,
    MAFIA_ROLE_SERIAL,
    MAFIA_ROLE_WITCH,
}

MAFIA_HIDDEN_MOVERS: set[str] = {
    MAFIA_ROLE_NINJA,
}

MAFIA_KILLER_ROLES: set[str] = {
    MAFIA_ROLE_MAFIA,
    MAFIA_ROLE_DON,
    MAFIA_ROLE_NINJA,
    MAFIA_ROLE_POISONER,
    MAFIA_ROLE_TERRORIST,
    MAFIA_ROLE_MANIAC,
    MAFIA_ROLE_SERIAL,
    MAFIA_ROLE_WITCH,
}

MAFIA_VISITOR_ROLES: set[str] = {
    MAFIA_ROLE_MAFIA,
    MAFIA_ROLE_DON,
    MAFIA_ROLE_POISONER,
    MAFIA_ROLE_NINJA,
    MAFIA_ROLE_COMMISSIONER,
    MAFIA_ROLE_DOCTOR,
    MAFIA_ROLE_ESCORT,
    MAFIA_ROLE_BODYGUARD,
    MAFIA_ROLE_JOURNALIST,
    MAFIA_ROLE_INSPECTOR,
    MAFIA_ROLE_PRIEST,
    MAFIA_ROLE_REANIMATOR,
    MAFIA_ROLE_PSYCHOLOGIST,
    MAFIA_ROLE_DETECTIVE,
    MAFIA_ROLE_MANIAC,
    MAFIA_ROLE_SERIAL,
    MAFIA_ROLE_WITCH,
    MAFIA_ROLE_VAMPIRE,
    MAFIA_ROLE_BOMBER,
    MAFIA_ROLE_LAWYER,
}


@dataclass(frozen=True)
class NightResolution:
    killed_user_id: int | None
    killed_user_label: str | None
    killed_user_role: str | None
    sheriff_checked_user_id: int | None
    sheriff_checked_user_label: str | None
    sheriff_checked_is_mafia: bool | None
    tie_on_mafia_vote: bool
    winner_text: str | None
    killed_user_ids: tuple[int, ...] = ()
    public_notes: tuple[str, ...] = ()
    private_reports: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class DayVoteResolution:
    candidate_user_id: int | None
    candidate_user_label: str | None
    tie: bool
    opened_execution_confirm: bool
    vote_protocol: tuple[tuple[int, int | None], ...]
    winner_text: str | None
    public_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionConfirmResolution:
    yes_count: int
    no_count: int
    passed: bool
    executed_user_id: int | None
    executed_user_label: str | None
    executed_user_role: str | None
    vote_protocol: tuple[tuple[int, bool | None], ...]
    winner_text: str | None
    public_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpyVoteResolution:
    candidate_user_id: int | None
    candidate_user_label: str | None
    candidate_votes: int
    voted_count: int
    total_players: int
    tie: bool
    candidate_is_spy: bool | None
    winner_text: str | None


@dataclass(frozen=True)
class SpyGuessResolution:
    spy_user_id: int
    spy_user_label: str | None
    guessed_location: str
    guessed_correctly: bool
    actual_location: str | None
    winner_text: str


@dataclass(frozen=True)
class DiceRollResult:
    roller_user_id: int
    roll_value: int
    rolled_count: int
    total_players: int
    finished: bool
    winner_text: str | None


@dataclass(frozen=True)
class NumberGuessResult:
    guess: int
    direction: Literal["up", "down", "correct"]
    attempts_for_user: int
    attempts_total: int
    winner_user_id: int | None
    winner_label: str | None
    winner_text: str | None
    distance_to_secret: int


@dataclass(frozen=True)
class QuizQuestion:
    prompt: str
    options: tuple[str, ...]
    answer_index: int


@dataclass(frozen=True)
class BredQuestion:
    category: str
    prompt_with_blank: str
    correct_answer: str
    fact_text: str | None = None


@dataclass(frozen=True)
class QuizAnswerResult:
    previous_answer_index: int | None
    answered_count: int
    total_players: int
    all_answered: bool


@dataclass(frozen=True)
class QuizRoundResolution:
    question_index: int
    question_text: str
    correct_option_index: int
    correct_option_text: str
    answered_count: int
    total_players: int
    per_player_answers: tuple[tuple[int, int | None, bool], ...]
    correct_players: tuple[int, ...]
    scores: tuple[tuple[int, int], ...]
    next_question_index: int | None
    finished: bool
    winner_text: str | None


@dataclass(frozen=True)
class BredSubmitResult:
    previous_lie: str | None
    submitted_count: int
    total_players: int
    all_submitted: bool
    vote_opened: bool


@dataclass(frozen=True)
class BredVoteResult:
    previous_option_index: int | None
    voted_count: int
    total_players: int
    all_voted: bool


@dataclass(frozen=True)
class BredRoundResolution:
    round_no: int
    category: str
    question_text: str
    correct_option_index: int
    correct_option_text: str
    fact_text: str | None
    options: tuple[str, ...]
    option_owner_user_ids: tuple[int | None, ...]
    vote_tally: tuple[int, ...]
    per_player_votes: tuple[tuple[int, int | None, bool], ...]
    gains: tuple[tuple[int, int], ...]
    scores: tuple[tuple[int, int], ...]
    finished: bool
    next_round_no: int | None
    next_selector_user_id: int | None
    next_selector_label: str | None
    winner_user_ids: tuple[int, ...]
    winner_text: str | None


@dataclass(frozen=True)
class ZlobBlackCard:
    text: str
    slots: Literal[1, 2]


@dataclass(frozen=True)
class ZlobSubmitResult:
    previous_submission: tuple[str, ...] | None
    submitted_count: int
    total_players: int
    all_submitted: bool
    vote_opened: bool


@dataclass(frozen=True)
class ZlobVoteResult:
    previous_option_index: int | None
    voted_count: int
    total_players: int
    all_voted: bool


@dataclass(frozen=True)
class ZlobRoundResolution:
    round_no: int
    black_text: str
    black_slots: int
    options: tuple[str, ...]
    option_owner_user_ids: tuple[int | None, ...]
    vote_tally: tuple[int, ...]
    winner_option_indexes: tuple[int, ...]
    per_player_votes: tuple[tuple[int, int | None, bool], ...]
    gains: tuple[tuple[int, int], ...]
    scores: tuple[tuple[int, int], ...]
    finished: bool
    next_round_no: int | None
    winner_user_ids: tuple[int, ...]
    winner_text: str | None


@dataclass(frozen=True)
class BunkerCard:
    profession: str
    age: str
    gender: str
    health_condition: str
    skill: str
    hobby: str
    phobia: str
    trait: str
    item: str


@dataclass(frozen=True)
class BunkerRevealResult:
    actor_user_id: int
    actor_user_label: str
    field_key: str | None
    field_label: str | None
    revealed_value: str | None
    revealed_count_for_actor: int
    total_fields_for_actor: int
    skipped: bool
    vote_opened: bool
    next_actor_user_id: int | None
    next_actor_label: str | None


@dataclass(frozen=True)
class BunkerVoteResolution:
    round_no: int
    voted_count: int
    total_alive: int
    tie: bool
    vote_protocol: tuple[tuple[int, int | None], ...]
    vote_tally: tuple[tuple[int, int], ...]
    eliminated_user_id: int | None
    eliminated_user_label: str | None
    eliminated_card: BunkerCard | None
    finished: bool
    winner_user_ids: tuple[int, ...]
    winner_text: str | None
    next_phase: GamePhase
    next_actor_user_id: int | None
    next_actor_label: str | None


@dataclass(frozen=True)
class WhoamiHistoryEntry:
    actor_user_id: int
    question_text: str | None = None
    answer_code: str | None = None
    answer_label: str | None = None
    responder_user_id: int | None = None
    guess_text: str | None = None
    guessed_correctly: bool | None = None


@dataclass(frozen=True)
class WhoamiQuestionResult:
    previous_question_text: str | None
    question_text: str
    actor_user_id: int
    actor_user_label: str | None


@dataclass(frozen=True)
class WhoamiAnswerResolution:
    actor_user_id: int
    actor_user_label: str | None
    responder_user_id: int
    responder_user_label: str | None
    question_text: str
    answer_code: Literal["yes", "no", "unknown", "irrelevant"]
    answer_label: str
    keeps_turn: bool
    next_actor_user_id: int | None
    next_actor_label: str | None


@dataclass(frozen=True)
class WhoamiGuessResolution:
    actor_user_id: int
    actor_user_label: str | None
    guess_text: str
    actual_identity: str | None
    guessed_correctly: bool
    finished: bool
    next_actor_user_id: int | None
    next_actor_label: str | None
    winner_text: str | None


_SPY_FALLBACK_CATEGORY = "Классические локации"


def _clean_unique_text_values(items_raw: list[object]) -> tuple[str, ...]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items_raw:
        if not isinstance(item, str):
            continue
        value = " ".join(item.split()).strip()
        if not value:
            continue
        lowered = value.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        cleaned.append(value)
    return tuple(cleaned)


def _load_spy_locations_by_category() -> dict[str, tuple[str, ...]]:
    path = Path(__file__).with_name("spy_locations.json")
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if isinstance(raw, list):
        fallback_locations = _clean_unique_text_values(raw)
        return {_SPY_FALLBACK_CATEGORY: fallback_locations} if fallback_locations else {}

    if not isinstance(raw, dict):
        return {}

    result: dict[str, tuple[str, ...]] = {}
    seen_categories: set[str] = set()
    for category_raw, items_raw in raw.items():
        if not isinstance(category_raw, str) or not isinstance(items_raw, list):
            continue
        category = " ".join(category_raw.split()).strip()
        if not category:
            continue
        lowered_category = category.casefold()
        if lowered_category in seen_categories:
            continue
        cleaned = _clean_unique_text_values(items_raw)
        if not cleaned:
            continue
        seen_categories.add(lowered_category)
        result[category] = cleaned
    return result


def _flatten_spy_locations(locations_by_category: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    flattened: list[str] = []
    seen: set[str] = set()
    for items in locations_by_category.values():
        for item in items:
            lowered = item.casefold()
            if lowered in seen:
                continue
            seen.add(lowered)
            flattened.append(item)
    return tuple(flattened)


def _load_quiz_question_bank() -> tuple[QuizQuestion, ...]:
    path = Path(__file__).with_name("quiz_questions.json")
    if not path.exists():
        return ()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()

    if not isinstance(raw, list):
        return ()

    result: list[QuizQuestion] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt", "")).strip()
        options_raw = item.get("options")
        answer_index_raw = item.get("answer_index")

        if not prompt:
            continue
        if not isinstance(options_raw, list):
            continue
        if not isinstance(answer_index_raw, int):
            continue

        options = [str(option).strip() for option in options_raw]
        options = [option for option in options if option]
        if len(options) < 2:
            continue
        if answer_index_raw < 0 or answer_index_raw >= len(options):
            continue

        result.append(
            QuizQuestion(
                prompt=prompt,
                options=tuple(options),
                answer_index=answer_index_raw,
            )
        )

    return tuple(result)


SPY_LOCATIONS_BY_CATEGORY: dict[str, tuple[str, ...]] = _load_spy_locations_by_category()
SPY_CATEGORIES: tuple[str, ...] = tuple(SPY_LOCATIONS_BY_CATEGORY.keys())
SPY_LOCATIONS: tuple[str, ...] = _flatten_spy_locations(SPY_LOCATIONS_BY_CATEGORY)
QUIZ_QUESTION_BANK: tuple[QuizQuestion, ...] = _load_quiz_question_bank()


def _load_bred_questions_by_category() -> dict[str, tuple[BredQuestion, ...]]:
    path = Path(__file__).with_name("bredovukha_questions.json")
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(raw, dict):
        return {}

    result: dict[str, tuple[BredQuestion, ...]] = {}
    for category_raw, items_raw in raw.items():
        if not isinstance(category_raw, str):
            continue
        if not isinstance(items_raw, list):
            continue

        category = category_raw.strip()
        if not category:
            continue

        questions: list[BredQuestion] = []
        for item in items_raw:
            if not isinstance(item, dict):
                continue
            prompt = str(item.get("prompt_with_blank", "")).strip()
            answer = str(item.get("correct_answer", "")).strip()
            fact_text = str(item.get("fact_text") or item.get("explanation") or "").strip() or None
            if not prompt or not answer:
                continue
            questions.append(
                BredQuestion(
                    category=category,
                    prompt_with_blank=prompt,
                    correct_answer=answer,
                    fact_text=fact_text,
                )
            )

        if questions:
            result[category] = tuple(questions)

    return result


BRED_QUESTIONS_BY_CATEGORY: dict[str, tuple[BredQuestion, ...]] = _load_bred_questions_by_category()
BRED_CATEGORIES: tuple[str, ...] = tuple(sorted(BRED_QUESTIONS_BY_CATEGORY.keys()))


def _load_zlob_cards_by_category() -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, tuple[ZlobBlackCard, ...]],
    frozenset[str],
]:
    path = Path(__file__).with_name("zlobcards_cards.json")
    if not path.exists():
        return {}, {}, frozenset()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}, frozenset()

    if not isinstance(raw, dict):
        return {}, {}, frozenset()

    white_by_category: dict[str, tuple[str, ...]] = {}
    black_by_category: dict[str, tuple[ZlobBlackCard, ...]] = {}
    explicit_categories: set[str] = set()

    for category_raw, payload_raw in raw.items():
        if not isinstance(category_raw, str) or not isinstance(payload_raw, dict):
            continue

        category = " ".join(category_raw.split()).strip()
        if not category:
            continue

        white_raw = payload_raw.get("white")
        black_raw = payload_raw.get("black")
        explicit = bool(payload_raw.get("explicit", False))
        if not isinstance(white_raw, list) or not isinstance(black_raw, list):
            continue

        white_cards: list[str] = []
        white_seen: set[str] = set()
        for item in white_raw:
            if not isinstance(item, str):
                continue
            value = " ".join(item.split()).strip()
            if not value:
                continue
            if len(value) > ZLOBCARDS_MAX_CARD_TEXT_LEN:
                value = value[:ZLOBCARDS_MAX_CARD_TEXT_LEN].rstrip()
            lowered = value.casefold()
            if lowered in white_seen:
                continue
            white_seen.add(lowered)
            white_cards.append(value)

        black_cards: list[ZlobBlackCard] = []
        black_seen: set[tuple[str, int]] = set()
        for item in black_raw:
            text = ""
            slots: int = 1
            if isinstance(item, str):
                text = " ".join(item.split()).strip()
            elif isinstance(item, dict):
                text = " ".join(str(item.get("text") or "").split()).strip()
                slots_raw = item.get("slots")
                if isinstance(slots_raw, int) and slots_raw in {1, 2}:
                    slots = slots_raw
            else:
                continue

            if not text:
                continue
            if len(text) > ZLOBCARDS_MAX_CARD_TEXT_LEN:
                text = text[:ZLOBCARDS_MAX_CARD_TEXT_LEN].rstrip()
            if slots == 2 and text.count("____") < 2:
                continue
            marker = (text.casefold(), slots)
            if marker in black_seen:
                continue
            black_seen.add(marker)
            black_cards.append(ZlobBlackCard(text=text, slots=slots))  # type: ignore[arg-type]

        if not white_cards or not black_cards:
            continue

        white_by_category[category] = tuple(white_cards)
        black_by_category[category] = tuple(black_cards)
        if explicit:
            explicit_categories.add(category)

    return white_by_category, black_by_category, frozenset(explicit_categories)


ZLOBCARDS_WHITE_BY_CATEGORY, ZLOBCARDS_BLACK_BY_CATEGORY, ZLOBCARDS_EXPLICIT_CATEGORIES = _load_zlob_cards_by_category()
ZLOBCARDS_CATEGORIES: tuple[str, ...] = tuple(sorted(ZLOBCARDS_WHITE_BY_CATEGORY.keys()))


def _load_whoami_cards_by_category() -> dict[str, tuple[str, ...]]:
    path = Path(__file__).with_name("whoami_cards.json")
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(raw, dict):
        return {}

    result: dict[str, tuple[str, ...]] = {}
    for category_raw, items_raw in raw.items():
        if not isinstance(category_raw, str) or not isinstance(items_raw, list):
            continue

        category = category_raw.strip()
        if not category:
            continue

        cleaned: list[str] = []
        seen: set[str] = set()
        for item in items_raw:
            if not isinstance(item, str):
                continue
            value = " ".join(item.split()).strip()
            if not value:
                continue
            lowered = value.casefold()
            if lowered in seen:
                continue
            seen.add(lowered)
            cleaned.append(value)

        if cleaned:
            result[category] = tuple(cleaned)

    return result


WHOAMI_CARDS_BY_CATEGORY: dict[str, tuple[str, ...]] = _load_whoami_cards_by_category()
WHOAMI_CATEGORIES: tuple[str, ...] = tuple(sorted(WHOAMI_CARDS_BY_CATEGORY.keys()))
WHOAMI_EXPLICIT_CATEGORIES: frozenset[str] = frozenset({"18+ и пикантное"})


def _normalize_spy_category(category: str | None) -> str | None:
    if category is None:
        return None
    normalized = " ".join(category.split()).strip()
    return normalized or None


def spy_locations_for_category(category: str | None) -> tuple[str, ...]:
    normalized = _normalize_spy_category(category)
    if normalized is None:
        return SPY_LOCATIONS
    return SPY_LOCATIONS_BY_CATEGORY.get(normalized, SPY_LOCATIONS)


def _normalize_whoami_category(category: str | None) -> str | None:
    if category is None:
        return None
    value = " ".join(category.split()).strip()
    return value or None


def _normalize_zlob_category(category: str | None) -> str | None:
    if category is None:
        return None
    value = " ".join(category.split()).strip()
    return value or None


def is_whoami_category_explicit(category: str | None) -> bool:
    normalized = _normalize_whoami_category(category)
    return normalized in WHOAMI_EXPLICIT_CATEGORIES if normalized is not None else False


def allowed_whoami_categories(*, actions_18_enabled: bool) -> tuple[str, ...]:
    if actions_18_enabled:
        return WHOAMI_CATEGORIES
    return tuple(category for category in WHOAMI_CATEGORIES if category not in WHOAMI_EXPLICIT_CATEGORIES)


def is_whoami_category_allowed(category: str | None, *, actions_18_enabled: bool) -> bool:
    normalized = _normalize_whoami_category(category)
    if normalized is None:
        return True
    if normalized not in WHOAMI_CARDS_BY_CATEGORY:
        return False
    return actions_18_enabled or normalized not in WHOAMI_EXPLICIT_CATEGORIES


def is_zlob_category_explicit(category: str | None) -> bool:
    normalized = _normalize_zlob_category(category)
    return normalized in ZLOBCARDS_EXPLICIT_CATEGORIES if normalized is not None else False


def allowed_zlob_categories(*, actions_18_enabled: bool) -> tuple[str, ...]:
    if actions_18_enabled:
        return ZLOBCARDS_CATEGORIES
    return tuple(category for category in ZLOBCARDS_CATEGORIES if category not in ZLOBCARDS_EXPLICIT_CATEGORIES)


def is_zlob_category_allowed(category: str | None, *, actions_18_enabled: bool) -> bool:
    normalized = _normalize_zlob_category(category)
    if normalized is None:
        return True
    if normalized not in ZLOBCARDS_WHITE_BY_CATEGORY or normalized not in ZLOBCARDS_BLACK_BY_CATEGORY:
        return False
    return actions_18_enabled or normalized not in ZLOBCARDS_EXPLICIT_CATEGORIES


def _load_bunker_data() -> dict[str, tuple[str, ...]]:
    path = Path(__file__).with_name("bunker_cards.json")
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(raw, dict):
        return {}

    result: dict[str, tuple[str, ...]] = {}
    for key in BUNKER_DATA_KEYS_REQUIRED:
        values_raw = raw.get(key)
        if not isinstance(values_raw, list):
            continue

        cleaned: list[str] = []
        seen: set[str] = set()
        for item in values_raw:
            if not isinstance(item, str):
                continue
            value = item.strip()
            if not value:
                continue
            lowered = value.casefold()
            if lowered in seen:
                continue
            seen.add(lowered)
            cleaned.append(value)

        if cleaned:
            result[key] = tuple(cleaned)

    return result


BUNKER_DATA: dict[str, tuple[str, ...]] = _load_bunker_data()


@dataclass
class GroupGame:
    game_id: str
    kind: GameKind
    chat_id: int
    chat_title: str | None
    owner_user_id: int
    players: dict[int, str] = field(default_factory=dict)
    roles: dict[int, str] = field(default_factory=dict)
    spy_votes: dict[int, int] = field(default_factory=dict)
    dice_scores: dict[int, int] = field(default_factory=dict)
    alive_player_ids: set[int] = field(default_factory=set)
    spy_location: str | None = None
    spy_category: str | None = None
    number_secret: int | None = None
    number_attempts: dict[int, int] = field(default_factory=dict)
    number_attempts_total: int = 0
    quiz_questions: tuple[QuizQuestion, ...] = field(default_factory=tuple)
    quiz_current_question_index: int | None = None
    quiz_answers: dict[int, int] = field(default_factory=dict)
    quiz_scores: dict[int, int] = field(default_factory=dict)
    whoami_category: str | None = None
    whoami_turn_order: tuple[int, ...] = field(default_factory=tuple)
    whoami_current_actor_index: int = 0
    whoami_current_actor_user_id: int | None = None
    whoami_pending_question_text: str | None = None
    whoami_pending_question_user_id: int | None = None
    whoami_history: list[WhoamiHistoryEntry] = field(default_factory=list)
    whoami_solved_user_ids: set[int] = field(default_factory=set)
    whoami_finish_order: list[int] = field(default_factory=list)
    whoami_winner_user_id: int | None = None
    bred_rounds: int = BRED_DEFAULT_ROUNDS
    bred_current_category: str | None = None
    bred_current_selector_user_id: int | None = None
    bred_category_options: tuple[str, ...] = field(default_factory=tuple)
    bred_selector_user_ids_by_round: tuple[int, ...] = field(default_factory=tuple)
    bred_used_question_keys: set[str] = field(default_factory=set)
    bred_question_prompt: str | None = None
    bred_correct_answer: str | None = None
    bred_fact_text: str | None = None
    bred_lies: dict[int, str] = field(default_factory=dict)
    bred_options: tuple[str, ...] = field(default_factory=tuple)
    bred_option_owner_user_ids: tuple[int | None, ...] = field(default_factory=tuple)
    bred_votes: dict[int, int] = field(default_factory=dict)
    bred_scores: dict[int, int] = field(default_factory=dict)
    bred_last_round_no: int | None = None
    bred_last_category: str | None = None
    bred_last_question_prompt: str | None = None
    bred_last_correct_answer: str | None = None
    bred_last_fact_text: str | None = None
    bred_last_options: tuple[str, ...] = field(default_factory=tuple)
    bred_last_option_owner_user_ids: tuple[int | None, ...] = field(default_factory=tuple)
    bred_last_vote_tally: tuple[int, ...] = field(default_factory=tuple)
    bred_last_correct_option_index: int | None = None
    zlob_rounds: int = ZLOBCARDS_DEFAULT_ROUNDS
    zlob_target_score: int = ZLOBCARDS_DEFAULT_TARGET_SCORE
    zlob_category: str | None = None
    zlob_black_text: str | None = None
    zlob_black_slots: int = 1
    zlob_hands: dict[int, tuple[str, ...]] = field(default_factory=dict)
    zlob_white_deck: list[str] = field(default_factory=list)
    zlob_white_discard: list[str] = field(default_factory=list)
    zlob_black_deck: list[ZlobBlackCard] = field(default_factory=list)
    zlob_black_discard: list[ZlobBlackCard] = field(default_factory=list)
    zlob_submissions: dict[int, tuple[str, ...]] = field(default_factory=dict)
    zlob_options: tuple[str, ...] = field(default_factory=tuple)
    zlob_option_owner_user_ids: tuple[int | None, ...] = field(default_factory=tuple)
    zlob_votes: dict[int, int] = field(default_factory=dict)
    zlob_scores: dict[int, int] = field(default_factory=dict)
    zlob_last_round_no: int | None = None
    zlob_last_black_text: str | None = None
    zlob_last_black_slots: int = 1
    zlob_last_options: tuple[str, ...] = field(default_factory=tuple)
    zlob_last_option_owner_user_ids: tuple[int | None, ...] = field(default_factory=tuple)
    zlob_last_vote_tally: tuple[int, ...] = field(default_factory=tuple)
    zlob_last_winner_option_indexes: tuple[int, ...] = field(default_factory=tuple)
    bunker_seats: int = 0
    bunker_seats_tuned: bool = False
    bunker_catastrophe: str | None = None
    bunker_condition: str | None = None
    bunker_cards: dict[int, BunkerCard] = field(default_factory=dict)
    bunker_revealed_fields: dict[int, set[str]] = field(default_factory=dict)
    bunker_reveal_order: tuple[int, ...] = field(default_factory=tuple)
    bunker_round_reveal_user_ids: tuple[int, ...] = field(default_factory=tuple)
    bunker_reveal_cursor: int = 0
    bunker_current_actor_user_id: int | None = None
    bunker_votes: dict[int, int] = field(default_factory=dict)
    bunker_pool_overflow_fields: set[str] = field(default_factory=set)
    bunker_last_eliminated_user_id: int | None = None

    reveal_eliminated_role: bool = True

    mafia_votes: dict[int, int] = field(default_factory=dict)
    sheriff_checks: dict[int, int] = field(default_factory=dict)
    inspector_checks: dict[int, int] = field(default_factory=dict)
    doctor_saves: dict[int, int] = field(default_factory=dict)
    escort_blocks: dict[int, int] = field(default_factory=dict)
    bodyguard_protects: dict[int, int] = field(default_factory=dict)
    journalist_checks: dict[int, tuple[int, int]] = field(default_factory=dict)
    journalist_first_pick: dict[int, int] = field(default_factory=dict)
    priest_protects: dict[int, int] = field(default_factory=dict)
    psychologist_checks: dict[int, int] = field(default_factory=dict)
    detective_checks: dict[int, int] = field(default_factory=dict)
    don_checks: dict[int, int] = field(default_factory=dict)
    lawyer_targets: dict[int, int] = field(default_factory=dict)
    poisoner_targets: dict[int, int] = field(default_factory=dict)
    reanimator_targets: dict[int, int] = field(default_factory=dict)
    maniac_kills: dict[int, int] = field(default_factory=dict)
    serial_kills: dict[int, int] = field(default_factory=dict)
    witch_save_targets: dict[int, int] = field(default_factory=dict)
    witch_kill_targets: dict[int, int] = field(default_factory=dict)
    vampire_bites: dict[int, int] = field(default_factory=dict)
    bomber_mines: dict[int, int] = field(default_factory=dict)
    veteran_alerts: set[int] = field(default_factory=set)
    veteran_used: set[int] = field(default_factory=set)
    reanimator_used: set[int] = field(default_factory=set)
    witch_save_used: set[int] = field(default_factory=set)
    witch_kill_used: set[int] = field(default_factory=set)
    child_revealed: set[int] = field(default_factory=set)
    child_revealed_announced: set[int] = field(default_factory=set)
    poisoned_players: dict[int, int] = field(default_factory=dict)
    mined_players: set[int] = field(default_factory=set)
    vampire_team: set[int] = field(default_factory=set)
    last_night_killers: set[int] = field(default_factory=set)
    last_night_movers: set[int] = field(default_factory=set)
    last_night_hidden_movers: set[int] = field(default_factory=set)
    mafia_private_reports: dict[int, str] = field(default_factory=dict)
    day_vote_immune_user_id: int | None = None
    day_votes: dict[int, int] = field(default_factory=dict)
    mafia_execution_candidate_user_id: int | None = None
    execution_confirm_votes: dict[int, bool] = field(default_factory=dict)

    status: GameStatus = "lobby"
    phase: GamePhase = "lobby"
    round_no: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    phase_started_at: datetime | None = None
    message_id: int | None = None
    execution_confirm_message_id: int | None = None
    quiz_feed_message_id: int | None = None
    winner_text: str | None = None
    economy_rewards_granted: bool = False


class GameStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_id: dict[str, GroupGame] = {}
        self._active_by_chat: dict[int, str] = {}

    async def create_lobby(
        self,
        *,
        kind: GameKind,
        chat_id: int,
        chat_title: str | None,
        owner_user_id: int,
        owner_label: str,
        reveal_eliminated_role: bool,
        spy_category: str | None = None,
        whoami_category: str | None = None,
        zlob_category: str | None = None,
        actions_18_enabled: bool = True,
    ) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            active_id = self._active_by_chat.get(chat_id)
            if active_id:
                active_game = self._by_id.get(active_id)
                if active_game and active_game.status in {"lobby", "started"}:
                    return None, "В этом чате уже есть активная игра. Завершите её перед стартом новой."

            selected_spy_category = None
            if kind == "spy":
                if not SPY_CATEGORIES:
                    return None, "Не удалось загрузить категории локаций для игры «Шпион»"
                selected_spy_category = _normalize_spy_category(spy_category)
                if selected_spy_category is not None and selected_spy_category not in SPY_LOCATIONS_BY_CATEGORY:
                    return None, "Неизвестная тема для игры «Шпион»"

            selected_whoami_category = None
            if kind == "whoami":
                if not WHOAMI_CARDS_BY_CATEGORY:
                    return None, "Не удалось загрузить банк карточек для игры «Кто я»"
                selected_whoami_category = _normalize_whoami_category(whoami_category)
                if selected_whoami_category is not None and selected_whoami_category not in WHOAMI_CARDS_BY_CATEGORY:
                    return None, "Неизвестная тема для игры «Кто я»"
                if not is_whoami_category_allowed(selected_whoami_category, actions_18_enabled=actions_18_enabled):
                    return None, "18+ темы для игры «Кто я» отключены в этом чате"

            selected_zlob_category = None
            if kind == "zlobcards":
                if not ZLOBCARDS_CATEGORIES:
                    return None, "Не удалось загрузить банк карточек для игры «500 Злобных Карт»"
                selected_zlob_category = _normalize_zlob_category(zlob_category)
                if selected_zlob_category is not None and selected_zlob_category not in ZLOBCARDS_CATEGORIES:
                    return None, "Неизвестная тема для игры «500 Злобных Карт»"
                if not is_zlob_category_allowed(selected_zlob_category, actions_18_enabled=actions_18_enabled):
                    return None, "18+ темы для игры «500 Злобных Карт» отключены в этом чате"

            game_id = uuid4().hex[:10]
            game = GroupGame(
                game_id=game_id,
                kind=kind,
                chat_id=chat_id,
                chat_title=chat_title,
                owner_user_id=owner_user_id,
                players={owner_user_id: owner_label},
                reveal_eliminated_role=reveal_eliminated_role,
                spy_category=selected_spy_category,
                whoami_category=selected_whoami_category,
                zlob_category=selected_zlob_category,
            )
            if kind == "bunker":
                game.bunker_seats = self._default_bunker_seats(players_count=len(game.players))
                game.bunker_seats_tuned = False
            self._by_id[game_id] = game
            self._active_by_chat[chat_id] = game_id
            return game, None

    async def set_message_id(self, *, game_id: str, message_id: int) -> GroupGame | None:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None
            game.message_id = message_id
            return game

    async def set_execution_confirm_message_id(self, *, game_id: str, message_id: int | None) -> GroupGame | None:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None
            game.execution_confirm_message_id = message_id
            return game

    async def set_quiz_feed_message_id(self, *, game_id: str, message_id: int | None) -> GroupGame | None:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None
            game.quiz_feed_message_id = message_id
            return game

    async def set_player_label(self, *, chat_id: int, user_id: int, user_label: str) -> GroupGame | None:
        async with self._lock:
            active_id = self._active_by_chat.get(chat_id)
            if active_id is None:
                return None

            game = self._by_id.get(active_id)
            if game is None or game.status == "finished":
                return None
            if user_id not in game.players:
                return game

            game.players[user_id] = user_label
            return game

    async def set_mafia_reveal_eliminated_role(self, *, game_id: str, reveal_eliminated_role: bool) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"
            if game.kind != "mafia":
                return game, "Настройка доступна только для мафии"
            if game.status != "lobby":
                return game, "Настройку можно менять только в лобби"

            game.reveal_eliminated_role = reveal_eliminated_role
            return game, None

    async def set_bred_rounds(self, *, game_id: str, rounds: int) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"
            if game.kind != "bredovukha":
                return game, "Настройка доступна только для «Бредовухи»"
            if game.status != "lobby":
                return game, "Настройку можно менять только в лобби"
            if rounds < BRED_MIN_ROUNDS:
                return game, f"Раундов должно быть минимум {BRED_MIN_ROUNDS}"
            if rounds < len(game.players):
                return game, f"Раундов должно быть не меньше количества игроков: {len(game.players)}"

            game.bred_rounds = rounds
            return game, None

    async def set_zlob_rounds(self, *, game_id: str, rounds: int) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"
            if game.kind != "zlobcards":
                return game, "Настройка доступна только для «500 Злобных Карт»"
            if game.status != "lobby":
                return game, "Настройку можно менять только в лобби"
            if rounds < ZLOBCARDS_MIN_ROUNDS:
                return game, f"Раундов должно быть минимум {ZLOBCARDS_MIN_ROUNDS}"
            if rounds > ZLOBCARDS_MAX_ROUNDS:
                return game, f"Раундов должно быть максимум {ZLOBCARDS_MAX_ROUNDS}"

            game.zlob_rounds = rounds
            return game, None

    async def set_zlob_target_score(self, *, game_id: str, target_score: int) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"
            if game.kind != "zlobcards":
                return game, "Настройка доступна только для «500 Злобных Карт»"
            if game.status != "lobby":
                return game, "Настройку можно менять только в лобби"
            if target_score < ZLOBCARDS_MIN_TARGET_SCORE:
                return game, f"Цель по очкам должна быть минимум {ZLOBCARDS_MIN_TARGET_SCORE}"
            if target_score > ZLOBCARDS_MAX_TARGET_SCORE:
                return game, f"Цель по очкам должна быть максимум {ZLOBCARDS_MAX_TARGET_SCORE}"

            game.zlob_target_score = target_score
            return game, None

    async def set_bunker_seats(self, *, game_id: str, seats: int) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"
            if game.kind != "bunker":
                return game, "Настройка доступна только для «Бункера»"
            if game.status != "lobby":
                return game, "Настройку можно менять только в лобби"

            players_count = len(game.players)
            if seats < 2:
                return game, "Мест в бункере должно быть минимум 2"
            if players_count > 0 and seats >= players_count:
                return game, f"Мест должно быть меньше числа игроков ({players_count})"

            game.bunker_seats = seats
            game.bunker_seats_tuned = True
            return game, None

    async def set_spy_category(
        self,
        *,
        game_id: str,
        category: str | None,
    ) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"
            if game.kind != "spy":
                return game, "Настройка доступна только для «Шпиона»"
            if game.status != "lobby":
                return game, "Тему можно менять только в лобби"
            if not SPY_CATEGORIES:
                return game, "Не удалось загрузить темы для игры «Шпион»"

            selected_category = _normalize_spy_category(category)
            if selected_category is not None and selected_category not in SPY_LOCATIONS_BY_CATEGORY:
                return game, "Неизвестная тема для игры «Шпион»"

            game.spy_category = selected_category
            return game, None

    async def cycle_spy_category(
        self,
        *,
        game_id: str,
    ) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"
            if game.kind != "spy":
                return game, "Настройка доступна только для «Шпиона»"
            if game.status != "lobby":
                return game, "Тему можно менять только в лобби"
            if not SPY_CATEGORIES:
                return game, "Не удалось загрузить темы для игры «Шпион»"

            options: list[str | None] = [None, *SPY_CATEGORIES]
            try:
                current_index = options.index(game.spy_category)
            except ValueError:
                current_index = 0
            game.spy_category = options[(current_index + 1) % len(options)]
            return game, None

    async def set_whoami_category(
        self,
        *,
        game_id: str,
        category: str | None,
        actions_18_enabled: bool = True,
    ) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"
            if game.kind != "whoami":
                return game, "Настройка доступна только для «Кто я»"
            if game.status != "lobby":
                return game, "Категорию можно менять только в лобби"
            if not WHOAMI_CATEGORIES:
                return game, "Не удалось загрузить категории для игры «Кто я»"

            selected_category = _normalize_whoami_category(category)
            if selected_category is not None and selected_category not in WHOAMI_CARDS_BY_CATEGORY:
                return game, "Неизвестная тема для игры «Кто я»"
            if not is_whoami_category_allowed(selected_category, actions_18_enabled=actions_18_enabled):
                return game, "18+ темы для игры «Кто я» отключены в этом чате"

            game.whoami_category = selected_category
            return game, None

    async def cycle_whoami_category(
        self,
        *,
        game_id: str,
        actions_18_enabled: bool = True,
    ) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"
            if game.kind != "whoami":
                return game, "Настройка доступна только для «Кто я»"
            if game.status != "lobby":
                return game, "Категорию можно менять только в лобби"
            if not WHOAMI_CATEGORIES:
                return game, "Не удалось загрузить категории для игры «Кто я»"

            allowed_categories = allowed_whoami_categories(actions_18_enabled=actions_18_enabled)
            if not allowed_categories:
                return game, "Не удалось загрузить разрешённые категории для игры «Кто я»"

            options: list[str | None] = [None, *allowed_categories]
            try:
                current_index = options.index(game.whoami_category)
            except ValueError:
                current_index = 0
            game.whoami_category = options[(current_index + 1) % len(options)]
            return game, None

    async def set_zlob_category(
        self,
        *,
        game_id: str,
        category: str | None,
        actions_18_enabled: bool = True,
    ) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"
            if game.kind != "zlobcards":
                return game, "Настройка доступна только для «500 Злобных Карт»"
            if game.status != "lobby":
                return game, "Категорию можно менять только в лобби"
            if not ZLOBCARDS_CATEGORIES:
                return game, "Не удалось загрузить категории для игры «500 Злобных Карт»"

            selected_category = _normalize_zlob_category(category)
            if selected_category is not None and selected_category not in ZLOBCARDS_CATEGORIES:
                return game, "Неизвестная тема для игры «500 Злобных Карт»"
            if not is_zlob_category_allowed(selected_category, actions_18_enabled=actions_18_enabled):
                return game, "18+ темы для игры «500 Злобных Карт» отключены в этом чате"

            game.zlob_category = selected_category
            return game, None

    async def cycle_zlob_category(
        self,
        *,
        game_id: str,
        actions_18_enabled: bool = True,
    ) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"
            if game.kind != "zlobcards":
                return game, "Настройка доступна только для «500 Злобных Карт»"
            if game.status != "lobby":
                return game, "Категорию можно менять только в лобби"
            if not ZLOBCARDS_CATEGORIES:
                return game, "Не удалось загрузить категории для игры «500 Злобных Карт»"

            allowed_categories = allowed_zlob_categories(actions_18_enabled=actions_18_enabled)
            if not allowed_categories:
                return game, "Не удалось загрузить разрешённые категории для игры «500 Злобных Карт»"

            options: list[str | None] = [None, *allowed_categories]
            try:
                current_index = options.index(game.zlob_category)
            except ValueError:
                current_index = 0
            game.zlob_category = options[(current_index + 1) % len(options)]
            return game, None

    async def get_game(self, game_id: str) -> GroupGame | None:
        async with self._lock:
            return self._by_id.get(game_id)

    async def get_active_game_for_chat(self, *, chat_id: int) -> GroupGame | None:
        async with self._lock:
            active_id = self._active_by_chat.get(chat_id)
            if active_id is None:
                return None
            game = self._by_id.get(active_id)
            if game is None or game.status == "finished":
                return None
            return game

    async def list_active_games(self, *, chat_ids: set[int] | None = None) -> list[GroupGame]:
        async with self._lock:
            games: list[GroupGame] = []
            for game_id in self._active_by_chat.values():
                game = self._by_id.get(game_id)
                if game is None or game.status == "finished":
                    continue
                if chat_ids is not None and game.chat_id not in chat_ids:
                    continue
                games.append(game)

            games.sort(
                key=lambda item: item.started_at or item.created_at,
                reverse=True,
            )
            return games

    async def list_recent_games_for_user(
        self,
        *,
        user_id: int,
        chat_ids: set[int] | None = None,
        limit: int = 6,
    ) -> list[GroupGame]:
        async with self._lock:
            games = [
                game
                for game in self._by_id.values()
                if (
                    game.status == "finished"
                    and user_id in game.players
                    and (chat_ids is None or game.chat_id in chat_ids)
                )
            ]
            games.sort(
                key=lambda item: item.started_at or item.created_at,
                reverse=True,
            )
            return games[: max(1, limit)]

    async def migrate_chat_id(self, *, old_chat_id: int, new_chat_id: int, new_chat_title: str | None = None) -> int:
        if old_chat_id == new_chat_id:
            return 0

        async with self._lock:
            migrated = 0
            old_active_id = self._active_by_chat.pop(old_chat_id, None)
            if old_active_id is not None and new_chat_id not in self._active_by_chat:
                self._active_by_chat[new_chat_id] = old_active_id

            for game in self._by_id.values():
                if game.chat_id != old_chat_id:
                    continue
                game.chat_id = new_chat_id
                if new_chat_title is not None:
                    game.chat_title = new_chat_title
                if game.status in {"lobby", "started"} and new_chat_id not in self._active_by_chat:
                    self._active_by_chat[new_chat_id] = game.game_id
                migrated += 1

            return migrated

    async def join(self, *, game_id: str, user_id: int, user_label: str) -> tuple[GroupGame | None, str]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "not_found"
            if game.status != "lobby":
                return game, "not_lobby"

            if user_id in game.players:
                return game, "already_joined"

            game.players[user_id] = user_label
            if game.kind == "bredovukha" and game.bred_rounds < len(game.players):
                game.bred_rounds = len(game.players)
            if game.kind == "bunker" and not game.bunker_seats_tuned:
                game.bunker_seats = self._default_bunker_seats(players_count=len(game.players))
            return game, "joined"

    async def start(self, *, game_id: str, actions_18_enabled: bool = True) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"

            definition = GAME_DEFINITIONS[game.kind]
            if game.status != "lobby":
                return game, "Игра уже запущена"
            if len(game.players) < definition.min_players:
                return game, f"Для старта нужно минимум {definition.min_players} игроков"

            def _restore_lobby(message: str) -> tuple[GroupGame, str]:
                game.started_at = None
                game.phase_started_at = None
                game.status = "lobby"
                game.phase = "lobby"
                return game, message

            game.started_at = datetime.now(timezone.utc)
            game.phase_started_at = game.started_at
            game.status = "started"

            if game.kind == "spy":
                if not SPY_CATEGORIES:
                    return _restore_lobby("Не удалось загрузить банк локаций для игры «Шпион»")
                category = game.spy_category
                if category not in SPY_LOCATIONS_BY_CATEGORY:
                    category = random.choice(SPY_CATEGORIES)
                locations_pool = list(SPY_LOCATIONS_BY_CATEGORY.get(category, ()))
                if not locations_pool:
                    return _restore_lobby(f"В теме «{category}» не осталось доступных локаций для игры «Шпион»")
                player_ids = list(game.players.keys())
                random.shuffle(player_ids)
                spies_count = 1 if len(player_ids) <= 7 else 2
                spies = set(player_ids[:spies_count])
                game.roles = {player_id: ("Шпион" if player_id in spies else "Мирный") for player_id in player_ids}
                game.spy_category = category
                game.spy_location = random.choice(locations_pool)
                game.spy_votes.clear()
                game.phase = "freeplay"
                game.round_no = 1
                return game, None

            if game.kind == "whoami":
                if not WHOAMI_CATEGORIES:
                    return _restore_lobby("Не удалось загрузить банк карточек для игры «Кто я»")

                allowed_categories = allowed_whoami_categories(actions_18_enabled=actions_18_enabled)
                if not allowed_categories:
                    return _restore_lobby("Не удалось загрузить разрешённые категории для игры «Кто я»")

                category = game.whoami_category
                if category is not None and not is_whoami_category_allowed(category, actions_18_enabled=actions_18_enabled):
                    return _restore_lobby("18+ темы для игры «Кто я» отключены в этом чате")
                if category not in WHOAMI_CARDS_BY_CATEGORY:
                    category = random.choice(allowed_categories)

                cards_pool = list(WHOAMI_CARDS_BY_CATEGORY.get(category, ()))
                if len(cards_pool) < len(game.players):
                    return _restore_lobby(f"В категории «{category}» недостаточно карточек для {len(game.players)} игроков")

                random.shuffle(cards_pool)
                player_ids = list(game.players.keys())
                random.shuffle(player_ids)
                turn_order = list(player_ids)
                random.shuffle(turn_order)

                game.roles = {
                    player_id: cards_pool[index]
                    for index, player_id in enumerate(player_ids)
                }
                game.whoami_category = category
                game.whoami_turn_order = tuple(turn_order)
                game.whoami_current_actor_index = 0
                game.whoami_current_actor_user_id = turn_order[0] if turn_order else None
                game.whoami_pending_question_text = None
                game.whoami_pending_question_user_id = None
                game.whoami_history.clear()
                game.whoami_solved_user_ids.clear()
                game.whoami_finish_order.clear()
                game.whoami_winner_user_id = None
                game.phase = "whoami_ask"
                game.round_no = 1
                return game, None

            if game.kind == "zlobcards":
                if not ZLOBCARDS_CATEGORIES:
                    return _restore_lobby("Не удалось загрузить банк карточек для игры «500 Злобных Карт»")

                allowed_categories = allowed_zlob_categories(actions_18_enabled=actions_18_enabled)
                if not allowed_categories:
                    return _restore_lobby("Не удалось загрузить разрешённые категории для игры «500 Злобных Карт»")

                category = game.zlob_category
                if category is not None and not is_zlob_category_allowed(category, actions_18_enabled=actions_18_enabled):
                    return _restore_lobby("18+ темы для игры «500 Злобных Карт» отключены в этом чате")
                if category not in ZLOBCARDS_WHITE_BY_CATEGORY or category not in ZLOBCARDS_BLACK_BY_CATEGORY:
                    category = random.choice(allowed_categories)

                white_cards = list(ZLOBCARDS_WHITE_BY_CATEGORY.get(category, ()))
                black_cards = list(ZLOBCARDS_BLACK_BY_CATEGORY.get(category, ()))
                if len(white_cards) < len(game.players):
                    return _restore_lobby("Недостаточно белых карточек для текущего числа игроков")
                if not black_cards:
                    return _restore_lobby("Не удалось подготовить чёрные карточки для раунда")

                random.shuffle(white_cards)
                random.shuffle(black_cards)

                game.zlob_category = category
                game.zlob_rounds = min(max(game.zlob_rounds, ZLOBCARDS_MIN_ROUNDS), ZLOBCARDS_MAX_ROUNDS)
                game.zlob_target_score = min(
                    max(game.zlob_target_score, ZLOBCARDS_MIN_TARGET_SCORE),
                    ZLOBCARDS_MAX_TARGET_SCORE,
                )
                game.zlob_white_deck = white_cards
                game.zlob_white_discard.clear()
                game.zlob_black_deck = black_cards
                game.zlob_black_discard.clear()
                game.zlob_hands.clear()
                game.zlob_submissions.clear()
                game.zlob_options = ()
                game.zlob_option_owner_user_ids = ()
                game.zlob_votes.clear()
                game.zlob_scores = {player_id: 0 for player_id in game.players}
                game.zlob_last_round_no = None
                game.zlob_last_black_text = None
                game.zlob_last_black_slots = 1
                game.zlob_last_options = ()
                game.zlob_last_option_owner_user_ids = ()
                game.zlob_last_vote_tally = ()
                game.zlob_last_winner_option_indexes = ()
                game.round_no = 1

                for player_id in game.players:
                    game.zlob_hands[player_id] = tuple(self._zlob_draw_white_cards(game, count=ZLOBCARDS_HAND_SIZE))

                opened, error = self._prepare_zlob_private_phase(game)
                if not opened:
                    return _restore_lobby(error or "Не удалось открыть первый раунд")
                return game, None

            if game.kind == "mafia":
                self._assign_mafia_roles(game)
                game.alive_player_ids = set(game.players.keys())
                game.phase = "night"
                game.round_no = 1
                game.mafia_votes.clear()
                game.sheriff_checks.clear()
                game.inspector_checks.clear()
                game.doctor_saves.clear()
                game.escort_blocks.clear()
                game.bodyguard_protects.clear()
                game.journalist_checks.clear()
                game.journalist_first_pick.clear()
                game.priest_protects.clear()
                game.psychologist_checks.clear()
                game.detective_checks.clear()
                game.don_checks.clear()
                game.lawyer_targets.clear()
                game.poisoner_targets.clear()
                game.reanimator_targets.clear()
                game.maniac_kills.clear()
                game.serial_kills.clear()
                game.witch_save_targets.clear()
                game.witch_kill_targets.clear()
                game.vampire_bites.clear()
                game.bomber_mines.clear()
                game.veteran_alerts.clear()
                game.veteran_used.clear()
                game.reanimator_used.clear()
                game.witch_save_used.clear()
                game.witch_kill_used.clear()
                game.child_revealed.clear()
                game.child_revealed_announced.clear()
                game.poisoned_players.clear()
                game.mined_players.clear()
                game.vampire_team.clear()
                game.last_night_killers.clear()
                game.last_night_movers.clear()
                game.last_night_hidden_movers.clear()
                game.mafia_private_reports.clear()
                game.day_vote_immune_user_id = None
                game.day_votes.clear()
                game.mafia_execution_candidate_user_id = None
                game.execution_confirm_votes.clear()
                return game, None

            if game.kind == "dice":
                game.dice_scores.clear()
                game.phase = "freeplay"
                game.round_no = 1
                return game, None

            if game.kind == "number":
                game.phase = "freeplay"
                game.round_no = 1
                game.number_secret = random.randint(NUMBER_GUESS_MIN, NUMBER_GUESS_MAX)
                game.number_attempts.clear()
                game.number_attempts_total = 0
                return game, None

            if game.kind == "quiz":
                questions = self._build_quiz_questions(rounds=QUIZ_ROUNDS)
                if not questions:
                    return game, "Не удалось подготовить вопросы викторины"

                game.phase = "freeplay"
                game.round_no = 1
                game.quiz_questions = questions
                game.quiz_current_question_index = 0
                game.quiz_answers.clear()
                game.quiz_scores = {player_id: 0 for player_id in game.players}
                game.quiz_feed_message_id = None
                return game, None

            if game.kind == "bredovukha":
                if not BRED_CATEGORIES:
                    return game, "Не удалось загрузить банк вопросов «Бредовухи»"
                if game.bred_rounds < len(game.players):
                    return game, f"Раундов должно быть не меньше количества игроков: {len(game.players)}"

                selector_order = self._build_bred_selector_order(game)
                if not selector_order:
                    return game, "Не удалось определить порядок выбора категорий"

                game.bred_rounds = len(selector_order)
                game.phase = "category_pick"
                game.round_no = 1
                game.bred_current_category = None
                game.bred_current_selector_user_id = selector_order[0]
                game.bred_selector_user_ids_by_round = selector_order
                game.bred_used_question_keys.clear()
                game.bred_category_options = self._pick_bred_category_options(game)
                if not game.bred_category_options:
                    return game, "Не удалось выбрать категории для раунда"
                game.bred_question_prompt = None
                game.bred_correct_answer = None
                game.bred_fact_text = None
                game.bred_lies.clear()
                game.bred_options = ()
                game.bred_option_owner_user_ids = ()
                game.bred_votes.clear()
                game.bred_scores = {player_id: 0 for player_id in game.players}
                game.bred_last_round_no = None
                game.bred_last_category = None
                game.bred_last_question_prompt = None
                game.bred_last_correct_answer = None
                game.bred_last_fact_text = None
                game.bred_last_options = ()
                game.bred_last_option_owner_user_ids = ()
                game.bred_last_vote_tally = ()
                game.bred_last_correct_option_index = None
                return game, None

            if game.kind == "bunker":
                if any(not BUNKER_DATA.get(key) for key in BUNKER_DATA_KEYS_REQUIRED):
                    return game, "Не удалось загрузить карточки «Бункера»"

                players_count = len(game.players)
                if not game.bunker_seats_tuned:
                    game.bunker_seats = self._default_bunker_seats(players_count=players_count)
                if game.bunker_seats < 2:
                    return game, "В «Бункере» должно быть минимум 2 места"
                if game.bunker_seats >= players_count:
                    return game, "Мест в бункере должно быть меньше количества игроков"

                cards, overflow_fields, build_error = self._build_bunker_cards(game)
                if build_error:
                    return game, build_error

                game.alive_player_ids = set(game.players.keys())
                game.round_no = 1
                game.bunker_cards = cards
                game.bunker_revealed_fields = {player_id: set() for player_id in game.players}
                reveal_order = list(game.players.keys())
                random.shuffle(reveal_order)
                game.bunker_reveal_order = tuple(reveal_order)
                game.bunker_round_reveal_user_ids = ()
                game.bunker_reveal_cursor = 0
                game.bunker_current_actor_user_id = None
                game.bunker_votes.clear()
                game.bunker_pool_overflow_fields = set(overflow_fields)
                game.bunker_last_eliminated_user_id = None
                game.bunker_catastrophe = random.choice(BUNKER_DATA["catastrophes"])
                game.bunker_condition = random.choice(BUNKER_DATA["bunker_conditions"])

                self._prepare_bunker_reveal_phase(game)
                return game, None

            return game, "Неизвестный тип игры"

    async def finish(self, *, game_id: str, winner_text: str | None = None) -> GroupGame | None:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None

            game.status = "finished"
            game.phase = "finished"
            game.winner_text = winner_text
            game.execution_confirm_message_id = None
            game.quiz_feed_message_id = None
            self._active_by_chat.pop(game.chat_id, None)
            return game

    async def get_role(self, *, game_id: str, user_id: int) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None
            return game, game.roles.get(user_id)

    async def get_latest_role_game_for_user(self, *, user_id: int) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            candidates = [
                game
                for game in self._by_id.values()
                if (
                    game.kind in {"spy", "mafia", "whoami"}
                    and game.status == "started"
                    and game.roles
                    and user_id in game.roles
                )
            ]
            if not candidates:
                return None, None

            candidates.sort(key=lambda game: game.started_at or game.created_at, reverse=True)
            game = candidates[0]
            return game, game.roles.get(user_id)

    async def get_latest_bunker_game_for_user(self, *, user_id: int) -> GroupGame | None:
        async with self._lock:
            candidates = [
                game
                for game in self._by_id.values()
                if game.kind == "bunker" and game.status == "started" and user_id in game.players
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda game: game.started_at or game.created_at, reverse=True)
            return candidates[0]

    async def get_latest_bred_submission_game_for_user(self, *, user_id: int) -> GroupGame | None:
        async with self._lock:
            candidates = [
                game
                for game in self._by_id.values()
                if (
                    game.kind == "bredovukha"
                    and game.status == "started"
                    and game.phase == "private_answers"
                    and user_id in game.players
                )
            ]
            if not candidates:
                return None

            candidates.sort(key=lambda game: game.started_at or game.created_at, reverse=True)
            return candidates[0]

    async def get_latest_zlob_submission_game_for_user(self, *, user_id: int) -> GroupGame | None:
        async with self._lock:
            candidates = [
                game
                for game in self._by_id.values()
                if (
                    game.kind == "zlobcards"
                    and game.status == "started"
                    and game.phase == "private_answers"
                    and user_id in game.players
                )
            ]
            if not candidates:
                return None

            candidates.sort(key=lambda game: game.started_at or game.created_at, reverse=True)
            return candidates[0]

    async def bred_get_category_snapshot(self, *, game_id: str) -> tuple[GroupGame | None, int | None, tuple[str, ...]]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, ()
            return game, game.bred_current_selector_user_id, game.bred_category_options

    async def bred_choose_category(
        self,
        *,
        game_id: str,
        actor_user_id: int,
        option_index: int,
    ) -> tuple[GroupGame | None, str | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "bredovukha":
                return game, None, "Это не «Бредовуха»"
            if game.status != "started" or game.phase != "category_pick":
                return game, None, "Сейчас не этап выбора категории"
            if actor_user_id != game.bred_current_selector_user_id:
                return game, None, "Сейчас категорию выбирает другой игрок"
            if option_index < 0 or option_index >= len(game.bred_category_options):
                return game, None, "Некорректная категория"

            category = game.bred_category_options[option_index]
            question = self._pick_bred_question(game, category=category)
            if question is None:
                return game, None, "Для выбранной категории нет доступных вопросов"

            game.bred_current_category = category
            game.bred_question_prompt = question.prompt_with_blank
            game.bred_correct_answer = question.correct_answer
            game.bred_fact_text = self._build_bred_fact_text(question)
            game.bred_lies.clear()
            game.bred_options = ()
            game.bred_option_owner_user_ids = ()
            game.bred_votes.clear()
            game.phase = "private_answers"
            game.phase_started_at = datetime.now(timezone.utc)
            return game, category, None

    async def bred_force_pick_category(self, *, game_id: str) -> tuple[GroupGame | None, str | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "bredovukha":
                return game, None, "Это не «Бредовуха»"
            if game.status != "started" or game.phase != "category_pick":
                return game, None, "Сейчас не этап выбора категории"
            if not game.bred_category_options:
                return game, None, "Нет доступных категорий"

            option_index = random.randrange(len(game.bred_category_options))
            category = game.bred_category_options[option_index]
            question = self._pick_bred_question(game, category=category)
            if question is None:
                return game, None, "Не удалось выбрать вопрос по категории"

            game.bred_current_category = category
            game.bred_question_prompt = question.prompt_with_blank
            game.bred_correct_answer = question.correct_answer
            game.bred_fact_text = self._build_bred_fact_text(question)
            game.bred_lies.clear()
            game.bred_options = ()
            game.bred_option_owner_user_ids = ()
            game.bred_votes.clear()
            game.phase = "private_answers"
            game.phase_started_at = datetime.now(timezone.utc)
            return game, category, None

    async def spy_register_vote(
        self,
        *,
        game_id: str,
        voter_user_id: int,
        target_user_id: int,
    ) -> tuple[GroupGame | None, SpyVoteResolution | None, int | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, None, "Игра не найдена"
            if game.kind != "spy":
                return game, None, None, "Это не игра «Шпион»"
            if game.status != "started" or game.phase != "freeplay":
                return game, None, None, "Игра уже завершена или неактивна"
            if voter_user_id not in game.players:
                return game, None, None, "Вы не участник этой игры"
            if target_user_id not in game.players:
                return game, None, None, "Такого игрока нет в этом лобби"

            previous_target_user_id = game.spy_votes.get(voter_user_id)
            game.spy_votes[voter_user_id] = target_user_id

            vote_counts: dict[int, int] = {}
            for _, voted_target in game.spy_votes.items():
                if voted_target in game.players:
                    vote_counts[voted_target] = vote_counts.get(voted_target, 0) + 1

            total_players = len(game.players)
            voted_count = len(game.spy_votes)
            majority_needed = total_players // 2 + 1

            if not vote_counts:
                return game, None, previous_target_user_id, None

            top_votes = max(vote_counts.values())
            top_targets = [user_id for user_id, votes in vote_counts.items() if votes == top_votes]

            should_finish = voted_count == total_players or top_votes >= majority_needed
            if not should_finish:
                return game, None, previous_target_user_id, None

            tie = len(top_targets) != 1
            candidate_user_id: int | None = None
            candidate_votes = top_votes
            candidate_is_spy: bool | None = None
            winner_text: str

            if tie:
                winner_text = "Победа шпиона: мирные не смогли выбрать подозреваемого."
            else:
                candidate_user_id = top_targets[0]
                candidate_is_spy = game.roles.get(candidate_user_id) == "Шпион"
                candidate_label = game.players.get(candidate_user_id, f"user:{candidate_user_id}")
                if candidate_is_spy:
                    winner_text = f"Победа мирных: {candidate_label} оказался шпионом."
                else:
                    winner_text = f"Победа шпиона: стол выгнал мирного {candidate_label}."

            game.status = "finished"
            game.phase = "finished"
            game.winner_text = winner_text
            self._active_by_chat.pop(game.chat_id, None)

            resolution = SpyVoteResolution(
                candidate_user_id=candidate_user_id,
                candidate_user_label=game.players.get(candidate_user_id) if candidate_user_id is not None else None,
                candidate_votes=candidate_votes,
                voted_count=voted_count,
                total_players=total_players,
                tie=tie,
                candidate_is_spy=candidate_is_spy,
                winner_text=winner_text,
            )
            return game, resolution, previous_target_user_id, None

    async def spy_guess_location(
        self,
        *,
        game_id: str,
        actor_user_id: int,
        guessed_location: str,
    ) -> tuple[GroupGame | None, SpyGuessResolution | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "spy":
                return game, None, "Это не игра «Шпион»"
            if game.status != "started" or game.phase != "freeplay":
                return game, None, "Игра уже завершена или неактивна"
            if actor_user_id not in game.players:
                return game, None, "Вы не участник этой игры"
            if game.roles.get(actor_user_id) != "Шпион":
                return game, None, "Назвать локацию может только шпион"

            guess = guessed_location.strip()
            if not guess:
                return game, None, "Введите локацию"
            if not game.spy_location:
                return game, None, "Локация для этой партии не определена"

            guessed_correctly = self._normalize_spy_location(guess) == self._normalize_spy_location(game.spy_location)
            spy_label = game.players.get(actor_user_id, f"user:{actor_user_id}")
            if guessed_correctly:
                winner_text = f"Победа шпиона: {spy_label} угадал локацию."
            else:
                winner_text = f"Победа мирных: {spy_label} ошибся с локацией."

            game.status = "finished"
            game.phase = "finished"
            game.winner_text = winner_text
            self._active_by_chat.pop(game.chat_id, None)

            resolution = SpyGuessResolution(
                spy_user_id=actor_user_id,
                spy_user_label=game.players.get(actor_user_id),
                guessed_location=guess,
                guessed_correctly=guessed_correctly,
                actual_location=game.spy_location,
                winner_text=winner_text,
            )
            return game, resolution, None

    async def spy_get_vote_snapshot(
        self,
        *,
        game_id: str,
    ) -> tuple[GroupGame | None, int, int, int | None, int]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, 0, 0, None, 0

            vote_counts: dict[int, int] = {}
            for _, voted_target in game.spy_votes.items():
                if voted_target in game.players:
                    vote_counts[voted_target] = vote_counts.get(voted_target, 0) + 1

            leader_user_id: int | None = None
            leader_votes = 0
            if vote_counts:
                leader_votes = max(vote_counts.values())
                leaders = [user_id for user_id, votes in vote_counts.items() if votes == leader_votes]
                if len(leaders) == 1:
                    leader_user_id = leaders[0]

            return game, len(game.spy_votes), len(game.players), leader_user_id, leader_votes

    async def whoami_submit_question(
        self,
        *,
        game_id: str,
        actor_user_id: int,
        question_text: str,
    ) -> tuple[GroupGame | None, WhoamiQuestionResult | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "whoami":
                return game, None, "Это не игра «Кто я»"
            if game.status != "started" or game.phase != "whoami_ask":
                return game, None, "Сейчас нельзя задавать вопрос"
            if actor_user_id not in game.players:
                return game, None, "Вы не участник этой игры"
            if actor_user_id != game.whoami_current_actor_user_id:
                return game, None, "Сейчас ход другого игрока"
            if actor_user_id in game.whoami_solved_user_ids:
                return game, None, "Вы уже разгадали карточку и больше не задаёте вопросы"

            question = re.sub(r"\s+", " ", question_text.strip())
            if len(question) < WHOAMI_MIN_QUESTION_LEN:
                return game, None, "Вопрос слишком короткий"
            if len(question) > WHOAMI_MAX_QUESTION_LEN:
                return game, None, f"Вопрос должен быть короче {WHOAMI_MAX_QUESTION_LEN} символов"
            if not question.endswith("?"):
                question = f"{question}?"

            previous_question_text = game.whoami_pending_question_text
            game.whoami_pending_question_text = question
            game.whoami_pending_question_user_id = actor_user_id
            game.phase = "whoami_answer"
            game.phase_started_at = datetime.now(timezone.utc)
            return (
                game,
                WhoamiQuestionResult(
                    previous_question_text=previous_question_text,
                    question_text=question,
                    actor_user_id=actor_user_id,
                    actor_user_label=game.players.get(actor_user_id),
                ),
                None,
            )

    async def whoami_answer_question(
        self,
        *,
        game_id: str,
        responder_user_id: int,
        answer_code: Literal["yes", "no", "unknown", "irrelevant"],
    ) -> tuple[GroupGame | None, WhoamiAnswerResolution | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "whoami":
                return game, None, "Это не игра «Кто я»"
            if game.status != "started" or game.phase != "whoami_answer":
                return game, None, "Сейчас нет активного вопроса"
            if responder_user_id not in game.players:
                return game, None, "Вы не участник этой игры"

            actor_user_id = game.whoami_current_actor_user_id
            question_text = game.whoami_pending_question_text
            if actor_user_id is None or not question_text:
                return game, None, "Активный вопрос не найден"
            if responder_user_id == actor_user_id:
                return game, None, "Игрок, задавший вопрос, не может отвечать сам себе"

            answer_label = self._whoami_answer_label(answer_code)
            game.whoami_history.append(
                WhoamiHistoryEntry(
                    actor_user_id=actor_user_id,
                    question_text=question_text,
                    answer_code=answer_code,
                    answer_label=answer_label,
                    responder_user_id=responder_user_id,
                )
            )
            self._trim_whoami_history(game)
            game.whoami_pending_question_text = None
            game.whoami_pending_question_user_id = None

            keeps_turn = answer_code == "yes"
            next_actor_user_id: int | None
            next_actor_label: str | None
            if keeps_turn:
                game.phase = "whoami_ask"
                next_actor_user_id = actor_user_id
                next_actor_label = game.players.get(actor_user_id)
            else:
                self._advance_whoami_turn(game)
                game.phase = "whoami_ask"
                next_actor_user_id = game.whoami_current_actor_user_id
                next_actor_label = game.players.get(next_actor_user_id) if next_actor_user_id is not None else None
            game.phase_started_at = datetime.now(timezone.utc)

            return (
                game,
                WhoamiAnswerResolution(
                    actor_user_id=actor_user_id,
                    actor_user_label=game.players.get(actor_user_id),
                    responder_user_id=responder_user_id,
                    responder_user_label=game.players.get(responder_user_id),
                    question_text=question_text,
                    answer_code=answer_code,
                    answer_label=answer_label,
                    keeps_turn=keeps_turn,
                    next_actor_user_id=next_actor_user_id,
                    next_actor_label=next_actor_label,
                ),
                None,
            )

    async def whoami_guess_identity(
        self,
        *,
        game_id: str,
        actor_user_id: int,
        guess_text: str,
    ) -> tuple[GroupGame | None, WhoamiGuessResolution | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "whoami":
                return game, None, "Это не игра «Кто я»"
            if game.status != "started" or game.phase != "whoami_ask":
                return game, None, "Сейчас нельзя делать догадку"
            if actor_user_id not in game.players:
                return game, None, "Вы не участник этой игры"
            if actor_user_id != game.whoami_current_actor_user_id:
                return game, None, "Сейчас ход другого игрока"
            if actor_user_id in game.whoami_solved_user_ids:
                return game, None, "Вы уже разгадали карточку и больше не ходите"

            guess = re.sub(r"\s+", " ", guess_text.strip())
            if not guess:
                return game, None, "Введите догадку"
            if len(guess) > WHOAMI_MAX_GUESS_LEN:
                return game, None, f"Догадка должна быть короче {WHOAMI_MAX_GUESS_LEN} символов"

            actual_identity = game.roles.get(actor_user_id)
            if not actual_identity:
                return game, None, "Для вас не найдена карточка"

            guessed_correctly = self._normalize_whoami_identity(guess) == self._normalize_whoami_identity(actual_identity)
            winner_text: str | None = None
            next_actor_user_id: int | None = None
            next_actor_label: str | None = None

            game.whoami_history.append(
                WhoamiHistoryEntry(
                    actor_user_id=actor_user_id,
                    guess_text=guess,
                    guessed_correctly=guessed_correctly,
                )
            )
            self._trim_whoami_history(game)

            if guessed_correctly:
                actor_label = game.players.get(actor_user_id, f"user:{actor_user_id}")
                game.whoami_pending_question_text = None
                game.whoami_pending_question_user_id = None
                game.whoami_solved_user_ids.add(actor_user_id)
                if actor_user_id not in game.whoami_finish_order:
                    game.whoami_finish_order.append(actor_user_id)
                if game.whoami_winner_user_id is None:
                    game.whoami_winner_user_id = actor_user_id

                if len(game.whoami_solved_user_ids) >= len(game.players):
                    finish_labels = ", ".join(
                        game.players.get(user_id, f"user:{user_id}") for user_id in game.whoami_finish_order
                    )
                    winner_text = (
                        "Все карточки разгаданы. "
                        f"Порядок финиша: {finish_labels}."
                    )
                    game.status = "finished"
                    game.phase = "finished"
                    game.winner_text = winner_text
                    game.whoami_current_actor_user_id = None
                    self._active_by_chat.pop(game.chat_id, None)
                else:
                    self._advance_whoami_turn(game)
                    game.phase = "whoami_ask"
                    game.phase_started_at = datetime.now(timezone.utc)
                    next_actor_user_id = game.whoami_current_actor_user_id
                    next_actor_label = game.players.get(next_actor_user_id) if next_actor_user_id is not None else None
            else:
                self._advance_whoami_turn(game)
                game.phase = "whoami_ask"
                game.phase_started_at = datetime.now(timezone.utc)
                next_actor_user_id = game.whoami_current_actor_user_id
                next_actor_label = game.players.get(next_actor_user_id) if next_actor_user_id is not None else None

            return (
                game,
                WhoamiGuessResolution(
                    actor_user_id=actor_user_id,
                    actor_user_label=game.players.get(actor_user_id),
                    guess_text=guess,
                    actual_identity=actual_identity if game.status == "finished" else None,
                    guessed_correctly=guessed_correctly,
                    finished=game.status == "finished",
                    next_actor_user_id=next_actor_user_id,
                    next_actor_label=next_actor_label,
                    winner_text=winner_text,
                ),
                None,
            )

    @staticmethod
    def _whoami_answer_label(answer_code: str) -> str:
        return {
            "yes": "Да",
            "no": "Нет",
            "unknown": "Не знаю",
            "irrelevant": "Не имеет значения",
        }.get(answer_code, "Ответ")

    @staticmethod
    def _normalize_whoami_identity(value: str) -> str:
        cleaned = re.sub(r"[^0-9a-zа-яё]+", " ", value.casefold().replace("ё", "е"))
        return " ".join(cleaned.split())

    @staticmethod
    def _normalize_spy_location(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value.strip().casefold().replace("ё", "е"))
        return cleaned

    async def dice_register_roll(
        self,
        *,
        game_id: str,
        user_id: int,
    ) -> tuple[GroupGame | None, DiceRollResult | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "dice":
                return game, None, "Это не игра «Дуэль кубиков»"
            if game.status != "started" or game.phase != "freeplay":
                return game, None, "Игра уже завершена"
            if user_id not in game.players:
                return game, None, "Вы не участник этой игры"
            if user_id in game.dice_scores:
                return game, None, "Вы уже бросили кубик в этом раунде"

            roll_value = random.randint(1, 6)
            game.dice_scores[user_id] = roll_value

            rolled_count = len(game.dice_scores)
            total_players = len(game.players)
            finished = rolled_count >= total_players
            winner_text: str | None = None

            if finished:
                winner_text = self._resolve_dice_winner_text(game)
                game.status = "finished"
                game.phase = "finished"
                game.winner_text = winner_text
                self._active_by_chat.pop(game.chat_id, None)

            return (
                game,
                DiceRollResult(
                    roller_user_id=user_id,
                    roll_value=roll_value,
                    rolled_count=rolled_count,
                    total_players=total_players,
                    finished=finished,
                    winner_text=winner_text,
                ),
                None,
            )

    async def number_register_guess(
        self,
        *,
        game_id: str,
        user_id: int,
        guess: int,
    ) -> tuple[GroupGame | None, NumberGuessResult | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "number":
                return game, None, "Это не «Угадай число»"
            if game.status != "started" or game.phase != "freeplay":
                return game, None, "Игра уже завершена или неактивна"
            if user_id not in game.players:
                return game, None, "Вы не в списке игроков этой игры"
            if guess < NUMBER_GUESS_MIN or guess > NUMBER_GUESS_MAX:
                return game, None, f"Число должно быть в диапазоне {NUMBER_GUESS_MIN}..{NUMBER_GUESS_MAX}"
            if game.number_secret is None:
                return game, None, "Секретное число не инициализировано"

            attempts_for_user = game.number_attempts.get(user_id, 0) + 1
            game.number_attempts[user_id] = attempts_for_user
            game.number_attempts_total += 1

            if guess == game.number_secret:
                winner_label = game.players.get(user_id, f"user:{user_id}")
                winner_text = (
                    f"Победил {winner_label}: число {game.number_secret} угадано "
                    f"за {attempts_for_user} личн. попыток ({game.number_attempts_total} всего)."
                )
                game.status = "finished"
                game.phase = "finished"
                game.winner_text = winner_text
                self._active_by_chat.pop(game.chat_id, None)
                return (
                    game,
                    NumberGuessResult(
                        guess=guess,
                        direction="correct",
                        attempts_for_user=attempts_for_user,
                        attempts_total=game.number_attempts_total,
                        winner_user_id=user_id,
                        winner_label=winner_label,
                        winner_text=winner_text,
                        distance_to_secret=0,
                    ),
                    None,
                )

            direction: Literal["up", "down", "correct"] = "up" if guess < game.number_secret else "down"
            return (
                game,
                NumberGuessResult(
                    guess=guess,
                    direction=direction,
                    attempts_for_user=attempts_for_user,
                    attempts_total=game.number_attempts_total,
                    winner_user_id=None,
                    winner_label=None,
                    winner_text=None,
                    distance_to_secret=abs(guess - game.number_secret),
                ),
                None,
            )

    async def quiz_submit_answer(
        self,
        *,
        game_id: str,
        user_id: int,
        option_index: int,
    ) -> tuple[GroupGame | None, QuizAnswerResult | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "quiz":
                return game, None, "Это не викторина"
            if game.status != "started" or game.phase != "freeplay":
                return game, None, "Викторина неактивна"
            if user_id not in game.players:
                return game, None, "Вы не участник этой викторины"

            question = self._current_quiz_question(game)
            if question is None:
                return game, None, "Вопрос недоступен"

            if option_index < 0 or option_index >= len(question.options):
                return game, None, "Некорректный вариант ответа"

            previous_answer_index = game.quiz_answers.get(user_id)
            game.quiz_answers[user_id] = option_index
            answered_count = len({player_id for player_id in game.quiz_answers if player_id in game.players})
            total_players = len(game.players)
            return (
                game,
                QuizAnswerResult(
                    previous_answer_index=previous_answer_index,
                    answered_count=answered_count,
                    total_players=total_players,
                    all_answered=(answered_count == total_players and total_players > 0),
                ),
                None,
            )

    async def quiz_get_answer_snapshot(self, *, game_id: str) -> tuple[GroupGame | None, int, int]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, 0, 0
            answered_count = len({player_id for player_id in game.quiz_answers if player_id in game.players})
            return game, answered_count, len(game.players)

    async def quiz_resolve_round(
        self,
        *,
        game_id: str,
        force: bool,
    ) -> tuple[GroupGame | None, QuizRoundResolution | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "quiz":
                return game, None, "Это не викторина"
            if game.status != "started" or game.phase != "freeplay":
                return game, None, "Сейчас викторина не в активной фазе"

            question_index = game.quiz_current_question_index
            question = self._current_quiz_question(game)
            if question_index is None or question is None:
                return game, None, "Текущий вопрос не найден"

            answered_count = len({player_id for player_id in game.quiz_answers if player_id in game.players})
            total_players = len(game.players)
            if not force and answered_count < total_players:
                return game, None, "Ещё не все участники ответили"

            per_player_answers: list[tuple[int, int | None, bool]] = []
            correct_players: list[int] = []
            for player_id in sorted(game.players.keys()):
                answer_index = game.quiz_answers.get(player_id)
                is_correct = answer_index == question.answer_index
                per_player_answers.append((player_id, answer_index, is_correct))
                if is_correct:
                    game.quiz_scores[player_id] = game.quiz_scores.get(player_id, 0) + 1
                    correct_players.append(player_id)
                else:
                    game.quiz_scores.setdefault(player_id, 0)

            scores = tuple(self._sorted_quiz_scores(game))
            game.quiz_answers.clear()

            next_question_index = question_index + 1
            winner_text: str | None = None
            finished = next_question_index >= len(game.quiz_questions)
            if finished:
                winner_text = self._resolve_quiz_winner_text(game, scores)
                game.status = "finished"
                game.phase = "finished"
                game.winner_text = winner_text
                game.quiz_current_question_index = None
                self._active_by_chat.pop(game.chat_id, None)
            else:
                game.quiz_current_question_index = next_question_index
                game.round_no = next_question_index + 1
                game.phase_started_at = datetime.now(timezone.utc)

            return (
                game,
                QuizRoundResolution(
                    question_index=question_index,
                    question_text=question.prompt,
                    correct_option_index=question.answer_index,
                    correct_option_text=question.options[question.answer_index],
                    answered_count=answered_count,
                    total_players=total_players,
                    per_player_answers=tuple(per_player_answers),
                    correct_players=tuple(correct_players),
                    scores=scores,
                    next_question_index=None if finished else next_question_index,
                    finished=finished,
                    winner_text=winner_text,
                ),
                None,
            )

    async def bred_submit_lie(
        self,
        *,
        game_id: str,
        user_id: int,
        lie_text: str,
    ) -> tuple[GroupGame | None, BredSubmitResult | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "bredovukha":
                return game, None, "Это не «Бредовуха»"
            if game.status != "started" or game.phase != "private_answers":
                return game, None, "Сейчас не этап сбора ответов"
            if user_id not in game.players:
                return game, None, "Вы не участник этой игры"
            if game.bred_correct_answer is None:
                return game, None, "Правильный ответ не инициализирован"

            lie = lie_text.strip()
            if len(lie) < BRED_MIN_LIE_LEN:
                return game, None, f"Ответ слишком короткий (минимум {BRED_MIN_LIE_LEN} символа)"
            if len(lie) > BRED_MAX_LIE_LEN:
                return game, None, f"Ответ слишком длинный (максимум {BRED_MAX_LIE_LEN} символов)"

            normalized_lie = self._normalize_bred_answer(lie)
            if normalized_lie == self._normalize_bred_answer(game.bred_correct_answer):
                return game, None, "Нельзя отправлять правильный ответ. Нужна правдоподобная ложь."

            for player_id, existing_lie in game.bred_lies.items():
                if player_id == user_id:
                    continue
                if self._normalize_bred_answer(existing_lie) == normalized_lie:
                    return game, None, "Такой вариант уже отправил другой игрок. Придумайте другой."

            previous_lie = game.bred_lies.get(user_id)
            game.bred_lies[user_id] = lie

            submitted_count = len({player_id for player_id in game.players if player_id in game.bred_lies})
            total_players = len(game.players)
            all_submitted = total_players > 0 and submitted_count == total_players
            vote_opened = False

            if all_submitted:
                opened, open_error = self._open_bred_vote(game)
                if not opened:
                    return game, None, open_error or "Не удалось открыть голосование"
                vote_opened = True

            return (
                game,
                BredSubmitResult(
                    previous_lie=previous_lie,
                    submitted_count=submitted_count,
                    total_players=total_players,
                    all_submitted=all_submitted,
                    vote_opened=vote_opened,
                ),
                None,
            )

    async def bred_get_submit_snapshot(self, *, game_id: str) -> tuple[GroupGame | None, int, int]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, 0, 0

            submitted_count = len({player_id for player_id in game.players if player_id in game.bred_lies})
            return game, submitted_count, len(game.players)

    async def bred_open_vote(
        self,
        *,
        game_id: str,
        force: bool,
    ) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"
            if game.kind != "bredovukha":
                return game, "Это не «Бредовуха»"
            if game.status != "started" or game.phase != "private_answers":
                return game, "Сейчас не этап сбора ответов"

            submitted_count = len({player_id for player_id in game.players if player_id in game.bred_lies})
            total_players = len(game.players)
            if not force and submitted_count < total_players:
                return game, "Ещё не все игроки прислали ответы"

            opened, error = self._open_bred_vote(game)
            if not opened:
                return game, error or "Не удалось открыть голосование"
            return game, None

    async def bred_register_vote(
        self,
        *,
        game_id: str,
        voter_user_id: int,
        option_index: int,
    ) -> tuple[GroupGame | None, BredVoteResult | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "bredovukha":
                return game, None, "Это не «Бредовуха»"
            if game.status != "started" or game.phase != "public_vote":
                return game, None, "Сейчас не этап голосования"
            if voter_user_id not in game.players:
                return game, None, "Вы не участник этой игры"
            if option_index < 0 or option_index >= len(game.bred_options):
                return game, None, "Некорректный вариант"

            previous_option_index = game.bred_votes.get(voter_user_id)
            game.bred_votes[voter_user_id] = option_index

            voted_count = len({player_id for player_id in game.players if player_id in game.bred_votes})
            total_players = len(game.players)
            return (
                game,
                BredVoteResult(
                    previous_option_index=previous_option_index,
                    voted_count=voted_count,
                    total_players=total_players,
                    all_voted=(total_players > 0 and voted_count == total_players),
                ),
                None,
            )

    async def bred_get_vote_snapshot(self, *, game_id: str) -> tuple[GroupGame | None, int, int, tuple[int, ...]]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, 0, 0, ()

            option_count = len(game.bred_options)
            vote_tally = [0] * option_count
            voted_count = 0
            for player_id in game.players:
                option_index = game.bred_votes.get(player_id)
                if option_index is None:
                    continue
                voted_count += 1
                if 0 <= option_index < option_count:
                    vote_tally[option_index] += 1

            return game, voted_count, len(game.players), tuple(vote_tally)

    async def bred_resolve_round(
        self,
        *,
        game_id: str,
        force: bool,
    ) -> tuple[GroupGame | None, BredRoundResolution | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "bredovukha":
                return game, None, "Это не «Бредовуха»"
            if game.status != "started" or game.phase != "public_vote":
                return game, None, "Сейчас не этап голосования"
            if not game.bred_options or not game.bred_option_owner_user_ids:
                return game, None, "Варианты ответа не подготовлены"
            if len(game.bred_options) != len(game.bred_option_owner_user_ids):
                return game, None, "Неконсистентные варианты ответа"

            correct_option_index = next(
                (index for index, owner_user_id in enumerate(game.bred_option_owner_user_ids) if owner_user_id is None),
                None,
            )
            if correct_option_index is None:
                return game, None, "Не найден правильный вариант"

            round_category = game.bred_current_category or "-"
            round_question_text = game.bred_question_prompt or ""
            round_options = tuple(game.bred_options)
            round_option_owner_user_ids = tuple(game.bred_option_owner_user_ids)
            round_correct_option_text = round_options[correct_option_index]
            round_fact_text = game.bred_fact_text

            total_players = len(game.players)
            voted_count = len({player_id for player_id in game.players if player_id in game.bred_votes})
            if not force and voted_count < total_players:
                return game, None, "Ещё не все участники проголосовали"

            vote_tally = [0] * len(game.bred_options)
            per_player_votes: list[tuple[int, int | None, bool]] = []
            gains: dict[int, int] = {player_id: 0 for player_id in game.players}

            for player_id in sorted(game.players.keys()):
                voted_option_index = game.bred_votes.get(player_id)
                is_correct = voted_option_index == correct_option_index
                per_player_votes.append((player_id, voted_option_index, is_correct))
                if is_correct:
                    gains[player_id] += 2
                if voted_option_index is not None and 0 <= voted_option_index < len(vote_tally):
                    vote_tally[voted_option_index] += 1

            for option_index, owner_user_id in enumerate(game.bred_option_owner_user_ids):
                if owner_user_id is None or owner_user_id not in game.players:
                    continue
                fooled_count = vote_tally[option_index]
                if game.bred_votes.get(owner_user_id) == option_index:
                    fooled_count -= 1
                if fooled_count > 0:
                    gains[owner_user_id] += fooled_count

            for player_id in game.players:
                game.bred_scores[player_id] = game.bred_scores.get(player_id, 0) + gains.get(player_id, 0)

            scores = tuple(self._sorted_bred_scores(game))
            current_round_no = game.round_no
            finished = current_round_no >= game.bred_rounds

            winner_user_ids: tuple[int, ...] = ()
            winner_text: str | None = None
            next_round_no: int | None = None
            next_selector_user_id: int | None = None
            next_selector_label: str | None = None

            game.bred_last_round_no = current_round_no
            game.bred_last_category = round_category
            game.bred_last_question_prompt = round_question_text
            game.bred_last_correct_answer = round_correct_option_text
            game.bred_last_fact_text = round_fact_text
            game.bred_last_options = round_options
            game.bred_last_option_owner_user_ids = round_option_owner_user_ids
            game.bred_last_vote_tally = tuple(vote_tally)
            game.bred_last_correct_option_index = correct_option_index

            if finished:
                top_score = scores[0][1] if scores else 0
                winner_user_ids = tuple(user_id for user_id, score in scores if score == top_score)
                winner_text = self._resolve_bred_winner_text(game, winner_user_ids=winner_user_ids, top_score=top_score)
                game.status = "finished"
                game.phase = "finished"
                game.winner_text = winner_text
                game.bred_current_category = None
                game.bred_question_prompt = None
                game.bred_correct_answer = None
                game.bred_fact_text = None
                self._active_by_chat.pop(game.chat_id, None)
            else:
                next_round_no = current_round_no + 1
                next_selector_user_id = self._selector_for_round(game, round_no=next_round_no)
                if next_selector_user_id is None:
                    top_score = scores[0][1] if scores else 0
                    winner_user_ids = tuple(user_id for user_id, score in scores if score == top_score)
                    winner_text = self._resolve_bred_winner_text(game, winner_user_ids=winner_user_ids, top_score=top_score)
                    game.status = "finished"
                    game.phase = "finished"
                    game.winner_text = winner_text
                    game.bred_current_category = None
                    game.bred_question_prompt = None
                    game.bred_correct_answer = None
                    game.bred_fact_text = None
                    self._active_by_chat.pop(game.chat_id, None)
                    finished = True
                else:
                    next_selector_label = game.players.get(next_selector_user_id, f"user:{next_selector_user_id}")
                    game.round_no = next_round_no
                    game.phase = "category_pick"
                    game.phase_started_at = datetime.now(timezone.utc)
                    game.bred_current_selector_user_id = next_selector_user_id
                    game.bred_category_options = self._pick_bred_category_options(game)
                    game.bred_current_category = None
                    game.bred_question_prompt = None
                    game.bred_correct_answer = None
                    game.bred_fact_text = None
                    game.bred_lies.clear()
                    game.bred_options = ()
                    game.bred_option_owner_user_ids = ()
                    game.bred_votes.clear()
                    if not game.bred_category_options:
                        top_score = scores[0][1] if scores else 0
                        winner_user_ids = tuple(user_id for user_id, score in scores if score == top_score)
                        winner_text = self._resolve_bred_winner_text(game, winner_user_ids=winner_user_ids, top_score=top_score)
                        game.status = "finished"
                        game.phase = "finished"
                        game.winner_text = winner_text
                        game.bred_current_category = None
                        game.bred_question_prompt = None
                        game.bred_correct_answer = None
                        game.bred_fact_text = None
                        self._active_by_chat.pop(game.chat_id, None)
                        finished = True
                        next_round_no = None
                        next_selector_user_id = None
                        next_selector_label = None

            resolution = BredRoundResolution(
                round_no=current_round_no,
                category=round_category,
                question_text=round_question_text,
                correct_option_index=correct_option_index,
                correct_option_text=round_correct_option_text,
                fact_text=round_fact_text,
                options=round_options,
                option_owner_user_ids=round_option_owner_user_ids,
                vote_tally=tuple(vote_tally),
                per_player_votes=tuple(per_player_votes),
                gains=tuple(sorted(gains.items(), key=lambda item: item[0])),
                scores=scores,
                finished=finished,
                next_round_no=next_round_no,
                next_selector_user_id=next_selector_user_id,
                next_selector_label=next_selector_label,
                winner_user_ids=winner_user_ids,
                winner_text=winner_text,
            )
            return game, resolution, None

    async def zlob_submit_cards(
        self,
        *,
        game_id: str,
        user_id: int,
        card_indexes: tuple[int, ...],
    ) -> tuple[GroupGame | None, ZlobSubmitResult | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "zlobcards":
                return game, None, "Это не «500 Злобных Карт»"
            if game.status != "started" or game.phase != "private_answers":
                return game, None, "Сейчас не этап приватного выбора"
            if user_id not in game.players:
                return game, None, "Вы не участник этой игры"
            if not game.zlob_black_text:
                return game, None, "Чёрная карточка текущего раунда не выбрана"

            needed_slots = max(1, int(game.zlob_black_slots))
            if len(card_indexes) != needed_slots:
                return game, None, f"Нужно выбрать карточек: {needed_slots}"
            if needed_slots == 2 and len(set(card_indexes)) != 2:
                return game, None, "Для раунда с двумя пропусками нужны две разные карты"

            hand = list(game.zlob_hands.get(user_id, ()))
            if not hand:
                return game, None, "Ваша рука пуста"

            selected_cards: list[str] = []
            for index in card_indexes:
                if index < 0 or index >= len(hand):
                    return game, None, "Некорректная карта в выборе"
                selected_cards.append(hand[index])

            previous_submission = game.zlob_submissions.get(user_id)
            game.zlob_submissions[user_id] = tuple(selected_cards)

            submitted_count = len({player_id for player_id in game.players if player_id in game.zlob_submissions})
            total_players = len(game.players)
            all_submitted = total_players > 0 and submitted_count == total_players
            vote_opened = False
            if all_submitted:
                opened, open_error = self._open_zlob_vote(game)
                if not opened:
                    return game, None, open_error or "Не удалось открыть голосование"
                vote_opened = True

            return (
                game,
                ZlobSubmitResult(
                    previous_submission=previous_submission,
                    submitted_count=submitted_count,
                    total_players=total_players,
                    all_submitted=all_submitted,
                    vote_opened=vote_opened,
                ),
                None,
            )

    async def zlob_get_submit_snapshot(self, *, game_id: str) -> tuple[GroupGame | None, int, int]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, 0, 0
            submitted_count = len({player_id for player_id in game.players if player_id in game.zlob_submissions})
            return game, submitted_count, len(game.players)

    async def zlob_open_vote(
        self,
        *,
        game_id: str,
        force: bool,
    ) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"
            if game.kind != "zlobcards":
                return game, "Это не «500 Злобных Карт»"
            if game.status != "started" or game.phase != "private_answers":
                return game, "Сейчас не этап приватного выбора"

            submitted_count = len({player_id for player_id in game.players if player_id in game.zlob_submissions})
            total_players = len(game.players)
            if not force and submitted_count < total_players:
                return game, "Ещё не все игроки прислали карточки"

            opened, error = self._open_zlob_vote(game)
            if not opened:
                return game, error or "Не удалось открыть голосование"
            return game, None

    async def zlob_register_vote(
        self,
        *,
        game_id: str,
        voter_user_id: int,
        option_index: int,
    ) -> tuple[GroupGame | None, ZlobVoteResult | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "zlobcards":
                return game, None, "Это не «500 Злобных Карт»"
            if game.status != "started" or game.phase != "public_vote":
                return game, None, "Сейчас не этап голосования"
            if voter_user_id not in game.players:
                return game, None, "Вы не участник этой игры"
            if option_index < 0 or option_index >= len(game.zlob_options):
                return game, None, "Некорректный вариант"

            owner_user_id = (
                game.zlob_option_owner_user_ids[option_index]
                if option_index < len(game.zlob_option_owner_user_ids)
                else None
            )
            if owner_user_id == voter_user_id:
                return game, None, "Нельзя голосовать за свою карточку"

            previous_option_index = game.zlob_votes.get(voter_user_id)
            game.zlob_votes[voter_user_id] = option_index
            voted_count = len({player_id for player_id in game.players if player_id in game.zlob_votes})
            total_players = len(game.players)
            return (
                game,
                ZlobVoteResult(
                    previous_option_index=previous_option_index,
                    voted_count=voted_count,
                    total_players=total_players,
                    all_voted=(total_players > 0 and voted_count == total_players),
                ),
                None,
            )

    async def zlob_get_vote_snapshot(self, *, game_id: str) -> tuple[GroupGame | None, int, int, tuple[int, ...]]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, 0, 0, ()

            option_count = len(game.zlob_options)
            vote_tally = [0] * option_count
            voted_count = 0
            for player_id in game.players:
                option = game.zlob_votes.get(player_id)
                if option is None:
                    continue
                voted_count += 1
                if 0 <= option < option_count:
                    vote_tally[option] += 1

            return game, voted_count, len(game.players), tuple(vote_tally)

    async def zlob_resolve_round(
        self,
        *,
        game_id: str,
        force: bool,
    ) -> tuple[GroupGame | None, ZlobRoundResolution | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "zlobcards":
                return game, None, "Это не «500 Злобных Карт»"
            if game.status != "started" or game.phase != "public_vote":
                return game, None, "Сейчас не этап голосования"
            if not game.zlob_options or not game.zlob_option_owner_user_ids:
                return game, None, "Нет вариантов для голосования"
            if len(game.zlob_options) != len(game.zlob_option_owner_user_ids):
                return game, None, "Неконсистентные варианты голосования"
            if not game.zlob_black_text:
                return game, None, "Не задана чёрная карточка раунда"

            total_players = len(game.players)
            voted_count = len({player_id for player_id in game.players if player_id in game.zlob_votes})
            if not force and voted_count < total_players:
                return game, None, "Ещё не все участники проголосовали"

            vote_tally = [0] * len(game.zlob_options)
            per_player_votes: list[tuple[int, int | None, bool]] = []
            for player_id in sorted(game.players.keys()):
                voted_option_index = game.zlob_votes.get(player_id)
                if voted_option_index is not None and 0 <= voted_option_index < len(vote_tally):
                    vote_tally[voted_option_index] += 1
                per_player_votes.append((player_id, voted_option_index, False))

            winner_option_indexes: tuple[int, ...] = ()
            if vote_tally:
                top_votes = max(vote_tally)
                if top_votes > 0:
                    winner_option_indexes = tuple(idx for idx, votes in enumerate(vote_tally) if votes == top_votes)

            gains: dict[int, int] = {player_id: 0 for player_id in game.players}
            for option_index in winner_option_indexes:
                owner_user_id = game.zlob_option_owner_user_ids[option_index]
                if owner_user_id is None or owner_user_id not in game.players:
                    continue
                gains[owner_user_id] += 1

            for player_id in game.players:
                game.zlob_scores[player_id] = game.zlob_scores.get(player_id, 0) + gains.get(player_id, 0)
            scores = tuple(self._sorted_zlob_scores(game))

            for player_id in sorted(game.players.keys()):
                hand = list(game.zlob_hands.get(player_id, ()))
                submitted = game.zlob_submissions.get(player_id, ())
                for card_text in submitted:
                    if card_text in hand:
                        hand.remove(card_text)
                        game.zlob_white_discard.append(card_text)
                draw_count = max(0, ZLOBCARDS_HAND_SIZE - len(hand))
                if draw_count > 0:
                    hand.extend(self._zlob_draw_white_cards(game, count=draw_count))
                game.zlob_hands[player_id] = tuple(hand)

            current_round_no = max(game.round_no, 1)
            current_black_text = game.zlob_black_text
            current_black_slots = game.zlob_black_slots
            current_options = tuple(game.zlob_options)
            current_option_owner_user_ids = tuple(game.zlob_option_owner_user_ids)

            game.zlob_last_round_no = current_round_no
            game.zlob_last_black_text = current_black_text
            game.zlob_last_black_slots = current_black_slots
            game.zlob_last_options = current_options
            game.zlob_last_option_owner_user_ids = current_option_owner_user_ids
            game.zlob_last_vote_tally = tuple(vote_tally)
            game.zlob_last_winner_option_indexes = winner_option_indexes

            game.zlob_submissions.clear()
            game.zlob_options = ()
            game.zlob_option_owner_user_ids = ()
            game.zlob_votes.clear()

            top_score = scores[0][1] if scores else 0
            finished_by_round = current_round_no >= game.zlob_rounds
            finished_by_score = top_score >= game.zlob_target_score
            finished = finished_by_round or finished_by_score
            winner_user_ids: tuple[int, ...] = ()
            winner_text: str | None = None
            next_round_no: int | None = None

            if finished:
                winner_user_ids = tuple(user_id for user_id, score in scores if score == top_score)
                winner_text = self._resolve_zlob_winner_text(
                    game,
                    winner_user_ids=winner_user_ids,
                    top_score=top_score,
                )
                game.status = "finished"
                game.phase = "finished"
                game.winner_text = winner_text
                game.zlob_black_text = None
                game.zlob_black_slots = 1
                self._active_by_chat.pop(game.chat_id, None)
            else:
                next_round_no = current_round_no + 1
                game.round_no = next_round_no
                opened, error = self._prepare_zlob_private_phase(game)
                if not opened:
                    winner_user_ids = tuple(user_id for user_id, score in scores if score == top_score)
                    winner_text = self._resolve_zlob_winner_text(
                        game,
                        winner_user_ids=winner_user_ids,
                        top_score=top_score,
                    )
                    game.status = "finished"
                    game.phase = "finished"
                    game.winner_text = winner_text
                    game.zlob_black_text = None
                    game.zlob_black_slots = 1
                    self._active_by_chat.pop(game.chat_id, None)
                    finished = True
                    next_round_no = None

            resolution = ZlobRoundResolution(
                round_no=current_round_no,
                black_text=current_black_text,
                black_slots=current_black_slots,
                options=current_options,
                option_owner_user_ids=current_option_owner_user_ids,
                vote_tally=tuple(vote_tally),
                winner_option_indexes=winner_option_indexes,
                per_player_votes=tuple(per_player_votes),
                gains=tuple(sorted(gains.items(), key=lambda item: item[0])),
                scores=scores,
                finished=finished,
                next_round_no=next_round_no,
                winner_user_ids=winner_user_ids,
                winner_text=winner_text,
            )
            return game, resolution, None

    async def bunker_get_reveal_snapshot(
        self,
        *,
        game_id: str,
    ) -> tuple[GroupGame | None, int, int, int | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, 0, 0, None
            if game.kind != "bunker":
                return game, 0, 0, None
            total_in_round = len(game.bunker_round_reveal_user_ids)
            current_index = game.bunker_reveal_cursor
            return game, current_index, total_in_round, game.bunker_current_actor_user_id

    async def bunker_register_reveal(
        self,
        *,
        game_id: str,
        actor_user_id: int,
        field_key: str,
    ) -> tuple[GroupGame | None, BunkerRevealResult | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "bunker":
                return game, None, "Это не «Бункер»"
            if game.status != "started" or game.phase != "bunker_reveal":
                return game, None, "Сейчас не этап раскрытия"
            if actor_user_id not in game.alive_player_ids:
                return game, None, "Вы выбыли и не можете раскрывать карточку"
            if actor_user_id != game.bunker_current_actor_user_id:
                return game, None, "Сейчас раскрывается другой игрок"
            if field_key not in BUNKER_CARD_FIELDS:
                return game, None, "Некорректная характеристика"

            card = game.bunker_cards.get(actor_user_id)
            if card is None:
                return game, None, "Карточка игрока не найдена"

            revealed = game.bunker_revealed_fields.setdefault(actor_user_id, set())
            if field_key in revealed:
                return game, None, "Эта характеристика уже раскрыта"

            revealed.add(field_key)
            field_label = self._bunker_field_label(field_key)
            revealed_value = self._bunker_card_value(card, field_key)

            vote_opened, next_actor_user_id, next_actor_label = self._advance_bunker_reveal_cursor(game)
            result = BunkerRevealResult(
                actor_user_id=actor_user_id,
                actor_user_label=game.players.get(actor_user_id, f"user:{actor_user_id}"),
                field_key=field_key,
                field_label=field_label,
                revealed_value=revealed_value,
                revealed_count_for_actor=len(revealed),
                total_fields_for_actor=len(BUNKER_CARD_FIELDS),
                skipped=False,
                vote_opened=vote_opened,
                next_actor_user_id=next_actor_user_id,
                next_actor_label=next_actor_label,
            )
            return game, result, None

    async def bunker_force_advance_reveal(
        self,
        *,
        game_id: str,
    ) -> tuple[GroupGame | None, BunkerRevealResult | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "bunker":
                return game, None, "Это не «Бункер»"
            if game.status != "started" or game.phase != "bunker_reveal":
                return game, None, "Сейчас не этап раскрытия"

            actor_user_id = game.bunker_current_actor_user_id
            if actor_user_id is None:
                self._open_bunker_vote_phase(game)
                result = BunkerRevealResult(
                    actor_user_id=0,
                    actor_user_label="-",
                    field_key=None,
                    field_label=None,
                    revealed_value=None,
                    revealed_count_for_actor=0,
                    total_fields_for_actor=len(BUNKER_CARD_FIELDS),
                    skipped=True,
                    vote_opened=True,
                    next_actor_user_id=None,
                    next_actor_label=None,
                )
                return game, result, None

            revealed = game.bunker_revealed_fields.setdefault(actor_user_id, set())
            vote_opened, next_actor_user_id, next_actor_label = self._advance_bunker_reveal_cursor(game)
            result = BunkerRevealResult(
                actor_user_id=actor_user_id,
                actor_user_label=game.players.get(actor_user_id, f"user:{actor_user_id}"),
                field_key=None,
                field_label=None,
                revealed_value=None,
                revealed_count_for_actor=len(revealed),
                total_fields_for_actor=len(BUNKER_CARD_FIELDS),
                skipped=True,
                vote_opened=vote_opened,
                next_actor_user_id=next_actor_user_id,
                next_actor_label=next_actor_label,
            )
            return game, result, None

    async def bunker_register_vote(
        self,
        *,
        game_id: str,
        voter_user_id: int,
        target_user_id: int,
    ) -> tuple[GroupGame | None, int | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "bunker":
                return game, None, "Это не «Бункер»"
            if game.status != "started" or game.phase != "bunker_vote":
                return game, None, "Сейчас не этап голосования"
            current_round_no = game.round_no
            if voter_user_id not in game.alive_player_ids:
                return game, None, "Вы выбыли и не можете голосовать"
            if target_user_id not in game.alive_player_ids:
                return game, None, "Этот игрок уже выбыл"
            if voter_user_id == target_user_id:
                return game, None, "Нельзя голосовать против себя"

            previous_target_user_id = game.bunker_votes.get(voter_user_id)
            game.bunker_votes[voter_user_id] = target_user_id
            return game, previous_target_user_id, None

    async def bunker_get_vote_snapshot(
        self,
        *,
        game_id: str,
    ) -> tuple[GroupGame | None, int, int, int | None, int]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, 0, 0, None, 0

            alive = sorted(game.alive_player_ids)
            voted_count = len({voter for voter in game.bunker_votes if voter in game.alive_player_ids})
            vote_counts: dict[int, int] = {}
            for voter_user_id, target_user_id in game.bunker_votes.items():
                if voter_user_id not in game.alive_player_ids:
                    continue
                if target_user_id not in game.alive_player_ids:
                    continue
                vote_counts[target_user_id] = vote_counts.get(target_user_id, 0) + 1

            leader_user_id: int | None = None
            leader_votes = 0
            if vote_counts:
                leader_votes = max(vote_counts.values())
                leaders = [user_id for user_id, count in vote_counts.items() if count == leader_votes]
                if len(leaders) == 1:
                    leader_user_id = leaders[0]

            return game, voted_count, len(alive), leader_user_id, leader_votes

    async def bunker_resolve_vote(
        self,
        *,
        game_id: str,
        force: bool,
    ) -> tuple[GroupGame | None, BunkerVoteResolution | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "bunker":
                return game, None, "Это не «Бункер»"
            if game.status != "started" or game.phase != "bunker_vote":
                return game, None, "Сейчас не этап голосования"

            current_round_no = game.round_no
            alive_sorted = sorted(game.alive_player_ids)
            vote_protocol = tuple((voter_user_id, game.bunker_votes.get(voter_user_id)) for voter_user_id in alive_sorted)
            voted_count = len({voter for voter in game.bunker_votes if voter in game.alive_player_ids})
            total_alive = len(alive_sorted)
            if not force and total_alive > 0 and voted_count < total_alive:
                return game, None, "Ещё не все участники проголосовали"

            vote_counts: dict[int, int] = {}
            for voter_user_id, target_user_id in game.bunker_votes.items():
                if voter_user_id not in game.alive_player_ids:
                    continue
                if target_user_id not in game.alive_player_ids:
                    continue
                vote_counts[target_user_id] = vote_counts.get(target_user_id, 0) + 1

            tie = False
            eliminated_user_id: int | None = None
            if vote_counts:
                max_votes = max(vote_counts.values())
                leaders = sorted(user_id for user_id, count in vote_counts.items() if count == max_votes)
                if len(leaders) == 1:
                    eliminated_user_id = leaders[0]
                else:
                    tie = True
            else:
                tie = True

            eliminated_card: BunkerCard | None = None
            if eliminated_user_id is not None and eliminated_user_id in game.alive_player_ids:
                game.alive_player_ids.remove(eliminated_user_id)
                eliminated_card = game.bunker_cards.get(eliminated_user_id)
                game.bunker_last_eliminated_user_id = eliminated_user_id

            game.bunker_votes.clear()

            winner_text: str | None = None
            winner_user_ids: tuple[int, ...] = ()
            finished = len(game.alive_player_ids) <= game.bunker_seats
            next_phase: GamePhase = "finished"
            next_actor_user_id: int | None = None
            next_actor_label: str | None = None

            if finished:
                winner_user_ids = tuple(sorted(game.alive_player_ids))
                winner_labels = [game.players.get(user_id, f"user:{user_id}") for user_id in winner_user_ids]
                joined = ", ".join(winner_labels) if winner_labels else "-"
                winner_text = f"В бункер попали: {joined}."
                game.status = "finished"
                game.phase = "finished"
                game.winner_text = winner_text
                self._active_by_chat.pop(game.chat_id, None)
                next_phase = "finished"
            else:
                game.round_no += 1
                reveal_opened = self._prepare_bunker_reveal_phase(game)
                if reveal_opened:
                    next_phase = "bunker_reveal"
                    next_actor_user_id = game.bunker_current_actor_user_id
                    if next_actor_user_id is not None:
                        next_actor_label = game.players.get(next_actor_user_id, f"user:{next_actor_user_id}")
                else:
                    next_phase = "bunker_vote"

            vote_tally = tuple(sorted(vote_counts.items(), key=lambda item: (-item[1], game.players.get(item[0], f"user:{item[0]}").lower())))
            resolution = BunkerVoteResolution(
                round_no=max(current_round_no, 1),
                voted_count=voted_count,
                total_alive=total_alive,
                tie=tie,
                vote_protocol=vote_protocol,
                vote_tally=vote_tally,
                eliminated_user_id=eliminated_user_id,
                eliminated_user_label=game.players.get(eliminated_user_id) if eliminated_user_id is not None else None,
                eliminated_card=eliminated_card,
                finished=finished,
                winner_user_ids=winner_user_ids,
                winner_text=winner_text,
                next_phase=next_phase,
                next_actor_user_id=next_actor_user_id,
                next_actor_label=next_actor_label,
            )
            return game, resolution, None

    async def mafia_register_night_action(
        self,
        *,
        game_id: str,
        actor_user_id: int,
        target_user_id: int,
    ) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"
            if game.kind != "mafia":
                return game, "Это не мафия"
            if game.status != "started" or game.phase != "night":
                return game, "Сейчас не фаза ночи"
            if actor_user_id not in game.alive_player_ids:
                return game, "Вы выбиты и не можете делать ход"
            actor_role = game.roles.get(actor_user_id)
            if actor_role is None:
                return game, "Роль не найдена"

            alive = set(game.alive_player_ids)
            dead = set(game.players) - alive

            if actor_role in MAFIA_ATTACKER_ROLES:
                if target_user_id not in alive:
                    return game, "Цель уже выбыла"
                if actor_user_id == target_user_id:
                    return game, "Нельзя выбрать себя"
                if self._mafia_team_for_user(game, target_user_id) == MAFIA_TEAM_MAFIA:
                    return game, "Мафия не может атаковать своих"
                game.mafia_votes[actor_user_id] = target_user_id
                if actor_role == MAFIA_ROLE_DON:
                    game.don_checks[actor_user_id] = target_user_id
                if actor_role == MAFIA_ROLE_POISONER:
                    game.poisoner_targets[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_COMMISSIONER:
                if target_user_id not in alive or actor_user_id == target_user_id:
                    return game, "Нужно выбрать другого живого игрока"
                game.sheriff_checks[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_INSPECTOR:
                if target_user_id not in alive or actor_user_id == target_user_id:
                    return game, "Нужно выбрать другого живого игрока"
                game.inspector_checks[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_DOCTOR:
                if target_user_id not in alive:
                    return game, "Цель уже выбыла"
                game.doctor_saves[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_ESCORT:
                if target_user_id not in alive or actor_user_id == target_user_id:
                    return game, "Нужно выбрать другого живого игрока"
                game.escort_blocks[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_BODYGUARD:
                if target_user_id not in alive or actor_user_id == target_user_id:
                    return game, "Нужно выбрать другого живого игрока"
                game.bodyguard_protects[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_JOURNALIST:
                if target_user_id not in alive or actor_user_id == target_user_id:
                    return game, "Нужно выбрать другого живого игрока"
                first_target = game.journalist_first_pick.get(actor_user_id)
                if first_target is None:
                    game.journalist_first_pick[actor_user_id] = target_user_id
                    return game, None
                if first_target == target_user_id:
                    return game, "Для сравнения выберите второго игрока"
                game.journalist_checks[actor_user_id] = (first_target, target_user_id)
                game.journalist_first_pick.pop(actor_user_id, None)
                return game, None

            if actor_role == MAFIA_ROLE_PRIEST:
                if target_user_id not in alive:
                    return game, "Цель уже выбыла"
                game.priest_protects[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_VETERAN:
                if target_user_id != actor_user_id:
                    return game, "Ветеран может объявить боеготовность только для себя"
                if actor_user_id in game.veteran_used:
                    return game, "Боевая готовность уже была использована"
                game.veteran_alerts.add(actor_user_id)
                game.veteran_used.add(actor_user_id)
                return game, None

            if actor_role == MAFIA_ROLE_REANIMATOR:
                if actor_user_id in game.reanimator_used:
                    return game, "Реанимация уже использована"
                if target_user_id not in dead:
                    return game, "Для реанимации выберите выбывшего игрока"
                game.reanimator_targets[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_PSYCHOLOGIST:
                if target_user_id not in alive or actor_user_id == target_user_id:
                    return game, "Нужно выбрать другого живого игрока"
                game.psychologist_checks[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_DETECTIVE:
                if target_user_id not in alive or actor_user_id == target_user_id:
                    return game, "Нужно выбрать другого живого игрока"
                game.detective_checks[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_LAWYER:
                if target_user_id not in alive:
                    return game, "Цель уже выбыла"
                game.lawyer_targets[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_MANIAC:
                if target_user_id not in alive or actor_user_id == target_user_id:
                    return game, "Нужно выбрать другого живого игрока"
                game.maniac_kills[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_SERIAL:
                if target_user_id not in alive or actor_user_id == target_user_id:
                    return game, "Нужно выбрать другого живого игрока"
                game.serial_kills[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_WITCH:
                if target_user_id not in alive:
                    return game, "Цель уже выбыла"
                if target_user_id == actor_user_id:
                    if actor_user_id in game.witch_save_used:
                        return game, "Зелье спасения уже использовано"
                    game.witch_save_targets[actor_user_id] = target_user_id
                    return game, None
                if actor_user_id in game.witch_kill_used:
                    return game, "Зелье убийства уже использовано"
                game.witch_kill_targets[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_VAMPIRE:
                if target_user_id not in alive or actor_user_id == target_user_id:
                    return game, "Нужно выбрать другого живого игрока"
                game.vampire_bites[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_BOMBER:
                if target_user_id not in alive or actor_user_id == target_user_id:
                    return game, "Нужно выбрать другого живого игрока"
                game.bomber_mines[actor_user_id] = target_user_id
                return game, None

            if actor_role == MAFIA_ROLE_CHILD:
                if target_user_id != actor_user_id:
                    return game, "Ребёнок может раскрыться только сам"
                game.child_revealed.add(actor_user_id)
                return game, None

            return game, "У вашей роли нет ночного действия"

    async def mafia_is_night_ready(self, *, game_id: str) -> tuple[GroupGame | None, bool, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, False, "Игра не найдена"
            if game.kind != "mafia":
                return game, False, "Это не мафия"
            if game.status != "started" or game.phase != "night":
                return game, False, "Сейчас не фаза ночи"

            alive = set(game.alive_player_ids)
            blocked_actors = {
                target_user_id
                for actor_user_id, target_user_id in game.escort_blocks.items()
                if actor_user_id in alive and target_user_id in alive
            }

            mafia_attackers = {
                user_id
                for user_id in alive
                if game.roles.get(user_id) in MAFIA_ATTACKER_ROLES
                and (
                    user_id not in blocked_actors
                    or game.roles.get(user_id) in MAFIA_BLOCK_IMMUNE_ROLES
                )
            }
            mafia_ready = not mafia_attackers or any(user_id in game.mafia_votes for user_id in mafia_attackers)

            required_checks: list[bool] = [mafia_ready]

            for user_id in alive:
                role = game.roles.get(user_id)
                is_blocked = user_id in blocked_actors and role not in MAFIA_BLOCK_IMMUNE_ROLES
                if is_blocked:
                    continue

                if role in {MAFIA_ROLE_COMMISSIONER}:
                    required_checks.append(user_id in game.sheriff_checks)
                elif role in {MAFIA_ROLE_INSPECTOR}:
                    required_checks.append(user_id in game.inspector_checks)
                elif role in {MAFIA_ROLE_DOCTOR}:
                    required_checks.append(user_id in game.doctor_saves)
                elif role in {MAFIA_ROLE_ESCORT}:
                    required_checks.append(user_id in game.escort_blocks)
                elif role in {MAFIA_ROLE_BODYGUARD}:
                    required_checks.append(user_id in game.bodyguard_protects)
                elif role in {MAFIA_ROLE_JOURNALIST}:
                    required_checks.append(user_id in game.journalist_checks)
                elif role in {MAFIA_ROLE_PRIEST}:
                    required_checks.append(user_id in game.priest_protects)
                elif role in {MAFIA_ROLE_PSYCHOLOGIST}:
                    required_checks.append(user_id in game.psychologist_checks)
                elif role in {MAFIA_ROLE_DETECTIVE}:
                    required_checks.append(user_id in game.detective_checks)
                elif role in {MAFIA_ROLE_LAWYER}:
                    required_checks.append(user_id in game.lawyer_targets)
                elif role in {MAFIA_ROLE_MANIAC}:
                    required_checks.append(user_id in game.maniac_kills)
                elif role in {MAFIA_ROLE_SERIAL}:
                    required_checks.append(user_id in game.serial_kills)
                elif role in {MAFIA_ROLE_VAMPIRE}:
                    required_checks.append(user_id in game.vampire_bites)
                elif role in {MAFIA_ROLE_BOMBER}:
                    required_checks.append(user_id in game.bomber_mines)

            return game, bool(required_checks and all(required_checks)), None

    async def mafia_open_day_vote(self, *, game_id: str) -> tuple[GroupGame | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, "Игра не найдена"
            if game.kind != "mafia":
                return game, "Это не мафия"
            if game.status != "started" or game.phase != "day_discussion":
                return game, "Сейчас не обсуждение дня"

            game.phase = "day_vote"
            game.phase_started_at = datetime.now(timezone.utc)
            game.day_votes.clear()
            game.execution_confirm_message_id = None
            return game, None

    async def mafia_register_day_vote(
        self,
        *,
        game_id: str,
        voter_user_id: int,
        target_user_id: int,
    ) -> tuple[GroupGame | None, int | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "mafia":
                return game, None, "Это не мафия"
            if game.status != "started" or game.phase != "day_vote":
                return game, None, "Сейчас не фаза голосования"
            if voter_user_id not in game.alive_player_ids:
                return game, None, "Вы выбиты и не можете голосовать"
            if target_user_id not in game.alive_player_ids:
                return game, None, "Цель уже выбыла"
            if voter_user_id == target_user_id:
                return game, None, "Нельзя голосовать против себя"
            if target_user_id == game.day_vote_immune_user_id:
                return game, None, "Этого игрока сегодня прикрыл адвокат"

            previous_target_user_id = game.day_votes.get(voter_user_id)
            game.day_votes[voter_user_id] = target_user_id
            return game, previous_target_user_id, None

    async def mafia_resolve_night(self, *, game_id: str) -> tuple[GroupGame | None, NightResolution | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "mafia":
                return game, None, "Это не мафия"
            if game.status != "started" or game.phase != "night":
                return game, None, "Сейчас не фаза ночи"
            alive = set(game.alive_player_ids)
            blocked_actors = {
                target_user_id
                for actor_user_id, target_user_id in game.escort_blocks.items()
                if actor_user_id in alive and target_user_id in alive
            }

            killed_by_veteran: set[int] = set()
            night_killers: set[int] = set()
            night_movers: set[int] = set()
            night_hidden_movers: set[int] = set()
            public_notes: list[str] = []
            private_reports: list[tuple[int, str]] = []

            def actor_can_act(user_id: int) -> bool:
                if user_id not in alive:
                    return False
                role = game.roles.get(user_id)
                if role in MAFIA_BLOCK_IMMUNE_ROLES:
                    return True
                return user_id not in blocked_actors

            active_veterans = {
                user_id
                for user_id in game.veteran_alerts
                if user_id in alive and actor_can_act(user_id)
            }

            def visit_and_check(actor_user_id: int, target_user_id: int) -> bool:
                role = game.roles.get(actor_user_id)
                if role is None:
                    return False
                night_movers.add(actor_user_id)
                if role in MAFIA_HIDDEN_MOVERS:
                    night_hidden_movers.add(actor_user_id)
                if target_user_id in active_veterans and target_user_id != actor_user_id:
                    killed_by_veteran.add(actor_user_id)
                    return True
                return False

            doctor_saved: set[int] = set()
            for actor_user_id, target_user_id in game.doctor_saves.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_DOCTOR:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if target_user_id not in alive:
                    continue
                if visit_and_check(actor_user_id, target_user_id):
                    continue
                doctor_saved.add(target_user_id)

            priest_saved: set[int] = set()
            for actor_user_id, target_user_id in game.priest_protects.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_PRIEST:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if target_user_id not in alive:
                    continue
                if visit_and_check(actor_user_id, target_user_id):
                    continue
                priest_saved.add(target_user_id)

            bodyguard_by_target: dict[int, int] = {}
            for actor_user_id, target_user_id in game.bodyguard_protects.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_BODYGUARD:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if target_user_id not in alive:
                    continue
                if visit_and_check(actor_user_id, target_user_id):
                    continue
                if target_user_id not in bodyguard_by_target:
                    bodyguard_by_target[target_user_id] = actor_user_id

            game.day_vote_immune_user_id = None
            lawyer_choices: list[tuple[int, int]] = []
            for actor_user_id, target_user_id in game.lawyer_targets.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_LAWYER:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if target_user_id not in alive:
                    continue
                if visit_and_check(actor_user_id, target_user_id):
                    continue
                lawyer_choices.append((actor_user_id, target_user_id))
            if lawyer_choices:
                lawyer_choices.sort(key=lambda item: item[0])
                game.day_vote_immune_user_id = lawyer_choices[0][1]

            sheriff_checked_user_id: int | None = None
            sheriff_checked_is_mafia: bool | None = None
            for actor_user_id, target_user_id in game.sheriff_checks.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_COMMISSIONER:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if target_user_id not in alive:
                    continue
                if visit_and_check(actor_user_id, target_user_id):
                    continue
                target_role = game.roles.get(target_user_id)
                detected_team = self._mafia_team_for_user(game, target_user_id)
                if target_role == MAFIA_ROLE_WEREWOLF:
                    detected_team = MAFIA_TEAM_CIVILIAN
                if sheriff_checked_user_id is None:
                    sheriff_checked_user_id = target_user_id
                    sheriff_checked_is_mafia = detected_team == MAFIA_TEAM_MAFIA
                team_human = self._human_team_name(detected_team)
                target_label = game.players.get(target_user_id, f"user:{target_user_id}")
                safe_target_label = html_escape(target_label)
                private_reports.append(
                    (
                        actor_user_id,
                        (
                            f"<b>Отчёт комиссара (ночь {game.round_no})</b>\n"
                            f"Проверка: <b>{safe_target_label}</b>\n"
                            f"Команда: <code>{team_human}</code>"
                        ),
                    )
                )

            for actor_user_id, target_user_id in game.inspector_checks.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_INSPECTOR:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if target_user_id not in alive:
                    continue
                if visit_and_check(actor_user_id, target_user_id):
                    continue
                target_label = game.players.get(target_user_id, f"user:{target_user_id}")
                target_role = game.roles.get(target_user_id, "-")
                safe_target_label = html_escape(target_label)
                safe_target_role = html_escape(target_role)
                private_reports.append(
                    (
                        actor_user_id,
                        (
                            f"<b>Отчёт инспектора (ночь {game.round_no})</b>\n"
                            f"Проверка: <b>{safe_target_label}</b>\n"
                            f"Роль: <code>{safe_target_role}</code>"
                        ),
                    )
                )

            for actor_user_id, target_user_id in game.don_checks.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_DON:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if target_user_id not in alive:
                    continue
                if visit_and_check(actor_user_id, target_user_id):
                    continue
                target_label = game.players.get(target_user_id, f"user:{target_user_id}")
                safe_target_label = html_escape(target_label)
                is_commissioner = game.roles.get(target_user_id) == MAFIA_ROLE_COMMISSIONER
                verdict = "да" if is_commissioner else "нет"
                private_reports.append(
                    (
                        actor_user_id,
                        (
                            f"<b>Отчёт дона (ночь {game.round_no})</b>\n"
                            f"Проверка: <b>{safe_target_label}</b>\n"
                            f"Это комиссар: <code>{verdict}</code>"
                        ),
                    )
                )

            for actor_user_id, pair in game.journalist_checks.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_JOURNALIST:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                first_user_id, second_user_id = pair
                if first_user_id not in alive or second_user_id not in alive:
                    continue
                if visit_and_check(actor_user_id, first_user_id):
                    continue
                if actor_user_id in killed_by_veteran:
                    continue
                if visit_and_check(actor_user_id, second_user_id):
                    continue
                team_a = self._mafia_team_for_user(game, first_user_id)
                team_b = self._mafia_team_for_user(game, second_user_id)
                first_label = game.players.get(first_user_id, f"user:{first_user_id}")
                second_label = game.players.get(second_user_id, f"user:{second_user_id}")
                safe_first_label = html_escape(first_label)
                safe_second_label = html_escape(second_label)
                verdict = "в одной команде" if team_a == team_b else "в разных командах"
                private_reports.append(
                    (
                        actor_user_id,
                        (
                            f"<b>Отчёт журналиста (ночь {game.round_no})</b>\n"
                            f"Пара: <b>{safe_first_label}</b> и <b>{safe_second_label}</b>\n"
                            f"Вердикт: <code>{verdict}</code>"
                        ),
                    )
                )

            for actor_user_id, target_user_id in game.psychologist_checks.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_PSYCHOLOGIST:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if target_user_id not in alive:
                    continue
                if visit_and_check(actor_user_id, target_user_id):
                    continue
                target_label = game.players.get(target_user_id, f"user:{target_user_id}")
                safe_target_label = html_escape(target_label)
                did_kill = target_user_id in game.last_night_killers
                verdict = "да" if did_kill else "нет"
                private_reports.append(
                    (
                        actor_user_id,
                        (
                            f"<b>Отчёт психолога (ночь {game.round_no})</b>\n"
                            f"Проверка: <b>{safe_target_label}</b>\n"
                            f"Совершал убийство прошлой ночью: <code>{verdict}</code>"
                        ),
                    )
                )

            vote_counts: dict[int, int] = {}
            poison_targets: set[int] = set()
            tie_on_mafia_vote = False
            for actor_user_id, target_user_id in game.mafia_votes.items():
                actor_role = game.roles.get(actor_user_id)
                if actor_role not in MAFIA_ATTACKER_ROLES:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if target_user_id not in alive:
                    continue
                if self._mafia_team_for_user(game, target_user_id) == MAFIA_TEAM_MAFIA:
                    continue
                if visit_and_check(actor_user_id, target_user_id):
                    continue
                vote_counts[target_user_id] = vote_counts.get(target_user_id, 0) + 1
                night_killers.add(actor_user_id)
                if actor_role == MAFIA_ROLE_POISONER:
                    poison_targets.add(target_user_id)

            mafia_target_user_id: int | None = None
            if vote_counts:
                max_votes = max(vote_counts.values())
                top_targets = [user_id for user_id, votes in vote_counts.items() if votes == max_votes]
                if len(top_targets) == 1:
                    mafia_target_user_id = top_targets[0]
                else:
                    tie_on_mafia_vote = True

            kill_attempts: list[tuple[int, str, int | None]] = []
            if mafia_target_user_id is not None:
                kill_attempts.append((mafia_target_user_id, "mafia", None))

            for actor_user_id, target_user_id in game.maniac_kills.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_MANIAC:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if target_user_id not in alive or actor_user_id == target_user_id:
                    continue
                if visit_and_check(actor_user_id, target_user_id):
                    continue
                night_killers.add(actor_user_id)
                kill_attempts.append((target_user_id, "maniac", actor_user_id))

            for actor_user_id, target_user_id in game.serial_kills.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_SERIAL:
                    continue
                if actor_user_id not in alive:
                    continue
                if target_user_id not in alive or actor_user_id == target_user_id:
                    continue
                if visit_and_check(actor_user_id, target_user_id):
                    continue
                night_killers.add(actor_user_id)
                kill_attempts.append((target_user_id, "serial", actor_user_id))

            witch_saved_targets: set[int] = set()
            for actor_user_id, target_user_id in game.witch_save_targets.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_WITCH:
                    continue
                if actor_user_id in game.witch_save_used:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if target_user_id not in alive:
                    continue
                if target_user_id != actor_user_id and visit_and_check(actor_user_id, target_user_id):
                    continue
                game.witch_save_used.add(actor_user_id)
                witch_saved_targets.add(target_user_id)

            for actor_user_id, target_user_id in game.witch_kill_targets.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_WITCH:
                    continue
                if actor_user_id in game.witch_kill_used:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if target_user_id not in alive or actor_user_id == target_user_id:
                    continue
                if visit_and_check(actor_user_id, target_user_id):
                    continue
                game.witch_kill_used.add(actor_user_id)
                night_killers.add(actor_user_id)
                kill_attempts.append((target_user_id, "witch", actor_user_id))

            bomber_targets: set[int] = set()
            for actor_user_id, target_user_id in game.bomber_mines.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_BOMBER:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if target_user_id not in alive or actor_user_id == target_user_id:
                    continue
                if visit_and_check(actor_user_id, target_user_id):
                    continue
                bomber_targets.add(target_user_id)

            vampire_bites: list[tuple[int, int]] = []
            for actor_user_id, target_user_id in game.vampire_bites.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_VAMPIRE:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if target_user_id not in alive or actor_user_id == target_user_id:
                    continue
                if visit_and_check(actor_user_id, target_user_id):
                    continue
                vampire_bites.append((actor_user_id, target_user_id))

            detective_checks: list[tuple[int, int]] = []
            for actor_user_id, target_user_id in game.detective_checks.items():
                if game.roles.get(actor_user_id) != MAFIA_ROLE_DETECTIVE:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if target_user_id not in alive or actor_user_id == target_user_id:
                    continue
                if visit_and_check(actor_user_id, target_user_id):
                    continue
                detective_checks.append((actor_user_id, target_user_id))

            for actor_user_id, target_user_id in detective_checks:
                target_label = game.players.get(target_user_id, f"user:{target_user_id}")
                safe_target_label = html_escape(target_label)
                moved = target_user_id in night_movers and target_user_id not in night_hidden_movers
                verdict = "да" if moved else "нет"
                private_reports.append(
                    (
                        actor_user_id,
                        (
                            f"<b>Отчёт детектива (ночь {game.round_no})</b>\n"
                            f"Проверка: <b>{safe_target_label}</b>\n"
                            f"Выходил ночью из дома: <code>{verdict}</code>"
                        ),
                    )
                )

            for actor_user_id in sorted(killed_by_veteran):
                kill_attempts.insert(0, (actor_user_id, "veteran", None))

            for target_user_id, turns_left in list(game.poisoned_players.items()):
                if target_user_id not in alive:
                    game.poisoned_players.pop(target_user_id, None)
                    continue
                if target_user_id in priest_saved:
                    game.poisoned_players.pop(target_user_id, None)
                    public_notes.append(
                        f"{game.players.get(target_user_id, f'user:{target_user_id}')} получил защиту священника от проклятия."
                    )
                    continue
                if turns_left <= 1:
                    game.poisoned_players.pop(target_user_id, None)
                    kill_attempts.append((target_user_id, "poison", None))
                else:
                    game.poisoned_players[target_user_id] = turns_left - 1

            eliminated: set[int] = set()
            eliminated_by: dict[int, int | None] = {}

            def try_eliminate(target_user_id: int, *, source: str, attacker_user_id: int | None) -> None:
                if target_user_id not in game.alive_player_ids:
                    return
                if target_user_id in eliminated:
                    return
                if source in {"maniac", "witch", "poison"} and target_user_id in priest_saved:
                    return
                if source != "veteran" and target_user_id in doctor_saved:
                    return
                if source != "veteran":
                    bodyguard_user_id = bodyguard_by_target.get(target_user_id)
                    if (
                        bodyguard_user_id is not None
                        and bodyguard_user_id in game.alive_player_ids
                        and bodyguard_user_id not in eliminated
                        and bodyguard_user_id != target_user_id
                    ):
                        eliminated.add(bodyguard_user_id)
                        eliminated_by[bodyguard_user_id] = attacker_user_id
                        protected_label = game.players.get(target_user_id, f"user:{target_user_id}")
                        guard_label = game.players.get(bodyguard_user_id, f"user:{bodyguard_user_id}")
                        public_notes.append(f"{guard_label} закрыл собой {protected_label}.")
                        return

                eliminated.add(target_user_id)
                eliminated_by[target_user_id] = attacker_user_id

            for target_user_id, source, attacker_user_id in kill_attempts:
                try_eliminate(target_user_id, source=source, attacker_user_id=attacker_user_id)

            for user_id in sorted(eliminated):
                game.alive_player_ids.discard(user_id)

            terrorist_chain: list[int] = [
                user_id for user_id in sorted(eliminated) if game.roles.get(user_id) == MAFIA_ROLE_TERRORIST
            ]
            extra_eliminated: set[int] = set()
            while terrorist_chain:
                terrorist_user_id = terrorist_chain.pop(0)
                candidates = [user_id for user_id in game.alive_player_ids if user_id != terrorist_user_id]
                if not candidates:
                    continue
                preferred = eliminated_by.get(terrorist_user_id)
                if preferred is not None and preferred in candidates:
                    target_user_id = preferred
                else:
                    target_user_id = random.choice(candidates)
                if target_user_id in extra_eliminated:
                    continue
                game.alive_player_ids.discard(target_user_id)
                extra_eliminated.add(target_user_id)
                terrorist_label = game.players.get(terrorist_user_id, f"user:{terrorist_user_id}")
                target_label = game.players.get(target_user_id, f"user:{target_user_id}")
                public_notes.append(f"{terrorist_label} забрал с собой {target_label}.")
                if game.roles.get(target_user_id) == MAFIA_ROLE_TERRORIST:
                    terrorist_chain.append(target_user_id)

            for target_user_id in bomber_targets:
                if target_user_id in game.alive_player_ids:
                    game.mined_players.add(target_user_id)

            for target_user_id in poison_targets:
                if target_user_id not in game.alive_player_ids:
                    continue
                if target_user_id in doctor_saved or target_user_id in priest_saved or target_user_id in witch_saved_targets:
                    continue
                game.poisoned_players[target_user_id] = 1

            revived_user_id: int | None = None
            for actor_user_id, target_user_id in sorted(game.reanimator_targets.items(), key=lambda item: item[0]):
                if game.roles.get(actor_user_id) != MAFIA_ROLE_REANIMATOR:
                    continue
                if actor_user_id in game.reanimator_used:
                    continue
                if not actor_can_act(actor_user_id):
                    continue
                if actor_user_id not in game.alive_player_ids:
                    continue
                if target_user_id in game.alive_player_ids:
                    continue
                game.alive_player_ids.add(target_user_id)
                game.reanimator_used.add(actor_user_id)
                revived_user_id = target_user_id
                revived_label = game.players.get(target_user_id, f"user:{target_user_id}")
                public_notes.append(f"Реаниматор вернул в игру {revived_label}.")
                break

            converted_targets: list[int] = []
            for actor_user_id, target_user_id in vampire_bites:
                if actor_user_id not in game.alive_player_ids:
                    continue
                if target_user_id not in game.alive_player_ids:
                    continue
                target_team = self._mafia_team_for_user(game, target_user_id)
                if target_team in {MAFIA_TEAM_MAFIA, MAFIA_TEAM_VAMPIRE}:
                    continue
                if target_user_id in doctor_saved or target_user_id in priest_saved:
                    continue
                game.vampire_team.add(actor_user_id)
                game.vampire_team.add(target_user_id)
                game.roles[target_user_id] = MAFIA_ROLE_VAMPIRE_THRALL
                converted_targets.append(target_user_id)

            if converted_targets:
                converted_names = ", ".join(game.players.get(user_id, f"user:{user_id}") for user_id in converted_targets)
                public_notes.append(f"Вампиры обратили игроков: {converted_names}.")

            final_eliminated = set(eliminated) | set(extra_eliminated)
            if revived_user_id is not None:
                final_eliminated.discard(revived_user_id)
                eliminated_by.pop(revived_user_id, None)

            killed_user_ids = tuple(sorted(final_eliminated))
            killed_user_id = killed_user_ids[0] if killed_user_ids else None
            killed_user_role = game.roles.get(killed_user_id) if killed_user_id is not None else None

            for user_id in sorted(game.child_revealed):
                if user_id in game.child_revealed_announced:
                    continue
                if user_id in game.alive_player_ids:
                    label = game.players.get(user_id, f"user:{user_id}")
                    public_notes.append(f"{label} раскрылся как подтверждённый мирный.")
                    game.child_revealed_announced.add(user_id)

            game.last_night_killers = set(night_killers)
            game.last_night_movers = set(night_movers)
            game.last_night_hidden_movers = set(night_hidden_movers)
            game.mafia_private_reports = {user_id: text for user_id, text in private_reports}

            self._clear_mafia_night_actions(game)

            winner_text = self._resolve_mafia_winner(game)
            if winner_text is not None:
                game.status = "finished"
                game.phase = "finished"
                game.winner_text = winner_text
                self._active_by_chat.pop(game.chat_id, None)
            else:
                game.phase = "day_discussion"
                game.phase_started_at = datetime.now(timezone.utc)

            resolution = NightResolution(
                killed_user_id=killed_user_id,
                killed_user_label=game.players.get(killed_user_id) if killed_user_id is not None else None,
                killed_user_role=killed_user_role,
                sheriff_checked_user_id=sheriff_checked_user_id,
                sheriff_checked_user_label=game.players.get(sheriff_checked_user_id)
                if sheriff_checked_user_id is not None
                else None,
                sheriff_checked_is_mafia=sheriff_checked_is_mafia,
                tie_on_mafia_vote=tie_on_mafia_vote,
                winner_text=winner_text,
                killed_user_ids=killed_user_ids,
                public_notes=tuple(public_notes),
                private_reports=tuple(private_reports),
            )
            return game, resolution, None

    async def mafia_resolve_day_vote(self, *, game_id: str) -> tuple[GroupGame | None, DayVoteResolution | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "mafia":
                return game, None, "Это не мафия"
            if game.status != "started" or game.phase != "day_vote":
                return game, None, "Сейчас не фаза голосования"

            alive_sorted = sorted(game.alive_player_ids)
            vote_protocol = tuple((voter, game.day_votes.get(voter)) for voter in alive_sorted)
            public_notes: list[str] = []

            vote_counts: dict[int, int] = {}
            for voter_user_id, target_user_id in game.day_votes.items():
                if voter_user_id not in game.alive_player_ids:
                    continue
                if target_user_id not in game.alive_player_ids:
                    continue
                if target_user_id == game.day_vote_immune_user_id:
                    continue
                vote_counts[target_user_id] = vote_counts.get(target_user_id, 0) + 1

            candidate_user_id: int | None = None
            tie = False
            if vote_counts:
                max_votes = max(vote_counts.values())
                top_targets = [user_id for user_id, votes in vote_counts.items() if votes == max_votes]
                if len(top_targets) == 1:
                    candidate_user_id = top_targets[0]
                else:
                    tie = True

            game.day_votes.clear()

            winner_text: str | None = None
            opened_execution_confirm = False

            if candidate_user_id is not None:
                game.phase = "day_execution_confirm"
                game.phase_started_at = datetime.now(timezone.utc)
                game.mafia_execution_candidate_user_id = candidate_user_id
                game.execution_confirm_votes.clear()
                game.execution_confirm_message_id = None
                opened_execution_confirm = True
            else:
                winner_text = self._resolve_mafia_winner(game)
                if winner_text is not None:
                    game.status = "finished"
                    game.phase = "finished"
                    game.winner_text = winner_text
                    game.execution_confirm_message_id = None
                    self._active_by_chat.pop(game.chat_id, None)
                else:
                    game.round_no += 1
                    game.phase = "night"
                    game.phase_started_at = datetime.now(timezone.utc)
                    self._clear_mafia_night_actions(game)
                    game.mafia_execution_candidate_user_id = None
                    game.execution_confirm_votes.clear()
                    game.execution_confirm_message_id = None

            if game.day_vote_immune_user_id is not None and game.day_vote_immune_user_id in game.players:
                immune_label = game.players.get(game.day_vote_immune_user_id, f"user:{game.day_vote_immune_user_id}")
                public_notes.append(f"Адвокат обеспечил дневную неприкосновенность для {immune_label}.")

            resolution = DayVoteResolution(
                candidate_user_id=candidate_user_id,
                candidate_user_label=game.players.get(candidate_user_id) if candidate_user_id is not None else None,
                tie=tie,
                opened_execution_confirm=opened_execution_confirm,
                vote_protocol=vote_protocol,
                winner_text=winner_text,
                public_notes=tuple(public_notes),
            )
            return game, resolution, None

    async def mafia_register_execution_confirm_vote(
        self,
        *,
        game_id: str,
        voter_user_id: int,
        approve: bool,
    ) -> tuple[GroupGame | None, bool | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "mafia":
                return game, None, "Это не мафия"
            if game.status != "started" or game.phase != "day_execution_confirm":
                return game, None, "Сейчас не фаза подтверждения"
            if voter_user_id not in game.alive_player_ids:
                return game, None, "Вы выбиты и не можете голосовать"

            previous = game.execution_confirm_votes.get(voter_user_id)
            game.execution_confirm_votes[voter_user_id] = approve
            return game, previous, None

    async def mafia_is_execution_confirm_ready(self, *, game_id: str) -> tuple[GroupGame | None, bool, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, False, "Игра не найдена"
            if game.kind != "mafia":
                return game, False, "Это не мафия"
            if game.status != "started" or game.phase != "day_execution_confirm":
                return game, False, "Сейчас не фаза подтверждения"

            alive = set(game.alive_player_ids)
            voted = {voter for voter in game.execution_confirm_votes if voter in alive}
            return game, len(voted) == len(alive), None

    async def mafia_resolve_execution_confirm(
        self,
        *,
        game_id: str,
    ) -> tuple[GroupGame | None, ExecutionConfirmResolution | None, str | None]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, None, "Игра не найдена"
            if game.kind != "mafia":
                return game, None, "Это не мафия"
            if game.status != "started" or game.phase != "day_execution_confirm":
                return game, None, "Сейчас не фаза подтверждения"

            candidate_user_id = game.mafia_execution_candidate_user_id
            alive_sorted = sorted(game.alive_player_ids)

            protocol: list[tuple[int, bool | None]] = []
            yes_count = 0
            no_count = 0
            for voter in alive_sorted:
                vote = game.execution_confirm_votes.get(voter)
                protocol.append((voter, vote))
                if vote is True:
                    yes_count += 1
                elif vote is False:
                    no_count += 1

            passed = yes_count > no_count
            executed_user_id: int | None = None
            executed_user_role: str | None = None
            public_notes: list[str] = []

            if passed and candidate_user_id is not None and candidate_user_id in game.alive_player_ids:
                executed_user_id = candidate_user_id
                executed_user_role = game.roles.get(candidate_user_id)
                game.alive_player_ids.remove(candidate_user_id)

                if candidate_user_id in game.mined_players:
                    game.mined_players.discard(candidate_user_id)
                    explosion_targets = [user_id for user_id in game.alive_player_ids if user_id != candidate_user_id]
                    if explosion_targets:
                        exploded_user_id = random.choice(explosion_targets)
                        game.alive_player_ids.discard(exploded_user_id)
                        exploded_label = game.players.get(exploded_user_id, f"user:{exploded_user_id}")
                        public_notes.append(f"Мина сработала: вместе с целью выбыл {exploded_label}.")

                if executed_user_role == MAFIA_ROLE_TERRORIST:
                    candidates = [user_id for user_id in game.alive_player_ids if user_id != executed_user_id]
                    if candidates:
                        extra_user_id = random.choice(candidates)
                        game.alive_player_ids.discard(extra_user_id)
                        extra_label = game.players.get(extra_user_id, f"user:{extra_user_id}")
                        public_notes.append(f"Террорист забрал с собой {extra_label}.")

            game.mafia_execution_candidate_user_id = None
            game.execution_confirm_votes.clear()
            game.execution_confirm_message_id = None

            if executed_user_role == MAFIA_ROLE_JESTER:
                winner_text = "Победа шута: его казнили днём."
            else:
                winner_text = self._resolve_mafia_winner(game)
            if winner_text is not None:
                game.status = "finished"
                game.phase = "finished"
                game.winner_text = winner_text
                game.execution_confirm_message_id = None
                self._active_by_chat.pop(game.chat_id, None)
            else:
                game.round_no += 1
                game.phase = "night"
                game.phase_started_at = datetime.now(timezone.utc)
                self._clear_mafia_night_actions(game)
                game.day_votes.clear()
                game.day_vote_immune_user_id = None

            resolution = ExecutionConfirmResolution(
                yes_count=yes_count,
                no_count=no_count,
                passed=passed,
                executed_user_id=executed_user_id,
                executed_user_label=game.players.get(executed_user_id) if executed_user_id is not None else None,
                executed_user_role=executed_user_role,
                vote_protocol=tuple(protocol),
                winner_text=winner_text,
                public_notes=tuple(public_notes),
            )
            return game, resolution, None

    async def mafia_get_vote_snapshot(self, *, game_id: str) -> tuple[GroupGame | None, int, int]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, 0, 0
            unique_votes = len({voter for voter in game.day_votes if voter in game.alive_player_ids})
            alive = len(game.alive_player_ids)
            return game, unique_votes, alive

    async def mafia_get_execution_confirm_snapshot(self, *, game_id: str) -> tuple[GroupGame | None, int, int, int, int]:
        async with self._lock:
            game = self._by_id.get(game_id)
            if game is None:
                return None, 0, 0, 0, 0

            alive = set(game.alive_player_ids)
            voted = {voter for voter in game.execution_confirm_votes if voter in alive}
            yes_count = sum(1 for voter in voted if game.execution_confirm_votes.get(voter) is True)
            no_count = sum(1 for voter in voted if game.execution_confirm_votes.get(voter) is False)
            return game, len(voted), len(alive), yes_count, no_count

    @staticmethod
    def _clear_mafia_night_actions(game: GroupGame) -> None:
        game.mafia_votes.clear()
        game.sheriff_checks.clear()
        game.inspector_checks.clear()
        game.doctor_saves.clear()
        game.escort_blocks.clear()
        game.bodyguard_protects.clear()
        game.journalist_checks.clear()
        game.journalist_first_pick.clear()
        game.priest_protects.clear()
        game.psychologist_checks.clear()
        game.detective_checks.clear()
        game.don_checks.clear()
        game.lawyer_targets.clear()
        game.poisoner_targets.clear()
        game.reanimator_targets.clear()
        game.maniac_kills.clear()
        game.serial_kills.clear()
        game.witch_save_targets.clear()
        game.witch_kill_targets.clear()
        game.vampire_bites.clear()
        game.bomber_mines.clear()
        game.veteran_alerts.clear()

    @staticmethod
    def _base_mafia_team_for_role(role: str | None) -> str:
        if role in {MAFIA_ROLE_VAMPIRE, MAFIA_ROLE_VAMPIRE_THRALL}:
            return MAFIA_TEAM_VAMPIRE
        if role in MAFIA_MAFIA_ROLES:
            return MAFIA_TEAM_MAFIA
        if role in MAFIA_NEUTRAL_ROLES:
            return MAFIA_TEAM_NEUTRAL
        return MAFIA_TEAM_CIVILIAN

    @classmethod
    def _mafia_team_for_user(cls, game: GroupGame, user_id: int) -> str:
        if user_id in game.vampire_team:
            return MAFIA_TEAM_VAMPIRE
        return cls._base_mafia_team_for_role(game.roles.get(user_id))

    @staticmethod
    def _human_team_name(team_code: str) -> str:
        if team_code == MAFIA_TEAM_MAFIA:
            return "мафия"
        if team_code == MAFIA_TEAM_CIVILIAN:
            return "мирные"
        if team_code == MAFIA_TEAM_VAMPIRE:
            return "вампиры"
        return "нейтрал"

    @classmethod
    def _mafia_night_action_targets(cls, game: GroupGame, *, actor_user_id: int) -> tuple[int, ...]:
        if game.kind != "mafia" or game.status != "started" or game.phase != "night":
            return ()
        if actor_user_id not in game.alive_player_ids:
            return ()

        role = game.roles.get(actor_user_id)
        if role is None:
            return ()

        alive = set(game.alive_player_ids)
        dead = set(game.players) - alive

        if role in MAFIA_ATTACKER_ROLES:
            return tuple(
                user_id
                for user_id in sorted(alive)
                if user_id != actor_user_id and cls._mafia_team_for_user(game, user_id) != MAFIA_TEAM_MAFIA
            )

        if role in {
            MAFIA_ROLE_COMMISSIONER,
            MAFIA_ROLE_INSPECTOR,
            MAFIA_ROLE_ESCORT,
            MAFIA_ROLE_BODYGUARD,
            MAFIA_ROLE_PSYCHOLOGIST,
            MAFIA_ROLE_DETECTIVE,
            MAFIA_ROLE_MANIAC,
            MAFIA_ROLE_SERIAL,
            MAFIA_ROLE_VAMPIRE,
            MAFIA_ROLE_BOMBER,
        }:
            return tuple(user_id for user_id in sorted(alive) if user_id != actor_user_id)

        if role in {MAFIA_ROLE_DOCTOR, MAFIA_ROLE_PRIEST, MAFIA_ROLE_LAWYER}:
            return tuple(sorted(alive))

        if role == MAFIA_ROLE_JOURNALIST:
            first = game.journalist_first_pick.get(actor_user_id)
            if first is None:
                return tuple(user_id for user_id in sorted(alive) if user_id != actor_user_id)
            return tuple(user_id for user_id in sorted(alive) if user_id != actor_user_id and user_id != first)

        if role == MAFIA_ROLE_VETERAN:
            if actor_user_id in game.veteran_used:
                return ()
            return (actor_user_id,)

        if role == MAFIA_ROLE_REANIMATOR:
            if actor_user_id in game.reanimator_used:
                return ()
            return tuple(sorted(dead))

        if role == MAFIA_ROLE_WITCH:
            targets: list[int] = []
            if actor_user_id not in game.witch_save_used:
                targets.append(actor_user_id)
            if actor_user_id not in game.witch_kill_used:
                targets.extend(user_id for user_id in sorted(alive) if user_id != actor_user_id)
            return tuple(targets)

        if role == MAFIA_ROLE_CHILD:
            if actor_user_id in game.child_revealed:
                return ()
            return (actor_user_id,)

        return ()

    @staticmethod
    def _default_bunker_seats(*, players_count: int) -> int:
        if players_count <= 8:
            return 2
        if players_count <= 10:
            return 4
        if players_count <= 12:
            return 5
        return max(2, int(players_count * 0.4))

    @staticmethod
    def _bunker_field_label(field_key: str) -> str:
        labels = {
            "profession": "Профессия",
            "age": "Возраст",
            "gender": "Пол",
            "health_condition": "Состояние здоровья",
            "skill": "Навык",
            "hobby": "Хобби",
            "phobia": "Фобия",
            "trait": "Особенность",
            "item": "Предмет в рюкзаке",
        }
        return labels.get(field_key, field_key)

    @staticmethod
    def _bunker_card_value(card: BunkerCard, field_key: str) -> str:
        return getattr(card, field_key, "-")

    @staticmethod
    def _alive_in_bunker_order(game: GroupGame) -> list[int]:
        ordered: list[int] = []
        seen: set[int] = set()
        for user_id in game.bunker_reveal_order:
            if user_id in game.alive_player_ids and user_id not in seen:
                ordered.append(user_id)
                seen.add(user_id)
        for user_id in sorted(game.alive_player_ids):
            if user_id not in seen:
                ordered.append(user_id)
                seen.add(user_id)
        return ordered

    @classmethod
    def _hidden_bunker_fields_for_user(cls, game: GroupGame, user_id: int) -> tuple[str, ...]:
        if user_id not in game.bunker_cards:
            return ()
        revealed = game.bunker_revealed_fields.get(user_id, set())
        return tuple(field_key for field_key in BUNKER_CARD_FIELDS if field_key not in revealed)

    @classmethod
    def _prepare_bunker_reveal_phase(cls, game: GroupGame) -> bool:
        alive_order = cls._alive_in_bunker_order(game)
        reveal_users = [user_id for user_id in alive_order if cls._hidden_bunker_fields_for_user(game, user_id)]
        if reveal_users:
            game.phase = "bunker_reveal"
            game.phase_started_at = datetime.now(timezone.utc)
            game.bunker_round_reveal_user_ids = tuple(reveal_users)
            game.bunker_reveal_cursor = 0
            game.bunker_current_actor_user_id = reveal_users[0]
            game.bunker_votes.clear()
            return True

        cls._open_bunker_vote_phase(game)
        return False

    @staticmethod
    def _open_bunker_vote_phase(game: GroupGame) -> None:
        game.phase = "bunker_vote"
        game.phase_started_at = datetime.now(timezone.utc)
        game.bunker_round_reveal_user_ids = ()
        game.bunker_reveal_cursor = 0
        game.bunker_current_actor_user_id = None
        game.bunker_votes.clear()

    @classmethod
    def _advance_bunker_reveal_cursor(cls, game: GroupGame) -> tuple[bool, int | None, str | None]:
        participants = game.bunker_round_reveal_user_ids
        if not participants:
            cls._open_bunker_vote_phase(game)
            return True, None, None

        next_index = game.bunker_reveal_cursor + 1
        if next_index >= len(participants):
            cls._open_bunker_vote_phase(game)
            return True, None, None

        next_actor_user_id = participants[next_index]
        game.bunker_reveal_cursor = next_index
        game.bunker_current_actor_user_id = next_actor_user_id
        game.phase_started_at = datetime.now(timezone.utc)
        next_actor_label = game.players.get(next_actor_user_id, f"user:{next_actor_user_id}")
        return False, next_actor_user_id, next_actor_label

    @staticmethod
    def _build_bunker_cards(game: GroupGame) -> tuple[dict[int, BunkerCard], set[str], str | None]:
        player_ids = list(game.players.keys())
        random.shuffle(player_ids)
        players_count = len(player_ids)

        if players_count == 0:
            return {}, set(), "В лобби нет игроков"

        assigned_values_by_field: dict[str, list[str]] = {}
        overflow_fields: set[str] = set()

        for field_key in BUNKER_CARD_FIELDS:
            data_key = BUNKER_FIELD_TO_DATA_KEY[field_key]
            pool = list(BUNKER_DATA.get(data_key, ()))
            if not pool:
                return {}, set(), f"Для поля «{GameStore._bunker_field_label(field_key)}» не найдено значений"

            random.shuffle(pool)
            if len(pool) >= players_count:
                assigned_values_by_field[field_key] = pool[:players_count]
                continue

            overflow_fields.add(field_key)
            values = list(pool)
            for _ in range(players_count - len(pool)):
                values.append(random.choice(pool))
            random.shuffle(values)
            assigned_values_by_field[field_key] = values

        cards: dict[int, BunkerCard] = {}
        for index, user_id in enumerate(player_ids):
            cards[user_id] = BunkerCard(
                profession=assigned_values_by_field["profession"][index],
                age=assigned_values_by_field["age"][index],
                gender=assigned_values_by_field["gender"][index],
                health_condition=assigned_values_by_field["health_condition"][index],
                skill=assigned_values_by_field["skill"][index],
                hobby=assigned_values_by_field["hobby"][index],
                phobia=assigned_values_by_field["phobia"][index],
                trait=assigned_values_by_field["trait"][index],
                item=assigned_values_by_field["item"][index],
            )

        return cards, overflow_fields, None

    @staticmethod
    def _zlob_draw_white_cards(game: GroupGame, *, count: int) -> list[str]:
        cards: list[str] = []
        need = max(0, count)
        while len(cards) < need:
            if not game.zlob_white_deck:
                if not game.zlob_white_discard:
                    break
                random.shuffle(game.zlob_white_discard)
                game.zlob_white_deck.extend(game.zlob_white_discard)
                game.zlob_white_discard.clear()
            if not game.zlob_white_deck:
                break
            cards.append(game.zlob_white_deck.pop())
        return cards

    @staticmethod
    def _zlob_draw_black_card(game: GroupGame) -> ZlobBlackCard | None:
        if not game.zlob_black_deck:
            if not game.zlob_black_discard:
                return None
            random.shuffle(game.zlob_black_discard)
            game.zlob_black_deck.extend(game.zlob_black_discard)
            game.zlob_black_discard.clear()
        if not game.zlob_black_deck:
            return None
        return game.zlob_black_deck.pop()

    @staticmethod
    def _prepare_zlob_private_phase(game: GroupGame) -> tuple[bool, str | None]:
        black_card = GameStore._zlob_draw_black_card(game)
        if black_card is None:
            return False, "Закончились чёрные карточки для следующего раунда"

        game.zlob_black_text = black_card.text
        game.zlob_black_slots = black_card.slots
        game.zlob_black_discard.append(black_card)
        game.zlob_submissions.clear()
        game.zlob_options = ()
        game.zlob_option_owner_user_ids = ()
        game.zlob_votes.clear()
        game.phase = "private_answers"
        game.phase_started_at = datetime.now(timezone.utc)
        return True, None

    @staticmethod
    def _open_zlob_vote(game: GroupGame) -> tuple[bool, str | None]:
        if game.zlob_black_text is None:
            return False, "Чёрная карточка не задана"

        options: list[tuple[str, int]] = []
        needed_slots = max(1, int(game.zlob_black_slots))
        for player_id in sorted(game.players.keys()):
            submission = game.zlob_submissions.get(player_id)
            if not submission:
                continue
            if len(submission) != needed_slots:
                continue
            option_text = submission[0] if len(submission) == 1 else " + ".join(submission)
            options.append((option_text, player_id))

        if len(options) < 2:
            return False, "Нужно минимум два ответа для голосования"

        random.shuffle(options)
        game.zlob_options = tuple(option_text for option_text, _ in options)
        game.zlob_option_owner_user_ids = tuple(owner_user_id for _, owner_user_id in options)
        game.zlob_votes.clear()
        game.phase = "public_vote"
        game.phase_started_at = datetime.now(timezone.utc)
        return True, None

    @staticmethod
    def _sorted_zlob_scores(game: GroupGame) -> list[tuple[int, int]]:
        return sorted(
            game.zlob_scores.items(),
            key=lambda item: (
                -item[1],
                game.players.get(item[0], f"user:{item[0]}").lower(),
                item[0],
            ),
        )

    @staticmethod
    def _resolve_zlob_winner_text(game: GroupGame, *, winner_user_ids: tuple[int, ...], top_score: int) -> str:
        if not winner_user_ids:
            return "Победитель не определён."
        if len(winner_user_ids) == 1:
            winner_label = game.players.get(winner_user_ids[0], f"user:{winner_user_ids[0]}")
            return f"Побеждает {winner_label} с результатом {top_score}."
        labels = ", ".join(game.players.get(user_id, f"user:{user_id}") for user_id in winner_user_ids)
        return f"Ничья: {labels}. У каждого по {top_score}."

    @staticmethod
    def _build_bred_selector_order(game: GroupGame) -> tuple[int, ...]:
        players = list(game.players.keys())
        if not players:
            return ()
        rounds = max(game.bred_rounds, len(players))
        random.shuffle(players)

        # First pass guarantees everyone picks at least once, extra rounds stay random.
        order = list(players)
        for _ in range(rounds - len(players)):
            order.append(random.choice(players))
        return tuple(order)

    @staticmethod
    def _selector_for_round(game: GroupGame, *, round_no: int) -> int | None:
        if round_no <= 0:
            return None
        index = round_no - 1
        if index >= len(game.bred_selector_user_ids_by_round):
            return None
        return game.bred_selector_user_ids_by_round[index]

    @staticmethod
    def _question_key(category: str, question_index: int) -> str:
        return f"{category}::{question_index}"

    @staticmethod
    def _available_bred_categories(game: GroupGame) -> list[str]:
        available: list[str] = []
        for category in BRED_CATEGORIES:
            questions = BRED_QUESTIONS_BY_CATEGORY.get(category, ())
            if not questions:
                continue
            has_unused = any(
                GameStore._question_key(category, idx) not in game.bred_used_question_keys
                for idx in range(len(questions))
            )
            if has_unused:
                available.append(category)
        return available

    @staticmethod
    def _pick_bred_category_options(game: GroupGame) -> tuple[str, ...]:
        categories = GameStore._available_bred_categories(game)
        if not categories:
            return ()
        random.shuffle(categories)
        return tuple(categories[: min(BRED_CATEGORY_CHOICES, len(categories))])

    @staticmethod
    def _pick_bred_question(game: GroupGame, *, category: str) -> BredQuestion | None:
        questions = BRED_QUESTIONS_BY_CATEGORY.get(category, ())
        if not questions:
            return None

        candidate_indexes = [
            idx
            for idx in range(len(questions))
            if GameStore._question_key(category, idx) not in game.bred_used_question_keys
        ]
        if not candidate_indexes:
            return None

        question_index = random.choice(candidate_indexes)
        game.bred_used_question_keys.add(GameStore._question_key(category, question_index))
        return questions[question_index]

    @staticmethod
    def _fill_bred_prompt(prompt_with_blank: str, correct_answer: str) -> str:
        filled = re.sub(r"_{3,}", correct_answer, prompt_with_blank, count=1)
        if filled != prompt_with_blank:
            return filled
        if correct_answer in prompt_with_blank:
            return prompt_with_blank
        return f"{prompt_with_blank} {correct_answer}".strip()

    @staticmethod
    def _build_bred_fact_text(question: BredQuestion) -> str:
        if question.fact_text:
            return question.fact_text
        return GameStore._fill_bred_prompt(question.prompt_with_blank, question.correct_answer)

    @staticmethod
    def _open_bred_vote(game: GroupGame) -> tuple[bool, str | None]:
        if game.bred_correct_answer is None:
            return False, "Не задан правильный ответ"

        options: list[tuple[str, int | None]] = []
        for player_id in sorted(game.players.keys()):
            lie = game.bred_lies.get(player_id)
            if lie is None:
                continue
            options.append((lie, player_id))

        if not options:
            return False, "Нужен хотя бы один ложный вариант перед голосованием"

        options.append((game.bred_correct_answer, None))
        random.shuffle(options)

        game.bred_options = tuple(option_text for option_text, _ in options)
        game.bred_option_owner_user_ids = tuple(owner_user_id for _, owner_user_id in options)
        game.bred_votes.clear()
        game.phase = "public_vote"
        game.phase_started_at = datetime.now(timezone.utc)
        return True, None

    @staticmethod
    def _normalize_bred_answer(value: str) -> str:
        return " ".join(value.casefold().split())

    @staticmethod
    def _sorted_bred_scores(game: GroupGame) -> list[tuple[int, int]]:
        return sorted(
            game.bred_scores.items(),
            key=lambda item: (
                -item[1],
                game.players.get(item[0], f"user:{item[0]}").lower(),
                item[0],
            ),
        )

    @staticmethod
    def _resolve_bred_winner_text(game: GroupGame, *, winner_user_ids: tuple[int, ...], top_score: int) -> str:
        if not winner_user_ids:
            return "Бредовуха завершена без результатов."

        if len(winner_user_ids) == 1:
            winner_label = game.players.get(winner_user_ids[0], f"user:{winner_user_ids[0]}")
            return f"Победил {winner_label} с результатом {top_score}."

        labels = ", ".join(game.players.get(user_id, f"user:{user_id}") for user_id in winner_user_ids)
        return f"Ничья между {labels} ({top_score})."

    @staticmethod
    def _advance_whoami_turn(game: GroupGame) -> None:
        if not game.whoami_turn_order:
            game.whoami_current_actor_user_id = None
            game.whoami_current_actor_index = 0
            return

        if len(game.whoami_solved_user_ids) >= len(game.whoami_turn_order):
            game.whoami_current_actor_user_id = None
            game.whoami_current_actor_index = 0
            return

        total_players = len(game.whoami_turn_order)
        current_index = game.whoami_current_actor_index
        for offset in range(1, total_players + 1):
            next_index = (current_index + offset) % total_players
            candidate_user_id = game.whoami_turn_order[next_index]
            if candidate_user_id in game.whoami_solved_user_ids:
                continue
            if current_index + offset >= total_players:
                game.round_no += 1
            game.whoami_current_actor_index = next_index
            game.whoami_current_actor_user_id = candidate_user_id
            return

        game.whoami_current_actor_user_id = None

    @staticmethod
    def _trim_whoami_history(game: GroupGame) -> None:
        if len(game.whoami_history) <= WHOAMI_HISTORY_LIMIT:
            return
        game.whoami_history = game.whoami_history[-WHOAMI_HISTORY_LIMIT:]

    @staticmethod
    def _build_quiz_questions(*, rounds: int) -> tuple[QuizQuestion, ...]:
        if rounds <= 0:
            return ()

        pool = list(QUIZ_QUESTION_BANK)
        random.shuffle(pool)
        return tuple(pool[: min(rounds, len(pool))])

    @staticmethod
    def _current_quiz_question(game: GroupGame) -> QuizQuestion | None:
        if game.quiz_current_question_index is None:
            return None
        if game.quiz_current_question_index < 0 or game.quiz_current_question_index >= len(game.quiz_questions):
            return None
        return game.quiz_questions[game.quiz_current_question_index]

    @staticmethod
    def _sorted_quiz_scores(game: GroupGame) -> list[tuple[int, int]]:
        return sorted(
            game.quiz_scores.items(),
            key=lambda item: (
                -item[1],
                game.players.get(item[0], f"user:{item[0]}").lower(),
                item[0],
            ),
        )

    @staticmethod
    def _resolve_quiz_winner_text(game: GroupGame, scores: tuple[tuple[int, int], ...]) -> str:
        if not scores:
            return "Викторина завершена без результатов."

        top_score = scores[0][1]
        top_user_ids = [user_id for user_id, score in scores if score == top_score]
        if len(top_user_ids) == 1:
            winner_label = game.players.get(top_user_ids[0], f"user:{top_user_ids[0]}")
            return f"Победил {winner_label} с результатом {top_score}."

        labels = ", ".join(game.players.get(user_id, f"user:{user_id}") for user_id in top_user_ids)
        return f"Ничья между {labels} ({top_score})."

    @staticmethod
    def _resolve_dice_winner_text(game: GroupGame) -> str:
        if not game.dice_scores:
            return "Раунд кубиков завершён без бросков."

        ranking = sorted(
            game.dice_scores.items(),
            key=lambda item: (-item[1], game.players.get(item[0], f"user:{item[0]}").lower(), item[0]),
        )
        top_score = ranking[0][1]
        winners = [user_id for user_id, score in ranking if score == top_score]
        if len(winners) == 1:
            winner_label = game.players.get(winners[0], f"user:{winners[0]}")
            return f"Победил {winner_label}: бросок {top_score}."

        labels = ", ".join(game.players.get(user_id, f"user:{user_id}") for user_id in winners)
        return f"Ничья по максимуму {top_score}: {labels}."

    @staticmethod
    def _assign_mafia_roles(game: GroupGame) -> None:
        player_ids = list(game.players.keys())
        random.shuffle(player_ids)
        players_count = len(player_ids)

        mafia_count = max(1, players_count // 3)
        special_cap = 2 if players_count <= 7 else 5 if players_count <= 10 else 8
        target_specials = min(special_cap, max(1, players_count // 2))

        available_civilian_specials = [role for role, min_players in MAFIA_CIVILIAN_SPECIAL_POOL if players_count >= min_players]
        available_mafia_specials = [role for role, min_players in MAFIA_SPECIAL_MAFIA_POOL if players_count >= min_players]
        available_neutrals = [role for role, min_players in MAFIA_NEUTRAL_POOL if players_count >= min_players]

        mafia_roles: list[str] = [MAFIA_ROLE_MAFIA for _ in range(mafia_count)]
        mafia_special_max = min(len(available_mafia_specials), max(0, mafia_count))
        mafia_special_count = min(mafia_special_max, max(0, target_specials // 2))
        if mafia_special_count > 0:
            for index, role in enumerate(random.sample(available_mafia_specials, k=mafia_special_count)):
                mafia_roles[index] = role

        assigned_specials = mafia_special_count
        remaining_slots = players_count - mafia_count

        neutral_count = 0
        if players_count >= 11 and available_neutrals and remaining_slots >= 2:
            neutral_cap = 1 if players_count <= 12 else 2
            neutral_count = min(len(available_neutrals), neutral_cap, remaining_slots - 1)
            neutral_count = min(neutral_count, max(0, target_specials - assigned_specials))

        assigned_specials += neutral_count
        remaining_slots_after_neutral = max(0, remaining_slots - neutral_count)
        civilian_special_count = min(
            len(available_civilian_specials),
            remaining_slots_after_neutral,
            max(0, target_specials - assigned_specials),
        )

        civilian_roles: list[str] = []
        if civilian_special_count > 0:
            civilian_roles.extend(random.sample(available_civilian_specials, k=civilian_special_count))
        civilian_roles.extend([MAFIA_ROLE_CIVILIAN for _ in range(max(0, remaining_slots_after_neutral - len(civilian_roles)))])

        neutral_roles: list[str] = random.sample(available_neutrals, k=neutral_count) if neutral_count > 0 else []
        all_roles = mafia_roles + neutral_roles + civilian_roles
        if len(all_roles) < players_count:
            all_roles.extend([MAFIA_ROLE_CIVILIAN for _ in range(players_count - len(all_roles))])
        random.shuffle(all_roles)

        roles = {player_id: all_roles[index] for index, player_id in enumerate(player_ids)}
        game.roles = roles
        game.vampire_team = {
            user_id for user_id, role in roles.items() if role in {MAFIA_ROLE_VAMPIRE, MAFIA_ROLE_VAMPIRE_THRALL}
        }

    @staticmethod
    def _resolve_mafia_winner(game: GroupGame) -> str | None:
        alive = sorted(game.alive_player_ids)
        if not alive:
            return "Ничья: в живых никого не осталось."

        by_team = {
            MAFIA_TEAM_CIVILIAN: 0,
            MAFIA_TEAM_MAFIA: 0,
            MAFIA_TEAM_NEUTRAL: 0,
            MAFIA_TEAM_VAMPIRE: 0,
        }
        alive_roles: dict[int, str] = {}
        for user_id in alive:
            role = game.roles.get(user_id, MAFIA_ROLE_CIVILIAN)
            alive_roles[user_id] = role
            team = GameStore._mafia_team_for_user(game, user_id)
            by_team[team] = by_team.get(team, 0) + 1

        if by_team[MAFIA_TEAM_VAMPIRE] > 0 and by_team[MAFIA_TEAM_VAMPIRE] == len(alive):
            return "Победа вампиров: вся деревня обращена."

        if by_team[MAFIA_TEAM_MAFIA] > 0 and by_team[MAFIA_TEAM_MAFIA] >= (
            by_team[MAFIA_TEAM_CIVILIAN] + by_team[MAFIA_TEAM_NEUTRAL] + by_team[MAFIA_TEAM_VAMPIRE]
        ):
            return "Победа мафии: мафия получила контроль над голосованием."

        if by_team[MAFIA_TEAM_VAMPIRE] > 0 and by_team[MAFIA_TEAM_VAMPIRE] >= (
            by_team[MAFIA_TEAM_CIVILIAN] + by_team[MAFIA_TEAM_NEUTRAL] + by_team[MAFIA_TEAM_MAFIA]
        ):
            return "Победа вампиров: вампирский клан доминирует."

        hostile_neutral_roles = {MAFIA_ROLE_MANIAC, MAFIA_ROLE_SERIAL, MAFIA_ROLE_WITCH}
        hostile_neutral_alive = [user_id for user_id, role in alive_roles.items() if role in hostile_neutral_roles]
        if len(alive) == 1 and hostile_neutral_alive:
            role = alive_roles[hostile_neutral_alive[0]]
            if role == MAFIA_ROLE_MANIAC:
                return "Победа маньяка: в живых остался только он."
            if role == MAFIA_ROLE_SERIAL:
                return "Победа серийного убийцы: в живых остался только он."
            return "Победа нейтрала: в живых остался только он."

        if by_team[MAFIA_TEAM_MAFIA] == 0 and by_team[MAFIA_TEAM_VAMPIRE] == 0 and not hostile_neutral_alive:
            return "Победа мирных: все угрозы устранены."

        return None

InMemoryGameStore = GameStore


@dataclass(frozen=True)
class LiveEvent:
    event_type: str
    scope: str
    revision: str
    chat_id: int | None = None
    game_id: str | None = None

    def to_payload(self) -> dict[str, str | int | None]:
        return {
            "type": self.event_type,
            "scope": self.scope,
            "revision": self.revision,
            "chat_id": self.chat_id,
            "game_id": self.game_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "LiveEvent":
        payload = json.loads(raw)
        return cls(
            event_type=str(payload.get("type") or "game_updated"),
            scope=str(payload.get("scope") or "games"),
            revision=str(payload.get("revision") or ""),
            chat_id=int(payload["chat_id"]) if payload.get("chat_id") is not None else None,
            game_id=str(payload["game_id"]) if payload.get("game_id") is not None else None,
        )


class LiveEventBroker(Protocol):
    async def publish(self, event: LiveEvent) -> None: ...
    async def subscribe(
        self,
        *,
        scope: str,
        chat_id: int | None = None,
        game_id: str | None = None,
    ) -> AsyncIterator[LiveEvent | None]: ...
    async def close(self) -> None: ...


class GameStateCodec:
    _TYPE_KEY = "$type"
    _CLASS_KEY = "class"
    _FIELDS_KEY = "fields"
    _ITEMS_KEY = "items"
    _VALUE_KEY = "value"

    def __init__(self) -> None:
        self._dataclass_registry: dict[str, type[Any]] = {}
        self._load_registry()

    def _load_registry(self) -> None:
        for value in globals().values():
            if inspect.isclass(value) and is_dataclass(value):
                self._dataclass_registry[value.__name__] = value

    def dumps(self, game: GroupGame) -> str:
        return json.dumps(self._encode(game), ensure_ascii=False, separators=(",", ":"))

    def loads(self, raw: str) -> GroupGame:
        decoded = self._decode(json.loads(raw))
        if not isinstance(decoded, GroupGame):
            raise ValueError("Decoded game payload is not GroupGame")
        return decoded

    def _encode(self, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, datetime):
            return {
                self._TYPE_KEY: "datetime",
                self._VALUE_KEY: value.isoformat(),
            }
        if is_dataclass(value):
            return {
                self._TYPE_KEY: "dataclass",
                self._CLASS_KEY: type(value).__name__,
                self._FIELDS_KEY: {
                    field_info.name: self._encode(getattr(value, field_info.name))
                    for field_info in fields(value)
                },
            }
        if isinstance(value, tuple):
            return {
                self._TYPE_KEY: "tuple",
                self._ITEMS_KEY: [self._encode(item) for item in value],
            }
        if isinstance(value, list):
            return [self._encode(item) for item in value]
        if isinstance(value, set):
            return {
                self._TYPE_KEY: "set",
                self._ITEMS_KEY: [self._encode(item) for item in sorted(value, key=repr)],
            }
        if isinstance(value, dict):
            return {
                self._TYPE_KEY: "dict",
                self._ITEMS_KEY: [
                    [self._encode(key), self._encode(item)]
                    for key, item in value.items()
                ],
            }
        raise TypeError(f"Unsupported game state value: {type(value)!r}")

    def _decode(self, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, list):
            return [self._decode(item) for item in value]
        if not isinstance(value, dict):
            return value
        marker = value.get(self._TYPE_KEY)
        if marker == "datetime":
            return datetime.fromisoformat(str(value[self._VALUE_KEY]))
        if marker == "tuple":
            return tuple(self._decode(item) for item in value.get(self._ITEMS_KEY, []))
        if marker == "set":
            return set(self._decode(item) for item in value.get(self._ITEMS_KEY, []))
        if marker == "dict":
            return {
                self._decode(item[0]): self._decode(item[1])
                for item in value.get(self._ITEMS_KEY, [])
                if isinstance(item, list) and len(item) == 2
            }
        if marker == "dataclass":
            class_name = str(value.get(self._CLASS_KEY) or "")
            dataclass_type = self._dataclass_registry.get(class_name)
            if dataclass_type is None:
                raise ValueError(f"Unknown dataclass in game payload: {class_name}")
            payload = value.get(self._FIELDS_KEY, {})
            if not isinstance(payload, dict):
                raise ValueError(f"Invalid dataclass payload for {class_name}")
            kwargs = {
                field_info.name: self._decode(payload.get(field_info.name))
                for field_info in fields(dataclass_type)
            }
            return dataclass_type(**kwargs)
        return {
            key: self._decode(item)
            for key, item in value.items()
        }


class RedisGameStateRepository:
    _GAME_KEY_PREFIX = "selara:game"
    _ACTIVE_KEY_PREFIX = "selara:chat_active"
    _RECENT_KEY_PREFIX = "selara:user_recent_games"

    def __init__(self, *, client, codec: GameStateCodec, ttl: timedelta) -> None:
        self._client = client
        self._codec = codec
        self._ttl = max(timedelta(minutes=5), ttl)

    @classmethod
    def from_url(cls, *, redis_url: str, codec: GameStateCodec, ttl: timedelta) -> "RedisGameStateRepository":
        try:
            from redis.asyncio import Redis
        except ModuleNotFoundError as exc:
            raise RuntimeError("Redis support requires the `redis` package to be installed.") from exc
        client = Redis.from_url(redis_url, decode_responses=True)
        return cls(client=client, codec=codec, ttl=ttl)

    @property
    def ttl_seconds(self) -> int:
        return max(300, int(self._ttl.total_seconds()))

    def _game_key(self, game_id: str) -> str:
        return f"{self._GAME_KEY_PREFIX}:{game_id}"

    def _active_key(self, chat_id: int) -> str:
        return f"{self._ACTIVE_KEY_PREFIX}:{chat_id}"

    def _recent_key(self, user_id: int) -> str:
        return f"{self._RECENT_KEY_PREFIX}:{user_id}"

    async def load_game(self, game_id: str) -> GroupGame | None:
        raw = await self._client.get(self._game_key(game_id))
        if not raw:
            return None
        game = self._codec.loads(raw)
        await self._client.expire(self._game_key(game_id), self.ttl_seconds)
        return game

    async def save_game(self, game: GroupGame, *, is_active: bool) -> None:
        await self._client.set(self._game_key(game.game_id), self._codec.dumps(game), ex=self.ttl_seconds)
        active_key = self._active_key(game.chat_id)
        if is_active and game.status != "finished":
            await self._client.set(active_key, game.game_id, ex=self.ttl_seconds)
            return

        current_active = await self._client.get(active_key)
        if current_active == game.game_id:
            await self._client.delete(active_key)

        if game.status != "finished":
            return

        score = int((game.started_at or game.created_at).timestamp())
        pipe = self._client.pipeline()
        for user_id in game.players:
            recent_key = self._recent_key(user_id)
            pipe.zadd(recent_key, {game.game_id: score})
            pipe.expire(recent_key, self.ttl_seconds)
        await pipe.execute()

    async def load_active_game_id(self, chat_id: int) -> str | None:
        value = await self._client.get(self._active_key(chat_id))
        if value is not None:
            await self._client.expire(self._active_key(chat_id), self.ttl_seconds)
        return value

    async def list_active_game_ids(self, *, chat_ids: set[int] | None = None) -> list[str]:
        if chat_ids:
            game_ids: list[str] = []
            for chat_id in sorted(chat_ids):
                value = await self.load_active_game_id(chat_id)
                if value:
                    game_ids.append(value)
            return game_ids

        seen: list[str] = []
        async for key in self._client.scan_iter(match=f"{self._ACTIVE_KEY_PREFIX}:*"):
            value = await self._client.get(key)
            if value:
                seen.append(value)
        return seen

    async def list_recent_game_ids_for_user(self, *, user_id: int, limit: int = 6) -> list[str]:
        return [str(value) for value in await self._client.zrevrange(self._recent_key(user_id), 0, max(0, limit - 1))]

    async def close(self) -> None:
        close = getattr(self._client, "aclose", None) or getattr(self._client, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result


class RedisLiveEventBroker:
    _BASE_CHANNEL = "selara:live"

    def __init__(self, *, client) -> None:
        self._client = client

    @classmethod
    def from_url(cls, *, redis_url: str) -> "RedisLiveEventBroker":
        try:
            from redis.asyncio import Redis
        except ModuleNotFoundError as exc:
            raise RuntimeError("Redis support requires the `redis` package to be installed.") from exc
        return cls(client=Redis.from_url(redis_url, decode_responses=True))

    def _channel(self, *, scope: str, chat_id: int | None = None, game_id: str | None = None) -> str:
        if game_id is not None:
            return f"{self._BASE_CHANNEL}:game:{game_id}"
        if chat_id is not None:
            return f"{self._BASE_CHANNEL}:chat:{chat_id}"
        return f"{self._BASE_CHANNEL}:{scope}"

    async def publish(self, event: LiveEvent) -> None:
        channels = {
            self._channel(scope=event.scope),
        }
        if event.chat_id is not None:
            channels.add(self._channel(scope=event.scope, chat_id=event.chat_id))
        if event.game_id is not None:
            channels.add(self._channel(scope=event.scope, game_id=event.game_id))
        payload = event.to_json()
        for channel in channels:
            await self._client.publish(channel, payload)

    async def subscribe(
        self,
        *,
        scope: str,
        chat_id: int | None = None,
        game_id: str | None = None,
    ) -> AsyncIterator[LiveEvent | None]:
        pubsub = self._client.pubsub()
        channels = [self._channel(scope=scope)]
        if chat_id is not None:
            channels.append(self._channel(scope=scope, chat_id=chat_id))
        if game_id is not None:
            channels.append(self._channel(scope=scope, game_id=game_id))
        await pubsub.subscribe(*channels)
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None or message.get("data") is None:
                    yield None
                    continue
                yield LiveEvent.from_json(str(message["data"]))
        finally:
            await pubsub.unsubscribe(*channels)
            close = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result

    async def close(self) -> None:
        close = getattr(self._client, "aclose", None) or getattr(self._client, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result


_READ_ONLY_GAMESTORE_METHODS: set[str] = {
    "get_game",
    "get_active_game_for_chat",
    "list_active_games",
    "list_recent_games_for_user",
    "get_role",
    "get_latest_role_game_for_user",
    "get_latest_bunker_game_for_user",
    "get_latest_bred_submission_game_for_user",
    "get_latest_zlob_submission_game_for_user",
    "quiz_get_answer_snapshot",
    "spy_get_vote_snapshot",
    "bred_get_vote_snapshot",
    "bred_get_category_snapshot",
    "zlob_get_submit_snapshot",
    "zlob_get_vote_snapshot",
    "bunker_get_reveal_snapshot",
    "bunker_get_vote_snapshot",
    "mafia_is_night_ready",
    "mafia_get_vote_snapshot",
    "mafia_get_execution_confirm_snapshot",
}
_ACTIVE_CHAT_HYDRATION_METHODS: set[str] = {
    "set_player_label",
    "get_active_game_for_chat",
}


def _collect_group_games(value: Any) -> list[GroupGame]:
    if isinstance(value, GroupGame):
        return [value]
    if isinstance(value, tuple | list | set):
        games: list[GroupGame] = []
        for item in value:
            games.extend(_collect_group_games(item))
        return games
    if isinstance(value, dict):
        games: list[GroupGame] = []
        for item in value.values():
            games.extend(_collect_group_games(item))
        return games
    return []


def _event_type_for_method(name: str) -> str:
    if "vote" in name:
        return "new_vote"
    if name in {
        "start",
        "finish",
        "mafia_resolve_night",
        "mafia_open_day_vote",
        "mafia_resolve_day_vote",
        "mafia_resolve_execution_confirm",
        "quiz_resolve_round",
        "bred_resolve_round",
        "zlob_resolve_round",
        "bunker_resolve_vote",
        "bunker_force_advance_reveal",
        "spy_guess_location",
        "whoami_guess_identity",
    }:
        return "phase_change"
    return "game_updated"


class RuntimeGameStore:
    def __init__(self, backend: InMemoryGameStore | None = None) -> None:
        self._backend = backend or InMemoryGameStore()
        self._codec = GameStateCodec()
        self._state_repo: RedisGameStateRepository | None = None
        self._broker: LiveEventBroker | None = None
        self._redis_degraded = False

    @staticmethod
    def _is_redis_error(exc: Exception) -> bool:
        return _RedisError is not None and isinstance(exc, _RedisError)

    def _degrade_to_in_memory(self, *, stage: str, exc: Exception) -> None:
        was_using_redis = self._state_repo is not None or self._broker is not None
        self._state_repo = None
        self._broker = None
        if was_using_redis and not self._redis_degraded:
            self._redis_degraded = True
            logger.warning(
                "Redis is unavailable during %s; falling back to in-memory game store. Error: %s",
                stage,
                exc,
            )

    @property
    def backend(self) -> InMemoryGameStore:
        return self._backend

    @property
    def live_broker(self) -> LiveEventBroker | None:
        return self._broker

    def configure_runtime(self, *, redis_url: str, ttl_hours: int) -> None:
        ttl = timedelta(hours=max(1, ttl_hours))
        self._backend = InMemoryGameStore()
        self._state_repo = RedisGameStateRepository.from_url(redis_url=redis_url, codec=self._codec, ttl=ttl)
        self._broker = RedisLiveEventBroker.from_url(redis_url=redis_url)
        self._redis_degraded = False

    def use_in_memory(self) -> None:
        self._backend = InMemoryGameStore()
        self._state_repo = None
        self._broker = None
        self._redis_degraded = False

    async def close(self) -> None:
        if self._broker is not None:
            await self._broker.close()
        if self._state_repo is not None:
            await self._state_repo.close()

    async def publish_event(
        self,
        *,
        event_type: str,
        scope: str,
        chat_id: int | None = None,
        game_id: str | None = None,
    ) -> None:
        if self._broker is None:
            return
        revision = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        try:
            await self._broker.publish(
                LiveEvent(
                    event_type=event_type,
                    scope=scope,
                    revision=revision,
                    chat_id=chat_id,
                    game_id=game_id,
                )
            )
        except Exception as exc:
            if self._is_redis_error(exc):
                self._degrade_to_in_memory(stage="publish_event", exc=exc)
                return
            raise

    async def _hydrate_game(self, game_id: str) -> None:
        if self._state_repo is None or game_id in self._backend._by_id:
            return
        game = await self._state_repo.load_game(game_id)
        if game is None:
            return
        self._backend._by_id[game_id] = game
        if game.status != "finished":
            self._backend._active_by_chat[game.chat_id] = game.game_id

    async def _hydrate_active_for_chat(self, chat_id: int) -> None:
        if self._state_repo is None:
            return
        game_id = await self._state_repo.load_active_game_id(chat_id)
        if game_id:
            await self._hydrate_game(game_id)

    async def _hydrate_active_games(self, *, chat_ids: set[int] | None = None) -> None:
        if self._state_repo is None:
            return
        for game_id in await self._state_repo.list_active_game_ids(chat_ids=chat_ids):
            await self._hydrate_game(game_id)

    async def _hydrate_recent_games_for_user(self, *, user_id: int, chat_ids: set[int] | None = None, limit: int = 6) -> None:
        if self._state_repo is None:
            return
        loaded = 0
        for game_id in await self._state_repo.list_recent_game_ids_for_user(user_id=user_id, limit=max(limit * 3, limit)):
            await self._hydrate_game(game_id)
            game = self._backend._by_id.get(game_id)
            if game is None:
                continue
            if chat_ids is not None and game.chat_id not in chat_ids:
                continue
            loaded += 1
            if loaded >= max(1, limit):
                break

    async def _hydrate_for_call(self, name: str, kwargs: dict[str, Any]) -> None:
        if self._state_repo is None:
            return
        if name == "list_active_games":
            await self._hydrate_active_games(chat_ids=kwargs.get("chat_ids"))
            return
        if name == "list_recent_games_for_user":
            await self._hydrate_recent_games_for_user(
                user_id=int(kwargs["user_id"]),
                chat_ids=kwargs.get("chat_ids"),
                limit=int(kwargs.get("limit", 6)),
            )
            return
        game_id = kwargs.get("game_id")
        if isinstance(game_id, str) and game_id:
            await self._hydrate_game(game_id)
            return
        if name in _ACTIVE_CHAT_HYDRATION_METHODS:
            chat_id = kwargs.get("chat_id")
            if isinstance(chat_id, int):
                await self._hydrate_active_for_chat(chat_id)

    async def _sync_cached_state(self) -> None:
        if self._state_repo is None:
            return
        active_ids = set(self._backend._active_by_chat.values())
        for game in self._backend._by_id.values():
            await self._state_repo.save_game(game, is_active=game.game_id in active_ids)

    async def _publish_after_call(self, name: str, result: Any, kwargs: dict[str, Any]) -> None:
        if self._broker is None:
            return
        games = _collect_group_games(result)
        if not games:
            game_id = kwargs.get("game_id")
            if isinstance(game_id, str):
                current = self._backend._by_id.get(game_id)
                if current is not None:
                    games = [current]
        if not games:
            return
        event_type = _event_type_for_method(name)
        for game in {game.game_id: game for game in games}.values():
            await self.publish_event(
                event_type=event_type,
                scope="games",
                chat_id=game.chat_id,
                game_id=game.game_id,
            )

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._backend, name)
        if not callable(attr) or not inspect.iscoroutinefunction(attr):
            return attr

        async def _wrapped(*args, **kwargs):
            try:
                await self._hydrate_for_call(name, kwargs)
            except Exception as exc:
                if self._is_redis_error(exc):
                    self._degrade_to_in_memory(stage=f"{name}:hydrate", exc=exc)
                else:
                    raise
            result = await attr(*args, **kwargs)
            if name not in _READ_ONLY_GAMESTORE_METHODS:
                try:
                    await self._sync_cached_state()
                    await self._publish_after_call(name, result, kwargs)
                except Exception as exc:
                    if self._is_redis_error(exc):
                        self._degrade_to_in_memory(stage=f"{name}:sync", exc=exc)
                    else:
                        raise
            return result

        return _wrapped


GAME_STORE: RuntimeGameStore = RuntimeGameStore()
