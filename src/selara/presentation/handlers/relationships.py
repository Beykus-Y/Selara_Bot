from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from selara.domain.entities import (
    ChatSnapshot,
    MarriageState,
    RelationshipActionCode,
    RelationshipKind,
    RelationshipState,
    UserSnapshot,
)
from selara.domain.value_objects import display_name_from_parts
from selara.presentation.audit import log_chat_action
from selara.presentation.handlers.common import safe_callback_answer as _safe_callback_answer

router = Router(name="relationships")

_PROPOSAL_TTL_HOURS = 24
_RELATION_ACTION_COOLDOWN_SECONDS = 30 * 60

_RELATION_ACTION_RANGES: dict[RelationshipActionCode, dict[RelationshipKind, tuple[int, int]]] = {
    "love": {"marriage": (4, 12)},
    "care": {"pair": (2, 6), "marriage": (3, 7)},
    "date": {"pair": (3, 8), "marriage": (4, 9)},
    "gift": {"pair": (2, 7), "marriage": (3, 8)},
    "support": {"pair": (2, 6), "marriage": (3, 7)},
    "flirt": {"pair": (3, 7)},
    "surprise": {"pair": (3, 8)},
    "vow": {"marriage": (5, 11)},
}

_RELATION_ACTION_LABELS: dict[RelationshipActionCode, str] = {
    "love": "/love",
    "care": "/care",
    "date": "/date",
    "gift": "/gift",
    "support": "/support",
    "flirt": "/flirt",
    "surprise": "/surprise",
    "vow": "/vow",
}

_RELATION_ACTION_MESSAGES: dict[RelationshipActionCode, dict[RelationshipKind, str]] = {
    "love": {"marriage": "признался(ась) в любви"},
    "care": {"pair": "проявил(а) заботу к", "marriage": "заботливо поддержал(а) супруга(у)"},
    "date": {"pair": "устроил(а) романтическое свидание с", "marriage": "устроил(а) семейный вечер с"},
    "gift": {"pair": "порадовал(а) подарком", "marriage": "сделал(а) тёплый подарок для"},
    "support": {"pair": "поддержал(а)", "marriage": "стал(а) опорой для"},
    "flirt": {"pair": "флиртует с"},
    "surprise": {"pair": "подготовил(а) сюрприз для"},
    "vow": {"marriage": "дал(а) семейное обещание"},
}

_RELATION_VIEW_RELATION = "relation"
_RELATION_VIEW_MARRIAGE = "marriage"


def _relationship_action_codes(kind: RelationshipKind) -> list[RelationshipActionCode]:
    return (
        ["care", "date", "gift", "support", "flirt", "surprise"]
        if kind == "pair"
        else ["love", "care", "date", "gift", "support", "vow"]
    )


def _format_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}с"
    mins, sec = divmod(seconds, 60)
    if mins < 60:
        return f"{mins}м {sec:02d}с"
    hours, mins = divmod(mins, 60)
    return f"{hours}ч {mins:02d}м"


def _format_relationship_duration(*, started_at: datetime, now: datetime) -> str:
    total_seconds = max(0, int((now - started_at).total_seconds()))
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    months, days = divmod(days, 30)
    if months:
        return f"{months} мес." if days == 0 else f"{months} мес. {days} дн."
    if days:
        return f"{days} дн." if hours == 0 else f"{days} дн. {hours} ч."
    if hours:
        return f"{hours} ч. {minutes:02d} м."
    return f"{max(1, minutes)} мин."


def _action_unavailable_text(*, action_code: RelationshipActionCode, relationship_kind: RelationshipKind) -> str:
    if relationship_kind == "pair":
        return f"Команда {_RELATION_ACTION_LABELS[action_code]} доступна только в браке."
    return f"Команда {_RELATION_ACTION_LABELS[action_code]} доступна только на стадии пары."


def _relationship_state_from_marriage(marriage: MarriageState) -> RelationshipState:
    return RelationshipState(
        kind="marriage",
        id=marriage.id,
        user_low_id=marriage.user_low_id,
        user_high_id=marriage.user_high_id,
        chat_id=marriage.chat_id,
        started_at=marriage.married_at,
        affection_points=marriage.affection_points,
        last_affection_at=marriage.last_affection_at,
        last_affection_by_user_id=marriage.last_affection_by_user_id,
    )


def _build_proposal_keyboard(proposal_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принять", callback_data=f"rel:accept:{proposal_id}")
    builder.button(text="❌ Отклонить", callback_data=f"rel:reject:{proposal_id}")
    builder.button(text="🚫 Отменить", callback_data=f"rel:cancel:{proposal_id}")
    builder.adjust(2, 1)
    return builder.as_markup()


def _build_relationship_end_keyboard(*, action: str, owner_user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=f"relend:confirm:{action}:{owner_user_id}")
    builder.button(text="❌ Отмена", callback_data=f"relend:cancel:{action}:{owner_user_id}")
    builder.adjust(2)
    return builder.as_markup()

async def _get_user_label(activity_repo, *, chat_id: int, user: UserSnapshot | None, user_id: int) -> str:
    display_name = await activity_repo.get_chat_display_name(chat_id=chat_id, user_id=user_id)
    if display_name:
        return display_name
    if user is None:
        return f"user:{user_id}"
    return display_name_from_parts(
        user_id=user.telegram_user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        chat_display_name=user.chat_display_name,
    )


async def _mention(activity_repo, *, chat_id: int, user: UserSnapshot | None, user_id: int) -> str:
    label = await _get_user_label(activity_repo, chat_id=chat_id, user=user, user_id=user_id)
    return f'<a href="tg://user?id={user_id}">{escape(label)}</a>'


async def _build_actor_snapshot(
    activity_repo,
    *,
    chat_id: int,
    telegram_user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    is_bot: bool,
) -> UserSnapshot:
    return UserSnapshot(
        telegram_user_id=telegram_user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        is_bot=is_bot,
        chat_display_name=await activity_repo.get_chat_display_name(chat_id=chat_id, user_id=telegram_user_id),
    )


async def _resolve_target_user(message: Message, activity_repo, *, args: str | None) -> UserSnapshot | None:
    if message.reply_to_message and message.reply_to_message.from_user is not None:
        reply_user = message.reply_to_message.from_user
        chat_display_name = await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=reply_user.id)
        return UserSnapshot(
            telegram_user_id=reply_user.id,
            username=reply_user.username,
            first_name=reply_user.first_name,
            last_name=reply_user.last_name,
            is_bot=bool(reply_user.is_bot),
            chat_display_name=chat_display_name,
        )

    raw = (args or "").strip()
    if not raw:
        return None
    token = raw.split(maxsplit=1)[0]

    if token.startswith("@"):
        return await activity_repo.find_chat_user_by_username(chat_id=message.chat.id, username=token)

    if token.lstrip("-").isdigit():
        user_id = int(token)
        existing = await activity_repo.get_user_snapshot(user_id=user_id)
        chat_display_name = await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=user_id)
        if existing is not None:
            return UserSnapshot(
                telegram_user_id=existing.telegram_user_id,
                username=existing.username,
                first_name=existing.first_name,
                last_name=existing.last_name,
                is_bot=existing.is_bot,
                chat_display_name=chat_display_name,
            )
        return UserSnapshot(
            telegram_user_id=user_id,
            username=None,
            first_name=None,
            last_name=None,
            is_bot=False,
            chat_display_name=chat_display_name,
        )

    return None


def _partner_id(relationship: RelationshipState, *, user_id: int) -> int:
    return relationship.user_high_id if relationship.user_low_id == user_id else relationship.user_low_id


def _proposal_kind_title(kind: str) -> str:
    return "Предложение отношений" if kind == "pair" else "Предложение брака"


def _relation_status_title(kind: str) -> str:
    return "Пара" if kind == "pair" else "Брак"


async def _build_actor_mention(
    activity_repo,
    *,
    chat_id: int,
    telegram_user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    is_bot: bool,
) -> str:
    actor = await _build_actor_snapshot(
        activity_repo,
        chat_id=chat_id,
        telegram_user_id=telegram_user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        is_bot=is_bot,
    )
    return await _mention(activity_repo, chat_id=chat_id, user=actor, user_id=actor.telegram_user_id)


async def _build_partner_mention(activity_repo, *, chat_id: int, partner_user_id: int) -> str:
    partner = await activity_repo.get_user_snapshot(user_id=partner_user_id)
    return await _mention(activity_repo, chat_id=chat_id, user=partner, user_id=partner_user_id)


def _build_relationship_action_keyboard(
    kind: RelationshipKind,
    *,
    owner_user_id: int,
    view: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code in _relationship_action_codes(kind):
        builder.button(
            text=_RELATION_ACTION_LABELS[code],
            callback_data=f"relact:{code}:{view}:{owner_user_id}",
        )
    builder.button(
        text="Обновить",
        callback_data=f"relact:refresh:{view}:{owner_user_id}",
    )
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


async def _build_action_cooldown_line(
    activity_repo,
    *,
    relationship: RelationshipState,
    actor_user_id: int,
    action_code: RelationshipActionCode,
    now: datetime,
) -> str:
    last_used_at = await activity_repo.get_relationship_action_last_used_at(
        relationship=relationship,
        actor_user_id=actor_user_id,
        action_code=action_code,
    )
    if last_used_at is None:
        return "готово"
    next_time = last_used_at + timedelta(seconds=_RELATION_ACTION_COOLDOWN_SECONDS)
    if next_time <= now:
        return "готово"
    return _format_seconds(int((next_time - now).total_seconds()))


async def _build_relationship_status_text(
    activity_repo,
    *,
    relationship: RelationshipState,
    actor_user_id: int,
    chat_id: int,
    title: str,
) -> str:
    partner_user_id = _partner_id(relationship, user_id=actor_user_id)
    partner = await activity_repo.get_user_snapshot(user_id=partner_user_id)
    partner_mention = await _mention(activity_repo, chat_id=chat_id, user=partner, user_id=partner_user_id)

    now = datetime.now(timezone.utc)
    action_lines: list[str] = []
    for code in _relationship_action_codes(relationship.kind):
        status = await _build_action_cooldown_line(
            activity_repo,
            relationship=relationship,
            actor_user_id=actor_user_id,
            action_code=code,
            now=now,
        )
        action_lines.append(f"<b>{escape(_RELATION_ACTION_LABELS[code])}:</b> {escape(status)}")

    return (
        f"<b>{escape(title)}</b>\n"
        f"<b>Статус:</b> <code>{escape(_relation_status_title(relationship.kind))}</code>\n"
        f"<b>Партнёр:</b> {partner_mention}\n"
        f"<b>Вместе:</b> <code>{_format_relationship_duration(started_at=relationship.started_at, now=now)}</code>\n"
        f"<b>Уровень отношений:</b> <code>{relationship.affection_points}</code> 💞\n"
        + "\n".join(action_lines)
    )


async def _render_relationship_panel(
    activity_repo,
    *,
    relationship: RelationshipState,
    actor_user_id: int,
    chat_id: int,
    title: str,
    view: str,
) -> tuple[str, InlineKeyboardMarkup]:
    return (
        await _build_relationship_status_text(
            activity_repo,
            relationship=relationship,
            actor_user_id=actor_user_id,
            chat_id=chat_id,
            title=title,
        ),
        _build_relationship_action_keyboard(relationship.kind, owner_user_id=actor_user_id, view=view),
    )


async def _edit_relationship_panel(
    query: CallbackQuery,
    *,
    activity_repo,
    view: str,
) -> None:
    if query.from_user is None or query.message is None:
        return

    text: str
    reply_markup: InlineKeyboardMarkup | None = None
    if view == _RELATION_VIEW_MARRIAGE:
        marriage = await activity_repo.get_active_marriage(user_id=query.from_user.id, chat_id=query.message.chat.id)
        if marriage is None:
            relationship = await activity_repo.get_active_relationship(user_id=query.from_user.id, chat_id=query.message.chat.id)
            if relationship is not None and relationship.kind == "pair":
                text = (
                    "У вас пока нет активного брака.\n"
                    "Сейчас у вас пара, откройте <code>мои отношения</code> или <code>/relation</code>."
                )
            else:
                text = "У вас нет активного брака.\nПредложение брака: <code>/marry @username</code>"
        else:
            text, reply_markup = await _render_relationship_panel(
                activity_repo,
                relationship=_relationship_state_from_marriage(marriage),
                actor_user_id=query.from_user.id,
                chat_id=query.message.chat.id,
                title="Ваш брак",
                view=_RELATION_VIEW_MARRIAGE,
            )
    else:
        relationship = await activity_repo.get_active_relationship(user_id=query.from_user.id, chat_id=query.message.chat.id)
        if relationship is None:
            text = (
                "Вы пока не в отношениях.\n"
                "Пара: <code>/pair @username</code>\n"
                "Брак: <code>/marry @username</code>"
            )
        else:
            text, reply_markup = await _render_relationship_panel(
                activity_repo,
                relationship=relationship,
                actor_user_id=query.from_user.id,
                chat_id=query.message.chat.id,
                title="Ваши отношения",
                view=_RELATION_VIEW_RELATION,
            )

    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def _perform_relation_action(
    activity_repo,
    *,
    relationship: RelationshipState,
    actor_user: UserSnapshot,
    chat: ChatSnapshot,
    action_code: RelationshipActionCode,
    reply_target_user_id: int | None = None,
) -> tuple[RelationshipState | None, str | None, str | None]:
    action_kind_ranges = _RELATION_ACTION_RANGES[action_code]
    if relationship.kind not in action_kind_ranges:
        return None, None, _action_unavailable_text(action_code=action_code, relationship_kind=relationship.kind)

    partner_user_id = _partner_id(relationship, user_id=actor_user.telegram_user_id)
    if reply_target_user_id is not None and reply_target_user_id != partner_user_id:
        return None, None, "Это действие можно отправить только своему партнёру."

    now = datetime.now(timezone.utc)
    last_used_at = await activity_repo.get_relationship_action_last_used_at(
        relationship=relationship,
        actor_user_id=actor_user.telegram_user_id,
        action_code=action_code,
    )
    if last_used_at is not None:
        next_time = last_used_at + timedelta(seconds=_RELATION_ACTION_COOLDOWN_SECONDS)
        if next_time > now:
            remain = int((next_time - now).total_seconds())
            return None, None, f"Слишком рано. До {_RELATION_ACTION_LABELS[action_code]}: {_format_seconds(remain)}."

    min_gain, max_gain = action_kind_ranges[relationship.kind]
    gain = random.randint(min_gain, max_gain)
    updated = await activity_repo.touch_relationship_affection(
        relationship=relationship,
        actor_user_id=actor_user.telegram_user_id,
        affection_delta=gain,
        event_at=now,
    )
    if updated is None:
        return None, None, "Не удалось применить действие."

    await activity_repo.set_relationship_action_last_used_at(
        relationship=updated,
        actor_user_id=actor_user.telegram_user_id,
        action_code=action_code,
        used_at=now,
    )

    partner = await activity_repo.get_user_snapshot(user_id=partner_user_id)
    actor_mention = await _mention(
        activity_repo,
        chat_id=chat.telegram_chat_id,
        user=actor_user,
        user_id=actor_user.telegram_user_id,
    )
    partner_mention = await _mention(
        activity_repo,
        chat_id=chat.telegram_chat_id,
        user=partner,
        user_id=partner_user_id,
    )
    action_message = _RELATION_ACTION_MESSAGES[action_code][relationship.kind]
    text = (
        f"{actor_mention} {action_message} {partner_mention}.\n"
        f"+<code>{gain}</code> 💞 | Итого: <code>{updated.affection_points}</code> 💞\n"
        f"Кулдаун {_RELATION_ACTION_LABELS[action_code]}: <code>{_format_seconds(_RELATION_ACTION_COOLDOWN_SECONDS)}</code>"
    )
    return updated, text, None


async def _send_relationship_proposal(
    message: Message,
    *,
    activity_repo,
    kind: RelationshipKind,
    args: str | None,
) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Предложение можно отправить только в группе.")
        return

    target = await _resolve_target_user(message, activity_repo, args=args)
    if target is None:
        cmd = "/pair" if kind == "pair" else "/marry"
        await message.answer(f"Формат: reply + <code>{cmd}</code> или <code>{cmd} @username</code>.", parse_mode="HTML")
        return

    chat = ChatSnapshot(
        telegram_chat_id=message.chat.id,
        chat_type=message.chat.type,
        title=message.chat.title,
    )
    proposer = UserSnapshot(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        chat_display_name=await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=message.from_user.id),
    )
    proposal, error = await activity_repo.create_marriage_proposal(
        chat=chat,
        proposer=proposer,
        target=target,
        kind=kind,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=_PROPOSAL_TTL_HOURS),
        event_at=datetime.now(timezone.utc),
    )
    if proposal is None:
        await message.answer(error or "Не удалось отправить предложение.")
        return
    if error:
        await message.answer(error)
        return

    proposer_mention = await _mention(activity_repo, chat_id=message.chat.id, user=proposer, user_id=proposer.telegram_user_id)
    target_mention = await _mention(activity_repo, chat_id=message.chat.id, user=target, user_id=target.telegram_user_id)

    await message.answer(
        (
            f"<b>{_proposal_kind_title(kind)}</b>\n"
            f"{proposer_mention} предлагает {target_mention}.\n"
            f"Срок ответа: <code>{_PROPOSAL_TTL_HOURS}ч</code>"
        ),
        parse_mode="HTML",
        reply_markup=_build_proposal_keyboard(proposal.id),
    )


async def _run_relation_action(
    message: Message,
    *,
    activity_repo,
    action_code: RelationshipActionCode,
) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна в группе.")
        return

    relationship = await activity_repo.get_active_relationship(user_id=message.from_user.id, chat_id=message.chat.id)
    if relationship is None:
        await message.answer("Сначала нужны отношения: <code>/pair @username</code> или <code>/marry @username</code>.", parse_mode="HTML")
        return

    actor = await _build_actor_snapshot(
        activity_repo,
        chat_id=message.chat.id,
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
    )
    updated, action_text, error = await _perform_relation_action(
        activity_repo,
        relationship=relationship,
        actor_user=actor,
        chat=ChatSnapshot(
            telegram_chat_id=message.chat.id,
            chat_type=message.chat.type,
            title=message.chat.title,
        ),
        action_code=action_code,
        reply_target_user_id=(
            message.reply_to_message.from_user.id
            if message.reply_to_message and message.reply_to_message.from_user
            else None
        ),
    )
    if error:
        await message.answer(error)
        return
    if updated is None or action_text is None:
        await message.answer("Не удалось применить действие.")
        return

    await message.answer(action_text, parse_mode="HTML", disable_notification=True)


@router.message(Command("relation"))
async def relation_command(message: Message, activity_repo) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна в группе.")
        return

    relationship = await activity_repo.get_active_relationship(user_id=message.from_user.id, chat_id=message.chat.id)
    if relationship is None:
        await message.answer(
            "Вы пока не в отношениях.\n"
            "Пара: <code>/pair @username</code>\n"
            "Брак: <code>/marry @username</code>",
            parse_mode="HTML",
        )
        return

    text, keyboard = await _render_relationship_panel(
        activity_repo,
        relationship=relationship,
        actor_user_id=message.from_user.id,
        chat_id=message.chat.id,
        title="Ваши отношения",
        view=_RELATION_VIEW_RELATION,
    )
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


async def marriage_status_command(message: Message, activity_repo) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна в группе.")
        return

    marriage = await activity_repo.get_active_marriage(user_id=message.from_user.id, chat_id=message.chat.id)
    if marriage is None:
        relationship = await activity_repo.get_active_relationship(user_id=message.from_user.id, chat_id=message.chat.id)
        if relationship is not None and relationship.kind == "pair":
            await message.answer(
                "У вас пока нет активного брака.\n"
                "Сейчас у вас пара, откройте <code>мои отношения</code> или <code>/relation</code>.",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                "У вас нет активного брака.\n"
                "Предложение брака: <code>/marry @username</code>",
                parse_mode="HTML",
            )
        return

    text, keyboard = await _render_relationship_panel(
        activity_repo,
        relationship=_relationship_state_from_marriage(marriage),
        actor_user_id=message.from_user.id,
        chat_id=message.chat.id,
        title="Ваш брак",
        view=_RELATION_VIEW_MARRIAGE,
    )
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


async def marriages_command(message: Message, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна в группе.")
        return

    marriages = await activity_repo.list_active_marriages(chat_id=message.chat.id)
    if not marriages:
        await message.answer("В этой беседе пока нет активных браков.")
        return

    now = datetime.now(timezone.utc)
    lines = ["<b>Активные браки беседы</b>"]
    for index, marriage in enumerate(marriages, start=1):
        user_low = await activity_repo.get_user_snapshot(user_id=marriage.user_low_id)
        user_high = await activity_repo.get_user_snapshot(user_id=marriage.user_high_id)
        left = await _mention(activity_repo, chat_id=message.chat.id, user=user_low, user_id=marriage.user_low_id)
        right = await _mention(activity_repo, chat_id=message.chat.id, user=user_high, user_id=marriage.user_high_id)
        lines.append(
            f"{index}. {left} + {right} — <code>{_format_relationship_duration(started_at=marriage.married_at, now=now)}</code>"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


async def _request_breakup_confirmation(message: Message, activity_repo) -> None:
    pair = await activity_repo.get_active_relationship(user_id=message.from_user.id, chat_id=message.chat.id)
    if pair is None or pair.kind != "pair":
        await message.answer("У вас нет активных отношений (пары).")
        return

    partner_user_id = _partner_id(pair, user_id=message.from_user.id)
    actor_mention = await _build_actor_mention(
        activity_repo,
        chat_id=message.chat.id,
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
    )
    partner_mention = await _build_partner_mention(activity_repo, chat_id=message.chat.id, partner_user_id=partner_user_id)
    await message.answer(
        (
            f"{actor_mention}, вы точно хотите завершить отношения с {partner_mention}?\n"
            "Нужно отдельное подтверждение, чтобы не разорвать связь случайно."
        ),
        parse_mode="HTML",
        reply_markup=_build_relationship_end_keyboard(action="breakup", owner_user_id=message.from_user.id),
    )


async def _request_divorce_confirmation(message: Message, activity_repo) -> None:
    marriage = await activity_repo.get_active_marriage(user_id=message.from_user.id, chat_id=message.chat.id)
    if marriage is None:
        await message.answer("У вас нет активного брака.")
        return

    partner_user_id = marriage.user_high_id if marriage.user_low_id == message.from_user.id else marriage.user_low_id
    actor_mention = await _build_actor_mention(
        activity_repo,
        chat_id=message.chat.id,
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
    )
    partner_mention = await _build_partner_mention(activity_repo, chat_id=message.chat.id, partner_user_id=partner_user_id)
    await message.answer(
        (
            f"{actor_mention}, вы точно хотите развестись с {partner_mention}?\n"
            "Нужно отдельное подтверждение, чтобы не расторгнуть брак случайно."
        ),
        parse_mode="HTML",
        reply_markup=_build_relationship_end_keyboard(action="divorce", owner_user_id=message.from_user.id),
    )


async def _confirm_breakup(query: CallbackQuery, activity_repo) -> tuple[str, int | None]:
    pair = await activity_repo.dissolve_pair(user_id=query.from_user.id, chat_id=query.message.chat.id)
    if pair is None:
        return "У вас уже нет активных отношений (пары).", None

    partner_user_id = _partner_id(pair, user_id=query.from_user.id)
    actor_mention = await _build_actor_mention(
        activity_repo,
        chat_id=query.message.chat.id,
        telegram_user_id=query.from_user.id,
        username=query.from_user.username,
        first_name=query.from_user.first_name,
        last_name=query.from_user.last_name,
        is_bot=bool(query.from_user.is_bot),
    )
    partner_mention = await _build_partner_mention(activity_repo, chat_id=query.message.chat.id, partner_user_id=partner_user_id)
    return f"{actor_mention} и {partner_mention} больше не в отношениях.", partner_user_id


async def _confirm_divorce(query: CallbackQuery, activity_repo) -> tuple[str, int | None]:
    marriage = await activity_repo.dissolve_marriage(user_id=query.from_user.id, chat_id=query.message.chat.id)
    if marriage is None:
        return "У вас уже нет активного брака.", None

    partner_user_id = marriage.user_high_id if marriage.user_low_id == query.from_user.id else marriage.user_low_id
    actor_mention = await _build_actor_mention(
        activity_repo,
        chat_id=query.message.chat.id,
        telegram_user_id=query.from_user.id,
        username=query.from_user.username,
        first_name=query.from_user.first_name,
        last_name=query.from_user.last_name,
        is_bot=bool(query.from_user.is_bot),
    )
    partner_mention = await _build_partner_mention(activity_repo, chat_id=query.message.chat.id, partner_user_id=partner_user_id)

    await activity_repo.remove_graph_relationship(
        chat_id=query.message.chat.id,
        user_a=query.from_user.id,
        user_b=partner_user_id,
        relation_type="spouse",
    )
    await log_chat_action(
        activity_repo,
        chat_id=query.message.chat.id,
        chat_type=query.message.chat.type,
        chat_title=query.message.chat.title,
        action_code="marriage_divorce",
        description=f"Брак расторгнут: {query.from_user.id} и {partner_user_id}.",
        actor_user_id=query.from_user.id,
        target_user_id=partner_user_id,
    )
    return f"{actor_mention} и {partner_mention} теперь не состоят в браке.", partner_user_id


@router.message(Command("pair"))
async def pair_command(message: Message, command: CommandObject, activity_repo) -> None:
    await _send_relationship_proposal(message, activity_repo=activity_repo, kind="pair", args=command.args)


@router.message(Command("marry"))
async def marry_command(message: Message, command: CommandObject, activity_repo) -> None:
    await _send_relationship_proposal(message, activity_repo=activity_repo, kind="marriage", args=command.args)


@router.message(Command("breakup"))
async def breakup_command(message: Message, activity_repo) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна в группе.")
        return
    await _request_breakup_confirmation(message, activity_repo)


@router.message(Command("love"))
async def love_command(message: Message, activity_repo) -> None:
    await _run_relation_action(message, activity_repo=activity_repo, action_code="love")


@router.message(Command("care"))
async def care_command(message: Message, activity_repo) -> None:
    await _run_relation_action(message, activity_repo=activity_repo, action_code="care")


@router.message(Command("date"))
async def date_command(message: Message, activity_repo) -> None:
    await _run_relation_action(message, activity_repo=activity_repo, action_code="date")


@router.message(Command("gift"))
async def gift_command(message: Message, activity_repo) -> None:
    await _run_relation_action(message, activity_repo=activity_repo, action_code="gift")


@router.message(Command("support"))
async def support_command(message: Message, activity_repo) -> None:
    await _run_relation_action(message, activity_repo=activity_repo, action_code="support")


@router.message(Command("flirt"))
async def flirt_command(message: Message, activity_repo) -> None:
    await _run_relation_action(message, activity_repo=activity_repo, action_code="flirt")


@router.message(Command("surprise"))
async def surprise_command(message: Message, activity_repo) -> None:
    await _run_relation_action(message, activity_repo=activity_repo, action_code="surprise")


@router.message(Command("vow"))
async def vow_command(message: Message, activity_repo) -> None:
    await _run_relation_action(message, activity_repo=activity_repo, action_code="vow")


@router.message(Command("divorce"))
async def divorce_command(message: Message, activity_repo) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна в группе.")
        return
    await _request_divorce_confirmation(message, activity_repo)


async def _enforce_relationship_panel_owner(query: CallbackQuery, *, owner_user_id: int) -> bool:
    if query.from_user is None:
        return False
    if query.from_user.id == owner_user_id:
        return True
    await _safe_callback_answer(query, "Это панель другого пользователя.", show_alert=True)
    return False


@router.callback_query(F.data.startswith("relend:"))
async def relationship_end_callback(query: CallbackQuery, activity_repo) -> None:
    if query.from_user is None or query.data is None or query.message is None:
        await _safe_callback_answer(query)
        return

    parts = query.data.split(":")
    if len(parts) != 4:
        await _safe_callback_answer(query, "Некорректная кнопка", show_alert=True)
        return

    _, decision, action, owner_raw = parts
    if decision not in {"confirm", "cancel"}:
        await _safe_callback_answer(query, "Неизвестное действие", show_alert=True)
        return
    if action not in {"breakup", "divorce"}:
        await _safe_callback_answer(query, "Неизвестный сценарий", show_alert=True)
        return
    if not owner_raw.isdigit():
        await _safe_callback_answer(query, "Некорректный владелец", show_alert=True)
        return

    owner_user_id = int(owner_raw)
    if query.from_user.id != owner_user_id:
        await _safe_callback_answer(query, "Подтвердить или отменить может только инициатор.", show_alert=True)
        return

    if decision == "cancel":
        cancelled_text = "Разрыв отношений отменён." if action == "breakup" else "Развод отменён."
        await query.message.edit_text(cancelled_text, reply_markup=None)
        await _safe_callback_answer(query, "Отменено")
        return

    result_text, _ = (
        await _confirm_breakup(query, activity_repo)
        if action == "breakup"
        else await _confirm_divorce(query, activity_repo)
    )
    await query.message.edit_text(result_text, parse_mode="HTML", reply_markup=None)
    await _safe_callback_answer(query, "Подтверждено")


@router.callback_query(F.data.startswith("relact:"))
async def relationship_action_callback(query: CallbackQuery, activity_repo) -> None:
    if query.from_user is None or query.data is None or query.message is None:
        await _safe_callback_answer(query)
        return

    parts = query.data.split(":")
    if len(parts) != 4:
        await _safe_callback_answer(query, "Некорректная кнопка", show_alert=True)
        return

    _, action_raw, view, owner_raw = parts
    if view not in {_RELATION_VIEW_RELATION, _RELATION_VIEW_MARRIAGE}:
        await _safe_callback_answer(query, "Неизвестный экран", show_alert=True)
        return
    if not owner_raw.isdigit():
        await _safe_callback_answer(query, "Некорректный владелец", show_alert=True)
        return
    if action_raw != "refresh" and action_raw not in _RELATION_ACTION_LABELS:
        await _safe_callback_answer(query, "Неизвестное действие", show_alert=True)
        return

    owner_user_id = int(owner_raw)
    if not await _enforce_relationship_panel_owner(query, owner_user_id=owner_user_id):
        return

    if action_raw == "refresh":
        await _edit_relationship_panel(query, activity_repo=activity_repo, view=view)
        await _safe_callback_answer(query, "Обновлено")
        return

    action_code: RelationshipActionCode = action_raw  # type: ignore[assignment]
    if view == _RELATION_VIEW_MARRIAGE:
        marriage = await activity_repo.get_active_marriage(user_id=query.from_user.id, chat_id=query.message.chat.id)
        relationship = None if marriage is None else _relationship_state_from_marriage(marriage)
    else:
        relationship = await activity_repo.get_active_relationship(user_id=query.from_user.id, chat_id=query.message.chat.id)
    if relationship is None:
        await _edit_relationship_panel(query, activity_repo=activity_repo, view=view)
        await _safe_callback_answer(query, "У вас нет активных отношений.", show_alert=True)
        return

    actor = await _build_actor_snapshot(
        activity_repo,
        chat_id=query.message.chat.id,
        telegram_user_id=query.from_user.id,
        username=query.from_user.username,
        first_name=query.from_user.first_name,
        last_name=query.from_user.last_name,
        is_bot=bool(query.from_user.is_bot),
    )
    updated, action_text, error = await _perform_relation_action(
        activity_repo,
        relationship=relationship,
        actor_user=actor,
        chat=ChatSnapshot(
            telegram_chat_id=query.message.chat.id,
            chat_type=query.message.chat.type,
            title=query.message.chat.title,
        ),
        action_code=action_code,
    )
    if error:
        await _safe_callback_answer(query, error, show_alert=True)
        return
    if updated is None or action_text is None:
        await _safe_callback_answer(query, "Не удалось применить действие.", show_alert=True)
        return

    await query.message.answer(action_text, parse_mode="HTML", disable_notification=True)
    await _edit_relationship_panel(query, activity_repo=activity_repo, view=view)
    await _safe_callback_answer(query, "Готово")


@router.callback_query(F.data.startswith("rel:"))
async def relationship_callback(query: CallbackQuery, activity_repo, achievement_orchestrator) -> None:
    if query.from_user is None or query.data is None:
        await _safe_callback_answer(query)
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await _safe_callback_answer(query, "Некорректная кнопка", show_alert=True)
        return
    _, action, proposal_raw = parts
    if action not in {"accept", "reject", "cancel"}:
        await _safe_callback_answer(query, "Неизвестное действие", show_alert=True)
        return
    if not proposal_raw.isdigit():
        await _safe_callback_answer(query, "Некорректный id", show_alert=True)
        return

    proposal_id = int(proposal_raw)
    accept = action == "accept"
    proposal, relationship, error = await activity_repo.respond_relationship_proposal(
        proposal_id=proposal_id,
        actor_user_id=query.from_user.id,
        accept=accept,
        event_at=datetime.now(timezone.utc),
    )
    if error:
        await _safe_callback_answer(query, error, show_alert=True)
        return
    if proposal is None:
        await _safe_callback_answer(query, "Предложение не найдено", show_alert=True)
        return

    chat_id = query.message.chat.id if query.message is not None else proposal.chat_id or 0
    proposer = await activity_repo.get_user_snapshot(user_id=proposal.proposer_user_id)
    target = await activity_repo.get_user_snapshot(user_id=proposal.target_user_id)
    proposer_mention = await _mention(activity_repo, chat_id=chat_id, user=proposer, user_id=proposal.proposer_user_id)
    target_mention = await _mention(activity_repo, chat_id=chat_id, user=target, user_id=proposal.target_user_id)

    if relationship is not None:
        if relationship.kind == "pair":
            text = f"💞 {proposer_mention} и {target_mention} теперь в отношениях!"
        else:
            text = f"💍 {proposer_mention} и {target_mention} теперь в браке!"
            if proposer is not None and target is not None and proposal.chat_id is not None:
                await activity_repo.upsert_graph_relationship(
                    chat=ChatSnapshot(
                        telegram_chat_id=proposal.chat_id,
                        chat_type=query.message.chat.type if query.message is not None else "group",
                        title=query.message.chat.title if query.message is not None else None,
                    ),
                    user_a=proposer,
                    user_b=target,
                    relation_type="spouse",
                    actor_user_id=query.from_user.id,
                )
        await log_chat_action(
            activity_repo,
            chat_id=chat_id,
            chat_type=query.message.chat.type if query.message is not None else "group",
            chat_title=query.message.chat.title if query.message is not None else None,
            action_code=f"relationship_{relationship.kind}_accepted",
            description=f"Связь подтверждена: {proposal.proposer_user_id} и {proposal.target_user_id}.",
            actor_user_id=query.from_user.id,
            target_user_id=proposal.target_user_id,
        )
        if achievement_orchestrator is not None:
            now = datetime.now(timezone.utc)
            await achievement_orchestrator.process_refresh(
                chat_id=proposal.chat_id,
                user_id=proposal.proposer_user_id,
                event_at=now,
                event_type=f"relationship_{relationship.kind}_accepted",
            )
            await achievement_orchestrator.process_refresh(
                chat_id=proposal.chat_id,
                user_id=proposal.target_user_id,
                event_at=now,
                event_type=f"relationship_{relationship.kind}_accepted",
            )
    elif proposal.status == "rejected":
        text = f"{target_mention} отклонил(а) предложение от {proposer_mention}."
        await log_chat_action(
            activity_repo,
            chat_id=chat_id,
            chat_type=query.message.chat.type if query.message is not None else "group",
            chat_title=query.message.chat.title if query.message is not None else None,
            action_code="relationship_rejected",
            description=f"Предложение отклонено: {proposal.proposer_user_id} -> {proposal.target_user_id}.",
            actor_user_id=query.from_user.id,
            target_user_id=proposal.target_user_id,
        )
    elif proposal.status == "cancelled":
        text = f"{proposer_mention} отменил(а) предложение."
        await log_chat_action(
            activity_repo,
            chat_id=chat_id,
            chat_type=query.message.chat.type if query.message is not None else "group",
            chat_title=query.message.chat.title if query.message is not None else None,
            action_code="relationship_cancelled",
            description=f"Предложение отменено: {proposal.proposer_user_id} -> {proposal.target_user_id}.",
            actor_user_id=query.from_user.id,
            target_user_id=proposal.target_user_id,
        )
    else:
        text = f"Предложение обновлено: <code>{escape(proposal.status)}</code>"

    if query.message is not None:
        try:
            await query.message.edit_text(text, parse_mode="HTML")
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
    await _safe_callback_answer(query, "Готово")
