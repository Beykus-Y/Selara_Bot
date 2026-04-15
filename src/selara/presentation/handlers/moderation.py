from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
import shlex
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.exc import IntegrityError

from selara.core.roles import (
    BOT_PERMISSIONS,
    SYSTEM_ROLE_BY_CODE,
    SYSTEM_ROLE_TEMPLATES,
    normalize_assigned_role_code,
)
from selara.domain.entities import BotRole, ModerationAction, UserSnapshot
from selara.domain.value_objects import display_name_from_parts
from selara.presentation.audit import log_chat_action
from selara.presentation.auth import (
    build_chat_snapshot,
    build_user_snapshot,
    get_actor_role,
    get_role_label_ru,
    has_command_access,
    has_permission,
)
from selara.presentation.targeting import resolve_chat_target_user, split_explicit_target_and_tail, strip_wrapping_quotes

router = Router(name="moderation")

_SLASH_MODERATION_COMMANDS: tuple[str, ...] = ("pred", "warn", "unwarn", "ban", "unban")
_MODERATION_ACTIONS: tuple[str, ...] = (*_SLASH_MODERATION_COMMANDS, "unpred")
_TEXT_MOD_ACTIONS: tuple[tuple[str, str], ...] = (
    ("снять пред", "unpred"),
    ("разпред", "unpred"),
    ("анпред", "unpred"),
    ("снять варн", "unwarn"),
    ("разварн", "unwarn"),
    ("анварн", "unwarn"),
    ("снять бан", "unban"),
    ("разбан", "unban"),
    ("анбан", "unban"),
    ("pred", "pred"),
    ("warn", "warn"),
    ("ban", "ban"),
    ("unban", "unban"),
    ("unwarn", "unwarn"),
    ("пред", "pred"),
    ("варн", "warn"),
    ("бан", "ban"),
)
_REPLY_MODERATION_PATTERN = re.compile(
    r"^\s*(?:снять\s+пред|разпред|анпред|снять\s+варн|разварн|анварн|снять\s+бан|разбан|анбан|pred|warn|ban|unban|unwarn|пред|варн|бан)\b",
    re.IGNORECASE,
)
_REST_GRANT_PATTERN = re.compile(r"^\s*выдать\s+рест\s+(?P<days>\d+)(?:\s+(?P<target>[\s\S]+))?\s*$", re.IGNORECASE)
_REST_LIST_PATTERN = re.compile(r"^\s*ресты\s*$", re.IGNORECASE)
_REST_REVOKE_PATTERN = re.compile(r"^\s*забрать\s+рест(?:\s+(?P<target>[\s\S]+))?\s*$", re.IGNORECASE)
_REPLY_ROLE_STEP_PATTERN = re.compile(r"^\s*(?:повысить|понизить)\b", re.IGNORECASE)
_PERSONA_GRANT_PATTERN = re.compile(r"^\s*выдать\s+образ\b(?P<body>[\s\S]*)$", re.IGNORECASE)
_PERSONA_CLEAR_PATTERN = re.compile(r"^\s*снять\s+образ(?:\s+(?P<target>[\s\S]+))?\s*$", re.IGNORECASE)
_PERSONA_LIST_PATTERN = re.compile(r"^\s*образы\s*$", re.IGNORECASE)
_PERSONA_CONFLICT_TTL = timedelta(minutes=15)
_PERMISSION_LABELS_RU: dict[str, str] = {
    "manage_roles": "управление ролями",
    "manage_settings": "управление настройками",
    "manage_games": "управление играми",
    "moderate_users": "модерация пользователей",
    "announce": "объявления",
    "manage_command_access": "доступ команд",
    "manage_role_templates": "шаблоны и кастомные роли",
}
_ROLE_ACTION_TO_COMMAND_KEY: dict[str, str] = {
    "promote": "roleadd",
    "demote": "roleremove",
}


@dataclass(frozen=True)
class PendingPersonaConflict:
    request_id: str
    chat_id: int
    actor_user_id: int
    target_user_id: int
    current_owner_user_id: int
    persona_label: str
    created_at: datetime


_PENDING_PERSONA_CONFLICTS: dict[str, PendingPersonaConflict] = {}


def _target_label(target: UserSnapshot) -> str:
    return display_name_from_parts(
        user_id=target.telegram_user_id,
        username=target.username,
        first_name=target.first_name,
        last_name=target.last_name,
        chat_display_name=target.chat_display_name,
    )


def _split_first_token(raw: str) -> tuple[str | None, str]:
    value = raw.strip()
    if not value:
        return None, ""
    parts = value.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1].strip()


def _extract_persona_label(match: re.Match[str]) -> str | None:
    for group_name in ("label_dq", "label_ruq", "label_lcq", "label_plain"):
        value = (match.group(group_name) or "").strip()
        if value:
            return value
    return None


async def _resolve_target_user(message: Message, activity_repo, explicit_token: str | None) -> UserSnapshot | None:
    return await resolve_chat_target_user(
        message,
        activity_repo,
        explicit_target=explicit_token,
        prefer_reply=True,
    )


def _extract_persona_grant_request(text: str) -> tuple[bool, str | None, str | None, str | None]:
    match = _PERSONA_GRANT_PATTERN.match(text)
    if match is None:
        return False, None, None, None

    body = (match.group("body") or "").strip()
    if not body:
        return True, None, None, (
            'Формат: reply + <code>выдать образ "Аль-Хайтам"</code> '
            'или <code>выдать образ @username "Аль-Хайтам"</code>.'
        )

    quoted_label_match = re.match(
        r'^(?P<before>[\s\S]*?)\s+(?:"(?P<label_dq>[^"]+)"|«(?P<label_ruq>[^»]+)»|“(?P<label_lcq>[^”]+)”)\s*$',
        body,
    )
    if quoted_label_match is not None:
        return (
            True,
            (quoted_label_match.group("before") or "").strip() or None,
            _extract_persona_label(quoted_label_match),
            None,
        )

    if "\n" in body:
        target_text, _, label_text = body.partition("\n")
        persona_label = strip_wrapping_quotes(label_text)
        if not persona_label.strip():
            return True, None, None, (
                'Формат: reply + <code>выдать образ "Аль-Хайтам"</code> '
                'или <code>выдать образ @username "Аль-Хайтам"</code>.'
            )
        return True, target_text.strip() or None, persona_label, None

    parts = body.split(maxsplit=1)
    first_token = parts[0]
    if first_token.startswith("@") or first_token.lstrip("-").isdigit():
        persona_label = strip_wrapping_quotes(parts[1] if len(parts) > 1 else "")
        if not persona_label.strip():
            return True, None, None, (
                'Формат: reply + <code>выдать образ "Аль-Хайтам"</code> '
                'или <code>выдать образ @username "Аль-Хайтам"</code>.'
            )
        return True, first_token, persona_label, None

    return True, None, strip_wrapping_quotes(body), None


def _cleanup_pending_persona_conflicts() -> None:
    now = datetime.now(timezone.utc)
    for request_id, pending in list(_PENDING_PERSONA_CONFLICTS.items()):
        if pending.created_at + _PERSONA_CONFLICT_TTL <= now:
            _PENDING_PERSONA_CONFLICTS.pop(request_id, None)


def _persona_conflict_markup(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Заменить", callback_data=f"persona:confirm:{request_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"persona:cancel:{request_id}"),
            ]
        ]
    )


async def _ensure_text_command_access(message: Message, activity_repo, *, command_key: str) -> bool:
    command_allowed, actor_role_code, required_role_code, _ = await has_command_access(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        command_key=command_key,
        bootstrap_if_missing_owner=True,
        bot=getattr(message, "bot", None),
    )
    if command_allowed:
        return True

    actor_label = await get_role_label_ru(activity_repo, chat_id=message.chat.id, role_code=actor_role_code)
    required_label = await get_role_label_ru(activity_repo, chat_id=message.chat.id, role_code=required_role_code)
    await message.answer(
        (
            f"Недостаточно прав для команды <code>{escape(command_key)}</code>.\n"
            f"Ваш ранг: <code>{escape(actor_label)}</code>\n"
            f"Нужный ранг: <code>{escape(required_label)}</code>"
        ),
        parse_mode="HTML",
    )
    return False


async def _ensure_rest_command_access(message: Message, activity_repo, *, command_key: str) -> bool:
    return await _ensure_text_command_access(message, activity_repo, command_key=command_key)


def _resolve_system_role_rank(role_code: BotRole | None) -> int | None:
    normalized = normalize_assigned_role_code(role_code)
    if normalized is None:
        return None
    definition = SYSTEM_ROLE_BY_CODE.get(normalized)
    if definition is None:
        return None
    return definition.rank


def _can_manage_target(
    *,
    actor_role_code: BotRole | None = None,
    actor_rank: int | None = None,
    target_rank: int | None = None,
    actor_role: BotRole | None = None,
    target_role: BotRole | None = None,
) -> bool:
    actor_role_code = normalize_assigned_role_code(actor_role_code or actor_role)
    if actor_role_code is None:
        raise TypeError("actor_role_code or actor_role is required")
    if actor_rank is None:
        actor_rank = _resolve_system_role_rank(actor_role_code)
    if actor_rank is None:
        raise TypeError("actor_rank is required when actor role rank cannot be resolved")
    if target_rank is None and target_role is not None:
        target_rank = _resolve_system_role_rank(target_role)
    if actor_role_code == "owner":
        return True
    if target_rank is None:
        return True
    return actor_rank > target_rank


def _role_add_allowed(
    *,
    actor_role_code: BotRole | None = None,
    actor_rank: int | None = None,
    target_current_rank: int | None = None,
    target_new_rank: int | None = None,
    actor_role: BotRole | None = None,
    target_current_role: BotRole | None = None,
    target_new_role: BotRole | None = None,
) -> bool:
    actor_role_code = normalize_assigned_role_code(actor_role_code or actor_role)
    if actor_role_code is None:
        raise TypeError("actor_role_code or actor_role is required")
    if actor_rank is None:
        actor_rank = _resolve_system_role_rank(actor_role_code)
    if actor_rank is None:
        raise TypeError("actor_rank is required when actor role rank cannot be resolved")
    if target_current_rank is None and target_current_role is not None:
        target_current_rank = _resolve_system_role_rank(target_current_role)
    if target_new_rank is None:
        target_new_rank = _resolve_system_role_rank(target_new_role)
    if target_new_rank is None:
        raise TypeError("target_new_rank or target_new_role is required")
    if actor_role_code == "owner":
        return True

    if target_new_rank >= actor_rank:
        return False
    if target_current_rank is not None and target_current_rank >= actor_rank:
        return False
    return True


def _parse_roleadd_args(raw_args: str) -> list[tuple[str, str | None]]:
    try:
        tokens = shlex.split(raw_args)
    except ValueError:
        return []
    if not tokens:
        return []

    first = tokens[0].strip()
    second = tokens[1].strip() if len(tokens) >= 2 else None
    if not first:
        return []
    if second is None or not second:
        return [(first, None)]
    if first == second:
        return [(first, second)]
    return [(first, second), (second, first)]


def _parse_shlex_args(raw_args: str) -> list[str] | None:
    try:
        return shlex.split(raw_args)
    except ValueError:
        return None


def _permissions_to_text(permissions: tuple[str, ...]) -> str:
    if not permissions:
        return "нет прав"
    return ", ".join(_PERMISSION_LABELS_RU.get(permission, permission) for permission in permissions)


def _format_role_definition_line(*, role_code: str, title_ru: str, rank: int, permissions: tuple[str, ...], is_system: bool) -> str:
    marker = "system" if is_system else "custom"
    return (
        f"• <code>{escape(title_ru)}</code> "
        f"(<code>{escape(role_code)}</code>, rank=<code>{rank}</code>, {marker})\n"
        f"  Права: {escape(_permissions_to_text(permissions))}"
    )


async def _try_restrict_user(bot: Bot, *, chat_id: int, user_id: int) -> bool:
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


async def _try_unrestrict_user(bot: Bot, *, chat_id: int, user_id: int) -> bool:
    try:
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


def _is_chat_member_admin(member) -> bool:
    return getattr(member, "status", None) in {"administrator", "creator"}


async def _target_is_telegram_admin(bot: Bot, *, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except (AttributeError, TelegramBadRequest, TelegramForbiddenError):
        return False
    return _is_chat_member_admin(member)


def _parse_text_action(text: str) -> tuple[str, str] | None:
    raw = text.strip()
    if not raw or raw.startswith("/"):
        return None

    words = raw.split()
    if not words:
        return None

    lowered_words = [word.lower() for word in words]
    lowered = " ".join(lowered_words)
    for token, action in _TEXT_MOD_ACTIONS:
        token_words = token.split()
        if lowered == token:
            return action, ""
        if len(lowered_words) > len(token_words) and lowered_words[: len(token_words)] == token_words:
            reason = " ".join(words[len(token_words) :]).strip()
            return action, reason

    return None


def _parse_reply_text_action(text: str) -> tuple[str, str] | None:
    return _parse_text_action(text)


def _format_rest_expires_at(value) -> str:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    localized = normalized.astimezone(timezone.utc)
    return localized.strftime("%d.%m.%Y %H:%M UTC")


def _build_rest_list_messages(entries) -> list[str]:
    if not entries:
        return ["✅ <b>Активных рестов сейчас нет.</b>"]

    header = "<b>Активные ресты:</b>"
    continuation = "<b>Продолжение списка рестов:</b>"
    chunks: list[str] = []
    current_lines = [header]
    current_len = len(header)

    for index, entry in enumerate(entries, start=1):
        line = (
            f"{index}. <b>{escape(_target_label(entry.user))}</b>"
            f" - до <code>{_format_rest_expires_at(entry.expires_at)}</code>"
        )
        extra_len = len(line) + 1
        if current_lines and current_len + extra_len > 3900:
            chunks.append("\n".join(current_lines))
            current_lines = [continuation]
            current_len = len(continuation)
        current_lines.append(line)
        current_len += extra_len

    chunks.append("\n".join(current_lines))
    return chunks


def _parse_role_step_action(text: str) -> str | None:
    raw = text.strip()
    if not raw or raw.startswith("/"):
        return None

    token = raw.split(maxsplit=1)[0].lower()
    if token == "повысить":
        return "promote"
    if token == "понизить":
        return "demote"
    return None


async def _apply_moderation_action(
    *,
    message: Message,
    activity_repo,
    bot: Bot,
    command_name: str,
    raw_tail: str,
    use_reply_target: bool,
) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    if command_name not in _MODERATION_ACTIONS:
        return

    command_allowed, actor_role_code, required_role_code, _ = await has_command_access(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        command_key=command_name,
        bootstrap_if_missing_owner=False,
    )
    if not command_allowed:
        actor_label = await get_role_label_ru(activity_repo, chat_id=message.chat.id, role_code=actor_role_code)
        required_label = await get_role_label_ru(activity_repo, chat_id=message.chat.id, role_code=required_role_code)
        await message.answer(
            (
                f"Недостаточно прав для команды <code>{escape(command_name)}</code>.\n"
                f"Ваш ранг: <code>{escape(actor_label)}</code>\n"
                f"Нужный ранг: <code>{escape(required_label)}</code>"
            ),
            parse_mode="HTML",
        )
        return

    allowed, actor_role, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="moderate_users",
        bootstrap_if_missing_owner=False,
    )
    if not allowed or actor_role is None:
        return

    actor_role_definition = await activity_repo.get_effective_role_definition(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
    )
    if actor_role_definition is None:
        return

    if use_reply_target and message.reply_to_message is not None:
        target_token = None
        reason = raw_tail.strip()
    else:
        target_token, reason = split_explicit_target_and_tail(raw_tail)

    target = await _resolve_target_user(message, activity_repo, target_token)
    if target is None:
        await message.answer("Укажите пользователя через reply, @username, id или текущий образ.")
        return

    if target.telegram_user_id == message.from_user.id:
        await message.answer("Нельзя применять модерацию к самому себе.")
        return

    if target.is_bot:
        await message.answer("Нельзя выдавать санкции боту.")
        return

    target_role_definition = await activity_repo.get_effective_role_definition(
        chat_id=message.chat.id,
        user_id=target.telegram_user_id,
    )
    if not _can_manage_target(
        actor_role_code=actor_role_definition.role_code,
        actor_rank=actor_role_definition.rank,
        target_rank=target_role_definition.rank if target_role_definition is not None else None,
    ):
        return

    if command_name == "ban" and await _target_is_telegram_admin(
        bot,
        chat_id=message.chat.id,
        user_id=target.telegram_user_id,
    ):
        return

    chat = build_chat_snapshot(chat_id=message.chat.id, chat_type=message.chat.type, chat_title=message.chat.title)
    actor = build_user_snapshot(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
    )

    action: ModerationAction = command_name  # type: ignore[assignment]
    result = await activity_repo.apply_moderation_action(
        chat=chat,
        actor=actor,
        target=target,
        action=action,
        reason=reason or None,
        amount=1,
    )

    restrict_applied = None
    if action == "ban" or result.auto_ban_triggered:
        restrict_applied = await _try_restrict_user(bot, chat_id=message.chat.id, user_id=target.telegram_user_id)
    elif action == "unban":
        restrict_applied = await _try_unrestrict_user(bot, chat_id=message.chat.id, user_id=target.telegram_user_id)

    st = result.state
    lines = [f"<b>{escape(_target_label(target))}</b>"]

    if action == "pred":
        lines.append("Пред.")
    elif action == "warn":
        lines.append("Варн.")
    elif action == "unpred":
        lines.append("Один пред снят.")
    elif action == "unwarn":
        lines.append("Один варн снят.")
    elif action == "ban":
        lines.append("Бан.")
    elif action == "unban":
        lines.append("Бан снят.")

    normalized_reason = (reason or "").strip()
    if action in {"pred", "warn", "ban"}:
        lines.append("Причина:")
        lines.append(escape(normalized_reason or "не указана"))

    if result.auto_warns_added > 0:
        lines.append(f"Авто-конвертация: +{result.auto_warns_added} варн(ов) из предов.")
    if result.auto_ban_triggered:
        lines.append("Авто-бан: достигнут порог 3 варна.")

    lines.append(f"Преды: <b>{st.pending_preds}</b>/3 | Варны: <b>{st.warn_count}</b>/3 | Банов: {st.total_bans}")

    if restrict_applied is False:
        lines.append("<i>Telegram-бан/разбан не применён (нет прав у бота), но внутренний статус обновлён.</i>")

    await message.answer("\n".join(lines), parse_mode="HTML")
async def _apply_rest_command(
    *,
    message: Message,
    activity_repo,
    action: str,
    duration_days: int | None,
    target_token: str | None,
) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    command_key = "rest_grant" if action == "grant" else "rest_revoke"
    if not await _ensure_rest_command_access(message, activity_repo, command_key=command_key):
        return

    if action == "grant" and (duration_days is None or duration_days <= 0):
        await message.answer("Формат: reply на сообщение или <code>выдать рест 7 @username</code>.", parse_mode="HTML")
        return

    target = await _resolve_target_user(message, activity_repo, target_token)
    if target is None:
        await message.answer("Укажите пользователя через reply, @username, id или текущий образ.")
        return
    if target.is_bot:
        await message.answer("Нельзя управлять рестом у бота.")
        return

    chat = build_chat_snapshot(chat_id=message.chat.id, chat_type=message.chat.type, chat_title=message.chat.title)
    actor = build_user_snapshot(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
    )

    if action == "grant":
        state = await activity_repo.grant_rest(
            chat=chat,
            actor=actor,
            target=target,
            duration_days=int(duration_days or 0),
        )
        await message.answer(
            "\n".join(
                [
                    f"<b>{escape(_target_label(target))}</b>",
                    f"Рест выдан на <b>{int(duration_days or 0)}</b> дн.",
                    f"Активен до <code>{_format_rest_expires_at(state.expires_at)}</code>",
                ]
            ),
            parse_mode="HTML",
        )
        return

    state = await activity_repo.revoke_rest(chat=chat, actor=actor, target=target)
    if state is None:
        await message.answer(
            f"У <b>{escape(_target_label(target))}</b> нет активного реста.",
            parse_mode="HTML",
        )
        return

    await message.answer(
        "\n".join(
            [
                f"<b>{escape(_target_label(target))}</b>",
                "Рест снят.",
            ]
        ),
        parse_mode="HTML",
    )


@router.message(F.text.regexp(_REST_LIST_PATTERN))
async def rest_list_text_command(message: Message, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return
    if not await _ensure_rest_command_access(message, activity_repo, command_key="rest_list"):
        return

    entries = await activity_repo.list_active_rest_entries(chat_id=message.chat.id)
    for chunk in _build_rest_list_messages(entries):
        await message.answer(chunk, parse_mode="HTML", disable_web_page_preview=True)


async def _apply_persona_grant(
    *,
    message: Message,
    activity_repo,
    persona_label: str,
    target_token: str | None,
) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    if not await _ensure_text_command_access(message, activity_repo, command_key="persona_grant"):
        return

    target = await _resolve_target_user(message, activity_repo, target_token)
    if target is None:
        await message.answer(
            'Формат: reply + <code>выдать образ "Аль-Хайтам"</code> или <code>выдать образ @username "Аль-Хайтам"</code>.',
            parse_mode="HTML",
        )
        return
    if target.is_bot:
        await message.answer("Нельзя выдать образ боту.")
        return

    try:
        existing_label = await activity_repo.get_chat_persona_label(chat_id=message.chat.id, user_id=target.telegram_user_id)
        owner = await activity_repo.find_chat_persona_owner(chat_id=message.chat.id, persona_label=persona_label)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    if owner is not None and owner.user.telegram_user_id == target.telegram_user_id:
        await message.answer(
            f"<b>{escape(_target_label(target))}</b> уже носит образ <code>[{escape(owner.persona_label)}]</code>.",
            parse_mode="HTML",
        )
        return

    if owner is not None:
        _cleanup_pending_persona_conflicts()
        request_id = secrets.token_hex(8)
        _PENDING_PERSONA_CONFLICTS[request_id] = PendingPersonaConflict(
            request_id=request_id,
            chat_id=message.chat.id,
            actor_user_id=message.from_user.id,
            target_user_id=target.telegram_user_id,
            current_owner_user_id=owner.user.telegram_user_id,
            persona_label=persona_label,
            created_at=datetime.now(timezone.utc),
        )
        await message.answer(
            (
                f'Образ <code>[{escape(owner.persona_label)}]</code> уже занят пользователем '
                f"<b>{escape(_target_label(owner.user))}</b>.\n"
                f"Заменить владельца на <b>{escape(_target_label(target))}</b>?"
            ),
            parse_mode="HTML",
            reply_markup=_persona_conflict_markup(request_id),
        )
        return

    chat = build_chat_snapshot(chat_id=message.chat.id, chat_type=message.chat.type, chat_title=message.chat.title)
    target_user = build_user_snapshot(
        user_id=target.telegram_user_id,
        username=target.username,
        first_name=target.first_name,
        last_name=target.last_name,
        is_bot=target.is_bot,
        chat_display_name=target.chat_display_name,
    )
    try:
        stored_label = await activity_repo.set_chat_persona_label(
            chat=chat,
            user=target_user,
            persona_label=persona_label,
            granted_by_user_id=message.from_user.id,
        )
    except (ValueError, IntegrityError) as exc:
        await message.answer(str(exc))
        return

    action_code = "persona_replaced" if existing_label and existing_label != stored_label else "persona_granted"
    await log_chat_action(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        action_code=action_code,
        description=f"Назначен образ [{stored_label}] пользователю {target.telegram_user_id}.",
        actor_user_id=message.from_user.id,
        target_user_id=target.telegram_user_id,
        meta_json={"persona_label": stored_label},
    )
    verb = "Образ обновлён" if existing_label and existing_label != stored_label else "Образ выдан"
    await message.answer(
        f"{verb}: <b>{escape(_target_label(target))}</b> -> <code>[{escape(stored_label or persona_label)}]</code>",
        parse_mode="HTML",
    )


async def _apply_persona_clear(
    *,
    message: Message,
    activity_repo,
    target_token: str | None,
) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    if not await _ensure_text_command_access(message, activity_repo, command_key="persona_clear"):
        return

    target = await _resolve_target_user(message, activity_repo, target_token)
    if target is None:
        await message.answer(
            "Формат: reply + <code>снять образ</code> или <code>снять образ @username</code>.",
            parse_mode="HTML",
        )
        return
    if target.is_bot:
        await message.answer("У бота нет чатовского образа.")
        return

    current_label = await activity_repo.get_chat_persona_label(chat_id=message.chat.id, user_id=target.telegram_user_id)
    if current_label is None:
        await message.answer(f"У <b>{escape(_target_label(target))}</b> нет выданного образа.", parse_mode="HTML")
        return

    removed = await activity_repo.clear_chat_persona_label(chat_id=message.chat.id, user_id=target.telegram_user_id)
    if not removed:
        await message.answer(f"У <b>{escape(_target_label(target))}</b> нет выданного образа.", parse_mode="HTML")
        return

    await log_chat_action(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        action_code="persona_cleared",
        description=f"Снят образ [{current_label}] у пользователя {target.telegram_user_id}.",
        actor_user_id=message.from_user.id,
        target_user_id=target.telegram_user_id,
        meta_json={"persona_label": current_label},
    )
    await message.answer(
        f"Образ снят: <b>{escape(_target_label(target))}</b> <- <code>[{escape(current_label)}]</code>",
        parse_mode="HTML",
    )


async def _send_persona_list(message: Message, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    if not await _ensure_text_command_access(message, activity_repo, command_key="persona_list"):
        return

    assignments = await activity_repo.list_chat_persona_assignments(chat_id=message.chat.id)
    if not assignments:
        await message.answer("В этом чате пока нет выданных образов.")
        return

    lines = ["<b>Образы чата</b>"]
    for assignment in assignments:
        lines.append(
            f"• <code>[{escape(assignment.persona_label)}]</code> — <b>{escape(_target_label(assignment.user))}</b>"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


async def _apply_role_step_action(*, message: Message, activity_repo, action: str) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return
    if action not in {"promote", "demote"}:
        return
    if message.reply_to_message is None:
        await message.answer("Команды «повысить/понизить» работают только reply на сообщение пользователя.")
        return

    command_key = _ROLE_ACTION_TO_COMMAND_KEY[action]
    command_allowed, actor_role_code, required_role_code, _ = await has_command_access(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        command_key=command_key,
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not command_allowed:
        actor_label = await get_role_label_ru(activity_repo, chat_id=message.chat.id, role_code=actor_role_code)
        required_label = await get_role_label_ru(activity_repo, chat_id=message.chat.id, role_code=required_role_code)
        await message.answer(
            (
                f"Недостаточно прав для команды <code>{escape(command_key)}</code>.\n"
                f"Ваш ранг: <code>{escape(actor_label)}</code>\n"
                f"Нужный ранг: <code>{escape(required_label)}</code>"
            ),
            parse_mode="HTML",
        )
        return

    allowed, _actor_role, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_roles",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed:
        await message.answer("Недостаточно прав для управления ролями.")
        return

    target = await _resolve_target_user(message, activity_repo, explicit_token=None)
    if target is None:
        await message.answer("Не удалось определить пользователя из reply.")
        return
    if target.is_bot:
        await message.answer("Нельзя менять роль у бота.")
        return

    actor_role_definition = await activity_repo.get_effective_role_definition(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
    )
    target_current_definition = await activity_repo.get_effective_role_definition(
        chat_id=message.chat.id,
        user_id=target.telegram_user_id,
    )
    target_assigned_role = await activity_repo.get_bot_role(chat_id=message.chat.id, user_id=target.telegram_user_id)

    if target.telegram_user_id == message.from_user.id and actor_role_definition.role_code != "owner":
        await message.answer("Нельзя менять свою роль, если вы не владелец.")
        return

    if not _can_manage_target(
        actor_role_code=actor_role_definition.role_code,
        actor_rank=actor_role_definition.rank,
        target_rank=target_current_definition.rank,
    ):
        await message.answer("Недостаточно уровня доступа для этого пользователя.")
        return

    roles = await activity_repo.list_chat_role_definitions(chat_id=message.chat.id)
    if not roles:
        await message.answer("Список ролей недоступен.")
        return

    roles_sorted = sorted(roles, key=lambda item: (item.rank, item.role_code))
    new_role = None
    if action == "promote":
        candidates = [item for item in roles_sorted if item.rank > target_current_definition.rank]
        if actor_role_definition.role_code != "owner":
            candidates = [item for item in candidates if item.rank < actor_role_definition.rank]
        if not candidates:
            await message.answer("Не могу повысить: достигнут максимум или не хватает ваших прав.")
            return
        new_role = candidates[0]
    else:
        if target_current_definition.role_code == "owner":
            role_items = await activity_repo.list_bot_roles(chat_id=message.chat.id)
            owner_count = sum(1 for _, role in role_items if role == "owner")
            if owner_count <= 1:
                await message.answer("Нельзя понизить последнего владельца.")
                return
        candidates = [item for item in roles_sorted if item.rank < target_current_definition.rank]
        if actor_role_definition.role_code != "owner":
            candidates = [item for item in candidates if item.rank < actor_role_definition.rank]
        if not candidates:
            await message.answer("Не могу понизить: уже минимальный ранг или не хватает ваших прав.")
            return
        new_role = candidates[-1]

    if new_role is None:
        await message.answer("Не удалось подобрать целевую роль.")
        return

    chat = build_chat_snapshot(chat_id=message.chat.id, chat_type=message.chat.type, chat_title=message.chat.title)
    if new_role.role_code == "participant":
        await activity_repo.remove_bot_role(chat_id=message.chat.id, user_id=target.telegram_user_id)
    else:
        await activity_repo.set_bot_role(
            chat=chat,
            target=target,
            role=new_role.role_code,
            assigned_by_user_id=message.from_user.id,
        )

    previous_title = target_current_definition.title_ru
    new_title = new_role.title_ru
    verb = "Повышено" if action == "promote" else "Понижено"
    if target_assigned_role is None and new_role.role_code == "participant":
        await message.answer("Изменений нет: у пользователя уже минимальный ранг.")
        return
    await message.answer(
        (
            f"{verb}: <b>{escape(_target_label(target))}</b>\n"
            f"Было: <code>{escape(previous_title)}</code>\n"
            f"Стало: <code>{escape(new_title)}</code>"
        ),
        parse_mode="HTML",
    )


@router.message(Command("roles"))
async def roles_command(message: Message, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    _actor_role, bootstrapped = await get_actor_role(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )

    items = await activity_repo.list_bot_roles(chat_id=message.chat.id)
    if not items:
        await message.answer("Назначенных ролей пока нет.")
        return

    role_definitions = await activity_repo.list_chat_role_definitions(chat_id=message.chat.id)
    role_titles = {item.role_code: item.title_ru for item in role_definitions}

    lines = ["<b>Роли бота в чате:</b>"]
    if bootstrapped:
        lines.append("<i>Инициализация: первый владелец назначен автоматически.</i>")

    for user, role in items:
        lines.append(f"- <code>{escape(role_titles.get(role, role))}</code>: {escape(_target_label(user))}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("roleadd"))
async def role_add_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    allowed, actor_role, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_roles",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed or actor_role is None:
        await message.answer("Недостаточно прав для управления ролями.")
        return

    raw_args = (command.args or "").strip()
    candidates = _parse_roleadd_args(raw_args)
    if not candidates:
        await message.answer('Формат: /roleadd "<роль|ранг>" [@user|id] или /roleadd [@user|id] "<роль|ранг>"')
        return

    target_role = None
    target = None
    role_found = False
    for role_token, target_token in candidates:
        resolved_role = await activity_repo.resolve_chat_role_definition(chat_id=message.chat.id, token=role_token)
        if resolved_role is None:
            continue
        role_found = True
        resolved_target = await _resolve_target_user(message, activity_repo, target_token)
        if resolved_target is None:
            continue
        target_role = resolved_role
        target = resolved_target
        break

    if target_role is None:
        if role_found:
            await message.answer("Укажите пользователя через reply или @username/id.")
            return
        roles = await activity_repo.list_chat_role_definitions(chat_id=message.chat.id)
        role_list = ", ".join(f"{item.title_ru} ({item.rank})" for item in roles)
        await message.answer(f"Неизвестная роль. Доступно: {role_list}")
        return

    actor_role_definition = await activity_repo.get_effective_role_definition(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
    )
    if target.telegram_user_id == message.from_user.id and actor_role_definition.role_code != "owner":
        await message.answer("Нельзя менять свою роль, если вы не владелец.")
        return

    current_target_definition = await activity_repo.get_effective_role_definition(
        chat_id=message.chat.id,
        user_id=target.telegram_user_id,
    )
    if not _role_add_allowed(
        actor_role_code=actor_role_definition.role_code,
        actor_rank=actor_role_definition.rank,
        target_current_rank=current_target_definition.rank if current_target_definition is not None else None,
        target_new_rank=target_role.rank,
    ):
        await message.answer("Эту роль выдать нельзя: недостаточно уровня доступа.")
        return

    chat = build_chat_snapshot(chat_id=message.chat.id, chat_type=message.chat.type, chat_title=message.chat.title)
    await activity_repo.set_bot_role(
        chat=chat,
        target=target,
        role=target_role.role_code,
        assigned_by_user_id=message.from_user.id,
    )
    await message.answer(
        f"Назначено: <b>{escape(_target_label(target))}</b> → <code>{escape(target_role.title_ru)}</code>",
        parse_mode="HTML",
    )


@router.message(Command("roleremove"))
async def role_remove_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    allowed, actor_role, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_roles",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed or actor_role is None:
        await message.answer("Недостаточно прав для управления ролями.")
        return

    target = await _resolve_target_user(message, activity_repo, (command.args or "").strip() or None)
    if target is None:
        await message.answer("Укажите пользователя через reply, @username, id или текущий образ.")
        return

    actor_role_definition = await activity_repo.get_effective_role_definition(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
    )
    current_role = await activity_repo.get_bot_role(chat_id=message.chat.id, user_id=target.telegram_user_id)
    if current_role is None:
        await message.answer("У пользователя нет роли бота.")
        return
    current_role_definition = await activity_repo.get_chat_role_definition(chat_id=message.chat.id, role_code=current_role)
    if current_role_definition is None:
        await message.answer("У пользователя некорректная роль.")
        return

    if current_role_definition.role_code == "owner":
        role_items = await activity_repo.list_bot_roles(chat_id=message.chat.id)
        owner_count = sum(1 for _, role in role_items if role == "owner")
        if owner_count <= 1:
            await message.answer("Нельзя снять последнего владельца.")
            return

    if not _can_manage_target(
        actor_role_code=actor_role_definition.role_code,
        actor_rank=actor_role_definition.rank,
        target_rank=current_role_definition.rank,
    ):
        await message.answer("Недостаточно уровня доступа для снятия этой роли.")
        return

    if target.telegram_user_id == message.from_user.id and current_role_definition.role_code == "owner":
        await message.answer("Нельзя снять роль владельца у самого себя.")
        return

    removed = await activity_repo.remove_bot_role(chat_id=message.chat.id, user_id=target.telegram_user_id)
    if not removed:
        await message.answer("Роль не найдена.")
        return

    await message.answer(f"Роль снята у <b>{escape(_target_label(target))}</b>.", parse_mode="HTML")


@router.message(Command("roledefs"))
async def role_definitions_command(message: Message, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return

    roles = await activity_repo.list_chat_role_definitions(chat_id=message.chat.id)
    if not roles:
        await message.answer("Роли не настроены.")
        return

    lines = ["<b>Роли бота (доступные для назначения):</b>"]
    for role in roles:
        lines.append(
            _format_role_definition_line(
                role_code=role.role_code,
                title_ru=role.title_ru,
                rank=role.rank,
                permissions=role.permissions,
                is_system=role.is_system,
            )
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("roletemplates"))
async def role_templates_command(message: Message, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_role_templates",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed:
        await message.answer("Недостаточно прав для управления шаблонами ролей.")
        return

    lines = ["<b>Системные шаблоны ролей:</b>"]
    for template in SYSTEM_ROLE_TEMPLATES:
        permissions = tuple(sorted(template.permissions))
        lines.append(
            _format_role_definition_line(
                role_code=template.role_code,
                title_ru=template.title_ru,
                rank=template.rank,
                permissions=permissions,
                is_system=True,
            )
        )
        lines.append(f"  Шаблон: <code>{escape(template.template_key)}</code>")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("rolecreate"))
async def role_create_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_role_templates",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed:
        await message.answer("Недостаточно прав для создания кастомных ролей.")
        return

    tokens = _parse_shlex_args((command.args or "").strip())
    if not tokens:
        await message.answer('Формат: /rolecreate "<название>" ["шаблон"] [ранг]')
        return

    title_ru = tokens[0]
    template_token = tokens[1] if len(tokens) >= 2 else "participant"
    rank: int | None = None
    if len(tokens) >= 3:
        if not tokens[2].lstrip("-").isdigit():
            await message.answer("Ранг должен быть числом.")
            return
        rank = int(tokens[2])

    chat = build_chat_snapshot(chat_id=message.chat.id, chat_type=message.chat.type, chat_title=message.chat.title)
    try:
        created = await activity_repo.create_custom_role_from_template(
            chat=chat,
            title_ru=title_ru,
            template_token=template_token,
            rank=rank,
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return

    await message.answer(
        (
            "<b>Кастомная роль создана:</b>\n"
            f"Название: <code>{escape(created.title_ru)}</code>\n"
            f"Код: <code>{escape(created.role_code)}</code>\n"
            f"Ранг: <code>{created.rank}</code>\n"
            f"Права: {escape(_permissions_to_text(created.permissions))}"
        ),
        parse_mode="HTML",
    )


@router.message(Command("rolesettitle"))
async def role_set_title_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_role_templates",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed:
        await message.answer("Недостаточно прав для редактирования кастомных ролей.")
        return

    tokens = _parse_shlex_args((command.args or "").strip())
    if tokens is None or len(tokens) != 2:
        await message.answer('Формат: /rolesettitle "<роль>" "<новое название>"')
        return

    try:
        updated = await activity_repo.update_custom_role(
            chat_id=message.chat.id,
            role_token=tokens[0],
            title_ru=tokens[1],
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return

    await message.answer(
        (
            "<b>Название роли обновлено:</b>\n"
            f"Роль: <code>{escape(updated.role_code)}</code>\n"
            f"Новое имя: <code>{escape(updated.title_ru)}</code>"
        ),
        parse_mode="HTML",
    )


@router.message(Command("rolesetrank"))
async def role_set_rank_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_role_templates",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed:
        await message.answer("Недостаточно прав для редактирования кастомных ролей.")
        return

    tokens = _parse_shlex_args((command.args or "").strip())
    if tokens is None or len(tokens) != 2:
        await message.answer('Формат: /rolesetrank "<роль>" <ранг>')
        return
    if not tokens[1].lstrip("-").isdigit():
        await message.answer("Ранг должен быть числом.")
        return

    try:
        updated = await activity_repo.update_custom_role(
            chat_id=message.chat.id,
            role_token=tokens[0],
            rank=int(tokens[1]),
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return

    await message.answer(
        (
            "<b>Ранг роли обновлён:</b>\n"
            f"Роль: <code>{escape(updated.title_ru)}</code>\n"
            f"Новый ранг: <code>{updated.rank}</code>"
        ),
        parse_mode="HTML",
    )


@router.message(Command("roleperms"))
async def role_permissions_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_role_templates",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed:
        await message.answer("Недостаточно прав для редактирования кастомных ролей.")
        return

    tokens = _parse_shlex_args((command.args or "").strip())
    if tokens is None or len(tokens) < 2:
        await message.answer(
            (
                'Формат: /roleperms "<роль>" +announce -manage_games ...\n'
                'Или полный набор: /roleperms "<роль>" =announce,manage_settings'
            )
        )
        return

    role_token = tokens[0]
    role = await activity_repo.resolve_chat_role_definition(chat_id=message.chat.id, token=role_token)
    if role is None:
        await message.answer("Роль не найдена.")
        return
    current_permissions = set(role.permissions)

    ops = tokens[1:]
    if len(ops) == 1 and ops[0].startswith("="):
        raw_items = [item.strip().lower() for item in ops[0][1:].split(",") if item.strip()]
        unknown = [item for item in raw_items if item not in BOT_PERMISSIONS]
        if unknown:
            await message.answer(f"Неизвестные права: {', '.join(unknown)}")
            return
        new_permissions = set(raw_items)
    else:
        new_permissions = set(current_permissions)
        for op in ops:
            if not op:
                continue
            sign = op[0]
            perm = op[1:] if sign in {"+", "-"} else op
            perm = perm.strip().lower()
            if perm not in BOT_PERMISSIONS:
                await message.answer(f"Неизвестное право: {perm}")
                return
            if sign == "-":
                new_permissions.discard(perm)
            else:
                new_permissions.add(perm)

    try:
        updated = await activity_repo.update_custom_role(
            chat_id=message.chat.id,
            role_token=role_token,
            permissions=tuple(sorted(new_permissions)),
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return

    await message.answer(
        (
            "<b>Права роли обновлены:</b>\n"
            f"Роль: <code>{escape(updated.title_ru)}</code>\n"
            f"Права: {escape(_permissions_to_text(updated.permissions))}"
        ),
        parse_mode="HTML",
    )


@router.message(Command("roledelete"))
async def role_delete_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_role_templates",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed:
        await message.answer("Недостаточно прав для удаления кастомных ролей.")
        return

    tokens = _parse_shlex_args((command.args or "").strip())
    if tokens is None or len(tokens) != 1:
        await message.answer('Формат: /roledelete "<роль>"')
        return

    try:
        deleted = await activity_repo.delete_custom_role(chat_id=message.chat.id, role_token=tokens[0])
    except ValueError as exc:
        await message.answer(str(exc))
        return

    if not deleted:
        await message.answer("Роль не найдена.")
        return
    await message.answer("Кастомная роль удалена.")


@router.message(Command("modstat"))
async def modstat_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    target = await _resolve_target_user(message, activity_repo, (command.args or "").strip() or None)
    if target is None:
        target = build_user_snapshot(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            is_bot=bool(message.from_user.is_bot),
        )

    state = await activity_repo.get_moderation_state(chat_id=message.chat.id, user_id=target.telegram_user_id)
    if state is None:
        await message.answer(
            f"<b>{escape(_target_label(target))}</b>: предов 0/3, варнов 0/3, банов 0.",
            parse_mode="HTML",
        )
        return

    lines = [
        f"<b>{escape(_target_label(target))}</b>",
        f"Преды: <b>{state.pending_preds}</b>/3",
        f"Варны: <b>{state.warn_count}</b>/3",
        f"Всего предов: {state.total_preds}",
        f"Всего варнов: {state.total_warns}",
        f"Банов: {state.total_bans}",
        f"Статус: {'<b>ЗАБАНЕН</b>' if state.is_banned else 'не забанен'}",
    ]
    if state.last_reason:
        lines.append(f"Причина: {escape(state.last_reason)}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(F.text.regexp(_REST_GRANT_PATTERN))
async def rest_grant_text_command(message: Message, activity_repo) -> None:
    match = _REST_GRANT_PATTERN.match(message.text or "")
    if match is None:
        return
    await _apply_rest_command(
        message=message,
        activity_repo=activity_repo,
        action="grant",
        duration_days=int(match.group("days")),
        target_token=(match.group("target") or "").strip() or None,
    )


@router.message(F.text.regexp(_REST_REVOKE_PATTERN))
async def rest_revoke_text_command(message: Message, activity_repo) -> None:
    match = _REST_REVOKE_PATTERN.match(message.text or "")
    if match is None:
        return
    await _apply_rest_command(
        message=message,
        activity_repo=activity_repo,
        action="revoke",
        duration_days=None,
        target_token=(match.group("target") or "").strip() or None,
    )


@router.message(F.text.regexp(_PERSONA_GRANT_PATTERN))
async def persona_grant_text_command(message: Message, activity_repo, chat_settings) -> None:
    if not chat_settings.persona_enabled:
        await message.answer("Образы отключены в этом чате.")
        return
    matched, target_token, persona_label, error = _extract_persona_grant_request(message.text or "")
    if not matched:
        return
    if persona_label is None:
        await message.answer(error or "Не удалось определить образ.", parse_mode="HTML")
        return
    await _apply_persona_grant(
        message=message,
        activity_repo=activity_repo,
        persona_label=persona_label,
        target_token=target_token,
    )


@router.message(F.text.regexp(_PERSONA_CLEAR_PATTERN))
async def persona_clear_text_command(message: Message, activity_repo, chat_settings) -> None:
    if not chat_settings.persona_enabled:
        await message.answer("Образы отключены в этом чате.")
        return
    match = _PERSONA_CLEAR_PATTERN.match(message.text or "")
    if match is None:
        return
    await _apply_persona_clear(
        message=message,
        activity_repo=activity_repo,
        target_token=(match.group("target") or "").strip() or None,
    )


@router.message(F.text.regexp(_PERSONA_LIST_PATTERN))
async def persona_list_text_command(message: Message, activity_repo, chat_settings) -> None:
    if not chat_settings.persona_enabled:
        await message.answer("Образы отключены в этом чате.")
        return
    await _send_persona_list(message, activity_repo)


@router.callback_query(F.data.startswith("persona:"))
async def persona_conflict_callback(query: CallbackQuery, activity_repo) -> None:
    if query.data is None or query.from_user is None or query.message is None:
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректная кнопка.", show_alert=True)
        return

    _cleanup_pending_persona_conflicts()
    _, action, request_id = parts
    pending = _PENDING_PERSONA_CONFLICTS.get(request_id)
    if pending is None:
        await query.answer("Запрос уже не активен.", show_alert=True)
        return
    if query.from_user.id != pending.actor_user_id:
        await query.answer("Подтвердить замену может только инициатор.", show_alert=True)
        return
    if query.message.chat.id != pending.chat_id:
        await query.answer("Чат запроса больше не совпадает.", show_alert=True)
        return

    _PENDING_PERSONA_CONFLICTS.pop(request_id, None)
    if action != "confirm":
        await query.message.edit_text("Замена образа отменена.")
        await query.answer("Отменено")
        return

    current_owner = await activity_repo.find_chat_persona_owner(chat_id=pending.chat_id, persona_label=pending.persona_label)
    if current_owner is not None and current_owner.user.telegram_user_id == pending.target_user_id:
        await query.message.edit_text(
            f'Образ <code>[{escape(current_owner.persona_label)}]</code> уже назначен этому пользователю.',
            parse_mode="HTML",
        )
        await query.answer("Без изменений")
        return
    if current_owner is not None and current_owner.user.telegram_user_id != pending.current_owner_user_id:
        await query.message.edit_text("Образ уже занят другим пользователем. Запустите команду заново.")
        await query.answer("Конфликт изменился", show_alert=True)
        return

    chat = build_chat_snapshot(
        chat_id=query.message.chat.id,
        chat_type=query.message.chat.type,
        chat_title=query.message.chat.title,
    )
    target_snapshot = await activity_repo.get_user_snapshot(user_id=pending.target_user_id)
    if target_snapshot is None:
        target_snapshot = build_user_snapshot(
            user_id=pending.target_user_id,
            username=None,
            first_name=None,
            last_name=None,
            is_bot=False,
        )

    previous_label = await activity_repo.get_chat_persona_label(chat_id=pending.chat_id, user_id=pending.target_user_id)
    if current_owner is not None and current_owner.user.telegram_user_id == pending.current_owner_user_id:
        await activity_repo.clear_chat_persona_label(chat_id=pending.chat_id, user_id=current_owner.user.telegram_user_id)

    try:
        stored_label = await activity_repo.set_chat_persona_label(
            chat=chat,
            user=target_snapshot,
            persona_label=pending.persona_label,
            granted_by_user_id=query.from_user.id,
        )
    except (ValueError, IntegrityError) as exc:
        await query.message.edit_text(str(exc))
        await query.answer("Ошибка", show_alert=True)
        return

    await log_chat_action(
        activity_repo,
        chat_id=query.message.chat.id,
        chat_type=query.message.chat.type,
        chat_title=query.message.chat.title,
        action_code="persona_replaced",
        description=f"Образ [{stored_label}] переназначен пользователю {pending.target_user_id}.",
        actor_user_id=query.from_user.id,
        target_user_id=pending.target_user_id,
        meta_json={
            "persona_label": stored_label,
            "previous_owner_user_id": pending.current_owner_user_id,
            "previous_target_label": previous_label,
        },
    )
    await query.message.edit_text(
        (
            f'Образ <code>[{escape(stored_label or pending.persona_label)}]</code> '
            f"теперь закреплён за пользователем <b>{escape(_target_label(target_snapshot))}</b>."
        ),
        parse_mode="HTML",
    )
    await query.answer("Готово")


@router.message(Command(*_SLASH_MODERATION_COMMANDS))
async def moderation_action_command(message: Message, command: CommandObject, activity_repo, bot: Bot) -> None:
    command_name = (command.command or "").lower().strip()
    raw_tail = (command.args or "").strip()
    await _apply_moderation_action(
        message=message,
        activity_repo=activity_repo,
        bot=bot,
        command_name=command_name,
        raw_tail=raw_tail,
        use_reply_target=True,
    )


@router.message(F.text.regexp(_REPLY_MODERATION_PATTERN))
async def moderation_text_command(message: Message, activity_repo, bot: Bot) -> None:
    parsed = _parse_text_action(message.text or "")
    if parsed is None:
        return

    command_name, reason = parsed
    await _apply_moderation_action(
        message=message,
        activity_repo=activity_repo,
        bot=bot,
        command_name=command_name,
        raw_tail=reason,
        use_reply_target=True,
    )


@router.message(F.reply_to_message, F.text.regexp(_REPLY_ROLE_STEP_PATTERN))
async def role_step_reply_text_command(message: Message, activity_repo) -> None:
    action = _parse_role_step_action(message.text or "")
    if action is None:
        return
    await _apply_role_step_action(message=message, activity_repo=activity_repo, action=action)


@router.message(F.text.regexp(_REPLY_ROLE_STEP_PATTERN))
async def role_step_text_without_reply_command(message: Message) -> None:
    action = _parse_role_step_action(message.text or "")
    if action is None:
        return
    if message.reply_to_message is not None:
        return
    await message.answer("Не могу выполнить: используйте «повысить»/«понизить» reply на сообщение пользователя.")
