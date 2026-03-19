from __future__ import annotations

import asyncio
import random
import secrets
import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from io import BytesIO

from aiogram import F, Bot, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    ChatMember,
    ChatMemberUpdated,
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyParameters,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error
from selara.core.chat_settings import ChatSettings
from selara.core.trigger_templates import build_trigger_template_variable_groups, render_template_variables
from selara.domain.entities import ChatSnapshot, ChatTrigger, CustomSocialAction, UserSnapshot
from selara.domain.value_objects import display_name_from_parts
from selara.presentation.audit import log_chat_action
from selara.presentation.auth import has_permission
from selara.presentation.family_tree import build_family_tree_image
from selara.presentation.handlers.settings_common import settings_to_dict

router = Router(name="chat_assistant")

_GROUP_CHAT_TYPES = {"group", "supergroup"}
_TRIGGER_CACHE_TTL = timedelta(seconds=45)
_CUSTOM_ACTION_CACHE_TTL = timedelta(seconds=45)
_CHAT_PERMISSION_FIELDS: tuple[str, ...] = tuple(ChatPermissions.model_fields.keys())
_SEND_PERMISSION_FIELDS: tuple[str, ...] = (
    "can_send_messages",
    "can_send_audios",
    "can_send_documents",
    "can_send_photos",
    "can_send_videos",
    "can_send_video_notes",
    "can_send_voice_notes",
    "can_send_polls",
    "can_send_other_messages",
    "can_add_web_page_previews",
)
_CHAT_PERMISSIONS_BASELINE_ACTION = "chat_permissions_baseline"
_CHAT_MEMBER_JOINED_ACTION = "member_joined"
_CAPTCHA_EMOJIS = ("🍎", "🍋", "🍇", "🍓", "🥝", "🍉", "🍒", "🥥")
_CAPTCHA_TIMEOUT_GRACE = 5
_FAMILY_REQUEST_TTL_HOURS = 24
_WEEKDAY_NAMES_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


@dataclass
class _CachedItems:
    loaded_at: datetime
    items: list


@dataclass
class PendingCaptcha:
    token: str
    chat_id: int
    user_id: int
    user_label: str
    chat_title: str | None
    welcome_text: str
    welcome_button_text: str
    welcome_button_url: str
    kick_on_fail: bool
    challenge_message_id: int
    expires_at: datetime
    task: asyncio.Task[None]


@dataclass(frozen=True)
class PendingFamilyRequest:
    request_id: str
    chat_id: int
    relation_type: str
    actor_user_id: int
    target_user_id: int
    created_at: datetime


@dataclass(frozen=True)
class MatchedChatTrigger:
    trigger: ChatTrigger
    args_text: str


_TRIGGER_CACHE: dict[int, _CachedItems] = {}
_CUSTOM_ACTION_CACHE: dict[int, _CachedItems] = {}
_CAPTCHA_STORE: dict[str, PendingCaptcha] = {}
_CAPTCHA_BY_USER: dict[tuple[int, int], str] = {}
_FAMILY_REQUESTS: dict[str, PendingFamilyRequest] = {}


def invalidate_chat_feature_cache(chat_id: int) -> None:
    _TRIGGER_CACHE.pop(chat_id, None)
    _CUSTOM_ACTION_CACHE.pop(chat_id, None)


def _reply_params(message: Message) -> ReplyParameters:
    return ReplyParameters(message_id=message.message_id)


def _group_only(message: Message) -> bool:
    return message.chat.type in _GROUP_CHAT_TYPES


def _is_chat_member_active(member: ChatMember) -> bool:
    status = getattr(member, "status", None)
    if status in {"member", "administrator", "creator"}:
        return True
    if status == "restricted":
        return bool(getattr(member, "is_member", False))
    return False


def _is_chat_member_admin(member: ChatMember) -> bool:
    return getattr(member, "status", None) in {"administrator", "creator"}


def _format_user_mention(*, user_id: int, label: str) -> str:
    return f'<a href="tg://user?id={user_id}">{escape(label)}</a>'


def _render_template_text(template: str, *, user_mention: str, chat_title: str | None) -> str:
    return render_template_variables(
        template,
        {
            "user": user_mention,
            "chat": escape(chat_title or "этот чат"),
            "chat_title": escape(chat_title or "этот чат"),
        },
    )


def _welcome_markup(*, button_text: str, button_url: str) -> InlineKeyboardMarkup | None:
    text = (button_text or "").strip()
    url = (button_url or "").strip()
    if not text or not url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text[:64], url=url)]])


def _full_member_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_invite_users=True,
    )


def _effective_chat_lock(chat_settings: ChatSettings) -> bool:
    return bool(chat_settings.chat_write_locked or chat_settings.antiraid_enabled)


def _permissions_to_payload(permissions: ChatPermissions | None) -> dict[str, bool]:
    if permissions is None:
        return {}
    dumped = permissions.model_dump(exclude_none=True)
    return {
        field: bool(dumped[field])
        for field in _CHAT_PERMISSION_FIELDS
        if field in dumped and isinstance(dumped[field], bool)
    }


def _permissions_from_payload(payload: dict | None) -> ChatPermissions | None:
    if not isinstance(payload, dict):
        return None
    normalized = {
        field: bool(payload[field])
        for field in _CHAT_PERMISSION_FIELDS
        if field in payload and isinstance(payload[field], bool)
    }
    if not normalized:
        return None
    return ChatPermissions(**normalized)


def _locked_chat_permissions(*, baseline: ChatPermissions | None) -> ChatPermissions:
    payload = _permissions_to_payload(baseline or _full_member_permissions())
    for field in _SEND_PERMISSION_FIELDS:
        payload[field] = False
    return ChatPermissions(**payload)


async def _safe_delete_message(bot: Bot, *, chat_id: int, message_id: int) -> bool:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


async def _safe_restrict(bot: Bot, *, chat_id: int, user_id: int) -> bool:
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(can_send_messages=False),
        )
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


async def _safe_restore_member(bot: Bot, *, chat_id: int, user_id: int) -> bool:
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=_full_member_permissions(),
        )
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


async def _safe_kick(bot: Bot, *, chat_id: int, user_id: int) -> bool:
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


async def _safe_ban(bot: Bot, *, chat_id: int, user_id: int) -> bool:
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


async def _save_chat_permissions_baseline(
    bot: Bot,
    activity_repo,
    *,
    chat_id: int,
    chat_type: str,
    chat_title: str | None,
    actor_user_id: int | None,
) -> ChatPermissions | None:
    try:
        chat = await bot.get_chat(chat_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        return None

    permissions = _permissions_from_payload(_permissions_to_payload(getattr(chat, "permissions", None)))
    await log_chat_action(
        activity_repo,
        chat_id=chat_id,
        chat_type=chat_type,
        chat_title=chat_title,
        action_code=_CHAT_PERMISSIONS_BASELINE_ACTION,
        description="Сохранён baseline default chat permissions перед закрытием чата.",
        actor_user_id=actor_user_id,
        meta_json={
            "permissions": _permissions_to_payload(permissions),
            "use_independent_chat_permissions": getattr(chat, "use_independent_chat_permissions", None),
        },
    )
    return permissions


async def _load_chat_permissions_baseline(activity_repo, *, chat_id: int) -> tuple[ChatPermissions | None, bool | None]:
    entries = await activity_repo.list_audit_logs_by_action(
        chat_id=chat_id,
        action_code=_CHAT_PERMISSIONS_BASELINE_ACTION,
        limit=1,
    )
    if not entries:
        return None, None
    meta = entries[0].meta_json if isinstance(entries[0].meta_json, dict) else {}
    use_independent = meta.get("use_independent_chat_permissions")
    return _permissions_from_payload(meta.get("permissions")), use_independent if isinstance(use_independent, bool) else None


async def _lock_chat(bot: Bot, *, chat_id: int, baseline_permissions: ChatPermissions | None) -> bool:
    try:
        await bot.set_chat_permissions(
            chat_id=chat_id,
            permissions=_locked_chat_permissions(baseline=baseline_permissions),
            use_independent_chat_permissions=True,
        )
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


async def _restore_chat_permissions(bot: Bot, activity_repo, *, chat_id: int) -> bool:
    permissions, use_independent = await _load_chat_permissions_baseline(activity_repo, chat_id=chat_id)
    restored = permissions or _full_member_permissions()
    try:
        await bot.set_chat_permissions(
            chat_id=chat_id,
            permissions=restored,
            use_independent_chat_permissions=True if use_independent is None else use_independent,
        )
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


async def _resolve_display_label(activity_repo, *, chat_id: int, user_id: int, fallback_user: UserSnapshot | None = None) -> str:
    if fallback_user is not None and fallback_user.chat_display_name:
        return fallback_user.chat_display_name
    display_name = await activity_repo.get_chat_display_name(chat_id=chat_id, user_id=user_id)
    if display_name:
        return display_name
    if fallback_user is not None:
        return display_name_from_parts(
            user_id=fallback_user.telegram_user_id,
            username=fallback_user.username,
            first_name=fallback_user.first_name,
            last_name=fallback_user.last_name,
            chat_display_name=None,
        )
    user = await activity_repo.get_user_snapshot(user_id=user_id)
    if user is not None:
        return display_name_from_parts(
            user_id=user.telegram_user_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            chat_display_name=None,
        )
    return f"user:{user_id}"


def _message_text(message: Message | None) -> str:
    if message is None:
        return ""
    return ((message.text or message.caption or "") or "").strip()


def _compact_text(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def _username_text(value: str | None) -> str:
    return escape(f"@{value}") if value else ""


def _empty_person_values() -> dict[str, str]:
    return {
        "mention": "",
        "name": "",
        "first_name": "",
        "last_name": "",
        "username": "",
        "id": "",
    }


def _inject_person_template_values(
    values: dict[str, str],
    *,
    prefix: str,
    aliases: tuple[str, ...],
    person_values: dict[str, str],
) -> None:
    suffix_to_key = {
        "": "mention",
        "_name": "name",
        "_first_name": "first_name",
        "_last_name": "last_name",
        "_username": "username",
        "_id": "id",
    }
    for base in (prefix, *aliases):
        for suffix, key in suffix_to_key.items():
            values[f"{base}{suffix}"] = person_values[key]


async def _build_person_template_values(activity_repo, *, chat_id: int, user: UserSnapshot | None) -> dict[str, str]:
    if user is None:
        return _empty_person_values()
    label = await _resolve_display_label(
        activity_repo,
        chat_id=chat_id,
        user_id=user.telegram_user_id,
        fallback_user=user,
    )
    return {
        "mention": _format_user_mention(user_id=user.telegram_user_id, label=label),
        "name": escape(label),
        "first_name": escape(user.first_name or ""),
        "last_name": escape(user.last_name or ""),
        "username": _username_text(user.username),
        "id": str(user.telegram_user_id),
    }


def _extract_template_args(text: str, *, keyword_norm: str, match_type: str) -> str:
    compact = _compact_text(text)
    if not compact or not keyword_norm:
        return ""
    lowered = compact.lower()
    if match_type == "exact":
        return ""
    if match_type == "starts_with" and lowered.startswith(keyword_norm):
        return compact[len(keyword_norm) :].strip()
    if match_type == "contains":
        index = lowered.find(keyword_norm)
        if index >= 0:
            return compact[index + len(keyword_norm) :].strip()
    return ""


async def _build_template_values(
    message: Message,
    activity_repo,
    *,
    trigger_text: str,
    match_type: str,
    args_text: str,
) -> dict[str, str]:
    actor_snapshot: UserSnapshot | None = None
    if message.from_user is not None:
        actor_snapshot = UserSnapshot(
            telegram_user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            is_bot=bool(message.from_user.is_bot),
            chat_display_name=await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=message.from_user.id),
        )

    reply_snapshot: UserSnapshot | None = None
    if message.reply_to_message is not None and message.reply_to_message.from_user is not None:
        reply_user = message.reply_to_message.from_user
        reply_snapshot = UserSnapshot(
            telegram_user_id=reply_user.id,
            username=reply_user.username,
            first_name=reply_user.first_name,
            last_name=reply_user.last_name,
            is_bot=bool(reply_user.is_bot),
            chat_display_name=await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=reply_user.id),
        )

    values: dict[str, str] = {}
    _inject_person_template_values(
        values,
        prefix="user",
        aliases=("actor", "sender"),
        person_values=await _build_person_template_values(activity_repo, chat_id=message.chat.id, user=actor_snapshot),
    )
    _inject_person_template_values(
        values,
        prefix="reply_user",
        aliases=("target",),
        person_values=await _build_person_template_values(activity_repo, chat_id=message.chat.id, user=reply_snapshot),
    )

    reply_text = escape(_message_text(message.reply_to_message))
    reply_message_id = str(message.reply_to_message.message_id) if message.reply_to_message is not None else ""
    current_text = escape(_message_text(message))
    chat_title = escape(message.chat.title or f"chat:{message.chat.id}")
    now = datetime.now(timezone.utc)

    values.update(
        {
            "reply_text": reply_text,
            "target_text": reply_text,
            "reply_message_id": reply_message_id,
            "target_message_id": reply_message_id,
            "chat": chat_title,
            "chat_title": chat_title,
            "chat_id": str(message.chat.id),
            "text": current_text,
            "message_text": current_text,
            "message_id": str(message.message_id),
            "trigger": escape(trigger_text),
            "keyword": escape(trigger_text),
            "match_type": escape(match_type),
            "args": escape(args_text),
            "date": now.strftime("%d.%m.%Y"),
            "time": now.strftime("%H:%M UTC"),
            "datetime": now.strftime("%d.%m.%Y %H:%M UTC"),
            "weekday": _WEEKDAY_NAMES_RU[now.weekday()],
        }
    )
    return values


def _build_trigger_variables_help_text() -> str:
    lines = [
        "<b>Переменные шаблонов для триггеров и RP</b>",
        "Поддерживаются в смарт-триггерах и кастомных RP-действиях.",
        "Для буквальных фигурных скобок используйте <code>{{</code> и <code>}}</code>.",
    ]
    for group in build_trigger_template_variable_groups():
        lines.append("")
        lines.append(f"<b>{escape(str(group['title']))}</b>")
        for item in group["items"]:
            aliases = ""
            if item["aliases"] != "—":
                aliases = f" (алиасы: <code>{escape(item['aliases'])}</code>)"
            lines.append(f"• <code>{escape(item['token'])}</code>{aliases} — {escape(item['description'])}")
    lines.extend(
        [
            "",
            "<b>Примеры</b>",
            '• <code>/settrigger "кто тут" "Сейчас тут {user}, чат: {chat}, время: {time}"</code>',
            '• <code>/settrigger "привет" "Привет, {user_name}. Ты ответил(а) на: {reply_text}"</code>',
            '• <code>/rpadd "куснуть" "{actor} кусает {target}. Комментарий: {args}"</code>',
        ]
    )
    return "\n".join(lines)


async def _build_target_snapshot(message: Message, activity_repo, *, raw_args: str | None) -> UserSnapshot | None:
    if message.reply_to_message and message.reply_to_message.from_user is not None:
        user = message.reply_to_message.from_user
        return UserSnapshot(
            telegram_user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            is_bot=bool(user.is_bot),
            chat_display_name=await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=user.id),
        )

    token = (raw_args or "").strip().split(maxsplit=1)[0] if raw_args else ""
    if not token:
        return None
    if token.startswith("@"):
        return await activity_repo.find_chat_user_by_username(chat_id=message.chat.id, username=token)
    if token.lstrip("-").isdigit():
        user_id = int(token)
        existing = await activity_repo.get_user_snapshot(user_id=user_id)
        return UserSnapshot(
            telegram_user_id=user_id,
            username=getattr(existing, "username", None),
            first_name=getattr(existing, "first_name", None),
            last_name=getattr(existing, "last_name", None),
            is_bot=bool(getattr(existing, "is_bot", False)),
            chat_display_name=await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=user_id),
        )
    return None


async def _require_manage_settings(message: Message, activity_repo) -> bool:
    if not _group_only(message):
        await message.answer("Команда доступна только в группе.")
        return False
    if message.from_user is None:
        return False
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
        permission="manage_settings",
        bootstrap_if_missing_owner=False,
    )
    if not allowed:
        await message.answer("Недостаточно прав для управления этой функцией.")
        return False
    return True


async def _require_moderate_users(message: Message, activity_repo) -> bool:
    if not _group_only(message):
        await message.answer("Команда доступна только в группе.")
        return False
    if message.from_user is None:
        return False
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
        permission="moderate_users",
        bootstrap_if_missing_owner=False,
    )
    if not allowed:
        await message.answer("Недостаточно прав для модерации этого чата.")
        return False
    return True


def _chat_snapshot_from_message(message: Message) -> ChatSnapshot:
    return ChatSnapshot(
        telegram_chat_id=message.chat.id,
        chat_type=message.chat.type,
        title=message.chat.title,
    )


def _user_snapshot_from_message(message: Message) -> UserSnapshot | None:
    if message.from_user is None:
        return None
    return UserSnapshot(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
    )


async def _ban_recent_joiners(
    bot: Bot,
    activity_repo,
    *,
    chat_id: int,
    chat_type: str,
    chat_title: str | None,
    window_minutes: int,
) -> int:
    joined_entries = await activity_repo.list_audit_logs_by_action(
        chat_id=chat_id,
        action_code=_CHAT_MEMBER_JOINED_ACTION,
        since=datetime.now(timezone.utc) - timedelta(minutes=window_minutes),
        limit=500,
    )
    banned_count = 0
    processed_user_ids: set[int] = set()
    for entry in sorted(joined_entries, key=lambda item: (item.created_at, item.id)):
        user_id = entry.target_user_id
        if user_id is None or user_id in processed_user_ids:
            continue
        processed_user_ids.add(user_id)
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            continue
        member_user = getattr(member, "user", None)
        if member_user is None or getattr(member_user, "is_bot", False):
            continue
        if _is_chat_member_admin(member) or getattr(member, "status", None) in {"left", "kicked"}:
            continue
        banned = await _safe_ban(bot, chat_id=chat_id, user_id=user_id)
        if not banned:
            continue
        banned_count += 1
        await activity_repo.set_chat_member_active(
            chat=ChatSnapshot(
                telegram_chat_id=chat_id,
                chat_type=chat_type,
                title=chat_title,
            ),
            user=UserSnapshot(
                telegram_user_id=member_user.id,
                username=getattr(member_user, "username", None),
                first_name=getattr(member_user, "first_name", None),
                last_name=getattr(member_user, "last_name", None),
                is_bot=bool(getattr(member_user, "is_bot", False)),
            ),
            is_active=False,
            event_at=datetime.now(timezone.utc),
        )
        await log_chat_action(
            activity_repo,
            chat_id=chat_id,
            chat_type=chat_type,
            chat_title=chat_title,
            action_code="antiraid_retro_ban",
            description=f"Пользователь {user_id} забанен retro-sweep антирейда.",
            target_user_id=user_id,
        )
    return banned_count


async def manage_chat_gate_command(
    message: Message,
    *,
    activity_repo,
    bot: Bot,
    chat_settings: ChatSettings,
    command_key: str,
    raw_args: str | None = None,
) -> None:
    if not await _require_moderate_users(message, activity_repo):
        return
    actor = _user_snapshot_from_message(message)
    if actor is None:
        return

    current_values = settings_to_dict(chat_settings)
    updated_values = dict(current_values)
    raw_tail = (raw_args or "").strip()
    antiraid_window = chat_settings.antiraid_recent_window_minutes

    if command_key == "antiraid_on":
        if raw_tail:
            antiraid_window = int(raw_tail)
        updated_values["antiraid_enabled"] = True
        updated_values["antiraid_recent_window_minutes"] = antiraid_window
    elif command_key == "antiraid_off":
        updated_values["antiraid_enabled"] = False
    elif command_key == "chat_lock":
        updated_values["chat_write_locked"] = True
    elif command_key == "chat_unlock":
        updated_values["chat_write_locked"] = False
    else:
        return

    current_effective_lock = _effective_chat_lock(chat_settings)
    next_effective_lock = bool(updated_values["chat_write_locked"] or updated_values["antiraid_enabled"])

    baseline_permissions: ChatPermissions | None = None
    if not current_effective_lock and next_effective_lock:
        baseline_permissions = await _save_chat_permissions_baseline(
            bot,
            activity_repo,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            chat_title=message.chat.title,
            actor_user_id=actor.telegram_user_id,
        )
        if not await _lock_chat(bot, chat_id=message.chat.id, baseline_permissions=baseline_permissions):
            await message.answer("Не удалось закрыть чат. Проверьте права бота на ограничение участников.")
            return
    elif current_effective_lock and not next_effective_lock:
        if not await _restore_chat_permissions(bot, activity_repo, chat_id=message.chat.id):
            await message.answer("Не удалось открыть чат. Проверьте права бота на управление разрешениями.")
            return

    updated = await activity_repo.upsert_chat_settings(chat=_chat_snapshot_from_message(message), values=updated_values)

    if command_key == "antiraid_on":
        banned_count = await _ban_recent_joiners(
            bot,
            activity_repo,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            chat_title=message.chat.title,
            window_minutes=updated.antiraid_recent_window_minutes,
        )
        await log_chat_action(
            activity_repo,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            chat_title=message.chat.title,
            action_code="antiraid_enabled",
            description=(
                f"Антирейд включён. Окно retro-ban: {updated.antiraid_recent_window_minutes} мин. "
                f"Забанено: {banned_count}."
            ),
            actor_user_id=actor.telegram_user_id,
            meta_json={
                "window_minutes": updated.antiraid_recent_window_minutes,
                "retro_ban_count": banned_count,
                "chat_write_locked": updated.chat_write_locked,
            },
        )
        if updated.chat_write_locked:
            await message.answer(
                f"Антирейд включён на {updated.antiraid_recent_window_minutes} мин. Чат уже был закрыт вручную. "
                f"Retro-ban: {banned_count}."
            )
        else:
            await message.answer(
                f"Антирейд включён на {updated.antiraid_recent_window_minutes} мин. Чат закрыт. Retro-ban: {banned_count}."
            )
        return

    if command_key == "antiraid_off":
        await log_chat_action(
            activity_repo,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            chat_title=message.chat.title,
            action_code="antiraid_disabled",
            description="Антирейд выключен.",
            actor_user_id=actor.telegram_user_id,
            meta_json={"chat_write_locked": updated.chat_write_locked},
        )
        if updated.chat_write_locked:
            await message.answer("Антирейд выключен. Чат остаётся закрытым из-за ручного lock-режима.")
        else:
            await message.answer("Антирейд выключен. Чат снова открыт.")
        return

    if command_key == "chat_lock":
        await log_chat_action(
            activity_repo,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            chat_title=message.chat.title,
            action_code="chat_locked",
            description="Чат закрыт для записи участников.",
            actor_user_id=actor.telegram_user_id,
            meta_json={"antiraid_enabled": updated.antiraid_enabled},
        )
        if updated.antiraid_enabled:
            await message.answer("Ручной lock включён. Антирейд уже держит чат закрытым.")
        else:
            await message.answer("Чат закрыт. Писать могут только админы.")
        return

    await log_chat_action(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        action_code="chat_unlocked",
        description="Ручной lock чата снят.",
        actor_user_id=actor.telegram_user_id,
        meta_json={"antiraid_enabled": updated.antiraid_enabled},
    )
    if updated.antiraid_enabled:
        await message.answer("Ручной lock снят, но чат остаётся закрытым, пока активен антирейд.")
    else:
        await message.answer("Чат открыт.")


async def _cached_triggers(activity_repo, *, chat_id: int) -> list[ChatTrigger]:
    now = datetime.now(timezone.utc)
    cached = _TRIGGER_CACHE.get(chat_id)
    if cached is not None and now - cached.loaded_at <= _TRIGGER_CACHE_TTL:
        return list(cached.items)
    items = await activity_repo.list_chat_triggers(chat_id=chat_id)
    _TRIGGER_CACHE[chat_id] = _CachedItems(loaded_at=now, items=list(items))
    return list(items)


async def _cached_custom_actions(activity_repo, *, chat_id: int) -> list[CustomSocialAction]:
    now = datetime.now(timezone.utc)
    cached = _CUSTOM_ACTION_CACHE.get(chat_id)
    if cached is not None and now - cached.loaded_at <= _CUSTOM_ACTION_CACHE_TTL:
        return list(cached.items)
    items = await activity_repo.list_custom_social_actions(chat_id=chat_id)
    _CUSTOM_ACTION_CACHE[chat_id] = _CachedItems(loaded_at=now, items=list(items))
    return list(items)


def _match_trigger_text(text: str, triggers: list[ChatTrigger]) -> MatchedChatTrigger | None:
    normalized = " ".join((text or "").strip().lower().split())
    if not normalized:
        return None

    matched: list[ChatTrigger] = []
    for trigger in triggers:
        keyword = trigger.keyword_norm
        if trigger.match_type == "exact" and normalized == keyword:
            matched.append(trigger)
        elif trigger.match_type == "starts_with" and normalized.startswith(keyword):
            matched.append(trigger)
        elif trigger.match_type == "contains" and keyword in normalized:
            matched.append(trigger)
    if not matched:
        return None
    winner = sorted(matched, key=lambda item: (len(item.keyword_norm), item.id), reverse=True)[0]
    return MatchedChatTrigger(
        trigger=winner,
        args_text=_extract_template_args(text, keyword_norm=winner.keyword_norm, match_type=winner.match_type),
    )


async def match_chat_trigger(activity_repo, *, chat_id: int, text: str) -> MatchedChatTrigger | None:
    return _match_trigger_text(text, await _cached_triggers(activity_repo, chat_id=chat_id))


async def send_chat_trigger(message: Message, activity_repo, matched_trigger: MatchedChatTrigger) -> None:
    trigger = matched_trigger.trigger
    bot = message.bot
    kwargs = {
        "chat_id": message.chat.id,
        "reply_parameters": _reply_params(message),
    }
    text = render_template_variables(
        trigger.response_text,
        await _build_template_values(
            message,
            activity_repo,
            trigger_text=trigger.keyword,
            match_type=trigger.match_type,
            args_text=matched_trigger.args_text,
        ),
    )

    if trigger.media_file_id and trigger.media_type == "sticker":
        await bot.send_sticker(sticker=trigger.media_file_id, **kwargs)
        if text:
            await bot.send_message(text=text, parse_mode="HTML", **kwargs)
        return

    if trigger.media_file_id and trigger.media_type == "photo":
        await bot.send_photo(photo=trigger.media_file_id, caption=text, parse_mode="HTML" if text else None, **kwargs)
        return

    if trigger.media_file_id and trigger.media_type == "animation":
        await bot.send_animation(animation=trigger.media_file_id, caption=text, parse_mode="HTML" if text else None, **kwargs)
        return

    if trigger.media_file_id and trigger.media_type == "document":
        await bot.send_document(document=trigger.media_file_id, caption=text, parse_mode="HTML" if text else None, **kwargs)
        return

    if trigger.media_file_id and trigger.media_type == "video":
        await bot.send_video(video=trigger.media_file_id, caption=text, parse_mode="HTML" if text else None, **kwargs)
        return

    if text:
        await bot.send_message(text=text, parse_mode="HTML", **kwargs)


async def match_custom_social_action(activity_repo, *, chat_id: int, text: str) -> CustomSocialAction | None:
    normalized = " ".join((text or "").strip().lower().split())
    if not normalized or normalized.startswith("/"):
        return None
    actions = await _cached_custom_actions(activity_repo, chat_id=chat_id)
    for action in sorted(actions, key=lambda item: (len(item.trigger_text_norm), item.id), reverse=True):
        if normalized == action.trigger_text_norm or normalized.startswith(f"{action.trigger_text_norm} "):
            return action
    return None


async def send_custom_social_action(message: Message, activity_repo, action: CustomSocialAction) -> None:
    if message.from_user is None:
        return
    if message.reply_to_message is None or message.reply_to_message.from_user is None:
        await message.answer(
            (
                f'Сделайте reply на сообщение участника и напишите <code>{escape(action.trigger_text)}</code>.\n'
                "Список переменных для шаблонов: <code>/triggervars</code>."
            ),
            parse_mode="HTML",
        )
        return

    rendered = render_template_variables(
        action.response_template,
        await _build_template_values(
            message,
            activity_repo,
            trigger_text=action.trigger_text,
            match_type="",
            args_text=_extract_template_args(
                _message_text(message),
                keyword_norm=action.trigger_text_norm,
                match_type="starts_with",
            ),
        ),
    )
    await message.answer(rendered, parse_mode="HTML", disable_web_page_preview=True)


async def _send_welcome(
    bot: Bot,
    *,
    chat_id: int,
    user_id: int,
    user_label: str,
    chat_title: str | None,
    welcome_text: str,
    button_text: str,
    button_url: str,
) -> None:
    user_mention = _format_user_mention(user_id=user_id, label=user_label)
    await bot.send_message(
        chat_id=chat_id,
        text=_render_template_text(welcome_text, user_mention=user_mention, chat_title=chat_title),
        parse_mode="HTML",
        reply_markup=_welcome_markup(button_text=button_text, button_url=button_url),
    )


def _build_captcha_keyboard(*, token: str, user_id: int, chat_id: int, correct_emoji: str) -> InlineKeyboardMarkup:
    emojis = random.sample(_CAPTCHA_EMOJIS, k=min(5, len(_CAPTCHA_EMOJIS)))
    if correct_emoji not in emojis:
        emojis[0] = correct_emoji
    random.shuffle(emojis)
    builder = InlineKeyboardBuilder()
    for emoji in emojis:
        builder.button(text=emoji, callback_data=f"cap:{token}:{chat_id}:{user_id}:{emoji}")
    builder.adjust(3, 2)
    return builder.as_markup()


async def _captcha_timeout(bot: Bot, activity_repo, *, token: str) -> None:
    pending = _CAPTCHA_STORE.get(token)
    if pending is None:
        return
    sleep_seconds = max(1, int((pending.expires_at - datetime.now(timezone.utc)).total_seconds()) + _CAPTCHA_TIMEOUT_GRACE)
    await asyncio.sleep(sleep_seconds)
    current = _CAPTCHA_STORE.get(token)
    if current is None:
        return
    _CAPTCHA_STORE.pop(token, None)
    _CAPTCHA_BY_USER.pop((current.chat_id, current.user_id), None)
    await _safe_delete_message(bot, chat_id=current.chat_id, message_id=current.challenge_message_id)
    if current.kick_on_fail:
        await _safe_kick(bot, chat_id=current.chat_id, user_id=current.user_id)
        await log_chat_action(
            activity_repo,
            chat_id=current.chat_id,
            chat_type="group",
            chat_title=current.chat_title,
            action_code="captcha_timeout_kick",
            description=f"Пользователь {current.user_id} исключён за таймаут капчи.",
            target_user_id=current.user_id,
        )


async def _upsert_trigger_from_text_command(message: Message, activity_repo, *, raw_args: str) -> None:
    if not await _require_manage_settings(message, activity_repo):
        return
    try:
        tokens = shlex.split(raw_args)
    except ValueError:
        await message.answer('Формат: научить "ключ" "ответ" [exact|contains|starts_with]\nПеременные: /triggervars')
        return
    if len(tokens) < 2:
        await message.answer('Формат: научить "ключ" "ответ" [exact|contains|starts_with]\nПеременные: /triggervars')
        return
    keyword = tokens[0]
    response_text = tokens[1]
    match_type = tokens[2].lower() if len(tokens) >= 3 else "contains"
    try:
        trigger = await activity_repo.upsert_chat_trigger(
            chat=ChatSnapshot(message.chat.id, message.chat.type, message.chat.title),
            trigger_id=None,
            keyword=keyword,
            match_type=match_type,
            response_text=response_text,
            media_file_id=None,
            media_type=None,
            actor_user_id=message.from_user.id if message.from_user is not None else None,
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return
    invalidate_chat_feature_cache(message.chat.id)
    await message.answer(
        f'Триггер сохранён: <code>{escape(trigger.keyword)}</code> → <code>{escape(trigger.match_type)}</code>',
        parse_mode="HTML",
    )


@router.message(Command("settrigger"))
async def settrigger_command(message: Message, command: CommandObject, activity_repo) -> None:
    raw_args = (command.args or "").strip()
    await _upsert_trigger_from_text_command(message, activity_repo, raw_args=raw_args)


@router.message(Command("triggervars"))
async def triggervars_command(message: Message, activity_repo) -> None:
    if _group_only(message) and not await _require_manage_settings(message, activity_repo):
        return
    await message.answer(_build_trigger_variables_help_text(), parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("triggers"))
async def triggers_command(message: Message, activity_repo) -> None:
    if not await _require_manage_settings(message, activity_repo):
        return
    triggers = await activity_repo.list_chat_triggers(chat_id=message.chat.id)
    if not triggers:
        await message.answer("Смарт-триггеры в чате пока не заданы.")
        return
    lines = ["<b>Смарт-триггеры чата</b>", "Шаблоны поддерживают переменные: <code>/triggervars</code>"]
    for trigger in triggers[:50]:
        body = trigger.response_text or trigger.media_type or "ответ"
        lines.append(
            f'• <code>{trigger.id}</code> • <code>{escape(trigger.keyword)}</code> • '
            f'<code>{escape(trigger.match_type)}</code> • {escape(body[:60])}'
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("deltrigger"))
async def deltrigger_command(message: Message, command: CommandObject, activity_repo) -> None:
    if not await _require_manage_settings(message, activity_repo):
        return
    raw_value = (command.args or "").strip()
    if not raw_value.isdigit():
        await message.answer("Формат: /deltrigger <id>")
        return
    removed = await activity_repo.remove_chat_trigger(chat_id=message.chat.id, trigger_id=int(raw_value))
    if not removed:
        await message.answer("Триггер не найден.")
        return
    invalidate_chat_feature_cache(message.chat.id)
    await message.answer("Триггер удалён.")


@router.message(Command("rpadd"))
async def rpadd_command(message: Message, command: CommandObject, activity_repo) -> None:
    if not await _require_manage_settings(message, activity_repo):
        return
    try:
        tokens = shlex.split((command.args or "").strip())
    except ValueError:
        await message.answer('Формат: /rpadd "триггер" "шаблон"\nПеременные: /triggervars')
        return
    if len(tokens) != 2:
        await message.answer('Формат: /rpadd "триггер" "шаблон"\nПеременные: /triggervars')
        return
    try:
        action = await activity_repo.upsert_custom_social_action(
            chat=ChatSnapshot(message.chat.id, message.chat.type, message.chat.title),
            trigger_text=tokens[0],
            response_template=tokens[1],
            actor_user_id=message.from_user.id if message.from_user is not None else None,
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return
    invalidate_chat_feature_cache(message.chat.id)
    await message.answer(
        f'Кастомное действие сохранено: <code>{escape(action.trigger_text)}</code>',
        parse_mode="HTML",
    )


@router.message(Command("rps"))
async def rps_command(message: Message, activity_repo) -> None:
    if not await _require_manage_settings(message, activity_repo):
        return
    actions = await activity_repo.list_custom_social_actions(chat_id=message.chat.id)
    if not actions:
        await message.answer("Кастомные RP-действия не настроены.")
        return
    lines = ["<b>Кастомные RP-действия</b>"]
    for action in actions[:50]:
        lines.append(f'• <code>{escape(action.trigger_text)}</code> → {escape(action.response_template[:70])}')
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("rpdel"))
async def rpdel_command(message: Message, command: CommandObject, activity_repo) -> None:
    if not await _require_manage_settings(message, activity_repo):
        return
    raw_value = (command.args or "").strip()
    if not raw_value:
        await message.answer('Формат: /rpdel "триггер"')
        return
    try:
        tokens = shlex.split(raw_value)
    except ValueError:
        await message.answer('Формат: /rpdel "триггер"')
        return
    if len(tokens) != 1:
        await message.answer('Формат: /rpdel "триггер"')
        return
    removed = await activity_repo.remove_custom_social_action(chat_id=message.chat.id, trigger_text_norm=tokens[0])
    if not removed:
        await message.answer("Такое действие не найдено.")
        return
    invalidate_chat_feature_cache(message.chat.id)
    await message.answer("Кастомное действие удалено.")


@router.message(Command("title"))
async def title_command(message: Message, command: CommandObject, activity_repo, economy_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return
    if not _group_only(message):
        await message.answer("Команда доступна только в группе.")
        return
    if not chat_settings.titles_enabled:
        await message.answer("Титулы отключены в этом чате.")
        return
    if not chat_settings.economy_enabled:
        await message.answer("Экономика отключена в этом чате, титулы временно недоступны.")
        return

    raw_args = (command.args or "").strip()
    current = await activity_repo.get_chat_title_prefix(chat_id=message.chat.id, user_id=message.from_user.id)
    if not raw_args:
        current_text = f"[{current}]" if current else "не установлен"
        await message.answer(
            f"Текущий титул: <code>{escape(current_text)}</code>\nЦена первой установки: <code>{chat_settings.title_price}</code> монет.\n"
            "Команды: <code>/title buy Лорд</code>, <code>/title set Лорд</code>, <code>/title clear</code>",
            parse_mode="HTML",
        )
        return

    action, _, tail = raw_args.partition(" ")
    action = action.lower()
    title_value = " ".join(tail.split()).strip().strip("[]")
    if action in {"clear", "reset", "off"}:
        await activity_repo.set_chat_title_prefix(
            chat=ChatSnapshot(message.chat.id, message.chat.type, message.chat.title),
            user=UserSnapshot(
                telegram_user_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name,
                is_bot=bool(message.from_user.is_bot),
            ),
            title_prefix=None,
        )
        await log_chat_action(
            activity_repo,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            chat_title=message.chat.title,
            action_code="title_clear",
            description=f"Пользователь {message.from_user.id} снял свой титул.",
            actor_user_id=message.from_user.id,
        )
        await message.answer("Титул снят.")
        return

    if action not in {"buy", "set"} or not title_value:
        await message.answer("Формат: /title buy <текст>, /title set <текст> или /title clear")
        return
    if len(title_value) > 48:
        await message.answer("Титул слишком длинный. Оставьте до 48 символов.")
        return

    should_charge = current is None
    scope, error = await resolve_scope_or_error(
        economy_repo,
        economy_mode=chat_settings.economy_mode,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
    )
    if scope is None:
        await message.answer(error or "Не удалось определить режим экономики.")
        return
    account, _ = await get_account_or_error(economy_repo, scope=scope, user_id=message.from_user.id)
    if should_charge and account.balance < chat_settings.title_price:
        await message.answer(f"Недостаточно монет. Нужно <code>{chat_settings.title_price}</code>.", parse_mode="HTML")
        return
    if should_charge:
        await economy_repo.add_balance(account_id=account.id, delta=-chat_settings.title_price)
        await economy_repo.add_ledger(
            account_id=account.id,
            direction="out",
            amount=chat_settings.title_price,
            reason="title_purchase",
            meta_json=f'{{"chat_id": {message.chat.id}}}',
        )
    await activity_repo.set_chat_title_prefix(
        chat=ChatSnapshot(message.chat.id, message.chat.type, message.chat.title),
        user=UserSnapshot(
            telegram_user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            is_bot=bool(message.from_user.is_bot),
        ),
        title_prefix=title_value,
    )
    await log_chat_action(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        action_code="title_set",
        description=f"Пользователь {message.from_user.id} установил титул [{title_value}].",
        actor_user_id=message.from_user.id,
        meta_json={"charged": should_charge, "title": title_value},
    )
    await message.answer(f"Титул обновлён: <code>[{escape(title_value)}]</code>", parse_mode="HTML")


async def _send_family_request(
    message: Message,
    *,
    activity_repo,
    relation_type: str,
    raw_args: str | None,
) -> None:
    if message.from_user is None:
        return
    if not _group_only(message):
        await message.answer("Команда доступна только в группе.")
        return

    target = await _build_target_snapshot(message, activity_repo, raw_args=raw_args)
    if target is None:
        example = "/adopt @username" if relation_type == "parent" else "/pet @username"
        await message.answer(f"Формат: reply или <code>{example}</code>.", parse_mode="HTML")
        return
    if target.telegram_user_id == message.from_user.id:
        await message.answer("Нельзя отправить запрос самому себе.")
        return

    request_id = secrets.token_hex(8)
    _FAMILY_REQUESTS[request_id] = PendingFamilyRequest(
        request_id=request_id,
        chat_id=message.chat.id,
        relation_type=relation_type,
        actor_user_id=message.from_user.id,
        target_user_id=target.telegram_user_id,
        created_at=datetime.now(timezone.utc),
    )

    actor_label = await _resolve_display_label(activity_repo, chat_id=message.chat.id, user_id=message.from_user.id)
    target_label = await _resolve_display_label(activity_repo, chat_id=message.chat.id, user_id=target.telegram_user_id, fallback_user=target)
    if relation_type == "parent":
        headline = f"{_format_user_mention(user_id=message.from_user.id, label=actor_label)} хочет усыновить {_format_user_mention(user_id=target.telegram_user_id, label=target_label)}."
    else:
        headline = f"{_format_user_mention(user_id=message.from_user.id, label=actor_label)} хочет стать питомцем для {_format_user_mention(user_id=target.telegram_user_id, label=target_label)}."
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Согласен", callback_data=f"famreq:accept:{request_id}"),
                InlineKeyboardButton(text="❌ Нет", callback_data=f"famreq:reject:{request_id}"),
            ]
        ]
    )
    await message.answer(headline, parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("adopt"))
async def adopt_command(message: Message, command: CommandObject, activity_repo, chat_settings: ChatSettings) -> None:
    if not chat_settings.family_tree_enabled:
        await message.answer("Семейные команды отключены в этом чате.")
        return
    await _send_family_request(message, activity_repo=activity_repo, relation_type="parent", raw_args=command.args)


@router.message(Command("pet"))
async def pet_command(message: Message, command: CommandObject, activity_repo, chat_settings: ChatSettings) -> None:
    if not chat_settings.family_tree_enabled:
        await message.answer("Семейные команды отключены в этом чате.")
        return
    await _send_family_request(message, activity_repo=activity_repo, relation_type="pet", raw_args=command.args)


async def _build_family_section_labels(activity_repo, *, chat_id: int, user_ids: list[int]) -> list[str]:
    labels: list[str] = []
    for user_id in user_ids:
        labels.append(await _resolve_display_label(activity_repo, chat_id=chat_id, user_id=user_id))
    return labels


@router.message(Command("family"))
async def family_command(message: Message, command: CommandObject, activity_repo, chat_settings: ChatSettings) -> None:
    if not chat_settings.family_tree_enabled:
        await message.answer("Семейные команды отключены в этом чате.")
        return
    if message.from_user is None:
        return
    if not _group_only(message):
        await message.answer("Команда доступна только в группе.")
        return

    target = await _build_target_snapshot(message, activity_repo, raw_args=command.args)
    subject = target or UserSnapshot(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        chat_display_name=await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=message.from_user.id),
    )

    bundle = await activity_repo.list_family_bundle(chat_id=message.chat.id, user_id=subject.telegram_user_id)
    if not any(
        [
            bundle.parents,
            bundle.step_parents,
            bundle.children,
            bundle.pets,
            bundle.spouse_user_id,
            bundle.grandparents,
            bundle.siblings,
        ]
    ):
        await message.answer("Для этого пользователя семейные связи пока не найдены.")
        return

    subject_label = await _resolve_display_label(
        activity_repo,
        chat_id=message.chat.id,
        user_id=subject.telegram_user_id,
        fallback_user=subject,
    )
    spouse_label = (
        None
        if bundle.spouse_user_id is None
        else await _resolve_display_label(activity_repo, chat_id=message.chat.id, user_id=bundle.spouse_user_id)
    )
    image_bytes = build_family_tree_image(
        subject_label=subject_label,
        grandparents=await _build_family_section_labels(activity_repo, chat_id=message.chat.id, user_ids=list(bundle.grandparents)),
        parents=await _build_family_section_labels(activity_repo, chat_id=message.chat.id, user_ids=list(bundle.parents)),
        step_parents=await _build_family_section_labels(activity_repo, chat_id=message.chat.id, user_ids=list(bundle.step_parents)),
        spouse=spouse_label,
        siblings=await _build_family_section_labels(activity_repo, chat_id=message.chat.id, user_ids=list(bundle.siblings)),
        children=await _build_family_section_labels(activity_repo, chat_id=message.chat.id, user_ids=list(bundle.children)),
        pets=await _build_family_section_labels(activity_repo, chat_id=message.chat.id, user_ids=list(bundle.pets)),
    )
    await message.answer_photo(BufferedInputFile(image_bytes, filename="family_tree.png"))


@router.message(F.new_chat_members)
async def new_chat_members_handler(
    message: Message,
    bot: Bot,
    activity_repo,
    achievement_orchestrator,
    chat_settings: ChatSettings,
) -> None:
    if message.chat.type not in _GROUP_CHAT_TYPES:
        return
    new_members = [member for member in message.new_chat_members if not member.is_bot]
    if not new_members:
        return

    if chat_settings.welcome_cleanup_service_messages:
        deleted = await _safe_delete_message(bot, chat_id=message.chat.id, message_id=message.message_id)
        if deleted:
            await log_chat_action(
                activity_repo,
                chat_id=message.chat.id,
                chat_type=message.chat.type,
                chat_title=message.chat.title,
                action_code="service_join_deleted",
                description="Удалено сервисное сообщение о входе участника.",
            )

    for member in new_members:
        member_snapshot = UserSnapshot(
            telegram_user_id=member.id,
            username=member.username,
            first_name=member.first_name,
            last_name=member.last_name,
            is_bot=bool(member.is_bot),
        )
        await activity_repo.set_chat_member_active(
            chat=ChatSnapshot(
                telegram_chat_id=message.chat.id,
                chat_type=message.chat.type,
                title=message.chat.title,
            ),
            user=member_snapshot,
            is_active=True,
            event_at=message.date,
        )
        await log_chat_action(
            activity_repo,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            chat_title=message.chat.title,
            action_code=_CHAT_MEMBER_JOINED_ACTION,
            description=f"Пользователь {member.id} вошёл в чат.",
            target_user_id=member.id,
            created_at=message.date,
        )
        if achievement_orchestrator is not None:
            await achievement_orchestrator.process_membership(
                chat_id=message.chat.id,
                user_id=member.id,
                is_active=True,
                event_at=message.date,
            )
        label = await _resolve_display_label(
            activity_repo,
            chat_id=message.chat.id,
            user_id=member.id,
            fallback_user=member_snapshot,
        )
        if chat_settings.antiraid_enabled:
            try:
                current_member = await bot.get_chat_member(chat_id=message.chat.id, user_id=member.id)
            except (TelegramBadRequest, TelegramForbiddenError):
                current_member = None
            if current_member is None or not _is_chat_member_admin(current_member):
                banned = await _safe_ban(bot, chat_id=message.chat.id, user_id=member.id)
                if banned:
                    await activity_repo.set_chat_member_active(
                        chat=ChatSnapshot(
                            telegram_chat_id=message.chat.id,
                            chat_type=message.chat.type,
                            title=message.chat.title,
                        ),
                        user=member_snapshot,
                        is_active=False,
                        event_at=message.date,
                    )
                await log_chat_action(
                    activity_repo,
                    chat_id=message.chat.id,
                    chat_type=message.chat.type,
                    chat_title=message.chat.title,
                    action_code="antiraid_join_ban" if banned else "antiraid_join_ban_failed",
                    description=(
                        f"Пользователь {member.id} "
                        f"{'забанен' if banned else 'не был забанен'} при активном антирейде."
                    ),
                    target_user_id=member.id,
                )
            continue
        if chat_settings.entry_captcha_enabled:
            token = secrets.token_hex(8)
            correct = "🍎"
            await _safe_restrict(bot, chat_id=message.chat.id, user_id=member.id)
            challenge = await bot.send_message(
                chat_id=message.chat.id,
                text=(
                    f"{_format_user_mention(user_id=member.id, label=label)}, выбери <b>{correct}</b> на клавиатуре ниже, "
                    "чтобы получить доступ к чату."
                ),
                parse_mode="HTML",
                reply_markup=_build_captcha_keyboard(
                    token=token,
                    user_id=member.id,
                    chat_id=message.chat.id,
                    correct_emoji=correct,
                ),
            )
            timeout_task = asyncio.create_task(_captcha_timeout(bot, activity_repo, token=token))
            _CAPTCHA_STORE[token] = PendingCaptcha(
                token=token,
                chat_id=message.chat.id,
                user_id=member.id,
                user_label=label,
                chat_title=message.chat.title,
                welcome_text=chat_settings.welcome_text,
                welcome_button_text=chat_settings.welcome_button_text,
                welcome_button_url=chat_settings.welcome_button_url,
                kick_on_fail=chat_settings.entry_captcha_kick_on_fail,
                challenge_message_id=challenge.message_id,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=chat_settings.entry_captcha_timeout_seconds),
                task=timeout_task,
            )
            _CAPTCHA_BY_USER[(message.chat.id, member.id)] = token
            await log_chat_action(
                activity_repo,
                chat_id=message.chat.id,
                chat_type=message.chat.type,
                chat_title=message.chat.title,
                action_code="captcha_started",
                description=f"Запущена капча для пользователя {member.id}.",
                target_user_id=member.id,
            )
            continue

        if chat_settings.welcome_enabled:
            await _send_welcome(
                bot,
                chat_id=message.chat.id,
                user_id=member.id,
                user_label=label,
                chat_title=message.chat.title,
                welcome_text=chat_settings.welcome_text,
                button_text=chat_settings.welcome_button_text,
                button_url=chat_settings.welcome_button_url,
            )


@router.message(F.left_chat_member)
async def left_chat_member_handler(
    message: Message,
    bot: Bot,
    activity_repo,
    achievement_orchestrator,
    chat_settings: ChatSettings,
) -> None:
    if message.chat.type not in _GROUP_CHAT_TYPES or message.left_chat_member is None:
        return

    if chat_settings.welcome_cleanup_service_messages:
        deleted = await _safe_delete_message(bot, chat_id=message.chat.id, message_id=message.message_id)
        if deleted:
            await log_chat_action(
                activity_repo,
                chat_id=message.chat.id,
                chat_type=message.chat.type,
                chat_title=message.chat.title,
                action_code="service_leave_deleted",
                description="Удалено сервисное сообщение о выходе участника.",
            )

    left = message.left_chat_member
    if not left.is_bot:
        await activity_repo.set_chat_member_active(
            chat=ChatSnapshot(
                telegram_chat_id=message.chat.id,
                chat_type=message.chat.type,
                title=message.chat.title,
            ),
            user=UserSnapshot(
                telegram_user_id=left.id,
                username=left.username,
                first_name=left.first_name,
                last_name=left.last_name,
                is_bot=bool(left.is_bot),
            ),
            is_active=False,
            event_at=message.date,
        )
        if achievement_orchestrator is not None:
            await achievement_orchestrator.process_membership(
                chat_id=message.chat.id,
                user_id=left.id,
                is_active=False,
                event_at=message.date,
            )

    if not chat_settings.goodbye_enabled:
        return

    label = await _resolve_display_label(
        activity_repo,
        chat_id=message.chat.id,
        user_id=left.id,
        fallback_user=UserSnapshot(
            telegram_user_id=left.id,
            username=left.username,
            first_name=left.first_name,
            last_name=left.last_name,
            is_bot=bool(left.is_bot),
        ),
    )
    await bot.send_message(
        chat_id=message.chat.id,
        text=_render_template_text(
            chat_settings.goodbye_text,
            user_mention=_format_user_mention(user_id=left.id, label=label),
            chat_title=message.chat.title,
        ),
        parse_mode="HTML",
    )


@router.chat_member()
async def chat_member_updated_handler(event: ChatMemberUpdated, activity_repo, achievement_orchestrator) -> None:
    if event.chat.type not in _GROUP_CHAT_TYPES:
        return

    member_user = event.new_chat_member.user
    if member_user.is_bot:
        return

    await activity_repo.set_chat_member_active(
        chat=ChatSnapshot(
            telegram_chat_id=event.chat.id,
            chat_type=event.chat.type,
            title=event.chat.title,
        ),
        user=UserSnapshot(
            telegram_user_id=member_user.id,
            username=member_user.username,
            first_name=member_user.first_name,
            last_name=member_user.last_name,
            is_bot=bool(member_user.is_bot),
        ),
        is_active=_is_chat_member_active(event.new_chat_member),
        event_at=event.date,
    )
    if achievement_orchestrator is not None:
        await achievement_orchestrator.process_membership(
            chat_id=event.chat.id,
            user_id=member_user.id,
            is_active=_is_chat_member_active(event.new_chat_member),
            event_at=event.date,
        )


@router.callback_query(F.data.startswith("cap:"))
async def captcha_callback(query: CallbackQuery, bot: Bot, activity_repo) -> None:
    if query.data is None or query.from_user is None:
        return
    parts = query.data.split(":")
    if len(parts) != 5:
        await query.answer("Некорректная капча.", show_alert=True)
        return
    _, token, chat_raw, user_raw, emoji = parts
    if not (chat_raw.lstrip("-").isdigit() and user_raw.isdigit()):
        await query.answer("Некорректная капча.", show_alert=True)
        return
    pending = _CAPTCHA_STORE.get(token)
    if pending is None:
        await query.answer("Капча уже не активна.", show_alert=True)
        return
    if query.from_user.id != pending.user_id:
        await query.answer("Эта кнопка не для вас.", show_alert=True)
        return

    _CAPTCHA_STORE.pop(token, None)
    _CAPTCHA_BY_USER.pop((pending.chat_id, pending.user_id), None)
    pending.task.cancel()

    if emoji != "🍎":
        await _safe_delete_message(bot, chat_id=pending.chat_id, message_id=pending.challenge_message_id)
        if pending.kick_on_fail:
            await _safe_kick(bot, chat_id=pending.chat_id, user_id=pending.user_id)
            await log_chat_action(
                activity_repo,
                chat_id=pending.chat_id,
                chat_type="group",
                chat_title=pending.chat_title,
                action_code="captcha_failed_kick",
                description=f"Пользователь {pending.user_id} исключён за неверную капчу.",
                target_user_id=pending.user_id,
            )
        await query.answer("Неверный ответ.", show_alert=True)
        return

    await _safe_restore_member(bot, chat_id=pending.chat_id, user_id=pending.user_id)
    await _safe_delete_message(bot, chat_id=pending.chat_id, message_id=pending.challenge_message_id)
    await query.answer("Доступ открыт.")
    await log_chat_action(
        activity_repo,
        chat_id=pending.chat_id,
        chat_type="group",
        chat_title=pending.chat_title,
        action_code="captcha_passed",
        description=f"Пользователь {pending.user_id} успешно прошёл капчу.",
        target_user_id=pending.user_id,
    )
    if pending.welcome_text:
        await _send_welcome(
            bot,
            chat_id=pending.chat_id,
            user_id=pending.user_id,
            user_label=pending.user_label,
            chat_title=pending.chat_title,
            welcome_text=pending.welcome_text,
            button_text=pending.welcome_button_text,
            button_url=pending.welcome_button_url,
        )


@router.callback_query(F.data.startswith("famreq:"))
async def family_request_callback(query: CallbackQuery, activity_repo, achievement_orchestrator) -> None:
    if query.data is None or query.from_user is None or query.message is None:
        return
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Некорректная кнопка.", show_alert=True)
        return
    _, action, request_id = parts
    pending = _FAMILY_REQUESTS.get(request_id)
    if pending is None:
        await query.answer("Запрос уже не активен.", show_alert=True)
        return
    if datetime.now(timezone.utc) - pending.created_at > timedelta(hours=_FAMILY_REQUEST_TTL_HOURS):
        _FAMILY_REQUESTS.pop(request_id, None)
        await query.answer("Срок запроса истёк.", show_alert=True)
        return
    if query.from_user.id != pending.target_user_id:
        await query.answer("Подтвердить может только адресат.", show_alert=True)
        return

    _FAMILY_REQUESTS.pop(request_id, None)
    if action != "accept":
        await query.message.edit_text("Запрос на семейную связь отклонён.")
        await query.answer("Отклонено")
        return

    actor = await activity_repo.get_user_snapshot(user_id=pending.actor_user_id)
    target = await activity_repo.get_user_snapshot(user_id=pending.target_user_id)
    if actor is None or target is None:
        await query.answer("Не удалось загрузить участников.", show_alert=True)
        return
    relation_type = pending.relation_type
    if relation_type == "pet":
        relation = await activity_repo.upsert_graph_relationship(
            chat=ChatSnapshot(query.message.chat.id, query.message.chat.type, query.message.chat.title),
            user_a=target,
            user_b=actor,
            relation_type="pet",
            actor_user_id=query.from_user.id,
        )
        text = "Связь сохранена: теперь питомец официально закреплён."
    else:
        error = await activity_repo.validate_parent_link(
            chat_id=query.message.chat.id,
            actor_user_id=pending.actor_user_id,
            target_user_id=pending.target_user_id,
        )
        if error:
            await query.message.edit_text(error)
            await query.answer("Связь отклонена", show_alert=True)
            return
        relation = await activity_repo.upsert_graph_relationship(
            chat=ChatSnapshot(query.message.chat.id, query.message.chat.type, query.message.chat.title),
            user_a=actor,
            user_b=target,
            relation_type="parent",
            actor_user_id=query.from_user.id,
        )
        text = "Связь сохранена: усыновление подтверждено."
    await log_chat_action(
        activity_repo,
        chat_id=query.message.chat.id,
        chat_type=query.message.chat.type,
        chat_title=query.message.chat.title,
        action_code=f"family_{relation.relation_type}",
        description=f"Создана семейная связь {relation.relation_type}: {relation.user_a} -> {relation.user_b}.",
        actor_user_id=query.from_user.id,
    )
    if achievement_orchestrator is not None:
        now = datetime.now(timezone.utc)
        await achievement_orchestrator.process_refresh(
            chat_id=query.message.chat.id,
            user_id=relation.user_a,
            event_at=now,
            event_type=f"family_{relation.relation_type}_accepted",
        )
        await achievement_orchestrator.process_refresh(
            chat_id=query.message.chat.id,
            user_id=relation.user_b,
            event_at=now,
            event_type=f"family_{relation.relation_type}_accepted",
        )
    await query.message.edit_text(text)
    await query.answer("Готово")
