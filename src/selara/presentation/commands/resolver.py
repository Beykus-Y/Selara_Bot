from selara.application.dto import CommandIntent
from selara.presentation.commands.aliases import EXACT_ALIASES
from selara.presentation.commands.catalog import PREFIX_TRIGGER_TO_COMMAND_KEY, prefix_tail_is_valid
from selara.presentation.commands.normalizer import normalize_text_command


class TextCommandResolutionError(ValueError):
    pass


_GACHA_BANNER_ALIASES = {
    "генш": "genshin",
    "геншин": "genshin",
    "хср": "hsr",
}


def _parse_limit(raw: str, *, top_max: int) -> int:
    if not raw.isdigit():
        raise TextCommandResolutionError("Лимит должен быть числом")
    limit = int(raw)
    if not 1 <= limit <= top_max:
        raise TextCommandResolutionError(f"Лимит должен быть в диапазоне 1..{top_max}")
    return limit


def _parse_active_command(tokens: list[str], *, top_default: int, top_max: int) -> CommandIntent:
    if not tokens:
        return CommandIntent(name="active", args={"limit": top_default})
    if len(tokens) != 1:
        raise TextCommandResolutionError("Формат команды: актив [N]")
    return CommandIntent(name="active", args={"limit": _parse_limit(tokens[0], top_max=top_max)})


def _parse_top_command(tokens: list[str], *, top_default: int, top_max: int) -> CommandIntent:
    mode = "activity"
    period = "all"
    mode_aliases = {
        "карма": "karma",
        "karma": "karma",
        "актив": "activity",
        "activity": "activity",
        "гибрид": "mix",
        "mix": "mix",
        "hybrid": "mix",
    }
    period_aliases = {
        "час": "hour",
        "hour": "hour",
        "сутки": "day",
        "день": "day",
        "day": "day",
        "неделя": "week",
        "week": "week",
        "месяц": "month",
        "month": "month",
    }

    if tokens and tokens[0] in mode_aliases:
        mode = mode_aliases[tokens[0]]
        tokens = tokens[1:]

    if tokens and tokens[0] in period_aliases:
        period = period_aliases[tokens[0]]
        tokens = tokens[1:]
        mode = "activity"

    if not tokens:
        return CommandIntent(name="top", args={"mode": mode, "period": period, "limit": top_default})
    if len(tokens) != 1:
        raise TextCommandResolutionError("Формат команды: топ [karma|activity] [неделя|сутки|час|месяц] [N]")
    limit = _parse_limit(tokens[0], top_max=top_max)
    return CommandIntent(name="top", args={"mode": mode, "period": period, "limit": limit})


def _parse_gacha_command(tokens: list[str]) -> CommandIntent | None:
    usage = "Формат: гача генш|геншин|хср или моя гача генш|геншин|хср"
    skip_usage = "Формат: гача скип генш|геншин|хср [@username]"

    if len(tokens) >= 2 and tokens[0] == "гача" and tokens[1] == "скип":
        if len(tokens) not in {3, 4}:
            raise TextCommandResolutionError(skip_usage)
        banner = _GACHA_BANNER_ALIASES.get(tokens[2])
        if banner is None:
            raise TextCommandResolutionError(skip_usage)
        target_username = None
        if len(tokens) == 4:
            target_username = tokens[3]
            if not target_username.startswith("@") or len(target_username) < 2:
                raise TextCommandResolutionError(skip_usage)
        return CommandIntent(name="gacha_skip", args={"banner": banner, "target_username": target_username})

    if len(tokens) == 1 and tokens[0] == "гача":
        raise TextCommandResolutionError(usage)

    if len(tokens) == 2 and tokens[0] == "гача":
        banner = _GACHA_BANNER_ALIASES.get(tokens[1])
        if banner is not None:
            return CommandIntent(name="gacha_pull", args={"banner": banner})
        raise TextCommandResolutionError(usage)

    if len(tokens) == 2 and tokens[0] == "моя" and tokens[1] == "гача":
        raise TextCommandResolutionError(usage)

    if len(tokens) == 3 and tokens[0] == "моя" and tokens[1] == "гача":
        banner = _GACHA_BANNER_ALIASES.get(tokens[2])
        if banner is not None:
            return CommandIntent(name="gacha_profile", args={"banner": banner})
        raise TextCommandResolutionError(usage)

    return None


def _parse_chat_gate_command(tokens: list[str]) -> CommandIntent | None:
    if not tokens:
        return None

    if tokens[0] == "+антирейд":
        if len(tokens) == 1:
            return CommandIntent(name="antiraid_on")
        if len(tokens) == 2 and tokens[1] in {"5", "10"}:
            return CommandIntent(name="antiraid_on", args={"raw_args": tokens[1]})
        raise TextCommandResolutionError("Формат команды: +антирейд [5|10]")

    if tokens[0] == "-антирейд":
        if len(tokens) == 1:
            return CommandIntent(name="antiraid_off")
        raise TextCommandResolutionError("Формат команды: -антирейд")

    if tokens[0] == "-чат":
        if len(tokens) == 1:
            return CommandIntent(name="chat_lock")
        raise TextCommandResolutionError("Формат команды: -чат")

    if tokens[0] == "+чат":
        if len(tokens) == 1:
            return CommandIntent(name="chat_unlock")
        raise TextCommandResolutionError("Формат команды: +чат")

    return None


def resolve_text_command(
    text: str,
    *,
    top_default: int,
    top_max: int,
) -> CommandIntent | None:
    normalized = normalize_text_command(text)
    if not normalized:
        return None

    if normalized.startswith("/"):
        return None

    tokens = [token for token in normalized.split(" ") if token]
    if not tokens:
        return None

    gacha_command = _parse_gacha_command(tokens)
    if gacha_command is not None:
        return gacha_command

    chat_gate_command = _parse_chat_gate_command(tokens)
    if chat_gate_command is not None:
        return chat_gate_command

    if tokens[0] == "актив":
        if len(tokens) > 1 and not prefix_tail_is_valid(command_key="active", tail_text=" ".join(tokens[1:])):
            return None
        return _parse_active_command(tokens[1:], top_default=top_default, top_max=top_max)
    if tokens[0] == "топ":
        if len(tokens) > 1 and not prefix_tail_is_valid(command_key="top", tail_text=" ".join(tokens[1:])):
            return None
        return _parse_top_command(tokens[1:], top_default=top_default, top_max=top_max)

    alias_command = EXACT_ALIASES.get(normalized)
    if alias_command is not None:
        return CommandIntent(name=alias_command)

    for trigger in sorted(PREFIX_TRIGGER_TO_COMMAND_KEY, key=len, reverse=True):
        if normalized == trigger:
            return CommandIntent(name=PREFIX_TRIGGER_TO_COMMAND_KEY[trigger])
        if normalized.startswith(f"{trigger} "):
            tail = normalized[len(trigger) :].strip()
            command_key = PREFIX_TRIGGER_TO_COMMAND_KEY[trigger]
            if not prefix_tail_is_valid(command_key=command_key, tail_text=tail):
                continue
            return CommandIntent(name=command_key, args={"raw_args": tail})

    return None
