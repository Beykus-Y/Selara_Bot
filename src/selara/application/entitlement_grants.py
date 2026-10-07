"""Owner-granted subscriptions: the rules, the command grammar and the notice texts (no I/O)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from selara.application.selara_ai_product import SELARA_AI_PRODUCT_KEY, SELARA_PERSONAL_PRODUCT_KEY

Scope = Literal["chat", "user"]
RevokeMode = Literal["cancel_all", "shorten"]
Source = Literal["miniapp", "command", "admin_panel"]

SCOPES: tuple[str, ...] = ("chat", "user")
SOURCES: tuple[str, ...] = ("miniapp", "command", "admin_panel")
ACTIONS: tuple[str, ...] = ("grant", "extend", "revoke", "shorten")
MAX_DAYS_PER_GRANT = 365
MAX_DAYS_AHEAD = 730
REASON_MAX_LEN = 300
KEY_MAX_LEN = 64
DEFAULT_COMMAND_REASON = "manual via command"
PRODUCT_BY_SCOPE: dict[str, str] = {"chat": SELARA_AI_PRODUCT_KEY, "user": SELARA_PERSONAL_PRODUCT_KEY}

_SECRET_RE = re.compile(r"(?i)(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S+|\b\d{6,}:[\w-]{20,}\b")


class GrantError(Exception):
    """A refused grant or revoke; ``code`` is stable for the API, ``message`` is shown to the owner."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_days(days: object, *, label: str = "Срок") -> int:
    if isinstance(days, bool) or not isinstance(days, int):
        raise GrantError("invalid_days", f"{label}: целое число дней.")
    if not 1 <= days <= MAX_DAYS_PER_GRANT:
        raise GrantError("invalid_days", f"{label}: от 1 до {MAX_DAYS_PER_GRANT} дней за одну операцию.")
    return days


def validate_reason(reason: object) -> str:
    text = " ".join(str(reason or "").split())
    if not text:
        raise GrantError("invalid_reason", "Укажите причину (до 300 символов).")
    if len(text) > REASON_MAX_LEN:
        raise GrantError("invalid_reason", f"Причина не длиннее {REASON_MAX_LEN} символов (сейчас {len(text)}).")
    if _SECRET_RE.search(text):
        raise GrantError("invalid_reason", "Причина похожа на секрет или токен: уберите его из текста.")
    return text


def validate_key(key: object) -> str:
    text = str(key or "").strip()
    if not text or len(text) > KEY_MAX_LEN or not re.fullmatch(r"[\w:.\-]+", text):
        raise GrantError("invalid_key", f"Ключ идемпотентности: 1..{KEY_MAX_LEN} символов, буквы, цифры и «_:.-».")
    return text


def validate_target(scope: object, target_id: object, *, admin_user_id: int | None = None) -> tuple[str, int]:
    if scope not in SCOPES:
        raise GrantError("invalid_scope", "Область: user (Selara Personal) или chat (подписка группы).")
    if isinstance(target_id, bool) or not isinstance(target_id, int):
        raise GrantError("invalid_target", "Нужен числовой id.")
    if scope == "chat" and target_id >= 0:
        raise GrantError("invalid_target", "Id чата отрицательный (например -1001234567890).")
    if scope == "user":
        if target_id <= 0:
            raise GrantError("invalid_target", "Id пользователя положительный.")
        if admin_user_id is not None and target_id == admin_user_id:
            raise GrantError("invalid_target", "Себе выдавать не нужно: у владельца есть внутренний доступ.")
    return str(scope), int(target_id)


def validate_ahead(*, base_until: datetime, delta: timedelta, now: datetime) -> datetime:
    """The new end date, refused if it lies further than ``MAX_DAYS_AHEAD`` from today."""
    result = base_until + delta
    if result > now + timedelta(days=MAX_DAYS_AHEAD):
        raise GrantError("too_far", f"Подписка не может заканчиваться позже чем через {MAX_DAYS_AHEAD} дней от сегодня.")
    return result


# ----- commands ------------------------------------------------------------------------------------

GRANT_USAGE = (
    "Выдача подписки:\n"
    "<code>/grant_sub personal &lt;user_id|@username&gt; &lt;дни&gt; [причина]</code>\n"
    "<code>/grant_sub group &lt;chat_id&gt; &lt;дни&gt; [причина]</code>"
)
REVOKE_USAGE = (
    "Отзыв подписки:\n"
    "<code>/revoke_sub personal|group &lt;id&gt;</code> — отключить полностью\n"
    "<code>/revoke_sub personal|group &lt;id&gt; &lt;дни&gt; [причина]</code> — убрать выданные дни"
)

_SCOPE_WORDS = {"personal": "user", "user": "user", "group": "chat", "chat": "chat"}


@dataclass(frozen=True, slots=True)
class GrantCommand:
    scope: str
    target: str  # digits (with sign) or @username
    days: int | None
    reason: str


def _parse_scope_target(parts: list[str], usage: str) -> tuple[str, str]:
    if len(parts) < 2 or parts[0].casefold() not in _SCOPE_WORDS:
        raise GrantError("usage", usage)
    return _SCOPE_WORDS[parts[0].casefold()], parts[1]


def parse_grant_command(args: str) -> GrantCommand:
    parts = (args or "").split()
    scope, target = _parse_scope_target(parts, GRANT_USAGE)
    if len(parts) < 3 or not parts[2].isdigit():
        raise GrantError("usage", GRANT_USAGE)
    days = validate_days(int(parts[2]))
    reason = " ".join(parts[3:]) or DEFAULT_COMMAND_REASON
    return GrantCommand(scope=scope, target=target, days=days, reason=validate_reason(reason))


def parse_revoke_command(args: str) -> GrantCommand:
    """``days is None`` means cancel everything; with days only the granted days are removed."""
    parts = (args or "").split()
    scope, target = _parse_scope_target(parts, REVOKE_USAGE)
    if len(parts) == 2:
        return GrantCommand(scope=scope, target=target, days=None, reason=DEFAULT_COMMAND_REASON)
    if not parts[2].isdigit():
        raise GrantError("usage", REVOKE_USAGE)
    days = validate_days(int(parts[2]))
    reason = " ".join(parts[3:]) or DEFAULT_COMMAND_REASON
    return GrantCommand(scope=scope, target=target, days=days, reason=validate_reason(reason))


def target_id_from_text(target: str) -> int | None:
    """A numeric id from the command text; ``None`` for an @username (resolved by lookup)."""
    text = target.strip()
    if re.fullmatch(r"-?\d{1,20}", text):
        return int(text)
    return None


# ----- texts ---------------------------------------------------------------------------------------------


def format_until(value: datetime, timezone_name: str) -> str:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    return value.astimezone(zone).strftime("%d.%m.%Y %H:%M")


def grant_notice(*, scope: str, valid_until: datetime, timezone_name: str, chat_title: str | None = None) -> str:
    until = format_until(valid_until, timezone_name)
    if scope == "chat":
        return f"🎁 Для этого чата активирован Selara AI до {until}."
    return f"🎁 Администратор выдал вам Selara Personal до {until}."


def revoke_notice(*, scope: str, mode: str, valid_until: datetime | None, timezone_name: str) -> str:
    subject = "Selara AI для этого чата" if scope == "chat" else "Selara Personal"
    if mode == "shorten" and valid_until is not None:
        return f"ℹ️ Администратор сократил срок: {subject} теперь действует до {format_until(valid_until, timezone_name)}."
    return f"ℹ️ Администратор отключил {subject}."
