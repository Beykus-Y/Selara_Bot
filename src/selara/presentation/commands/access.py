from __future__ import annotations

import re
from dataclasses import dataclass

from selara.presentation.commands.aliases import EXACT_ALIASES
from selara.presentation.commands.catalog import BUILTIN_TRIGGER_TO_COMMAND_KEY, resolve_builtin_command_key
from selara.presentation.commands.normalizer import normalize_text_command
from selara.presentation.commands.resolver import TextCommandResolutionError, resolve_text_command

_KEY_SANITIZE_RE = re.compile(r"[^a-z0-9_]+")
_KEY_REPEAT_UNDERSCORE_RE = re.compile(r"_+")
_SET_COMMAND_RANK_RE = re.compile(
    r'^\s*установить\s+"(?P<command>[^"]+)"\s+ранг\s+внутри\s+бота\s+(?P<role>.+?)\s*$',
    re.IGNORECASE,
)
_RESET_COMMAND_RANK_RE = re.compile(
    r'^\s*(?:сбросить|удалить|очистить)\s+"(?P<command>[^"]+)"\s+ранг\s+внутри\s+бота\s*$',
    re.IGNORECASE,
)

SLASH_COMMAND_TO_KEY: dict[str, str] = {
    "start": "start",
    "me": "me",
    "iris_perenos": "iris_perenos",
    "iriskto_perenos": "iriskto_perenos",
    "rep": "rep",
    "top": "top",
    "active": "active",
    "game": "game",
    "role": "role",
    "naming": "naming",
    "desc": "desc",
    "award": "award",
    "awards": "awards",
    "relation": "relation",
    "pair": "pair",
    "marry": "marry",
    "breakup": "breakup",
    "love": "love",
    "care": "care",
    "date": "date",
    "gift": "gift",
    "support": "support",
    "flirt": "flirt",
    "surprise": "surprise",
    "vow": "vow",
    "divorce": "divorce",
    "eco": "eco",
    "farm": "farm",
    "shop": "shop",
    "inventory": "inventory",
    "craft": "craft",
    "tap": "tap",
    "daily": "daily",
    "growth": "growth",
    "lottery": "lottery",
    "market": "market",
    "pay": "pay",
    "auction": "auction",
    "bid": "bid",
    "roles": "roles",
    "roleadd": "roleadd",
    "roleremove": "roleremove",
    "roledefs": "roledefs",
    "roletemplates": "roletemplates",
    "rolecreate": "rolecreate",
    "rolesettitle": "rolesettitle",
    "rolesetrank": "rolesetrank",
    "roleperms": "roleperms",
    "roledelete": "roledelete",
    "pred": "pred",
    "warn": "warn",
    "unwarn": "unwarn",
    "ban": "ban",
    "unban": "unban",
    "modstat": "modstat",
    "settings": "settings",
    "setcfg": "setcfg",
    "setalias": "setalias",
    "aliases": "aliases",
    "unalias": "unalias",
    "aliasmode": "aliasmode",
    "settrigger": "settrigger",
    "triggers": "triggers",
    "triggervars": "triggervars",
    "deltrigger": "deltrigger",
    "rpadd": "rpadd",
    "rps": "rps",
    "rpdel": "rpdel",
    "title": "title",
    "adopt": "adopt",
    "pet": "pet",
    "family": "family",
    "setrank": "setrank",
    "ranks": "ranks",
    "lastseen": "lastseen",
    "help": "help",
    "gachagive": "gacha_skip",
}

KNOWN_COMMAND_KEYS: set[str] = set(SLASH_COMMAND_TO_KEY.values()) | set(BUILTIN_TRIGGER_TO_COMMAND_KEY.values()) | set(
    EXACT_ALIASES.values()
)
KNOWN_COMMAND_KEYS.update(
    {
        "alive",
        "announce",
        "announce_reg",
        "announce_unreg",
        "gacha_pull",
        "gacha_profile",
        "gacha_skip",
        "shipperim",
        "zhmyh",
        "growth_action",
    }
)
TEXT_TRIGGER_TO_COMMAND_KEY: dict[str, str] = {
    "пред": "pred",
    "снять пред": "unpred",
    "разпред": "unpred",
    "анпред": "unpred",
    "варн": "warn",
    "снять варн": "unwarn",
    "разварн": "unwarn",
    "анварн": "unwarn",
    "бан": "ban",
    "снять бан": "unban",
    "разбан": "unban",
    "анбан": "unban",
    "повысить": "roleadd",
    "понизить": "roleremove",
    "объява": "announce",
}


@dataclass(frozen=True)
class CommandRankPhrase:
    command_input: str
    role_input: str | None
    reset: bool


def canonical_command_key(value: str) -> str:
    lowered = (value or "").strip().lower()
    lowered = lowered.replace("-", "_")
    sanitized = _KEY_SANITIZE_RE.sub("_", lowered)
    sanitized = _KEY_REPEAT_UNDERSCORE_RE.sub("_", sanitized).strip("_")
    return sanitized[:64]


def resolve_command_key_input(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None

    if text.startswith("/"):
        token = text[1:].split(maxsplit=1)[0].split("@", maxsplit=1)[0].strip().lower()
        if not token:
            return None
        mapped = SLASH_COMMAND_TO_KEY.get(token)
        if mapped is not None:
            return mapped
        candidate = canonical_command_key(token)
        if candidate in KNOWN_COMMAND_KEYS:
            return candidate
        return None

    normalized = normalize_text_command(text)
    if not normalized:
        return None

    if normalized in TEXT_TRIGGER_TO_COMMAND_KEY:
        return TEXT_TRIGGER_TO_COMMAND_KEY[normalized]
    for trigger, key in TEXT_TRIGGER_TO_COMMAND_KEY.items():
        if normalized.startswith(f"{trigger} "):
            return key

    builtin = resolve_builtin_command_key(normalized)
    if builtin is not None:
        return builtin

    try:
        intent = resolve_text_command(normalized, top_default=10, top_max=100)
    except TextCommandResolutionError:
        intent = None
    if intent is not None:
        return intent.name

    if normalized in SLASH_COMMAND_TO_KEY:
        return SLASH_COMMAND_TO_KEY[normalized]

    candidate = canonical_command_key(normalized)
    if candidate in KNOWN_COMMAND_KEYS:
        return candidate
    return None


def parse_command_rank_phrase(text: str) -> CommandRankPhrase | None:
    reset_match = _RESET_COMMAND_RANK_RE.match(text or "")
    if reset_match is not None:
        command_input = reset_match.group("command").strip()
        if not command_input:
            return None
        return CommandRankPhrase(command_input=command_input, role_input=None, reset=True)

    set_match = _SET_COMMAND_RANK_RE.match(text or "")
    if set_match is None:
        return None

    command_input = set_match.group("command").strip()
    role_input = set_match.group("role").strip()
    if role_input.startswith('"') and role_input.endswith('"') and len(role_input) >= 2:
        role_input = role_input[1:-1].strip()
    if not command_input or not role_input:
        return None
    return CommandRankPhrase(command_input=command_input, role_input=role_input, reset=False)
