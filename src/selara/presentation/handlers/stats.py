from html import escape

import asyncio
from dataclasses import dataclass
from functools import partial
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.exc import SQLAlchemyError

from selara.application.achievements import get_achievement_catalog_from_settings
from selara.application.use_cases.get_last_seen import execute as get_last_seen
from selara.application.use_cases.get_my_stats import execute as get_my_stats
from selara.application.use_cases.get_rep_stats import execute as get_rep_stats
from selara.application.use_cases.get_top_users import execute as get_top_users
from selara.application.use_cases.iris_import import (
    IrisProfileImportData,
    parse_forwarded_awards_message,
    parse_forwarded_profile_message,
    strip_iris_award_prefix,
)
from selara.core.roles import SYSTEM_ROLE_BY_CODE
from selara.core.chat_settings import ChatSettings
from selara.core.config import Settings
from selara.core.timezone import to_timezone
from selara.domain.entities import ActivityStats, AchievementView, ChatSnapshot, LeaderboardMode, LeaderboardPeriod, UserSnapshot
from selara.domain.value_objects import display_name_from_parts
from selara.presentation.audit import log_chat_action
from selara.presentation.auth import get_actor_role_definition
from selara.presentation.charts import build_daily_activity_chart, build_leaderboard_chart, build_profile_chart
from selara.presentation.db_recovery import safe_rollback
from selara.presentation.formatters import (
    format_activity_pulse_line,
    format_elapsed_compact,
    format_last_seen,
    format_leaderboard,
    format_profile_karma_line,
    format_me,
    format_profile_positions_line,
    format_rep_stats,
    format_user_link,
    preferred_mention_label_from_parts,
)
from selara.presentation.handlers.activity import format_user_label, resolve_last_seen_target

router = Router(name="stats")

_CAPTION_LIMIT_SAFE = 1000
_ME_DAILY_CHART_DAYS = 14
_PROFILE_DESCRIPTION_MAX_LEN = 280
_AWARD_TITLE_MAX_LEN = 160
_PROFILE_AWARDS_LIMIT = 20
_PROFILE_CALLBACK_PREFIX = "profile"
_ACTIVITY_TOP_PERIOD_HELP = "Формат: /top <неделя|сутки|час|месяц> [N]"
_IRIS_IMPORT_TTL = timedelta(minutes=15)
_IRIS_SOURCE_BOT_USERNAME = "iris_moon_bot"
_AWARD_MIN_ACTOR_RANK = SYSTEM_ROLE_BY_CODE["junior_admin"].rank
_IRIS_IMPORT_ALLOWED_ROLES = {"owner", "co_owner", "senior_admin"}
_INACTIVE_THRESHOLD = timedelta(days=1)
_INACTIVE_HEADER_VARIANTS: tuple[str, ...] = (
    "✖️ <b>Кто в чате не писал больше суток:</b>",
    "✖️ <b>Список тех, кто молчит в чате уже дольше дня:</b>",
    "✖️ <b>Кого чат не видел в сообщениях больше 24 часов:</b>",
    "✖️ <b>Неактив чата старше одних суток:</b>",
)
_INACTIVE_EMPTY_VARIANTS: tuple[str, ...] = (
    "✅ <b>За последние сутки в чате все успели написать хотя бы одно сообщение.</b>",
    "✅ <b>Неактива старше суток сейчас нет.</b>",
    "✅ <b>Все участники были активны в течение последних 24 часов.</b>",
)
_INACTIVE_CONTINUATION_TITLE = "✖️ <b>Продолжение списка неактива:</b>"
_ACTIVITY_TOP_PERIOD_ALIASES: dict[str, LeaderboardPeriod] = {
    "week": "week",
    "неделя": "week",
    "day": "day",
    "день": "day",
    "сутки": "day",
    "час": "hour",
    "hour": "hour",
    "месяц": "month",
    "month": "month",
}


@dataclass
class _PendingIrisImportSession:
    source_chat_id: int
    source_chat_type: str
    source_chat_title: str | None
    target_user_id: int
    target_username: str
    target_label: str
    target_first_name: str | None
    target_last_name: str | None
    target_chat_display_name: str | None
    actor_user_id: int
    step: str
    expires_at: datetime
    profile_data: IrisProfileImportData | None = None
    profile_text: str | None = None


_pending_iris_imports: dict[int, _PendingIrisImportSession] = {}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _cleanup_pending_iris_imports(*, exclude_user_id: int | None = None) -> None:
    now = _now_utc()
    for user_id, session in list(_pending_iris_imports.items()):
        if user_id == exclude_user_id:
            continue
        if session.expires_at <= now:
            _pending_iris_imports.pop(user_id, None)


def _get_pending_iris_import(user_id: int) -> _PendingIrisImportSession | None:
    _cleanup_pending_iris_imports(exclude_user_id=user_id)
    return _pending_iris_imports.get(user_id)


def _set_pending_iris_import(*, importer_user_id: int, session: _PendingIrisImportSession) -> None:
    _cleanup_pending_iris_imports(exclude_user_id=importer_user_id)
    _pending_iris_imports[importer_user_id] = session


def _clear_pending_iris_import(user_id: int) -> None:
    _pending_iris_imports.pop(user_id, None)


def _is_pending_iris_import_expired(session: _PendingIrisImportSession, *, now: datetime | None = None) -> bool:
    return session.expires_at <= (now or _now_utc())


def _normalize_username(value: str | None) -> str | None:
    normalized = (value or "").strip().lstrip("@").lower()
    return normalized or None


def _is_iris_import_manager_role(role_code: str | None) -> bool:
    return (role_code or "").strip().lower() in _IRIS_IMPORT_ALLOWED_ROLES


def _can_start_iris_import(*, actor_user_id: int, target_user_id: int, role_code: str | None) -> bool:
    if int(actor_user_id) == int(target_user_id):
        return True
    return _is_iris_import_manager_role(role_code)


def _is_forwarded_message(message: Message) -> bool:
    return any(
        getattr(message, attr, None) is not None
        for attr in ("forward_origin", "forward_from", "forward_from_chat", "forward_sender_name")
    )


def _resolve_forwarded_bot_username(message: Message) -> str | None:
    forward_origin = getattr(message, "forward_origin", None)
    if forward_origin is not None:
        sender_user = getattr(forward_origin, "sender_user", None)
        if sender_user is not None and getattr(sender_user, "is_bot", False):
            return _normalize_username(getattr(sender_user, "username", None))
        sender_chat = getattr(forward_origin, "chat", None)
        if sender_chat is not None:
            return _normalize_username(getattr(sender_chat, "username", None))

    forward_from = getattr(message, "forward_from", None)
    if forward_from is not None and getattr(forward_from, "is_bot", False):
        return _normalize_username(getattr(forward_from, "username", None))

    forward_from_chat = getattr(message, "forward_from_chat", None)
    if forward_from_chat is not None:
        return _normalize_username(getattr(forward_from_chat, "username", None))

    return None


def _validate_iris_forward_source(message: Message) -> str | None:
    if not _is_forwarded_message(message):
        return f"Нужно переслать сюда именно ответ от @{_IRIS_SOURCE_BOT_USERNAME}, а не копию текста."

    forwarded_username = _resolve_forwarded_bot_username(message)
    if forwarded_username != _IRIS_SOURCE_BOT_USERNAME:
        return f"Нужен пересланный ответ именно от @{_IRIS_SOURCE_BOT_USERNAME}."
    return None


def _detect_iris_message_kind(text: str) -> str | None:
    body = (text or "").strip().lower()
    if not body:
        return None
    if body.startswith("🏆 награды") or "\n1." in body:
        return "awards"
    if "первое появление:" in body or "актив (д|н|м|весь):" in body or "ранг:" in body:
        return "profile"
    return None


def _validate_iris_message_step(*, expected_step: str, text: str, target_username: str) -> str | None:
    detected = _detect_iris_message_kind(text)
    if detected is None or detected == expected_step:
        return None
    if expected_step == "profile":
        return (
            f"Сейчас нужен первый ответ Iris на команду <code>кто ты @{escape(target_username)}</code>. "
            f"Перешлите сначала карточку профиля."
        )
    return (
        f"Сейчас нужен второй ответ Iris на команду <code>награды @{escape(target_username)}</code>. "
        f"Перешлите список наград."
    )


def _validate_iris_target_username(*, expected_username: str, actual_username: str) -> str | None:
    if _normalize_username(actual_username) == _normalize_username(expected_username):
        return None
    return (
        f"Этот ответ Iris относится не к @{escape(expected_username)}. "
        "Проверьте, что пересылаете карточку именно нужного пользователя."
    )


def _format_iris_import_date(value: datetime, timezone_name: str, *, include_time: bool) -> str:
    local_value = to_timezone(value, timezone_name)
    return local_value.strftime("%d.%m.%Y %H:%M" if include_time else "%d.%m.%Y")


def _format_iris_already_imported_message(*, state, timezone_name: str, is_self: bool) -> str:
    imported_at_text = _format_iris_import_date(state.imported_at, timezone_name, include_time=True)
    variants = (
        (
            "Не нужно, вы уже перенесли себя {date}.",
            "Повторный перенос не требуется: импорт уже был {date}.",
            "Профиль из Iris уже переносили {date}, второй раз делать это не нужно.",
        )
        if is_self
        else (
            "Этот пользователь уже перенесён из Iris {date}.",
            "Повторный перенос не нужен: профиль уже импортирован {date}.",
            "Импорт для этого пользователя уже был выполнен {date}.",
        )
    )
    index = abs(int(state.chat_id) + int(state.user_id) + int(state.imported_at.day)) % len(variants)
    return variants[index].format(date=imported_at_text)


def _build_iris_import_intro(*, session: _PendingIrisImportSession) -> str:
    chat_title = escape((session.source_chat_title or "").strip() or f"chat:{session.source_chat_id}")
    target_username = escape(session.target_username)
    target_label = escape(session.target_label)
    return (
        f"<b>Перенос из Iris</b>\n"
        f"Чат: <b>{chat_title}</b>\n"
        f"Кого переносим: <b>{target_label}</b> (@{target_username})\n\n"
        f"1. В той же группе отправьте Iris команду <code>кто ты @{target_username}</code>.\n"
        f"2. Перешлите сюда ответ от @{_IRIS_SOURCE_BOT_USERNAME}.\n\n"
        "После этого я попрошу второй ответ с наградами. "
        f"Сессия активна {int(_IRIS_IMPORT_TTL.total_seconds() // 60)} минут."
    )


def _build_iris_awards_step_prompt(*, session: _PendingIrisImportSession) -> str:
    return (
        "Первый шаг принят.\n\n"
        f"Теперь отправьте в группе Iris команду <code>награды @{escape(session.target_username)}</code> "
        f"и перешлите сюда ответ от @{_IRIS_SOURCE_BOT_USERNAME}."
    )


def _build_iris_unrelated_message_text() -> str:
    return "Это не то сообщение для переноса. Если хотите отменить перенос, отправьте /cancel или «отмена»."


def _build_iris_import_success_text(
    *,
    session: _PendingIrisImportSession,
    profile: IrisProfileImportData,
    awards_count: int,
    imported_at: datetime,
    timezone_name: str,
) -> str:
    return (
        f"<b>Перенос завершён</b>\n"
        f"Чат: <b>{escape((session.source_chat_title or '').strip() or f'chat:{session.source_chat_id}')}</b>\n"
        f"Пользователь: <b>{escape(session.target_label)}</b> (@{escape(session.target_username)})\n"
        f"Карма Iris: <b>{profile.karma_all_time}</b>\n"
        f"Первое появление: <b>{_format_iris_import_date(profile.first_seen_at, timezone_name, include_time=False)}</b>\n"
        f"Актив (1д | 7д | 30д | всё): <b>{profile.activity_1d} | {profile.activity_7d} | {profile.activity_30d} | {profile.activity_all}</b>\n"
        f"Наград перенесено: <b>{awards_count}</b>\n"
        f"Дата переноса: <b>{_format_iris_import_date(imported_at, timezone_name, include_time=True)}</b>"
    )


def _message_text_and_entities(message: Message) -> tuple[str, tuple]:
    if message.text:
        return message.text, tuple(message.entities or ())
    if message.caption:
        return message.caption, tuple(message.caption_entities or ())
    return "", ()


def _extract_linked_user_label_from_message(message: Message | None, *, user_id: int) -> str | None:
    if message is None:
        return None

    text, entities = _message_text_and_entities(message)
    if not text or not entities:
        return None

    for entity in entities:
        entity_type = str(getattr(entity, "type", "") or "").lower()
        if entity_type == "text_mention":
            entity_user = getattr(entity, "user", None)
            if entity_user is None or int(getattr(entity_user, "id", 0) or 0) != int(user_id):
                continue
        elif entity_type == "text_link":
            parsed = urlparse(str(getattr(entity, "url", "") or ""))
            if parsed.scheme != "tg" or parsed.netloc != "user":
                continue
            linked_user_id = (parse_qs(parsed.query).get("id") or [None])[0]
            if linked_user_id is None or not str(linked_user_id).lstrip("-").isdigit():
                continue
            if int(linked_user_id) != int(user_id):
                continue
        else:
            continue

        offset = int(getattr(entity, "offset", 0) or 0)
        length = int(getattr(entity, "length", 0) or 0)
        if length <= 0:
            continue
        label = text[offset : offset + length].strip()
        if label:
            return label

    return None


class PendingIrisImportFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        if message.chat.type != "private" or message.from_user is None:
            return False
        _cleanup_pending_iris_imports(exclude_user_id=message.from_user.id)
        return message.from_user.id in _pending_iris_imports


def _group_status_label(status: str) -> str:
    mapping = {
        "creator": "владелец группы",
        "administrator": "администратор",
        "member": "участник",
        "restricted": "ограничен",
        "left": "вышел",
        "kicked": "заблокирован",
    }
    return mapping.get(status, status)


def _relationship_partner_id(*, user_id: int, user_low_id: int, user_high_id: int) -> int:
    return user_high_id if user_low_id == user_id else user_low_id


async def _resolve_profile_label(
    activity_repo,
    *,
    chat_id: int,
    user_id: int,
    cache: dict[int, str],
    fallback_user: UserSnapshot | None = None,
) -> str:
    cached = cache.get(user_id)
    if cached is not None:
        return cached

    display_name = await activity_repo.get_chat_display_name(chat_id=chat_id, user_id=user_id)
    if display_name:
        cache[user_id] = display_name
        return display_name

    user = fallback_user
    if user is None:
        user = await activity_repo.get_user_snapshot(user_id=user_id)
    if user is not None:
        label = preferred_mention_label_from_parts(
            user_id=user.telegram_user_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            chat_display_name=user.chat_display_name,
        )
    else:
        label = f"user:{user_id}"

    cache[user_id] = label
    return label


async def _resolve_profile_mention(
    activity_repo,
    *,
    chat_id: int,
    user_id: int,
    cache: dict[int, str],
    fallback_user: UserSnapshot | None = None,
) -> str:
    cached = cache.get(user_id)
    if cached is not None:
        return cached

    label = await _resolve_profile_label(
        activity_repo,
        chat_id=chat_id,
        user_id=user_id,
        cache={},
        fallback_user=fallback_user,
    )
    mention = format_user_link(user_id=user_id, label=label)
    cache[user_id] = mention
    return mention


def _join_profile_sections(sections: list[str]) -> str:
    return "\n\n".join(section for section in sections if section.strip())


def _pick_variant(*, values: tuple[str, ...], seed: int) -> str:
    if not values:
        return ""
    return values[abs(seed) % len(values)]


def _ru_plural(value: int, one: str, few: str, many: str) -> str:
    mod10 = value % 10
    mod100 = value % 100
    if mod10 == 1 and mod100 != 11:
        return one
    if 2 <= mod10 <= 4 and not 12 <= mod100 <= 14:
        return few
    return many


def _format_inactive_duration(*, last_seen_at: datetime, now: datetime) -> str:
    total_seconds = max(0, int((now - last_seen_at).total_seconds()))
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60

    parts: list[str] = []
    if days:
        parts.append(f"{days} {_ru_plural(days, 'день', 'дня', 'дней')}")
    if hours:
        parts.append(f"{hours} ч")
    elif not days and minutes:
        parts.append(f"{minutes} мин")
    return " ".join(parts) or "меньше минуты"


def _inactive_member_label(item: ActivityStats) -> str:
    label = preferred_mention_label_from_parts(
        user_id=item.user_id,
        username=item.username,
        first_name=item.first_name,
        last_name=item.last_name,
        chat_display_name=item.chat_display_name,
    )
    if label == f"user:{item.user_id}":
        return "Неизвестный"
    return label


def _build_inactive_members_messages(
    *,
    chat_id: int,
    members: list[ActivityStats],
    now: datetime,
) -> list[str]:
    seed = abs(int(chat_id)) + now.date().toordinal() + len(members)
    if not members:
        return [_pick_variant(values=_INACTIVE_EMPTY_VARIANTS, seed=seed)]

    header = _pick_variant(values=_INACTIVE_HEADER_VARIANTS, seed=seed)
    footer = f"🗓 <b>Всего неактивных:</b> {len(members)}"
    chunks: list[str] = []
    current_lines = [header]
    current_len = len(header)

    for index, item in enumerate(members, start=1):
        mention = format_user_link(user_id=item.user_id, label=_inactive_member_label(item))
        inactive_for = escape(_format_inactive_duration(last_seen_at=item.last_seen_at, now=now))
        line = f"{index}. {mention} (<code>{inactive_for}</code>)"
        extra_len = len(line) + 1
        footer_len = len(footer) + 2 if index == len(members) else 0
        if current_lines and current_len + extra_len + footer_len > 3900:
            chunks.append("\n".join(current_lines))
            current_lines = [_INACTIVE_CONTINUATION_TITLE]
            current_len = len(_INACTIVE_CONTINUATION_TITLE)
        current_lines.append(line)
        current_len += extra_len

    current_lines.append("")
    current_lines.append(footer)
    chunks.append("\n".join(current_lines))
    return chunks


def _profile_callback_data(*, action: str, user_id: int) -> str:
    return f"{_PROFILE_CALLBACK_PREFIX}:{action}:{user_id}"


def _build_profile_actions_markup(*, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Ачивки",
                    callback_data=_profile_callback_data(action="achievements", user_id=user_id),
                ),
                InlineKeyboardButton(
                    text="Награды",
                    callback_data=_profile_callback_data(action="awards", user_id=user_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="О себе",
                    callback_data=_profile_callback_data(action="about", user_id=user_id),
                ),
            ],
        ]
    )


def _parse_profile_callback_data(data: str | None) -> tuple[str | None, int | None]:
    if data is None or not data.startswith(f"{_PROFILE_CALLBACK_PREFIX}:"):
        return None, None
    parts = data.split(":")
    if len(parts) != 3:
        return None, None
    _, action, raw_user_id = parts
    if action not in {"achievements", "awards", "about"} or not raw_user_id.lstrip("-").isdigit():
        return None, None
    return action, int(raw_user_id)


def _mask_hidden_achievement(view: AchievementView) -> AchievementView:
    if view.awarded or not view.hidden:
        return view
    return AchievementView(
        achievement_id=view.achievement_id,
        scope=view.scope,
        title="Скрытое достижение",
        description="Описание откроется после получения.",
        icon="???",
        rarity=view.rarity,
        hidden=view.hidden,
        awarded=view.awarded,
        awarded_at=view.awarded_at,
        holders_count=view.holders_count,
        holders_percent=view.holders_percent,
        sort_order=view.sort_order,
    )


async def _build_achievement_views(*, activity_repo, settings: Settings, chat_id: int, user_id: int) -> tuple[list[AchievementView], list[AchievementView]]:
    catalog = get_achievement_catalog_from_settings(settings)
    chat_awards = {item.achievement_id: item for item in await activity_repo.list_user_chat_achievements(chat_id=chat_id, user_id=user_id)}
    global_awards = {item.achievement_id: item for item in await activity_repo.list_user_global_achievements(user_id=user_id)}
    chat_stats = await activity_repo.get_chat_achievement_stats_map(chat_id=chat_id)
    global_stats = await activity_repo.get_global_achievement_stats_map()

    chat_views: list[AchievementView] = []
    for definition in catalog.list_by_scope("chat"):
        award = chat_awards.get(definition.id)
        holders_count, holders_percent = chat_stats.get(definition.id, (0, 0.0))
        chat_views.append(
            _mask_hidden_achievement(
                AchievementView(
                    achievement_id=definition.id,
                    scope=definition.scope,
                    title=definition.title,
                    description=definition.description,
                    icon=definition.icon,
                    rarity=definition.rarity,
                    hidden=definition.hidden,
                    awarded=award is not None,
                    awarded_at=award.awarded_at if award is not None else None,
                    holders_count=holders_count,
                    holders_percent=holders_percent,
                    sort_order=definition.sort_order,
                )
            )
        )

    global_views: list[AchievementView] = []
    for definition in catalog.list_by_scope("global"):
        award = global_awards.get(definition.id)
        holders_count, holders_percent = global_stats.get(definition.id, (0, 0.0))
        global_views.append(
            _mask_hidden_achievement(
                AchievementView(
                    achievement_id=definition.id,
                    scope=definition.scope,
                    title=definition.title,
                    description=definition.description,
                    icon=definition.icon,
                    rarity=definition.rarity,
                    hidden=definition.hidden,
                    awarded=award is not None,
                    awarded_at=award.awarded_at if award is not None else None,
                    holders_count=holders_count,
                    holders_percent=holders_percent,
                    sort_order=definition.sort_order,
                )
            )
        )

    return chat_views, global_views


async def _build_achievements_message(
    *,
    activity_repo,
    settings: Settings,
    chat_id: int,
    user_id: int,
    timezone_name: str,
    fallback_user: UserSnapshot | None = None,
) -> str:
    target_mention = await _resolve_profile_mention(
        activity_repo,
        chat_id=chat_id,
        user_id=user_id,
        cache={},
        fallback_user=fallback_user,
    )
    chat_views, global_views = await _build_achievement_views(
        activity_repo=activity_repo,
        settings=settings,
        chat_id=chat_id,
        user_id=user_id,
    )
    chat_unlocked = sum(1 for item in chat_views if item.awarded)
    global_unlocked = sum(1 for item in global_views if item.awarded)
    total_unlocked = chat_unlocked + global_unlocked
    total_count = len(chat_views) + len(global_views)

    lines = [
        f"🏆 <b>Достижения · {target_mention}</b>",
        (
            f"<blockquote>Открыто: <b>{total_unlocked}/{total_count}</b> • "
            f"чат: <b>{chat_unlocked}/{len(chat_views)}</b> • "
            f"глобал: <b>{global_unlocked}/{len(global_views)}</b></blockquote>"
        ),
    ]

    def _append_section(title: str, items: list[AchievementView], *, empty_text: str) -> None:
        lines.append("")
        lines.append(f"<b>{escape(title)}</b>")
        if not items:
            lines.append(empty_text)
            return

        for item in items:
            if item.awarded and item.awarded_at is not None:
                status_line = f"получено • {escape(format_elapsed_compact(item.awarded_at, timezone_name))}"
            else:
                status_line = "не получено"
            lines.append(f"{escape(item.icon)} <b>{escape(item.title)}</b>")
            lines.append(
                f"└ {status_line} • {item.holders_percent:.2f}% • владельцев: {item.holders_count}"
            )
            lines.append(f"└ {escape(item.description)}")

    _append_section("Чатовые", chat_views, empty_text="Пока нет локальных достижений.")
    _append_section("Глобальные", global_views, empty_text="Пока нет глобальных достижений.")

    if total_count == 0:
        return f"У пользователя <b>{target_mention}</b> пока нет достижений."
    return "\n".join(lines)


async def _refresh_achievements_for_user(
    *,
    activity_repo,
    achievement_orchestrator,
    chat_id: int,
    user_id: int,
) -> None:
    if achievement_orchestrator is None:
        return
    try:
        await achievement_orchestrator.process_refresh(
            chat_id=chat_id,
            user_id=user_id,
            event_at=datetime.now(timezone.utc),
        )
    except SQLAlchemyError:
        await safe_rollback(activity_repo)


async def _resolve_stats_target_user(
    message: Message,
    *,
    command: CommandObject,
    activity_repo,
) -> tuple[UserSnapshot | None, str | None]:
    if message.reply_to_message and message.reply_to_message.from_user is not None:
        reply_user = message.reply_to_message.from_user
        return (
            UserSnapshot(
                telegram_user_id=reply_user.id,
                username=reply_user.username,
                first_name=reply_user.first_name,
                last_name=reply_user.last_name,
                is_bot=bool(reply_user.is_bot),
                chat_display_name=await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=reply_user.id),
            ),
            None,
        )

    raw = (command.args or "").strip()
    if not raw:
        return None, "Не удалось определить пользователя."

    token, _, _tail = raw.partition(" ")
    token = token.strip(" ,.;!?")
    if token.startswith("@"):
        if message.chat.type not in {"group", "supergroup"}:
            return None, "В личке поиск по @username недоступен."
        user = await activity_repo.find_chat_user_by_username(chat_id=message.chat.id, username=token)
        if user is None:
            return None, f"Пользователь {token} не найден в этом чате."
        return user, None

    if token.lstrip("-").isdigit():
        user_id = int(token)
        existing = await activity_repo.get_user_snapshot(user_id=user_id)
        chat_display_name = await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=user_id)
        if existing is not None:
            return (
                UserSnapshot(
                    telegram_user_id=existing.telegram_user_id,
                    username=existing.username,
                    first_name=existing.first_name,
                    last_name=existing.last_name,
                    is_bot=existing.is_bot,
                    chat_display_name=chat_display_name or existing.chat_display_name,
                ),
                None,
            )
        return (
            UserSnapshot(
                telegram_user_id=user_id,
                username=None,
                first_name=None,
                last_name=None,
                is_bot=False,
                chat_display_name=chat_display_name,
            ),
            None,
        )

    return None, "Формат: reply или /команда @username или /команда user_id."

async def _ensure_chat_admin(message: Message, bot: Bot) -> bool:
    if message.from_user is None:
        return False
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    except TelegramNetworkError:
        return False
    except Exception:
        await message.answer("Не удалось проверить права администратора.")
        return False
    if member.status not in {"creator", "administrator"}:
        await message.answer("Награды в этом чате могут выдавать только админы.")
        return False
    return True


async def _build_profile_about_message(
    *,
    activity_repo,
    chat_id: int,
    user_id: int,
    fallback_user: UserSnapshot | None = None,
) -> str:
    target_mention = await _resolve_profile_mention(
        activity_repo,
        chat_id=chat_id,
        user_id=user_id,
        cache={},
        fallback_user=fallback_user,
    )
    profile = await activity_repo.get_user_chat_profile(chat_id=chat_id, user_id=user_id)
    description = " ".join(((profile.description if profile is not None else "") or "").split()).strip()
    if not description:
        return f"У пользователя <b>{target_mention}</b> пока пусто в разделе «О себе»."
    return f"<b>О себе:</b> {target_mention}\n{escape(description)}"


async def _build_profile_awards_message(
    *,
    activity_repo,
    chat_id: int,
    user_id: int,
    timezone_name: str,
    fallback_user: UserSnapshot | None = None,
) -> str:
    target_mention = await _resolve_profile_mention(
        activity_repo,
        chat_id=chat_id,
        user_id=user_id,
        cache={},
        fallback_user=fallback_user,
    )
    awards = await activity_repo.list_user_chat_awards(chat_id=chat_id, user_id=user_id, limit=_PROFILE_AWARDS_LIMIT)
    if not awards:
        return f"У пользователя <b>{target_mention}</b> пока нет наград."

    lines = [f"<b>Награды:</b> {target_mention}"]
    for index, award in enumerate(awards, start=1):
        award_date = _format_iris_import_date(award.created_at, timezone_name, include_time=False)
        lines.append(
            f"{index}. {escape(strip_iris_award_prefix(award.title))} — {escape(award_date)} • "
            f"{escape(format_elapsed_compact(award.created_at, timezone_name))}"
        )
    return "\n".join(lines)


async def set_about_text_command(message: Message, activity_repo, *, about_text: str) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return

    normalized = " ".join((about_text or "").split()).strip()
    if normalized.lower() in {"очистить", "удалить", "сброс"}:
        normalized = ""
    if len(normalized) > _PROFILE_DESCRIPTION_MAX_LEN:
        await message.answer(
            f"Текст «О себе» слишком длинный. Оставьте до <code>{_PROFILE_DESCRIPTION_MAX_LEN}</code> символов.",
            parse_mode="HTML",
        )
        return

    description_value = normalized or None
    await activity_repo.set_user_chat_profile_description(
        chat=ChatSnapshot(message.chat.id, message.chat.type, message.chat.title),
        user=UserSnapshot(
            telegram_user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            is_bot=bool(message.from_user.is_bot),
        ),
        description=description_value,
    )
    await log_chat_action(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        action_code="profile_desc_set" if description_value else "profile_desc_clear",
        description=(
            f"Пользователь {message.from_user.id} обновил описание профиля."
            if description_value
            else f"Пользователь {message.from_user.id} очистил описание профиля."
        ),
        actor_user_id=message.from_user.id,
        meta_json={"length": len(normalized)} if description_value else None,
    )
    if description_value is None:
        await message.answer("Раздел «О себе» очищен.")
        return
    await message.answer("Раздел «О себе» обновлён.")


async def award_reply_text_command(message: Message, activity_repo, bot: Bot, *, title: str) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.reply_to_message is None or message.reply_to_message.from_user is None:
        await message.answer('Нужно сделать reply на сообщение участника и написать <code>наградить текст</code>.', parse_mode="HTML")
        return
    if not await _ensure_chat_admin(message, bot):
        return
    actor_role_definition, _bootstrapped = await get_actor_role_definition(
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
    )
    if actor_role_definition is None or int(actor_role_definition.rank) < _AWARD_MIN_ACTOR_RANK:
        await message.answer("Выдавать награды могут только админы бота с ролью «Мл. админ» и выше.")
        return

    target_user = message.reply_to_message.from_user
    if target_user.id == message.from_user.id:
        await message.answer("Себя награждать нельзя.")
        return
    if bool(target_user.is_bot):
        await message.answer("Ботам награды не выдаются.")
        return

    normalized = " ".join((title or "").split()).strip()
    if not normalized:
        await message.answer('Формат: reply на сообщение и <code>наградить текст награды</code>.', parse_mode="HTML")
        return
    if len(normalized) > _AWARD_TITLE_MAX_LEN:
        await message.answer(
            f"Название награды слишком длинное. До <code>{_AWARD_TITLE_MAX_LEN}</code> символов.",
            parse_mode="HTML",
        )
        return

    target = UserSnapshot(
        telegram_user_id=target_user.id,
        username=target_user.username,
        first_name=target_user.first_name,
        last_name=target_user.last_name,
        is_bot=bool(target_user.is_bot),
        chat_display_name=await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=target_user.id),
    )
    award = await activity_repo.add_user_chat_award(
        chat=ChatSnapshot(message.chat.id, message.chat.type, message.chat.title),
        target=target,
        title=normalized,
        granted_by_user_id=message.from_user.id,
        created_at=datetime.now(timezone.utc),
    )
    target_mention = await _resolve_profile_mention(activity_repo, chat_id=message.chat.id, user_id=target.telegram_user_id, cache={})
    await log_chat_action(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        action_code="profile_award_add",
        description=f"Пользователь {message.from_user.id} выдал награду {target.telegram_user_id}: {normalized}.",
        actor_user_id=message.from_user.id,
        target_user_id=target.telegram_user_id,
        meta_json={"award_id": award.id, "title": normalized},
    )
    await message.answer(
        f"Награда выдана <b>{target_mention}</b>: {escape(normalized)}",
        parse_mode="HTML",
    )


async def _build_profile_social_lines(message: Message, activity_repo, *, user_id: int) -> list[str]:
    lines: list[str] = []
    mention_cache: dict[int, str] = {}

    try:
        title_prefix = await activity_repo.get_chat_title_prefix(chat_id=message.chat.id, user_id=user_id)
    except SQLAlchemyError:
        await safe_rollback(activity_repo)
        title_prefix = None
    if title_prefix:
        lines.append(f"<b>Титул:</b> <code>[{escape(title_prefix)}]</code>")

    try:
        relationship = await activity_repo.get_active_relationship(user_id=user_id, chat_id=message.chat.id)
    except SQLAlchemyError:
        await safe_rollback(activity_repo)
        relationship = None

    try:
        relations = await activity_repo.list_graph_relationships(chat_id=message.chat.id, user_id=user_id)
    except SQLAlchemyError:
        await safe_rollback(activity_repo)
        relations = []

    parents: set[int] = set()
    children: set[int] = set()
    pets: set[int] = set()
    spouse_ids: set[int] = set()

    for item in relations:
        if item.relation_type == "parent":
            if item.user_b == user_id:
                parents.add(item.user_a)
            elif item.user_a == user_id:
                children.add(item.user_b)
        elif item.relation_type == "pet" and item.user_a == user_id:
            pets.add(item.user_b)
        elif item.relation_type == "spouse":
            spouse_ids.add(item.user_b if item.user_a == user_id else item.user_a)

    family_parts: list[str] = []
    if relationship is not None:
        partner_id = _relationship_partner_id(
            user_id=user_id,
            user_low_id=relationship.user_low_id,
            user_high_id=relationship.user_high_id,
        )
        partner_mention = await _resolve_profile_mention(
            activity_repo,
            chat_id=message.chat.id,
            user_id=partner_id,
            cache=mention_cache,
        )
        relation_label = "брак" if relationship.kind == "marriage" else "пара"
        family_parts.append(f"{relation_label} с {partner_mention}")
    elif spouse_ids:
        spouse_id = min(spouse_ids)
        spouse_mention = await _resolve_profile_mention(
            activity_repo,
            chat_id=message.chat.id,
            user_id=spouse_id,
            cache=mention_cache,
        )
        family_parts.append(f"супруг(а) {spouse_mention}")

    if parents:
        family_parts.append(f"родители {len(parents)}")
    if children:
        family_parts.append(f"дети {len(children)}")
    if pets:
        family_parts.append(f"питомцы {len(pets)}")

    if family_parts:
        lines.append(f"<b>Семья:</b> {' • '.join(family_parts)}")

    return lines

async def _build_profile_meta_lines(message: Message, activity_repo, bot: Bot, *, user_id: int) -> list[str]:
    lines: list[str] = []
    group_status: str | None = None
    if message.chat.type in {"group", "supergroup"}:
        try:
            member = await bot.get_chat_member(message.chat.id, user_id)
            group_status = _group_status_label(member.status)
        except Exception:
            pass

    role_title: str | None = None
    role_rank: int | None = None
    try:
        role = await activity_repo.get_bot_role(chat_id=message.chat.id, user_id=user_id)
    except SQLAlchemyError:
        await safe_rollback(activity_repo)
        role = None
    if role is not None:
        role_title = role
        try:
            role_definition = await activity_repo.get_chat_role_definition(chat_id=message.chat.id, role_code=role)
            if role_definition is not None:
                role_title = role_definition.title_ru
                role_rank = role_definition.rank
        except SQLAlchemyError:
            await safe_rollback(activity_repo)
            pass
    status_parts: list[str] = []
    if group_status:
        status_parts.append(group_status)
    if role_title:
        role_part = escape(role_title)
        if role_rank is not None:
            role_part += f" <code>{role_rank}</code>"
        status_parts.append(role_part)
    if status_parts:
        lines.append(f"<b>Статус:</b> {' • '.join(status_parts)}")

    try:
        moderation = await activity_repo.get_moderation_state(chat_id=message.chat.id, user_id=user_id)
    except SQLAlchemyError:
        await safe_rollback(activity_repo)
        moderation = None
    if moderation is not None:
        lines.append(
            f"<b>Мод-статус:</b> преды <code>{moderation.pending_preds}/3</code>, "
            f"варны <code>{moderation.warn_count}/3</code>, "
            f"бан: <code>{'да' if moderation.is_banned else 'нет'}</code>"
        )

    return lines


def parse_leaderboard_request(
    raw_value: str | None,
    *,
    chat_settings: ChatSettings,
    default_mode: LeaderboardMode,
    allow_mode_switch: bool,
) -> tuple[LeaderboardMode | None, int | None, str | None]:
    tokens = [token for token in (raw_value or "").strip().split() if token]
    mode = default_mode

    if allow_mode_switch and tokens:
        mode_aliases: dict[str, LeaderboardMode] = {
            "mix": "mix",
            "hybrid": "mix",
            "гибрид": "mix",
            "activity": "activity",
            "актив": "activity",
            "karma": "karma",
            "карма": "karma",
        }
        candidate = mode_aliases.get(tokens[0].lower())
        if candidate is not None:
            mode = candidate
            tokens = tokens[1:]

    if not tokens:
        return mode, chat_settings.top_limit_default, None
    if len(tokens) > 1:
        return None, None, "Формат: /top [karma|activity] [N] или /active [N]"
    if not tokens[0].isdigit():
        return None, None, "Лимит должен быть числом"

    limit = int(tokens[0])
    if not 1 <= limit <= chat_settings.top_limit_max:
        return None, None, f"Лимит должен быть в диапазоне 1..{chat_settings.top_limit_max}"
    return mode, limit, None


def parse_activity_top_period_request(
    raw_value: str | None,
    *,
    chat_settings: ChatSettings,
) -> tuple[bool, LeaderboardPeriod | None, int | None, str | None]:
    tokens = [token for token in (raw_value or "").strip().split() if token]
    if not tokens:
        return False, None, None, None

    period = _ACTIVITY_TOP_PERIOD_ALIASES.get(tokens[0].lower())
    if period is None:
        return False, None, None, None

    if len(tokens) == 1:
        return True, period, chat_settings.top_limit_default, None

    if len(tokens) > 2:
        return True, None, None, _ACTIVITY_TOP_PERIOD_HELP

    raw_limit = tokens[1]
    if not raw_limit.isdigit():
        return True, None, None, "Лимит должен быть числом"

    limit = int(raw_limit)
    if not 1 <= limit <= chat_settings.top_limit_max:
        return True, None, None, f"Лимит должен быть в диапазоне 1..{chat_settings.top_limit_max}"

    return True, period, limit, None


def _build_leaderboard_keyboard(*, mode: LeaderboardMode, period: LeaderboardPeriod, limit: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    modes: list[tuple[LeaderboardMode, str]] = [
        ("mix", "Гибрид"),
        ("activity", "Актив"),
        ("karma", "Карма"),
    ]
    periods: list[tuple[LeaderboardPeriod, str]] = [("all", "За всё время"), ("7d", "7 дней")]

    for item_mode, title in modes:
        marker = "*" if item_mode == mode else ""
        builder.button(text=f"{title}{marker}", callback_data=f"lb:{item_mode}:{period}:{limit}")
    builder.adjust(3)

    for item_period, title in periods:
        marker = "*" if item_period == period else ""
        builder.button(text=f"{title}{marker}", callback_data=f"lb:{mode}:{item_period}:{limit}")
    builder.adjust(3, 2)
    return builder.as_markup()


def should_include_hybrid_top_keyboard(
    *,
    chat_settings: ChatSettings,
    mode: LeaderboardMode,
    period: LeaderboardPeriod,
) -> bool:
    return bool(
        chat_settings.leaderboard_hybrid_buttons_enabled
        and mode == "mix"
        and period in {"all", "7d"}
    )


async def _send_text_or_photo(
    message: Message,
    *,
    html_text: str,
    chart_bytes: bytes | None,
    filename: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if chart_bytes is not None and len(html_text) <= _CAPTION_LIMIT_SAFE:
        await message.answer_photo(
            BufferedInputFile(chart_bytes, filename=filename),
            caption=html_text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        return

    if chart_bytes is not None:
        await message.answer_photo(BufferedInputFile(chart_bytes, filename=filename))
    await message.answer(html_text, parse_mode="HTML", reply_markup=reply_markup)


async def _build_chart_async(builder, /, **kwargs) -> bytes | None:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(builder, **kwargs))


async def send_user_stats(
    message: Message,
    activity_repo,
    bot: Bot,
    settings: Settings,
    chat_settings: ChatSettings,
    *,
    user_id: int,
) -> None:
    stats = await get_my_stats(repo=activity_repo, chat_id=message.chat.id, user_id=user_id)

    rep = await get_rep_stats(
        repo=activity_repo,
        chat_id=message.chat.id,
        user_id=user_id,
        limit=chat_settings.top_limit_max,
        karma_weight=chat_settings.leaderboard_hybrid_karma_weight,
        activity_weight=chat_settings.leaderboard_hybrid_activity_weight,
        days=chat_settings.leaderboard_7d_days,
    )
    pulse = format_activity_pulse_line(
        day=rep.activity_1d,
        week=rep.activity_7d,
        month=rep.activity_30d,
        all_time=rep.activity_all,
    )
    text = format_me(
        stats,
        timezone_name=settings.bot_timezone,
        fallback_user_id=user_id,
        activity_pulse=pulse,
        user_label_html=await _resolve_profile_mention(activity_repo, chat_id=message.chat.id, user_id=user_id, cache={}),
    )

    social_lines = await _build_profile_social_lines(message, activity_repo, user_id=user_id)
    meta_lines = await _build_profile_meta_lines(message, activity_repo, bot, user_id=user_id)
    extra_section = "\n".join(meta_lines + social_lines) if (meta_lines or social_lines) else None

    text = _join_profile_sections(
        [
            text,
            "\n".join(
                [
                    format_profile_positions_line(rank_all=rep.rank_all, rank_7d=rep.rank_7d),
                    format_profile_karma_line(karma_all=rep.karma_all, karma_7d=rep.karma_7d),
                ]
            ),
            extra_section,
        ]
    )

    daily_series = await activity_repo.get_user_activity_daily_series(
        chat_id=message.chat.id,
        user_id=user_id,
        days=_ME_DAILY_CHART_DAYS,
    )
    chart = await _build_chart_async(
        build_daily_activity_chart,
        points=[(day.strftime("%d.%m"), count) for day, count in daily_series],
    )
    await _send_text_or_photo(
        message,
        html_text=text,
        chart_bytes=chart,
        filename="me_stats.png",
        reply_markup=_build_profile_actions_markup(user_id=user_id),
    )


async def send_me_stats(message: Message, activity_repo, bot: Bot, settings: Settings, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return

    await send_user_stats(
        message,
        activity_repo,
        bot,
        settings,
        chat_settings,
        user_id=message.from_user.id,
    )


async def send_top_stats(
    message: Message,
    activity_repo,
    settings: Settings,
    chat_settings: ChatSettings,
    limit: int,
    mode: LeaderboardMode = "mix",
    period: LeaderboardPeriod = "all",
    include_chart: bool = True,
    include_keyboard: bool = False,
) -> None:
    leaderboard = await get_top_users(
        repo=activity_repo,
        chat_id=message.chat.id,
        limit=limit,
        mode=mode,
        period=period,
        days=chat_settings.leaderboard_7d_days,
        week_start_weekday=chat_settings.leaderboard_week_start_weekday,
        week_start_hour=chat_settings.leaderboard_week_start_hour,
        karma_weight=chat_settings.leaderboard_hybrid_karma_weight,
        activity_weight=chat_settings.leaderboard_hybrid_activity_weight,
    )
    text = format_leaderboard(
        leaderboard,
        mode=mode,
        period=period,
        limit=limit,
        timezone_name=settings.bot_timezone,
    )

    chart = None
    if include_chart:
        chart = await _build_chart_async(build_leaderboard_chart, items=leaderboard, mode=mode)

    keyboard = _build_leaderboard_keyboard(mode=mode, period=period, limit=limit) if include_keyboard else None
    await _send_text_or_photo(
        message,
        html_text=text,
        chart_bytes=chart,
        filename="leaderboard.png",
        reply_markup=keyboard,
    )


async def send_last_seen(
    message: Message,
    activity_repo,
    settings: Settings,
    target_user_id: int | None = None,
    target_label: str | None = None,
) -> None:
    if target_user_id is None:
        target_user_id, user_label = resolve_last_seen_target(message)
    else:
        user_label = target_label or f"user:{target_user_id}"

    if target_user_id is None:
        await message.answer("Не удалось определить пользователя")
        return

    chat_display_name = await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=target_user_id)
    if chat_display_name:
        user_label = chat_display_name

    last_seen_at = await get_last_seen(repo=activity_repo, chat_id=message.chat.id, user_id=target_user_id)
    await message.answer(
        format_last_seen(
            user_label=user_label,
            last_seen_at=last_seen_at,
            timezone_name=settings.bot_timezone,
        ),
        parse_mode="HTML",
    )


async def send_inactive_members(message: Message, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда работает только в группе.")
        return

    now = _now_utc()
    inactive_since = now - _INACTIVE_THRESHOLD
    members = await activity_repo.list_inactive_members(
        chat_id=message.chat.id,
        inactive_since=inactive_since,
    )
    messages = _build_inactive_members_messages(
        chat_id=message.chat.id,
        members=members,
        now=now,
    )
    for chunk in messages:
        await message.answer(
            chunk,
            parse_mode="HTML",
            disable_notification=True,
            disable_web_page_preview=True,
        )


async def _resolve_last_seen_command_target(
    message: Message,
    *,
    command: CommandObject,
    activity_repo,
) -> tuple[int | None, str | None, str | None]:
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        return target.id, format_user_label(target), None

    raw = (command.args or "").strip()
    if not raw:
        if message.from_user is None:
            return None, None, "Не удалось определить пользователя"
        return message.from_user.id, format_user_label(message.from_user), None

    token = raw.split(maxsplit=1)[0].strip(" ,.;!?")
    if token.startswith("@"):
        if message.chat.type not in {"group", "supergroup"}:
            return None, None, "В личке поиск по @username недоступен. Используйте user_id или reply."
        user = await activity_repo.find_chat_user_by_username(chat_id=message.chat.id, username=token)
        if user is None:
            return None, None, f"Пользователь {token} не найден в этом чате."
        label = token
        if user.first_name or user.last_name:
            full_name = " ".join(filter(None, [user.first_name, user.last_name])).strip()
            if full_name:
                label = full_name
        return user.telegram_user_id, label, None

    if token.lstrip("-").isdigit():
        user_id = int(token)
        snapshot = await activity_repo.get_user_snapshot(user_id=user_id)
        if snapshot is not None:
            if snapshot.username:
                label = f"@{snapshot.username}"
            else:
                label = display_name_from_parts(
                    user_id=snapshot.telegram_user_id,
                    username=snapshot.username,
                    first_name=snapshot.first_name,
                    last_name=snapshot.last_name,
                    chat_display_name=snapshot.chat_display_name,
                )
            return user_id, label, None
        return user_id, f"user:{user_id}", None

    return None, None, "Формат: /lastseen [@username|user_id] или reply на сообщение пользователя."


async def send_rep_stats(message: Message, activity_repo, chat_settings: ChatSettings) -> None:
    if message.from_user is None:
        return

    rep = await get_rep_stats(
        repo=activity_repo,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        limit=chat_settings.top_limit_max,
        karma_weight=chat_settings.leaderboard_hybrid_karma_weight,
        activity_weight=chat_settings.leaderboard_hybrid_activity_weight,
        days=chat_settings.leaderboard_7d_days,
    )

    chart = await _build_chart_async(
        build_profile_chart,
        activity_all=rep.activity_all,
        activity_7d=rep.activity_7d,
        karma_all=rep.karma_all,
        karma_7d=rep.karma_7d,
    )
    await _send_text_or_photo(
        message,
        html_text=format_rep_stats(
            rep,
            user_label_html=await _resolve_profile_mention(
                activity_repo,
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                cache={},
            ),
        ),
        chart_bytes=chart,
        filename="rep_stats.png",
    )


@router.message(Command("desc"))
async def desc_command(message: Message) -> None:
    await message.answer(
        'Раздел «О себе» заполняется текстом: <code>добавить о себе "текст"</code>.',
        parse_mode="HTML",
    )


@router.message(Command("award"))
async def award_command(message: Message) -> None:
    await message.answer(
        'Награды выдаются только так: reply на сообщение участника и <code>наградить текст награды</code>.',
        parse_mode="HTML",
    )


@router.message(Command("iris_perenos"))
async def iris_import_command(
    message: Message,
    command: CommandObject,
    activity_repo,
    bot: Bot,
    settings: Settings,
) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return

    if (command.args or "").strip() or message.reply_to_message is not None:
        target, error = await _resolve_stats_target_user(message, command=command, activity_repo=activity_repo)
        if target is None:
            await message.answer(error or "Не удалось определить пользователя.")
            return
    else:
        target = UserSnapshot(
            telegram_user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            is_bot=bool(message.from_user.is_bot),
            chat_display_name=await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=message.from_user.id),
        )

    if target.is_bot:
        await message.answer("Нельзя переносить профиль бота.")
        return

    target_username = _normalize_username(target.username)
    if target_username is None:
        await message.answer("Для переноса из Iris в первой версии у пользователя должен быть актуальный @username.")
        return

    actor_role_code = None
    if int(message.from_user.id) != int(target.telegram_user_id):
        definition, _bootstrapped = await get_actor_role_definition(
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
        )
        actor_role_code = definition.role_code if definition is not None else None
        if not _can_start_iris_import(
            actor_user_id=message.from_user.id,
            target_user_id=target.telegram_user_id,
            role_code=actor_role_code,
        ):
            await message.answer(
                "Переносить чужой профиль из Iris можно только с ролью owner, co_owner или senior_admin."
            )
            return

    try:
        existing_state = await activity_repo.get_user_chat_iris_import_state(
            chat_id=message.chat.id,
            user_id=target.telegram_user_id,
        )
    except SQLAlchemyError:
        await safe_rollback(activity_repo)
        await message.answer("Не удалось подготовить перенос из Iris. Попробуйте позже.")
        return

    if existing_state is not None:
        await message.answer(
            _format_iris_already_imported_message(
                state=existing_state,
                timezone_name=settings.bot_timezone,
                is_self=int(message.from_user.id) == int(target.telegram_user_id),
            )
        )
        return

    target_label = display_name_from_parts(
        user_id=target.telegram_user_id,
        username=target.username,
        first_name=target.first_name,
        last_name=target.last_name,
        chat_display_name=target.chat_display_name,
    )
    session = _PendingIrisImportSession(
        source_chat_id=message.chat.id,
        source_chat_type=message.chat.type,
        source_chat_title=message.chat.title,
        target_user_id=target.telegram_user_id,
        target_username=target_username,
        target_label=target_label,
        target_first_name=target.first_name,
        target_last_name=target.last_name,
        target_chat_display_name=target.chat_display_name,
        actor_user_id=message.from_user.id,
        step="profile",
        expires_at=_now_utc() + _IRIS_IMPORT_TTL,
    )

    try:
        await bot.send_message(
            chat_id=message.from_user.id,
            text=_build_iris_import_intro(session=session),
            parse_mode="HTML",
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        await message.answer(
            "Не могу написать вам в личку. Откройте диалог с ботом, нажмите /start и повторите /iris_perenos."
        )
        return

    _set_pending_iris_import(importer_user_id=message.from_user.id, session=session)
    await message.answer("Инструкцию отправила в ЛС. Перешлите туда два ответа Iris по шагам.")


@router.message(PendingIrisImportFilter())
async def pending_iris_import_handler(message: Message, activity_repo, bot: Bot, settings: Settings) -> None:
    if message.from_user is None:
        return

    session = _get_pending_iris_import(message.from_user.id)
    if session is None:
        return
    if _is_pending_iris_import_expired(session):
        _clear_pending_iris_import(message.from_user.id)
        await message.answer("Сессия переноса из Iris истекла. Запустите /iris_perenos заново в группе.")
        return

    raw_text, entities = _message_text_and_entities(message)
    if (raw_text or "").strip().lower() in {"/cancel", "отмена", "cancel"}:
        _clear_pending_iris_import(message.from_user.id)
        await message.answer("Перенос из Iris отменён.")
        return
    if not raw_text.strip():
        await message.answer("Нужен пересланный текстовый ответ Iris.")
        return
    if not _is_forwarded_message(message):
        await message.answer(_build_iris_unrelated_message_text())
        return

    source_error = _validate_iris_forward_source(message)
    if source_error is not None:
        await message.answer(source_error)
        return

    step_error = _validate_iris_message_step(
        expected_step=session.step,
        text=raw_text,
        target_username=session.target_username,
    )
    if step_error is not None:
        await message.answer(step_error, parse_mode="HTML")
        return

    if session.step == "profile":
        try:
            profile = parse_forwarded_profile_message(
                text=raw_text,
                entities=list(entities),
                timezone_name=settings.bot_timezone,
                now=_now_utc(),
            )
        except ValueError as exc:
            await message.answer(
                f"Не удалось распознать карточку Iris: <code>{escape(str(exc))}</code>",
                parse_mode="HTML",
            )
            return

        target_error = _validate_iris_target_username(
            expected_username=session.target_username,
            actual_username=profile.target_username,
        )
        if target_error is not None:
            await message.answer(target_error, parse_mode="HTML")
            return

        session.profile_data = profile
        session.profile_text = raw_text
        session.step = "awards"
        session.expires_at = _now_utc() + _IRIS_IMPORT_TTL
        await message.answer(_build_iris_awards_step_prompt(session=session), parse_mode="HTML")
        return

    try:
        awards_data = parse_forwarded_awards_message(
            text=raw_text,
            entities=list(entities),
            timezone_name=settings.bot_timezone,
        )
    except ValueError as exc:
        await message.answer(
            f"Не удалось распознать список наград Iris: <code>{escape(str(exc))}</code>",
            parse_mode="HTML",
        )
        return

    target_error = _validate_iris_target_username(
        expected_username=session.target_username,
        actual_username=awards_data.target_username,
    )
    if target_error is not None:
        await message.answer(target_error, parse_mode="HTML")
        return

    if session.profile_data is None or session.profile_text is None:
        _clear_pending_iris_import(message.from_user.id)
        await message.answer("Сессия переноса повреждена. Запустите /iris_perenos заново в группе.")
        return

    imported_at = _now_utc()
    try:
        state = await activity_repo.apply_user_chat_iris_import(
            chat=ChatSnapshot(
                telegram_chat_id=session.source_chat_id,
                chat_type=session.source_chat_type,
                title=session.source_chat_title,
            ),
            target=UserSnapshot(
                telegram_user_id=session.target_user_id,
                username=session.target_username,
                first_name=session.target_first_name,
                last_name=session.target_last_name,
                is_bot=False,
                chat_display_name=session.target_chat_display_name,
            ),
            imported_by_user_id=session.actor_user_id,
            source_bot_username=_IRIS_SOURCE_BOT_USERNAME,
            source_target_username=awards_data.target_username,
            imported_at=imported_at,
            profile_text=session.profile_text,
            awards_text=raw_text,
            karma_base_all_time=session.profile_data.karma_all_time,
            first_seen_at=session.profile_data.first_seen_at,
            last_seen_at=session.profile_data.last_seen_at or imported_at,
            activity_1d=session.profile_data.activity_1d,
            activity_7d=session.profile_data.activity_7d,
            activity_30d=session.profile_data.activity_30d,
            activity_all=session.profile_data.activity_all,
            awards=list(awards_data.awards),
        )
        await log_chat_action(
            activity_repo,
            chat_id=session.source_chat_id,
            chat_type=session.source_chat_type,
            chat_title=session.source_chat_title,
            action_code="iris_import",
            description=f"Импорт из Iris для пользователя {session.target_user_id}.",
            actor_user_id=session.actor_user_id,
            target_user_id=session.target_user_id,
            meta_json={
                "source_bot_username": state.source_bot_username,
                "source_target_username": state.source_target_username,
                "karma_base_all_time": state.karma_base_all_time,
                "activity_1d": session.profile_data.activity_1d,
                "activity_7d": session.profile_data.activity_7d,
                "activity_30d": session.profile_data.activity_30d,
                "activity_all": session.profile_data.activity_all,
                "awards_count": len(awards_data.awards),
            },
        )
    except ValueError:
        try:
            existing_state = await activity_repo.get_user_chat_iris_import_state(
                chat_id=session.source_chat_id,
                user_id=session.target_user_id,
            )
        except SQLAlchemyError:
            await safe_rollback(activity_repo)
            _clear_pending_iris_import(message.from_user.id)
            await message.answer("Не удалось завершить перенос из Iris. Попробуйте позже.")
            return

        _clear_pending_iris_import(message.from_user.id)
        if existing_state is not None:
            await message.answer(
                _format_iris_already_imported_message(
                    state=existing_state,
                    timezone_name=settings.bot_timezone,
                    is_self=int(message.from_user.id) == int(session.target_user_id),
                )
            )
            return
        await message.answer("Не удалось завершить перенос из Iris. Запустите /iris_perenos заново.")
        return
    except SQLAlchemyError:
        await safe_rollback(activity_repo)
        await message.answer("Не удалось завершить перенос из Iris. Попробуйте позже.")
        return

    _clear_pending_iris_import(message.from_user.id)
    await message.answer(
        _build_iris_import_success_text(
            session=session,
            profile=session.profile_data,
            awards_count=len(awards_data.awards),
            imported_at=imported_at,
            timezone_name=settings.bot_timezone,
        ),
        parse_mode="HTML",
    )

    try:
        await bot.send_message(
            chat_id=session.source_chat_id,
            text=(
                f"Перенос из Iris завершён для "
                f"{format_user_link(user_id=session.target_user_id, label=session.target_label)}."
            ),
            parse_mode="HTML",
            disable_notification=True,
        )
    except TelegramBadRequest:
        pass


@router.message(Command("awards"))
async def awards_command(message: Message, command: CommandObject, activity_repo, settings: Settings) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return

    if (command.args or "").strip() or message.reply_to_message is not None:
        target, error = await _resolve_stats_target_user(message, command=command, activity_repo=activity_repo)
        if target is None:
            await message.answer(error or "Не удалось определить пользователя.")
            return
    else:
        target = UserSnapshot(
            telegram_user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            is_bot=bool(message.from_user.is_bot),
            chat_display_name=await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=message.from_user.id),
        )

    await message.answer(
        await _build_profile_awards_message(
            activity_repo=activity_repo,
            chat_id=message.chat.id,
            user_id=target.telegram_user_id,
            timezone_name=settings.bot_timezone,
        ),
        parse_mode="HTML",
    )


@router.message(Command("achievements"))
async def achievements_command(
    message: Message,
    command: CommandObject,
    activity_repo,
    settings: Settings,
    achievement_orchestrator=None,
) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return

    if (command.args or "").strip() or message.reply_to_message is not None:
        target, error = await _resolve_stats_target_user(message, command=command, activity_repo=activity_repo)
        if target is None:
            await message.answer(error or "Не удалось определить пользователя.")
            return
    else:
        target = UserSnapshot(
            telegram_user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            is_bot=bool(message.from_user.is_bot),
            chat_display_name=await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=message.from_user.id),
        )

    await _refresh_achievements_for_user(
        activity_repo=activity_repo,
        achievement_orchestrator=achievement_orchestrator,
        chat_id=message.chat.id,
        user_id=target.telegram_user_id,
    )
    await message.answer(
        await _build_achievements_message(
            activity_repo=activity_repo,
            settings=settings,
            chat_id=message.chat.id,
            user_id=target.telegram_user_id,
            timezone_name=settings.bot_timezone,
        ),
        parse_mode="HTML",
    )


@router.message(Command("achsync"))
async def achsync_command(message: Message, command: CommandObject, activity_repo, bot: Bot) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if not await _ensure_chat_admin(message, bot):
        return

    mode = (command.args or "").strip().lower()
    if mode == "global":
        await activity_repo.rebuild_global_achievement_state()
        await message.answer("Глобальные achievement stats пересчитаны.")
        return

    await activity_repo.rebuild_chat_achievement_state(chat_id=message.chat.id)
    await message.answer("Achievement stats текущего чата пересчитаны.")


@router.callback_query(F.data.startswith(f"{_PROFILE_CALLBACK_PREFIX}:"))
async def profile_card_callback(query: CallbackQuery, activity_repo, settings: Settings, achievement_orchestrator=None) -> None:
    if query.message is None:
        await query.answer()
        return

    action, target_user_id = _parse_profile_callback_data(query.data)
    if action is None or target_user_id is None:
        await query.answer("Некорректная кнопка", show_alert=False)
        return

    fallback_user: UserSnapshot | None = None
    message_label = _extract_linked_user_label_from_message(query.message, user_id=target_user_id)
    if message_label:
        fallback_user = UserSnapshot(
            telegram_user_id=target_user_id,
            username=None,
            first_name=None,
            last_name=None,
            is_bot=False,
            chat_display_name=message_label,
        )
    elif query.from_user is not None and int(query.from_user.id) == int(target_user_id):
        fallback_user = UserSnapshot(
            telegram_user_id=query.from_user.id,
            username=query.from_user.username,
            first_name=query.from_user.first_name,
            last_name=query.from_user.last_name,
            is_bot=bool(query.from_user.is_bot),
            chat_display_name=None,
        )

    if action == "about":
        text = await _build_profile_about_message(
            activity_repo=activity_repo,
            chat_id=query.message.chat.id,
            user_id=target_user_id,
            fallback_user=fallback_user,
        )
    elif action == "achievements":
        await _refresh_achievements_for_user(
            activity_repo=activity_repo,
            achievement_orchestrator=achievement_orchestrator,
            chat_id=query.message.chat.id,
            user_id=target_user_id,
        )
        text = await _build_achievements_message(
            activity_repo=activity_repo,
            settings=settings,
            chat_id=query.message.chat.id,
            user_id=target_user_id,
            timezone_name=settings.bot_timezone,
            fallback_user=fallback_user,
        )
    else:
        text = await _build_profile_awards_message(
            activity_repo=activity_repo,
            chat_id=query.message.chat.id,
            user_id=target_user_id,
            timezone_name=settings.bot_timezone,
            fallback_user=fallback_user,
        )

    await query.message.answer(
        text,
        parse_mode="HTML",
        disable_notification=query.message.chat.type in {"group", "supergroup"},
    )
    await query.answer()


@router.callback_query(F.data.startswith("lb:"))
async def leaderboard_callback(query: CallbackQuery, activity_repo, settings: Settings, chat_settings: ChatSettings) -> None:
    if query.message is None or not query.data:
        await query.answer()
        return

    if not chat_settings.leaderboard_hybrid_buttons_enabled:
        await query.answer("Кнопки гибридного топа отключены для этой группы", show_alert=False)
        return

    parts = query.data.split(":")
    if len(parts) != 4:
        await query.answer("Некорректные параметры", show_alert=False)
        return

    _, mode_value, period_value, limit_raw = parts
    if mode_value not in {"mix", "activity", "karma"}:
        await query.answer("Некорректный режим", show_alert=False)
        return
    if period_value not in {"all", "7d"}:
        await query.answer("Некорректный период", show_alert=False)
        return
    if not limit_raw.isdigit():
        await query.answer("Некорректный лимит", show_alert=False)
        return

    limit = int(limit_raw)
    if not 1 <= limit <= chat_settings.top_limit_max:
        await query.answer("Лимит вне диапазона", show_alert=False)
        return

    mode: LeaderboardMode = mode_value  # type: ignore[assignment]
    period: LeaderboardPeriod = period_value  # type: ignore[assignment]

    leaderboard = await get_top_users(
        repo=activity_repo,
        chat_id=query.message.chat.id,
        limit=limit,
        mode=mode,
        period=period,
        days=chat_settings.leaderboard_7d_days,
        week_start_weekday=chat_settings.leaderboard_week_start_weekday,
        week_start_hour=chat_settings.leaderboard_week_start_hour,
        karma_weight=chat_settings.leaderboard_hybrid_karma_weight,
        activity_weight=chat_settings.leaderboard_hybrid_activity_weight,
    )
    text = format_leaderboard(
        leaderboard,
        mode=mode,
        period=period,
        limit=limit,
        timezone_name=settings.bot_timezone,
    )
    chart = await _build_chart_async(build_leaderboard_chart, items=leaderboard, mode=mode)
    keyboard = _build_leaderboard_keyboard(mode=mode, period=period, limit=limit)
    uses_caption = query.message.caption is not None and query.message.text is None

    try:
        if query.message.photo and chart is not None and len(text) <= _CAPTION_LIMIT_SAFE:
            await query.message.edit_media(
                media=InputMediaPhoto(
                    media=BufferedInputFile(chart, filename="leaderboard.png"),
                    caption=text,
                    parse_mode="HTML",
                ),
                reply_markup=keyboard,
            )
        elif uses_caption:
            await query.message.edit_caption(
                caption=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        else:
            await query.message.edit_text(
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await query.answer()


@router.message(Command("me"))
async def me_command(message: Message, activity_repo, bot: Bot, settings: Settings, chat_settings: ChatSettings) -> None:
    await send_me_stats(message, activity_repo, bot, settings, chat_settings)


@router.message(Command("rep"))
async def rep_command(message: Message, activity_repo, chat_settings: ChatSettings) -> None:
    await send_rep_stats(message, activity_repo, chat_settings)


@router.message(Command("top"))
async def top_command(
    message: Message,
    command: CommandObject,
    activity_repo,
    settings: Settings,
    chat_settings: ChatSettings,
) -> None:
    period_matched, period, period_limit, period_error = parse_activity_top_period_request(
        command.args,
        chat_settings=chat_settings,
    )
    if period_matched:
        if period_error:
            await message.answer(period_error)
            return
        if period is None or period_limit is None:
            await message.answer("Некорректные параметры команды")
            return
        await send_top_stats(
            message,
            activity_repo,
            settings,
            chat_settings,
            limit=period_limit,
            mode="activity",
            period=period,
            include_chart=False,
            include_keyboard=False,
        )
        return

    mode, limit, error = parse_leaderboard_request(
        command.args,
        chat_settings=chat_settings,
        default_mode="activity",
        allow_mode_switch=True,
    )
    if error:
        await message.answer(error)
        return
    if mode is None or limit is None:
        await message.answer("Некорректные параметры команды")
        return

    await send_top_stats(
        message,
        activity_repo,
        settings,
        chat_settings,
        limit=limit,
        mode=mode,
        include_keyboard=should_include_hybrid_top_keyboard(
            chat_settings=chat_settings,
            mode=mode,
            period="all",
        ),
    )


@router.message(Command("active"))
async def active_command(
    message: Message,
    command: CommandObject,
    activity_repo,
    settings: Settings,
    chat_settings: ChatSettings,
) -> None:
    mode, limit, error = parse_leaderboard_request(
        command.args,
        chat_settings=chat_settings,
        default_mode="activity",
        allow_mode_switch=False,
    )
    if error:
        await message.answer(error)
        return
    if mode is None or limit is None:
        await message.answer("Некорректные параметры команды")
        return

    await send_top_stats(
        message,
        activity_repo,
        settings,
        chat_settings,
        limit=limit,
        mode=mode,
        include_keyboard=should_include_hybrid_top_keyboard(
            chat_settings=chat_settings,
            mode=mode,
            period="all",
        ),
    )


@router.message(Command("lastseen"))
async def last_seen_command(
    message: Message,
    command: CommandObject,
    activity_repo,
    settings: Settings,
) -> None:
    target_user_id, target_label, error = await _resolve_last_seen_command_target(
        message,
        command=command,
        activity_repo=activity_repo,
    )
    if error:
        await message.answer(error)
        return
    await send_last_seen(
        message,
        activity_repo,
        settings,
        target_user_id=target_user_id,
        target_label=target_label,
    )
