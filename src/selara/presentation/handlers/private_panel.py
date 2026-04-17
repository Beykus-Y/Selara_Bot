from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
import re
import shlex

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, Filter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from selara.application.use_cases.economy.catalog import FARM_LEVEL_PLOTS
from selara.application.use_cases.economy.get_dashboard import execute as get_dashboard
from selara.application.use_cases.get_last_seen import execute as get_last_seen
from selara.application.use_cases.get_my_stats import execute as get_my_stats
from selara.application.use_cases.get_rep_stats import execute as get_rep_stats
from selara.core.roles import BOT_PERMISSIONS
from selara.core.chat_settings import CHAT_SETTINGS_KEYS, default_chat_settings
from selara.core.config import Settings
from selara.core.web_auth import digest_login_code, generate_login_code
from selara.domain.entities import ChatSnapshot, UserChatOverview, UserSnapshot
from selara.domain.value_objects import display_name_from_parts
from selara.presentation.auth import has_permission
from selara.presentation.commands.access import parse_command_rank_phrase, resolve_command_key_input
from selara.presentation.commands.normalizer import normalize_text_command
from selara.presentation.formatters import format_last_seen
from selara.presentation.handlers.economy import _dashboard_text
from selara.presentation.handlers.settings_common import (
    CFG_BOOL_KEYS,
    CFG_ENUM_VALUES,
    apply_setting_update,
    render_settings_compact,
    render_setting_editor_text,
    setting_short_ru,
    setting_title_ru,
    settings_to_dict,
)

router = Router(name="private_panel")

_GROUP_PAGE_SIZE = 6
_CFG_PAGE_SIZE = 8
_PENDING_TTL = timedelta(minutes=15)
_ROLE_CREATE_RE = re.compile(
    r'^\s*создать\s+роль\s+"(?P<title>[^"]+)"(?:\s+из\s+"(?P<template>[^"]+)")?(?:\s+ранг\s+(?P<rank>-?\d+))?\s*$',
    re.IGNORECASE,
)
_ROLE_DELETE_RE = re.compile(r'^\s*удалить\s+роль\s+"(?P<role>[^"]+)"\s*$', re.IGNORECASE)
_ROLE_RANK_RE = re.compile(r'^\s*ранг\s+роли\s+"(?P<role>[^"]+)"\s+(?P<rank>-?\d+)\s*$', re.IGNORECASE)
_ROLE_TITLE_RE = re.compile(
    r'^\s*название\s+роли\s+"(?P<role>[^"]+)"\s+"(?P<title>[^"]+)"\s*$',
    re.IGNORECASE,
)
_ROLE_PERMS_RE = re.compile(
    r'^\s*права\s+роли\s+"(?P<role>[^"]+)"\s+(?P<ops>.+?)\s*$',
    re.IGNORECASE,
)


@dataclass
class _PendingCfgInput:
    chat_id: int
    key: str
    expires_at: datetime


@dataclass
class _PendingAdminInput:
    chat_id: int
    mode: str
    expires_at: datetime


_pending_cfg_inputs: dict[int, _PendingCfgInput] = {}
_pending_admin_inputs: dict[int, _PendingAdminInput] = {}


def encode_pm_callback(route: str, *parts: object) -> str:
    payload = [str(part) for part in parts]
    return ":".join(["pm", route, *payload])


def decode_pm_callback(data: str | None) -> tuple[str, list[str]] | None:
    if not data:
        return None
    parts = data.split(":")
    if len(parts) < 2 or parts[0] != "pm":
        return None
    return parts[1], parts[2:]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _cleanup_pending_cfg_inputs() -> None:
    now = _now_utc()
    for user_id, state in list(_pending_cfg_inputs.items()):
        if state.expires_at <= now:
            _pending_cfg_inputs.pop(user_id, None)
    for user_id, state in list(_pending_admin_inputs.items()):
        if state.expires_at <= now:
            _pending_admin_inputs.pop(user_id, None)


def _get_pending_cfg_input(user_id: int) -> _PendingCfgInput | None:
    _cleanup_pending_cfg_inputs()
    return _pending_cfg_inputs.get(user_id)


def _set_pending_cfg_input(*, user_id: int, chat_id: int, key: str) -> None:
    _pending_admin_inputs.pop(user_id, None)
    _pending_cfg_inputs[user_id] = _PendingCfgInput(
        chat_id=chat_id,
        key=key,
        expires_at=_now_utc() + _PENDING_TTL,
    )


def _clear_pending_cfg_input(user_id: int) -> None:
    _pending_cfg_inputs.pop(user_id, None)


def _get_pending_admin_input(user_id: int) -> _PendingAdminInput | None:
    _cleanup_pending_cfg_inputs()
    return _pending_admin_inputs.get(user_id)


def _set_pending_admin_input(*, user_id: int, chat_id: int, mode: str) -> None:
    _pending_cfg_inputs.pop(user_id, None)
    _pending_admin_inputs[user_id] = _PendingAdminInput(
        chat_id=chat_id,
        mode=mode,
        expires_at=_now_utc() + _PENDING_TTL,
    )


def _clear_pending_admin_input(user_id: int) -> None:
    _pending_admin_inputs.pop(user_id, None)


class PendingCfgInputFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        if message.chat.type != "private" or message.from_user is None:
            return False
        return _get_pending_cfg_input(message.from_user.id) is not None


class PendingAdminInputFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        if message.chat.type != "private" or message.from_user is None:
            return False
        return _get_pending_admin_input(message.from_user.id) is not None


def _chat_title(chat: UserChatOverview) -> str:
    title = (chat.chat_title or "").strip()
    if title:
        return title
    return f"chat:{chat.chat_id}"


def _chat_role_label(role: str | None) -> str:
    normalized = (role or "").strip()
    if normalized:
        return normalized
    return "-"


def _user_label_from_telegram_user(user) -> str:
    return display_name_from_parts(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        chat_display_name=None,
    )


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cfg_key_by_index(raw_idx: str | None) -> str | None:
    idx = _safe_int(raw_idx)
    if idx is None:
        return None
    if not 0 <= idx < len(CHAT_SETTINGS_KEYS):
        return None
    return CHAT_SETTINGS_KEYS[idx]


def _cfg_index(key: str) -> int:
    return CHAT_SETTINGS_KEYS.index(key)


def _short_to_value(key: str, short: str) -> str | None:
    if short == "d":
        return "default"
    if key in CFG_BOOL_KEYS:
        return {"t": "true", "f": "false"}.get(short)
    if key == "text_commands_locale":
        return {"ru": "ru", "en": "en"}.get(short)
    if key == "economy_mode":
        return {"g": "global", "l": "local"}.get(short)
    return None


def _build_home_keyboard(
    *,
    has_admin_groups: bool,
    has_user_groups: bool,
    miniapp_url: str | None = None,
    desktop_url: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_admin_groups:
        builder.button(text="🛠 Админ-панель", callback_data=encode_pm_callback("al", 0))
    if has_user_groups:
        builder.button(text="👤 Мои группы", callback_data=encode_pm_callback("ul", 0))
    if miniapp_url:
        builder.button(text="📱 Mini App", url=miniapp_url)
    if desktop_url:
        builder.button(text="🖥 ПК-панель", url=desktop_url)
    builder.button(text="🔄 Обновить", callback_data=encode_pm_callback("h"))
    builder.adjust(1)
    return builder.as_markup()


def _build_groups_keyboard(*, route_prefix: str, groups: list[UserChatOverview], page: int) -> InlineKeyboardMarkup:
    page = max(page, 0)
    start = page * _GROUP_PAGE_SIZE
    chunk = groups[start : start + _GROUP_PAGE_SIZE]

    builder = InlineKeyboardBuilder()
    for item in chunk:
        title = _chat_title(item)
        if len(title) > 24:
            title = f"{title[:21]}..."
        builder.button(text=title, callback_data=encode_pm_callback(f"{route_prefix}g", item.chat_id))

    has_prev = page > 0
    has_next = start + _GROUP_PAGE_SIZE < len(groups)
    if has_prev:
        builder.button(text="⬅️", callback_data=encode_pm_callback(f"{route_prefix}l", page - 1))
    if has_next:
        builder.button(text="➡️", callback_data=encode_pm_callback(f"{route_prefix}l", page + 1))
    if has_prev or has_next:
        builder.adjust(1, 2)

    builder.button(text="🏠 Главное меню", callback_data=encode_pm_callback("h"))
    builder.adjust(1)
    return builder.as_markup()


def _build_admin_group_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⚙️ Настройки", callback_data=encode_pm_callback("as", chat_id, 0))
    builder.button(text="🔐 Ранги команд", callback_data=encode_pm_callback("ar", chat_id))
    builder.button(text="🧩 Роли", callback_data=encode_pm_callback("rl", chat_id))
    builder.button(text="⬅️ К группам", callback_data=encode_pm_callback("al", 0))
    builder.button(text="🏠 Главное меню", callback_data=encode_pm_callback("h"))
    builder.adjust(1)
    return builder.as_markup()


def _build_settings_keys_keyboard(*, chat_id: int, page: int) -> InlineKeyboardMarkup:
    page = max(page, 0)
    start = page * _CFG_PAGE_SIZE
    chunk = CHAT_SETTINGS_KEYS[start : start + _CFG_PAGE_SIZE]

    builder = InlineKeyboardBuilder()
    for key in chunk:
        key_idx = _cfg_index(key)
        label = setting_short_ru(key)
        if len(label) > 24:
            label = f"{label[:21]}..."
        builder.button(text=label, callback_data=encode_pm_callback("ae", chat_id, key_idx))

    has_prev = page > 0
    has_next = start + _CFG_PAGE_SIZE < len(CHAT_SETTINGS_KEYS)
    if has_prev:
        builder.button(text="⬅️", callback_data=encode_pm_callback("as", chat_id, page - 1))
    if has_next:
        builder.button(text="➡️", callback_data=encode_pm_callback("as", chat_id, page + 1))
    if has_prev or has_next:
        builder.adjust(1, 2)

    builder.button(text="⬅️ К группе", callback_data=encode_pm_callback("ag", chat_id))
    builder.button(text="🏠 Главное меню", callback_data=encode_pm_callback("h"))
    builder.adjust(1)
    return builder.as_markup()


def _build_setting_editor_keyboard(*, chat_id: int, key: str, key_idx: int, current_value: object) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if key in CFG_BOOL_KEYS:
        current = bool(current_value)
        builder.button(text=f"true {'✓' if current else ''}".strip(), callback_data=encode_pm_callback("av", chat_id, key_idx, "t"))
        builder.button(text=f"false {'✓' if not current else ''}".strip(), callback_data=encode_pm_callback("av", chat_id, key_idx, "f"))
        builder.adjust(2)
    elif key in CFG_ENUM_VALUES:
        variants = CFG_ENUM_VALUES[key]
        for item in variants:
            short = "g" if item == "global" else "l" if item == "local" else item
            marker = " ✓" if str(current_value) == item else ""
            label = item
            if item == "global":
                label = "global (общий)"
            elif item == "local":
                label = "local (по группе)"
            builder.button(text=f"{label}{marker}", callback_data=encode_pm_callback("av", chat_id, key_idx, short))
        builder.adjust(len(variants))
    else:
        builder.button(text="⌨️ Ввести значение", callback_data=encode_pm_callback("ai", chat_id, key_idx))
        builder.adjust(1)

    builder.button(text="↩️ default", callback_data=encode_pm_callback("av", chat_id, key_idx, "d"))
    builder.button(text="⬅️ К настройкам", callback_data=encode_pm_callback("as", chat_id, key_idx // _CFG_PAGE_SIZE))
    builder.button(text="🏠 Главное меню", callback_data=encode_pm_callback("h"))
    builder.adjust(1)
    return builder.as_markup()


def _build_user_group_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data=encode_pm_callback("ur", chat_id))
    builder.button(text="💰 Экономика local", callback_data=encode_pm_callback("ue", chat_id))
    builder.button(text="⬅️ К группам", callback_data=encode_pm_callback("ul", 0))
    builder.button(text="🏠 Главное меню", callback_data=encode_pm_callback("h"))
    builder.adjust(1)
    return builder.as_markup()


async def _edit_or_answer(query: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    if query.message is None:
        await query.answer()
        return

    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except TelegramBadRequest:
        await query.message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
    await query.answer()


async def _render_home_text(*, user, admin_groups: list[UserChatOverview], user_groups: list[UserChatOverview]) -> str:
    display_name = escape(_user_label_from_telegram_user(user))
    lines = [
        f"<b>Привет, {display_name}!</b>",
        "",
        "Это ЛС-панель Selara.",
        f"<b>Группы, где у вас админ-права бота:</b> <code>{len(admin_groups)}</code>",
        f"<b>Группы из вашей активности:</b> <code>{len(user_groups)}</code>",
    ]
    if not admin_groups and not user_groups:
        lines.extend(
            [
                "",
                "<i>Пока нет данных.</i>",
                "Добавьте бота в группу, отправьте сообщения в группе и/или получите админ-права внутри бота.",
            ]
        )
    return "\n".join(lines)


def _build_miniapp_url(settings: Settings) -> str | None:
    if not settings.web_enabled:
        return None
    bot_username = (settings.bot_username or settings.bot_name or "").strip().lstrip("@")
    if not bot_username:
        return None
    return f"https://t.me/{bot_username}?startapp"


def _build_web_panel_url(settings: Settings) -> str | None:
    if not settings.web_enabled:
        return None
    return f"{settings.resolved_web_base_url}/login"


def _append_web_panel_info(text: str, *, miniapp_url: str | None, desktop_url: str | None) -> str:
    if not miniapp_url and not desktop_url:
        return text
    lines = [text, ""]
    if miniapp_url:
        lines.extend(
            [
                f'📱 <b>Mini App:</b> <a href="{escape(miniapp_url)}">открыть в Telegram</a>',
                "Это основной вход с телефона: гача, группы, игры и профиль доступны без кода.",
            ]
        )
    if desktop_url:
        lines.extend(
            [
                f'🖥 <b>ПК-панель:</b> <a href="{escape(desktop_url)}">открыть</a>',
                "Для входа на ПК получите одноразовый код командой <code>/login</code> в этом чате.",
            ]
        )
    return "\n".join(lines)


async def send_private_start_panel(message: Message, activity_repo, economy_repo, settings: Settings) -> None:
    if message.chat.type != "private" or message.from_user is None:
        return

    admin_groups = await activity_repo.list_user_admin_chats(user_id=message.from_user.id)
    user_groups = await activity_repo.list_user_activity_chats(user_id=message.from_user.id, limit=100)
    text = await _render_home_text(user=message.from_user, admin_groups=admin_groups, user_groups=user_groups)
    miniapp_url = _build_miniapp_url(settings)
    desktop_url = _build_web_panel_url(settings)
    text = _append_web_panel_info(text, miniapp_url=miniapp_url, desktop_url=desktop_url)

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=_build_home_keyboard(
            has_admin_groups=bool(admin_groups),
            has_user_groups=bool(user_groups),
            miniapp_url=miniapp_url,
            desktop_url=desktop_url,
        ),
    )


@router.message(Command("login"))
async def web_login_command(message: Message, web_auth_repo, settings: Settings) -> None:
    if message.chat.type != "private" or message.from_user is None:
        return

    if not settings.web_enabled:
        await message.answer("Веб-панель сейчас отключена.")
        return

    now = _now_utc()
    await web_auth_repo.purge_expired_state(now=now)
    await web_auth_repo.invalidate_user_login_codes(user_id=message.from_user.id, now=now)

    code = ""
    for _ in range(20):
        candidate = generate_login_code(length=6)
        candidate_digest = digest_login_code(secret=settings.resolved_web_auth_secret, code=candidate)
        if not await web_auth_repo.has_active_login_code_digest(code_digest=candidate_digest, now=now):
            code = candidate
            break

    if not code:
        await message.answer("Не удалось выдать код входа. Попробуйте ещё раз через несколько секунд.")
        return

    await web_auth_repo.create_login_code(
        user=UserSnapshot(
            telegram_user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            is_bot=bool(message.from_user.is_bot),
        ),
        code_digest=digest_login_code(secret=settings.resolved_web_auth_secret, code=code),
        expires_at=now + timedelta(minutes=max(1, settings.web_login_code_ttl_minutes)),
    )

    web_url = _build_web_panel_url(settings) or settings.resolved_web_base_url
    await message.answer(
        "\n".join(
            [
                "<b>Код для входа в ПК-панель</b>",
                f"Код: <code>{code}</code>",
                f"Действует: <code>{max(1, settings.web_login_code_ttl_minutes)}</code> мин.",
                f'Открыть на ПК: <a href="{escape(web_url)}">{escape(web_url)}</a>',
                "",
                "С телефона используйте кнопку <b>Mini App</b> из <code>/start</code>.",
                "<i>Никому не пересылайте этот код. Он одноразовый.</i>",
            ]
        ),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def _ensure_manage_settings(activity_repo, *, user, chat_id: int) -> bool:
    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=chat_id,
        chat_type="group",
        chat_title=None,
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_bot=bool(user.is_bot),
        permission="manage_settings",
        bootstrap_if_missing_owner=False,
    )
    return allowed


async def _ensure_manage_command_access(activity_repo, *, user, chat_id: int) -> bool:
    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=chat_id,
        chat_type="group",
        chat_title=None,
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_bot=bool(user.is_bot),
        permission="manage_command_access",
        bootstrap_if_missing_owner=False,
    )
    return allowed


async def _ensure_manage_role_templates(activity_repo, *, user, chat_id: int) -> bool:
    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=chat_id,
        chat_type="group",
        chat_title=None,
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_bot=bool(user.is_bot),
        permission="manage_role_templates",
        bootstrap_if_missing_owner=False,
    )
    return allowed


async def _resolve_admin_chat_overview(activity_repo, *, user_id: int, chat_id: int) -> UserChatOverview | None:
    groups = await activity_repo.list_user_admin_chats(user_id=user_id)
    return next((item for item in groups if item.chat_id == chat_id), None)


async def _load_chat_settings(activity_repo, settings: Settings, *, chat_id: int):
    defaults = default_chat_settings(settings)
    current = await activity_repo.get_chat_settings(chat_id=chat_id)
    if current is None:
        current = defaults
    return current, defaults


def _render_admin_group_text(chat: UserChatOverview, *, bot_role_title: str | None = None) -> str:
    role_display = bot_role_title or _chat_role_label(chat.bot_role)
    lines = [
        "<b>Группа (админ)</b>",
        f"<b>Название:</b> {escape(_chat_title(chat))}",
        f"<b>ID:</b> <code>{chat.chat_id}</code>",
        f"<b>Роль бота:</b> <code>{escape(role_display)}</code>",
    ]
    if chat.message_count is not None:
        lines.append(f"<b>Ваших сообщений:</b> <code>{chat.message_count}</code>")
    return "\n".join(lines)


def _build_command_ranks_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⌨️ Ввести правило", callback_data=encode_pm_callback("ari", chat_id))
    builder.button(text="⬅️ К группе", callback_data=encode_pm_callback("ag", chat_id))
    builder.button(text="🏠 Главное меню", callback_data=encode_pm_callback("h"))
    builder.adjust(1)
    return builder.as_markup()


def _build_roles_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⌨️ Ввести команду", callback_data=encode_pm_callback("rli", chat_id))
    builder.button(text="⬅️ К группе", callback_data=encode_pm_callback("ag", chat_id))
    builder.button(text="🏠 Главное меню", callback_data=encode_pm_callback("h"))
    builder.adjust(1)
    return builder.as_markup()


async def _render_command_ranks_text(activity_repo, *, chat_id: int) -> str:
    rules = await activity_repo.list_command_access_rules(chat_id=chat_id)
    if not rules:
        return (
            "<b>Ранги команд</b>\n"
            f"Группа: <code>{chat_id}</code>\n\n"
            "Индивидуальные ограничения не заданы.\n"
            "Пример: <code>установить \"топ\" ранг внутри бота совладелец</code>"
        )

    lines = ["<b>Ранги команд</b>", f"Группа: <code>{chat_id}</code>", "", "<b>Текущие правила:</b>"]
    for rule in rules:
        role = await activity_repo.get_chat_role_definition(chat_id=chat_id, role_code=rule.min_role_code)
        role_title = role.title_ru if role is not None else rule.min_role_code
        lines.append(f'• <code>{escape(rule.command_key)}</code> → <code>{escape(role_title)}</code>')
    lines.append("")
    lines.append('Установить: <code>установить "команда" ранг внутри бота роль</code>')
    lines.append('Сбросить: <code>сбросить "команда" ранг внутри бота</code>')
    return "\n".join(lines)


async def _render_roles_text(activity_repo, *, chat_id: int) -> str:
    roles = await activity_repo.list_chat_role_definitions(chat_id=chat_id)
    if not roles:
        return f"<b>Роли бота</b>\nГруппа: <code>{chat_id}</code>\nНет ролей."

    lines = ["<b>Роли бота</b>", f"Группа: <code>{chat_id}</code>", "", "<b>Текущие роли:</b>"]
    for role in roles:
        marker = "system" if role.is_system else "custom"
        permissions = ", ".join(role.permissions) if role.permissions else "нет"
        lines.append(
            f'• <code>{escape(role.title_ru)}</code> (<code>{escape(role.role_code)}</code>, rank=<code>{role.rank}</code>, {marker})'
        )
        lines.append(f"  Права: <code>{escape(permissions)}</code>")

    lines.append("")
    lines.append('Создать: <code>создать роль "Видеомонтажер" из "Мл. админ" ранг 15</code>')
    lines.append('Ранг: <code>ранг роли "Видеомонтажер" 16</code>')
    lines.append('Название: <code>название роли "Видеомонтажер" "Монтажер"</code>')
    lines.append('Права: <code>права роли "Монтажер" +announce -manage_games</code>')
    lines.append('Удалить: <code>удалить роль "Монтажер"</code>')
    lines.append(f"Доступные права: <code>{escape(', '.join(sorted(BOT_PERMISSIONS)))}</code>")
    return "\n".join(lines)


async def _apply_private_role_command(
    activity_repo,
    *,
    chat_snapshot: ChatSnapshot,
    text: str,
) -> str:
    normalized = normalize_text_command(text)
    if normalized in {"роли", "список ролей"}:
        return await _render_roles_text(activity_repo, chat_id=chat_snapshot.telegram_chat_id)

    match = _ROLE_CREATE_RE.match(text)
    if match is not None:
        title_ru = match.group("title").strip()
        template_token = (match.group("template") or "participant").strip()
        rank_raw = (match.group("rank") or "").strip()
        rank = int(rank_raw) if rank_raw else None
        created = await activity_repo.create_custom_role_from_template(
            chat=chat_snapshot,
            title_ru=title_ru,
            template_token=template_token,
            rank=rank,
        )
        return (
            "<b>Кастомная роль создана:</b>\n"
            f"Название: <code>{escape(created.title_ru)}</code>\n"
            f"Код: <code>{escape(created.role_code)}</code>\n"
            f"Ранг: <code>{created.rank}</code>\n"
            f"{await _render_roles_text(activity_repo, chat_id=chat_snapshot.telegram_chat_id)}"
        )

    match = _ROLE_DELETE_RE.match(text)
    if match is not None:
        role_token = match.group("role").strip()
        deleted = await activity_repo.delete_custom_role(chat_id=chat_snapshot.telegram_chat_id, role_token=role_token)
        if not deleted:
            return "Роль не найдена."
        return (
            "Кастомная роль удалена.\n\n"
            f"{await _render_roles_text(activity_repo, chat_id=chat_snapshot.telegram_chat_id)}"
        )

    match = _ROLE_RANK_RE.match(text)
    if match is not None:
        role_token = match.group("role").strip()
        rank = int(match.group("rank"))
        updated = await activity_repo.update_custom_role(
            chat_id=chat_snapshot.telegram_chat_id,
            role_token=role_token,
            rank=rank,
        )
        return (
            "<b>Ранг роли обновлён:</b>\n"
            f"Роль: <code>{escape(updated.title_ru)}</code>\n"
            f"Новый ранг: <code>{updated.rank}</code>\n\n"
            f"{await _render_roles_text(activity_repo, chat_id=chat_snapshot.telegram_chat_id)}"
        )

    match = _ROLE_TITLE_RE.match(text)
    if match is not None:
        role_token = match.group("role").strip()
        title_ru = match.group("title").strip()
        updated = await activity_repo.update_custom_role(
            chat_id=chat_snapshot.telegram_chat_id,
            role_token=role_token,
            title_ru=title_ru,
        )
        return (
            "<b>Название роли обновлено:</b>\n"
            f"Роль: <code>{escape(updated.role_code)}</code>\n"
            f"Новое имя: <code>{escape(updated.title_ru)}</code>\n\n"
            f"{await _render_roles_text(activity_repo, chat_id=chat_snapshot.telegram_chat_id)}"
        )

    match = _ROLE_PERMS_RE.match(text)
    if match is not None:
        role_token = match.group("role").strip()
        ops_raw = match.group("ops").strip()
        try:
            ops = shlex.split(ops_raw)
        except ValueError:
            return "Некорректный формат прав."
        if not ops:
            return "Укажите права после названия роли."

        role = await activity_repo.resolve_chat_role_definition(chat_id=chat_snapshot.telegram_chat_id, token=role_token)
        if role is None:
            return "Роль не найдена."
        permissions = set(role.permissions)

        if len(ops) == 1 and ops[0].startswith("="):
            raw_items = [item.strip().lower() for item in ops[0][1:].split(",") if item.strip()]
            unknown = [item for item in raw_items if item not in BOT_PERMISSIONS]
            if unknown:
                return f"Неизвестные права: {', '.join(unknown)}"
            permissions = set(raw_items)
        else:
            for op in ops:
                sign = op[0] if op else "+"
                perm = op[1:] if sign in {"+", "-"} else op
                perm = perm.strip().lower()
                if perm not in BOT_PERMISSIONS:
                    return f"Неизвестное право: {perm}"
                if sign == "-":
                    permissions.discard(perm)
                else:
                    permissions.add(perm)

        updated = await activity_repo.update_custom_role(
            chat_id=chat_snapshot.telegram_chat_id,
            role_token=role_token,
            permissions=tuple(sorted(permissions)),
        )
        perms_text = ", ".join(updated.permissions) if updated.permissions else "нет"
        return (
            "<b>Права роли обновлены:</b>\n"
            f"Роль: <code>{escape(updated.title_ru)}</code>\n"
            f"Права: <code>{escape(perms_text)}</code>\n\n"
            f"{await _render_roles_text(activity_repo, chat_id=chat_snapshot.telegram_chat_id)}"
        )

    return (
        "Неверный формат команды ролей.\n"
        'Пример: <code>создать роль "Видеомонтажер" из "Мл. админ" ранг 15</code>'
    )


async def _render_user_group_text(
    *,
    activity_repo,
    economy_repo,
    settings: Settings,
    user,
    chat: UserChatOverview,
) -> str:
    settings_for_chat = await activity_repo.get_chat_settings(chat_id=chat.chat_id)
    if settings_for_chat is None:
        settings_for_chat = default_chat_settings(settings)

    stats = await get_my_stats(repo=activity_repo, chat_id=chat.chat_id, user_id=user.id)
    rep = await get_rep_stats(
        repo=activity_repo,
        chat_id=chat.chat_id,
        user_id=user.id,
        limit=settings_for_chat.top_limit_max,
        karma_weight=settings_for_chat.leaderboard_hybrid_karma_weight,
        activity_weight=settings_for_chat.leaderboard_hybrid_activity_weight,
        days=settings_for_chat.leaderboard_7d_days,
    )
    last_seen = await get_last_seen(repo=activity_repo, chat_id=chat.chat_id, user_id=user.id)

    label = await activity_repo.get_chat_display_name(chat_id=chat.chat_id, user_id=user.id)
    if not label:
        label = _user_label_from_telegram_user(user)

    dashboard, dashboard_error = await get_dashboard(
        economy_repo,
        economy_mode="local",
        chat_id=chat.chat_id,
        user_id=user.id,
    )

    lines = [
        "<b>Группа (пользователь)</b>",
        f"<b>Название:</b> {escape(_chat_title(chat))}",
        f"<b>ID:</b> <code>{chat.chat_id}</code>",
        "",
        f"<b>Сообщений:</b> <code>{stats.message_count if stats is not None else 0}</code>",
        f"<b>Карма (всё время):</b> <code>{rep.karma_all}</code>",
        f"<b>Активность (всё время):</b> <code>{rep.activity_all}</code>",
        f"<b>Позиция (всё время):</b> <code>{rep.rank_all if rep.rank_all is not None else '-'}</code>",
        f"<b>Позиция (7д):</b> <code>{rep.rank_7d if rep.rank_7d is not None else '-'}</code>",
        format_last_seen(user_label=label, last_seen_at=last_seen, timezone_name=settings.bot_timezone),
    ]

    lines.append("")
    lines.append("<b>Экономика local:</b>")
    if dashboard is None:
        lines.append(escape(dashboard_error or "Нет данных по экономике для этой группы."))
    else:
        slots = FARM_LEVEL_PLOTS.get(dashboard.farm.farm_level, 2)
        crops = sum(item.quantity for item in dashboard.inventory if item.item_code.startswith("crop:"))
        items = sum(item.quantity for item in dashboard.inventory if not item.item_code.startswith("crop:"))
        lines.append(f"Баланс: <code>{dashboard.account.balance}</code>")
        lines.append(
            f"Ферма: ур. <code>{dashboard.farm.farm_level}</code>, "
            f"слоты <code>{slots}</code>, размер <code>{escape(dashboard.farm.size_tier)}</code>"
        )
        lines.append(f"Инвентарь: культуры <code>{crops}</code>, предметы <code>{items}</code>")

    return "\n".join(lines)


@router.callback_query(F.data.startswith("pm:"))
async def private_panel_callback(query: CallbackQuery, activity_repo, economy_repo, settings: Settings) -> None:
    if query.from_user is None:
        await query.answer()
        return
    if query.message is None or query.message.chat.type != "private":
        await query.answer("Доступно только в ЛС", show_alert=True)
        return

    decoded = decode_pm_callback(query.data)
    if decoded is None:
        await query.answer("Некорректная кнопка", show_alert=False)
        return
    route, args = decoded

    if route == "h":
        admin_groups = await activity_repo.list_user_admin_chats(user_id=query.from_user.id)
        user_groups = await activity_repo.list_user_activity_chats(user_id=query.from_user.id, limit=100)
        text = await _render_home_text(user=query.from_user, admin_groups=admin_groups, user_groups=user_groups)
        miniapp_url = _build_miniapp_url(settings)
        desktop_url = _build_web_panel_url(settings)
        await _edit_or_answer(
            query,
            _append_web_panel_info(text, miniapp_url=miniapp_url, desktop_url=desktop_url),
            _build_home_keyboard(
                has_admin_groups=bool(admin_groups),
                has_user_groups=bool(user_groups),
                miniapp_url=miniapp_url,
                desktop_url=desktop_url,
            ),
        )
        return

    if route == "al":
        page = max(0, _safe_int(args[0] if args else "0") or 0)
        groups = await activity_repo.list_user_admin_chats(user_id=query.from_user.id)
        if not groups:
            user_groups = await activity_repo.list_user_activity_chats(user_id=query.from_user.id, limit=100)
            miniapp_url = _build_miniapp_url(settings)
            desktop_url = _build_web_panel_url(settings)
            await _edit_or_answer(
                query,
                "Нет групп, где у вас есть админ-права бота.",
                _build_home_keyboard(
                    has_admin_groups=False,
                    has_user_groups=bool(user_groups),
                    miniapp_url=miniapp_url,
                    desktop_url=desktop_url,
                ),
            )
            return
        await _edit_or_answer(
            query,
            "<b>Ваши админ-группы</b>\nВыберите группу для управления.",
            _build_groups_keyboard(route_prefix="a", groups=groups, page=page),
        )
        return

    if route == "ag":
        chat_id = _safe_int(args[0] if args else None)
        if chat_id is None:
            await query.answer("Некорректный чат", show_alert=True)
            return
        groups = await activity_repo.list_user_admin_chats(user_id=query.from_user.id)
        selected = next((item for item in groups if item.chat_id == chat_id), None)
        if selected is None:
            await query.answer("Группа недоступна", show_alert=True)
            return
        can_manage_settings = await _ensure_manage_settings(activity_repo, user=query.from_user, chat_id=chat_id)
        can_manage_ranks = await _ensure_manage_command_access(activity_repo, user=query.from_user, chat_id=chat_id)
        can_manage_roles = await _ensure_manage_role_templates(activity_repo, user=query.from_user, chat_id=chat_id)
        if not (can_manage_settings or can_manage_ranks or can_manage_roles):
            await query.answer("Недостаточно прав", show_alert=True)
            return
        role_title = None
        if selected.bot_role:
            role = await activity_repo.get_chat_role_definition(chat_id=chat_id, role_code=selected.bot_role)
            role_title = role.title_ru if role is not None else None
        await _edit_or_answer(
            query,
            _render_admin_group_text(selected, bot_role_title=role_title),
            _build_admin_group_keyboard(chat_id),
        )
        return

    if route == "ar":
        chat_id = _safe_int(args[0] if args else None)
        if chat_id is None:
            await query.answer("Некорректный чат", show_alert=True)
            return
        if not await _ensure_manage_command_access(activity_repo, user=query.from_user, chat_id=chat_id):
            await query.answer("Недостаточно прав", show_alert=True)
            return
        text = await _render_command_ranks_text(activity_repo, chat_id=chat_id)
        await _edit_or_answer(query, text, _build_command_ranks_keyboard(chat_id))
        return

    if route == "ari":
        chat_id = _safe_int(args[0] if args else None)
        if chat_id is None:
            await query.answer("Некорректный чат", show_alert=True)
            return
        if not await _ensure_manage_command_access(activity_repo, user=query.from_user, chat_id=chat_id):
            await query.answer("Недостаточно прав", show_alert=True)
            return
        _set_pending_admin_input(user_id=query.from_user.id, chat_id=chat_id, mode="command_rank")
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="❌ Отмена ввода", callback_data=encode_pm_callback("arc", chat_id))
        keyboard.button(text="⬅️ К рангам", callback_data=encode_pm_callback("ar", chat_id))
        keyboard.adjust(1)
        await _edit_or_answer(
            query,
            (
                "<b>Ожидаю правило ранга команды</b>\n"
                f"Группа: <code>{chat_id}</code>\n"
                'Формат: <code>установить "команда" ранг внутри бота роль</code>\n'
                'Сброс: <code>сбросить "команда" ранг внутри бота</code>\n'
                "Для отмены: <code>/cancel</code>."
            ),
            keyboard.as_markup(),
        )
        return

    if route == "arc":
        _clear_pending_admin_input(query.from_user.id)
        chat_id = _safe_int(args[0] if args else None)
        if chat_id is None:
            admin_groups = await activity_repo.list_user_admin_chats(user_id=query.from_user.id)
            user_groups = await activity_repo.list_user_activity_chats(user_id=query.from_user.id, limit=100)
            miniapp_url = _build_miniapp_url(settings)
            desktop_url = _build_web_panel_url(settings)
            await _edit_or_answer(
                query,
                "Ввод отменён.",
                _build_home_keyboard(
                    has_admin_groups=bool(admin_groups),
                    has_user_groups=bool(user_groups),
                    miniapp_url=miniapp_url,
                    desktop_url=desktop_url,
                ),
            )
            return
        await _edit_or_answer(query, await _render_command_ranks_text(activity_repo, chat_id=chat_id), _build_command_ranks_keyboard(chat_id))
        return

    if route == "rl":
        chat_id = _safe_int(args[0] if args else None)
        if chat_id is None:
            await query.answer("Некорректный чат", show_alert=True)
            return
        if not await _ensure_manage_role_templates(activity_repo, user=query.from_user, chat_id=chat_id):
            await query.answer("Недостаточно прав", show_alert=True)
            return
        await _edit_or_answer(query, await _render_roles_text(activity_repo, chat_id=chat_id), _build_roles_keyboard(chat_id))
        return

    if route == "rli":
        chat_id = _safe_int(args[0] if args else None)
        if chat_id is None:
            await query.answer("Некорректный чат", show_alert=True)
            return
        if not await _ensure_manage_role_templates(activity_repo, user=query.from_user, chat_id=chat_id):
            await query.answer("Недостаточно прав", show_alert=True)
            return
        _set_pending_admin_input(user_id=query.from_user.id, chat_id=chat_id, mode="roles")
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="❌ Отмена ввода", callback_data=encode_pm_callback("rlc", chat_id))
        keyboard.button(text="⬅️ К ролям", callback_data=encode_pm_callback("rl", chat_id))
        keyboard.adjust(1)
        await _edit_or_answer(
            query,
            (
                "<b>Ожидаю команду управления ролями</b>\n"
                f"Группа: <code>{chat_id}</code>\n"
                'Пример: <code>создать роль "Видеомонтажер" из "Мл. админ" ранг 15</code>\n'
                "Для отмены: <code>/cancel</code>."
            ),
            keyboard.as_markup(),
        )
        return

    if route == "rlc":
        _clear_pending_admin_input(query.from_user.id)
        chat_id = _safe_int(args[0] if args else None)
        if chat_id is None:
            admin_groups = await activity_repo.list_user_admin_chats(user_id=query.from_user.id)
            user_groups = await activity_repo.list_user_activity_chats(user_id=query.from_user.id, limit=100)
            miniapp_url = _build_miniapp_url(settings)
            desktop_url = _build_web_panel_url(settings)
            await _edit_or_answer(
                query,
                "Ввод отменён.",
                _build_home_keyboard(
                    has_admin_groups=bool(admin_groups),
                    has_user_groups=bool(user_groups),
                    miniapp_url=miniapp_url,
                    desktop_url=desktop_url,
                ),
            )
            return
        await _edit_or_answer(query, await _render_roles_text(activity_repo, chat_id=chat_id), _build_roles_keyboard(chat_id))
        return

    if route == "as":
        chat_id = _safe_int(args[0] if len(args) >= 1 else None)
        page = max(0, _safe_int(args[1] if len(args) >= 2 else "0") or 0)
        if chat_id is None:
            await query.answer("Некорректный чат", show_alert=True)
            return
        if not await _ensure_manage_settings(activity_repo, user=query.from_user, chat_id=chat_id):
            await query.answer("Недостаточно прав", show_alert=True)
            return
        current, defaults = await _load_chat_settings(activity_repo, settings, chat_id=chat_id)
        text = (
            f"<b>Настройки группы</b>\nID: <code>{chat_id}</code>\n\n"
            f"{render_settings_compact(current, defaults)}"
        )
        await _edit_or_answer(query, text, _build_settings_keys_keyboard(chat_id=chat_id, page=page))
        return

    if route == "ae":
        chat_id = _safe_int(args[0] if len(args) >= 1 else None)
        key = _cfg_key_by_index(args[1] if len(args) >= 2 else None)
        if chat_id is None or key is None:
            await query.answer("Некорректные параметры", show_alert=True)
            return
        if not await _ensure_manage_settings(activity_repo, user=query.from_user, chat_id=chat_id):
            await query.answer("Недостаточно прав", show_alert=True)
            return
        current, _ = await _load_chat_settings(activity_repo, settings, chat_id=chat_id)
        current_map = settings_to_dict(current)
        key_idx = _cfg_index(key)
        text = render_setting_editor_text(chat_id=chat_id, key=key, current_value=current_map[key])
        await _edit_or_answer(
            query,
            text,
            _build_setting_editor_keyboard(
                chat_id=chat_id,
                key=key,
                key_idx=key_idx,
                current_value=current_map[key],
            ),
        )
        return

    if route == "av":
        chat_id = _safe_int(args[0] if len(args) >= 1 else None)
        key = _cfg_key_by_index(args[1] if len(args) >= 2 else None)
        short_value = args[2] if len(args) >= 3 else None
        if chat_id is None or key is None or short_value is None:
            await query.answer("Некорректные параметры", show_alert=True)
            return
        if not await _ensure_manage_settings(activity_repo, user=query.from_user, chat_id=chat_id):
            await query.answer("Недостаточно прав", show_alert=True)
            return

        raw_value = _short_to_value(key, short_value)
        if raw_value is None:
            await query.answer("Неверное значение", show_alert=True)
            return

        current, defaults = await _load_chat_settings(activity_repo, settings, chat_id=chat_id)
        updated_values, error = apply_setting_update(
            key=key,
            raw_value=raw_value,
            current=settings_to_dict(current),
            defaults=settings_to_dict(defaults),
        )
        if error is not None or updated_values is None:
            await query.answer(error or "Не удалось изменить настройку", show_alert=True)
            return

        chat_overview = await _resolve_admin_chat_overview(
            activity_repo,
            user_id=query.from_user.id,
            chat_id=chat_id,
        )
        chat_type = chat_overview.chat_type if chat_overview is not None else "group"
        chat_title = chat_overview.chat_title if chat_overview is not None else None
        await activity_repo.upsert_chat_settings(
            chat=ChatSnapshot(
                telegram_chat_id=chat_id,
                chat_type=chat_type,
                title=chat_title,
            ),
            values=updated_values,
        )

        refreshed, _ = await _load_chat_settings(activity_repo, settings, chat_id=chat_id)
        refreshed_map = settings_to_dict(refreshed)
        key_idx = _cfg_index(key)
        text = render_setting_editor_text(chat_id=chat_id, key=key, current_value=refreshed_map[key])
        await _edit_or_answer(
            query,
            text,
            _build_setting_editor_keyboard(
                chat_id=chat_id,
                key=key,
                key_idx=key_idx,
                current_value=refreshed_map[key],
            ),
        )
        return

    if route == "ai":
        chat_id = _safe_int(args[0] if len(args) >= 1 else None)
        key = _cfg_key_by_index(args[1] if len(args) >= 2 else None)
        if chat_id is None or key is None:
            await query.answer("Некорректные параметры", show_alert=True)
            return
        if not await _ensure_manage_settings(activity_repo, user=query.from_user, chat_id=chat_id):
            await query.answer("Недостаточно прав", show_alert=True)
            return
        _set_pending_cfg_input(user_id=query.from_user.id, chat_id=chat_id, key=key)
        text = (
            "<b>Ожидаю ввод значения</b>\n"
            f"Группа: <code>{chat_id}</code>\n"
            f"Ключ: <code>{key}</code>\n"
            "Отправьте следующее сообщение в ЛС.\n"
            "Для отмены: <code>/cancel</code>."
        )
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="❌ Отмена ввода", callback_data=encode_pm_callback("ac"))
        keyboard.button(text="⬅️ К ключу", callback_data=encode_pm_callback("ae", chat_id, _cfg_index(key)))
        keyboard.adjust(1)
        await _edit_or_answer(query, text, keyboard.as_markup())
        return

    if route == "ac":
        _clear_pending_cfg_input(query.from_user.id)
        admin_groups = await activity_repo.list_user_admin_chats(user_id=query.from_user.id)
        user_groups = await activity_repo.list_user_activity_chats(user_id=query.from_user.id, limit=100)
        miniapp_url = _build_miniapp_url(settings)
        desktop_url = _build_web_panel_url(settings)
        await _edit_or_answer(
            query,
            "Ввод значения отменён.",
            _build_home_keyboard(
                has_admin_groups=bool(admin_groups),
                has_user_groups=bool(user_groups),
                miniapp_url=miniapp_url,
                desktop_url=desktop_url,
            ),
        )
        return

    if route == "ul":
        page = max(0, _safe_int(args[0] if args else "0") or 0)
        groups = await activity_repo.list_user_activity_chats(user_id=query.from_user.id, limit=100)
        if not groups:
            admin_groups = await activity_repo.list_user_admin_chats(user_id=query.from_user.id)
            miniapp_url = _build_miniapp_url(settings)
            desktop_url = _build_web_panel_url(settings)
            await _edit_or_answer(
                query,
                "Нет групп в истории активности. Напишите что-нибудь в группе с ботом.",
                _build_home_keyboard(
                    has_admin_groups=bool(admin_groups),
                    has_user_groups=False,
                    miniapp_url=miniapp_url,
                    desktop_url=desktop_url,
                ),
            )
            return
        await _edit_or_answer(
            query,
            "<b>Ваши группы по активности</b>\nВыберите группу для просмотра личной статистики.",
            _build_groups_keyboard(route_prefix="u", groups=groups, page=page),
        )
        return

    if route in {"ug", "ur", "ue"}:
        chat_id = _safe_int(args[0] if args else None)
        if chat_id is None:
            await query.answer("Некорректный чат", show_alert=True)
            return
        groups = await activity_repo.list_user_activity_chats(user_id=query.from_user.id, limit=200)
        selected = next((item for item in groups if item.chat_id == chat_id), None)
        if selected is None:
            await query.answer("Группа не найдена в вашей активности", show_alert=True)
            return

        await economy_repo.set_private_chat_context(user_id=query.from_user.id, chat_id=chat_id)
        if route == "ue":
            dashboard, error = await get_dashboard(
                economy_repo,
                economy_mode="local",
                chat_id=chat_id,
                user_id=query.from_user.id,
            )
            if dashboard is None:
                await query.message.answer(error or "Не удалось открыть экономику")
            else:
                await query.message.answer(_dashboard_text(dashboard), parse_mode="HTML")

        text = await _render_user_group_text(
            activity_repo=activity_repo,
            economy_repo=economy_repo,
            settings=settings,
            user=query.from_user,
            chat=selected,
        )
        await _edit_or_answer(query, text, _build_user_group_keyboard(chat_id))
        return

    await query.answer("Неизвестное действие", show_alert=False)


@router.message(PendingCfgInputFilter())
async def pending_cfg_input_handler(message: Message, activity_repo, settings: Settings) -> None:
    if message.from_user is None:
        return
    pending = _get_pending_cfg_input(message.from_user.id)
    if pending is None:
        return

    text = (message.text or "").strip()
    if text.lower() in {"/cancel", "cancel", "отмена"}:
        _clear_pending_cfg_input(message.from_user.id)
        await message.answer("Ввод отменён.")
        return
    if not text:
        await message.answer("Пустое значение. Введите число/значение или /cancel.")
        return

    allowed = await _ensure_manage_settings(activity_repo, user=message.from_user, chat_id=pending.chat_id)
    if not allowed:
        _clear_pending_cfg_input(message.from_user.id)
        await message.answer("Недостаточно прав для изменения настроек этой группы.")
        return

    current, defaults = await _load_chat_settings(activity_repo, settings, chat_id=pending.chat_id)
    updated_values, error = apply_setting_update(
        key=pending.key,
        raw_value=text,
        current=settings_to_dict(current),
        defaults=settings_to_dict(defaults),
    )
    if error is not None or updated_values is None:
        await message.answer(f"Ошибка: {error or 'не удалось применить значение'}")
        await message.answer("Повторите ввод или отправьте /cancel.")
        return

    chat_overview = await _resolve_admin_chat_overview(
        activity_repo,
        user_id=message.from_user.id,
        chat_id=pending.chat_id,
    )
    chat_type = chat_overview.chat_type if chat_overview is not None else "group"
    chat_title = chat_overview.chat_title if chat_overview is not None else None
    await activity_repo.upsert_chat_settings(
        chat=ChatSnapshot(
            telegram_chat_id=pending.chat_id,
            chat_type=chat_type,
            title=chat_title,
        ),
        values=updated_values,
    )
    _clear_pending_cfg_input(message.from_user.id)

    refreshed, refreshed_defaults = await _load_chat_settings(activity_repo, settings, chat_id=pending.chat_id)
    await message.answer(
        (
            f"Сохранено: <b>{escape(setting_title_ru(pending.key))}</b>\n"
            f"Ключ: <code>{pending.key}</code>\n"
            f"Значение: <code>{escape(str(settings_to_dict(refreshed)[pending.key]))}</code>\n\n"
            f"{render_settings_compact(refreshed, refreshed_defaults)}"
        ),
        parse_mode="HTML",
        reply_markup=_build_settings_keys_keyboard(chat_id=pending.chat_id, page=_cfg_index(pending.key) // _CFG_PAGE_SIZE),
    )


@router.message(PendingAdminInputFilter())
async def pending_admin_input_handler(message: Message, activity_repo) -> None:
    if message.from_user is None:
        return
    pending = _get_pending_admin_input(message.from_user.id)
    if pending is None:
        return

    text = (message.text or "").strip()
    if text.lower() in {"/cancel", "cancel", "отмена"}:
        _clear_pending_admin_input(message.from_user.id)
        await message.answer("Ввод отменён.")
        return
    if not text:
        await message.answer("Пустой ввод. Отправьте правило или /cancel.")
        return

    if pending.mode == "command_rank":
        allowed = await _ensure_manage_command_access(activity_repo, user=message.from_user, chat_id=pending.chat_id)
        if not allowed:
            _clear_pending_admin_input(message.from_user.id)
            await message.answer("Недостаточно прав для настройки рангов команд этой группы.")
            return

        phrase = parse_command_rank_phrase(text)
        if phrase is None:
            await message.answer(
                'Неверный формат. Пример: <code>установить "топ" ранг внутри бота совладелец</code>',
                parse_mode="HTML",
            )
            return

        command_key = resolve_command_key_input(phrase.command_input)
        if command_key is None:
            normalized_input = normalize_text_command(phrase.command_input)
            if normalized_input:
                aliases = await activity_repo.list_chat_aliases(chat_id=pending.chat_id)
                for alias in aliases:
                    if alias.alias_text_norm == normalized_input or alias.source_trigger_norm == normalized_input:
                        command_key = alias.command_key
                        break
        if command_key is None:
            await message.answer("Не удалось распознать команду.")
            return

        if phrase.reset:
            removed = await activity_repo.remove_command_access_rule(chat_id=pending.chat_id, command_key=command_key)
            response = (
                f'Ограничение ранга снято для <code>{escape(command_key)}</code>.'
                if removed
                else f'Для <code>{escape(command_key)}</code> отдельный ранг не был установлен.'
            )
            await message.answer(response, parse_mode="HTML")
            await message.answer(
                await _render_command_ranks_text(activity_repo, chat_id=pending.chat_id),
                parse_mode="HTML",
                reply_markup=_build_command_ranks_keyboard(pending.chat_id),
            )
            return

        role_input = phrase.role_input or ""
        role = await activity_repo.resolve_chat_role_definition(chat_id=pending.chat_id, token=role_input)
        if role is None:
            await message.answer("Неизвестная роль.")
            return

        chat_overview = await _resolve_admin_chat_overview(
            activity_repo,
            user_id=message.from_user.id,
            chat_id=pending.chat_id,
        )
        chat_type = chat_overview.chat_type if chat_overview is not None else "group"
        chat_title = chat_overview.chat_title if chat_overview is not None else None
        try:
            await activity_repo.upsert_command_access_rule(
                chat=ChatSnapshot(telegram_chat_id=pending.chat_id, chat_type=chat_type, title=chat_title),
                command_key=command_key,
                min_role_token=role.role_code,
                updated_by_user_id=message.from_user.id,
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return

        await message.answer(
            (
                f'Установлено: <code>{escape(command_key)}</code> → <code>{escape(role.title_ru)}</code>\n\n'
                f"{await _render_command_ranks_text(activity_repo, chat_id=pending.chat_id)}"
            ),
            parse_mode="HTML",
            reply_markup=_build_command_ranks_keyboard(pending.chat_id),
        )
        return

    if pending.mode == "roles":
        allowed = await _ensure_manage_role_templates(activity_repo, user=message.from_user, chat_id=pending.chat_id)
        if not allowed:
            _clear_pending_admin_input(message.from_user.id)
            await message.answer("Недостаточно прав для управления ролями этой группы.")
            return

        chat_overview = await _resolve_admin_chat_overview(
            activity_repo,
            user_id=message.from_user.id,
            chat_id=pending.chat_id,
        )
        chat_type = chat_overview.chat_type if chat_overview is not None else "group"
        chat_title = chat_overview.chat_title if chat_overview is not None else None
        chat_snapshot = ChatSnapshot(
            telegram_chat_id=pending.chat_id,
            chat_type=chat_type,
            title=chat_title,
        )
        try:
            response = await _apply_private_role_command(
                activity_repo,
                chat_snapshot=chat_snapshot,
                text=text,
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer(
            response,
            parse_mode="HTML",
            reply_markup=_build_roles_keyboard(pending.chat_id),
        )
        return

    _clear_pending_admin_input(message.from_user.id)
    await message.answer("Режим ввода истёк. Откройте панель заново.")
