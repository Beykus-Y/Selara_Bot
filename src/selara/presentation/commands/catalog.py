from dataclasses import dataclass

from selara.application.use_cases.economy.catalog import RECIPES
from selara.presentation.commands.aliases import EXACT_ALIASES
from selara.presentation.commands.normalizer import normalize_text_command

TextCommandKey = str

SOCIAL_TRIGGER_TO_COMMAND_KEY: dict[str, TextCommandKey] = {
    "шлепнуть": "social_slap",
    "шлёпнуть": "social_slap",
    "шлепни": "social_slap",
    "шлёпни": "social_slap",
    "убить": "social_kill",
    "убей": "social_kill",
    "трахнуть": "social_fuck",
    "трахни": "social_fuck",
    "соблазнить": "social_seduce",
    "соблазни": "social_seduce",
    "засосать": "social_makeout",
    "засоси": "social_makeout",
    "провести ночь с": "social_night",
    "проведи ночь с": "social_night",
    "ударить": "social_hit",
    "ударь": "social_hit",
    "обнять": "social_hug",
    "обними": "social_hug",
    "поцеловать": "social_kiss",
    "поцелуй": "social_kiss",
    "пожать руку": "social_handshake",
    "пожми руку": "social_handshake",
    "пожать лапу": "social_handshake",
    "пожми лапу": "social_handshake",
    "дать пять": "social_highfive",
    "дай пять": "social_highfive",
    "хайфайв": "social_highfive",
    "погладить": "social_pat",
    "погладь": "social_pat",
    "пощекотать": "social_tickle",
    "пощекочи": "social_tickle",
    "ткнуть": "social_poke",
    "ткни": "social_poke",
    "подмигнуть": "social_wink",
    "подмигни": "social_wink",
    "потанцевать": "social_dance",
    "потанцуй": "social_dance",
    "поклониться": "social_bow",
    "поклонись": "social_bow",
    "подбодрить": "social_cheer",
    "подбодри": "social_cheer",
    "угостить": "social_treat",
    "угости": "social_treat",
    "похвалить": "social_praise",
    "похвали": "social_praise",
    "дать кулак": "social_fistbump",
    "дай кулак": "social_fistbump",
    "кулачок": "social_fistbump",
}

SOCIAL_COMMAND_KEY_TO_ACTION: dict[TextCommandKey, str] = {
    "social_slap": "slap",
    "social_kill": "kill",
    "social_fuck": "fuck",
    "social_seduce": "seduce",
    "social_makeout": "makeout",
    "social_night": "night",
    "social_hit": "hit",
    "social_hug": "hug",
    "social_kiss": "kiss",
    "social_handshake": "handshake",
    "social_highfive": "highfive",
    "social_pat": "pat",
    "social_tickle": "tickle",
    "social_poke": "poke",
    "social_wink": "wink",
    "social_dance": "dance",
    "social_bow": "bow",
    "social_cheer": "cheer",
    "social_treat": "treat",
    "social_praise": "praise",
    "social_fistbump": "fistbump",
}

PREFIX_TRIGGER_TO_COMMAND_KEY: dict[str, TextCommandKey] = {
    "нейминг": "naming",
    "объява": "announce",
    "игра": "game",
    "кто ты": "me",
    "актив": "active",
    "топ": "top",
    "баланс": "eco",
    "ферма": "farm",
    "магазин": "shop",
    "инвентарь": "inventory",
    "крафт": "craft",
    "лотерея": "lottery",
    "рынок": "market",
    "аукцион": "auction",
    "ставка": "bid",
    "рост": "growth",
    "перевод": "pay",
    "платеж": "pay",
    "пара": "pair",
    "предложить встречаться": "pair",
    "жениться": "marry",
    "предложить брак": "marry",
    "роль": "role",
    "титул": "title",
    "усыновить": "adopt",
    "стать питомцем": "pet",
    "семья": "family",
    **SOCIAL_TRIGGER_TO_COMMAND_KEY,
}

EXACT_TRIGGER_TO_COMMAND_KEY: dict[str, TextCommandKey] = {
    normalize_text_command(trigger): command_key
    for trigger, command_key in EXACT_ALIASES.items()
}

BUILTIN_TRIGGER_TO_COMMAND_KEY: dict[str, TextCommandKey] = dict(EXACT_TRIGGER_TO_COMMAND_KEY)
BUILTIN_TRIGGER_TO_COMMAND_KEY.update(PREFIX_TRIGGER_TO_COMMAND_KEY)

COMMAND_KEY_DEFAULT_SOURCE_TRIGGER: dict[TextCommandKey, str] = {
    "start": "старт",
    "alive": "бот",
    "game": "игра",
    "role": "роль",
    "me": "кто я",
    "iris_perenos": "ирис перенос",
    "iriskto_perenos": "ирис кто перенос",
    "help": "помощь",
    "active": "актив",
    "announce_reg": "рег",
    "announce_unreg": "анрег",
    "rep": "репутация",
    "achievements": "достижения",
    "lastseen": "когда был",
    "inactive": "кто неактив",
    "eco": "баланс",
    "farm": "ферма",
    "shop": "магазин",
    "inventory": "инвентарь",
    "craft": "крафт",
    "tap": "тап",
    "daily": "дейлик",
    "gacha_pull": "гача генш",
    "gacha_profile": "моя гача генш",
    "gacha_skip": "гача скип генш",
    "lottery": "лотерея",
    "market": "рынок",
    "article": "моя статья",
    "auction": "аукцион",
    "bid": "ставка",
    "pay": "перевод",
    "zhmyh": "жмых",
    "growth": "рост",
    "growth_action": "дрочка",
    "relation": "отношения",
    "marriage": "мой брак",
    "marriages": "браки",
    "pair": "пара",
    "breakup": "расстаться",
    "marry": "жениться",
    "divorce": "развод",
    "love": "любовь",
    "care": "забота",
    "date": "свидание",
    "gift": "подарок",
    "support": "поддержка",
    "flirt": "флирт",
    "surprise": "сюрприз",
    "vow": "клятва",
    "title": "титул",
    "adopt": "усыновить",
    "pet": "стать питомцем",
    "family": "семья",
    "shipperim": "шипперим",
    "naming": "нейминг",
    "announce": "объява",
    "top": "топ",
    "social_slap": "шлепнуть",
    "social_kill": "убить",
    "social_fuck": "трахнуть",
    "social_seduce": "соблазнить",
    "social_makeout": "засосать",
    "social_night": "провести ночь с",
    "social_hit": "ударить",
    "social_hug": "обнять",
    "social_kiss": "поцеловать",
    "social_handshake": "пожать руку",
    "social_highfive": "дать пять",
    "social_pat": "погладить",
    "social_tickle": "пощекотать",
    "social_poke": "ткнуть",
    "social_wink": "подмигнуть",
    "social_dance": "потанцевать",
    "social_bow": "поклониться",
    "social_cheer": "подбодрить",
    "social_treat": "угостить",
    "social_praise": "похвалить",
    "social_fistbump": "дать кулак",
}

COMMAND_KEYS_WITH_TAIL: set[TextCommandKey] = {
    "naming",
    "announce",
    "game",
    "me",
    "active",
    "top",
    "role",
    "eco",
    "farm",
    "shop",
    "inventory",
    "craft",
    "lottery",
    "market",
    "auction",
    "bid",
    "pay",
    "growth",
    "pair",
    "marry",
    "title",
    "adopt",
    "pet",
    "family",
    "social_slap",
    "social_kill",
    "social_fuck",
    "social_seduce",
    "social_makeout",
    "social_night",
    "social_hit",
    "social_hug",
    "social_kiss",
    "social_handshake",
    "social_highfive",
    "social_pat",
    "social_tickle",
    "social_poke",
    "social_wink",
    "social_dance",
    "social_bow",
    "social_cheer",
    "social_treat",
    "social_praise",
    "social_fistbump",
}

_MODE_HINT_TOKENS = {"global", "local"}
_FARM_ACTION_TOKENS = {"plant", "plantall", "plant_all", "harvest", "harvestall", "harvest_all", "upfarm", "upsize", "sell"}
_LOTTERY_ACTION_TOKENS = {"free", "paid", "status", "view"}
_GROWTH_ACTION_TOKENS = {"do", "d", "act", "go", "дрочить", "подрочить"}
_TITLE_RESET_TOKENS = {"clear", "reset", "off"}
_TITLE_SET_TOKENS = {"buy", "set"}
_GAME_KIND_TAILS = {
    "spy",
    "шпион",
    "spygame",
    "mafia",
    "мафия",
    "dice",
    "кости",
    "кубик",
    "кубики",
    "number",
    "num",
    "число",
    "угадай",
    "угадай число",
    "quiz",
    "викторина",
    "вик",
    "bredovukha",
    "bred",
    "бредовуха",
    "whoami",
    "who_am_i",
    "ктоя",
    "кто я",
    "bunker",
    "бункер",
    "zlobcards",
    "злобные карты",
    "злобкарты",
}
_CRAFT_RECIPE_TAILS = {normalize_text_command(recipe_code) for recipe_code in RECIPES}


def _split_tail_tokens(tail_text: str) -> list[str]:
    return [token for token in normalize_text_command(tail_text).split(" ") if token]


def _strip_mode_hint(tokens: list[str]) -> list[str]:
    if tokens and tokens[0] in _MODE_HINT_TOKENS:
        return tokens[1:]
    return tokens


def _is_user_ref_token(token: str) -> bool:
    return bool(token) and (token.startswith("@") and len(token) > 1 or token.lstrip("-").isdigit())


def prefix_tail_is_valid(*, command_key: TextCommandKey, tail_text: str) -> bool:
    normalized_tail = normalize_text_command(tail_text)
    tokens = _split_tail_tokens(tail_text)
    if not tokens:
        return True

    if command_key == "active":
        return len(tokens) == 1 and tokens[0].isdigit()

    if command_key == "top":
        mode_aliases = {"карма", "karma", "актив", "activity", "гибрид", "mix", "hybrid"}
        period_aliases = {"час", "hour", "сутки", "день", "day", "неделя", "week", "месяц", "month"}
        rest = list(tokens)
        if rest and rest[0] in mode_aliases:
            rest = rest[1:]
        if rest and rest[0] in period_aliases:
            rest = rest[1:]
        return not rest or len(rest) == 1 and rest[0].isdigit()

    if command_key in {"announce", "naming"}:
        return True

    if command_key == "game":
        return normalized_tail in _GAME_KIND_TAILS

    if command_key == "me":
        return len(tokens) <= 1 and (len(tokens) == 0 or _is_user_ref_token(tokens[0]))

    if command_key == "eco":
        rest = _strip_mode_hint(tokens)
        return not rest or len(rest) == 1 and rest[0].isdigit()

    if command_key == "farm":
        rest = _strip_mode_hint(tokens)
        return not rest or rest[0] in _FARM_ACTION_TOKENS

    if command_key == "shop":
        rest = _strip_mode_hint(tokens)
        return not rest or rest[0] == "buy"

    if command_key == "inventory":
        rest = _strip_mode_hint(tokens)
        return not rest or rest[0] == "use"

    if command_key == "lottery":
        rest = _strip_mode_hint(tokens)
        return not rest or rest[0] in _LOTTERY_ACTION_TOKENS

    if command_key == "market":
        rest = _strip_mode_hint(tokens)
        if not rest:
            return True
        if rest[0] == "sell":
            return len(rest) == 4 and rest[2].isdigit() and rest[3].isdigit()
        if rest[0] == "buy":
            return len(rest) == 3 and rest[1].isdigit() and rest[2].isdigit()
        if rest[0] == "cancel":
            return len(rest) == 2 and rest[1].isdigit()
        return False

    if command_key == "auction":
        if tokens[0] == "cancel":
            return len(tokens) == 1
        if tokens[0] in {"start", "sell"}:
            if len(tokens) not in {4, 5}:
                return False
            if not tokens[2].isdigit() or not tokens[3].isdigit():
                return False
            return len(tokens) == 4 or tokens[4].isdigit()
        return False

    if command_key == "bid":
        return len(tokens) == 1 and tokens[0].isdigit()

    if command_key == "pay":
        rest = _strip_mode_hint(tokens)
        if len(rest) == 1:
            return rest[0].isdigit()
        return len(rest) == 2 and _is_user_ref_token(rest[0]) and rest[1].isdigit()

    if command_key == "craft":
        rest = _strip_mode_hint(tokens)
        return not rest or " ".join(rest) in _CRAFT_RECIPE_TAILS

    if command_key == "growth":
        rest = _strip_mode_hint(tokens)
        return not rest or len(rest) == 1 and rest[0] in _GROWTH_ACTION_TOKENS

    if command_key in {"pair", "marry", "adopt", "pet", "family"}:
        return len(tokens) == 1 and _is_user_ref_token(tokens[0])

    if command_key == "title":
        action = tokens[0]
        if action in _TITLE_RESET_TOKENS:
            return len(tokens) == 1
        if action in _TITLE_SET_TOKENS:
            return len(tokens) >= 2
        return False

    if command_key == "role":
        return False

    return True


@dataclass(frozen=True)
class BuiltinTextMatch:
    command_key: TextCommandKey
    matched_trigger_norm: str


def resolve_builtin_command_key(trigger_text: str) -> TextCommandKey | None:
    normalized = normalize_text_command(trigger_text)
    if not normalized:
        return None
    return BUILTIN_TRIGGER_TO_COMMAND_KEY.get(normalized)


def match_builtin_command(text: str) -> BuiltinTextMatch | None:
    normalized = normalize_text_command(text)
    if not normalized or normalized.startswith("/"):
        return None

    exact_key = EXACT_TRIGGER_TO_COMMAND_KEY.get(normalized)
    if exact_key is not None:
        return BuiltinTextMatch(command_key=exact_key, matched_trigger_norm=normalized)

    for trigger in sorted(PREFIX_TRIGGER_TO_COMMAND_KEY, key=len, reverse=True):
        if normalized == trigger or normalized.startswith(f"{trigger} "):
            command_key = PREFIX_TRIGGER_TO_COMMAND_KEY[trigger]
            if normalized != trigger:
                tail = normalized[len(trigger) :].strip()
                if not prefix_tail_is_valid(command_key=command_key, tail_text=tail):
                    continue
            return BuiltinTextMatch(
                command_key=command_key,
                matched_trigger_norm=trigger,
            )

    return None
