from selara.application.dto import CommandIntent
from selara.presentation.commands.aliases import EXACT_ALIASES
from selara.presentation.commands.catalog import PREFIX_TRIGGER_TO_COMMAND_KEY
from selara.presentation.commands.normalizer import normalize_text_command


class TextCommandResolutionError(ValueError):
    pass


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

    if tokens[0] == "актив":
        return _parse_active_command(tokens[1:], top_default=top_default, top_max=top_max)
    if tokens[0] == "топ":
        return _parse_top_command(tokens[1:], top_default=top_default, top_max=top_max)

    alias_command = EXACT_ALIASES.get(normalized)
    if alias_command is not None:
        return CommandIntent(name=alias_command)

    for trigger in sorted(PREFIX_TRIGGER_TO_COMMAND_KEY, key=len, reverse=True):
        if normalized == trigger:
            return CommandIntent(name=PREFIX_TRIGGER_TO_COMMAND_KEY[trigger])
        if normalized.startswith(f"{trigger} "):
            tail = normalized[len(trigger) :].strip()
            return CommandIntent(name=PREFIX_TRIGGER_TO_COMMAND_KEY[trigger], args={"raw_args": tail})

    if tokens[0] in {"актив", "топ"}:
        raise TextCommandResolutionError(
            "Формат команды: актив [N] или топ [karma|activity] [неделя|сутки|час|месяц] [N]"
        )

    return None
