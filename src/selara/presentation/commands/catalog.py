from dataclasses import dataclass

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
    "жениться": "marry",
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
    "help": "помощь",
    "active": "актив",
    "announce_reg": "рег",
    "announce_unreg": "анрег",
    "rep": "репутация",
    "achievements": "достижения",
    "lastseen": "когда был",
    "eco": "баланс",
    "farm": "ферма",
    "shop": "магазин",
    "inventory": "инвентарь",
    "craft": "крафт",
    "tap": "тап",
    "daily": "дейлик",
    "lottery": "лотерея",
    "market": "рынок",
    "auction": "аукцион",
    "bid": "ставка",
    "pay": "перевод",
    "zhmyh": "жмых",
    "growth": "рост",
    "growth_action": "дрочка",
    "relation": "отношения",
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
            return BuiltinTextMatch(
                command_key=PREFIX_TRIGGER_TO_COMMAND_KEY[trigger],
                matched_trigger_norm=trigger,
            )

    return None
