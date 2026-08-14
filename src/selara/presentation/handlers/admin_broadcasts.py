from __future__ import annotations

import re
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    MessageReactionCountUpdated,
    MessageReactionUpdated,
)

from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.domain.reactions import (
    TelegramReactionTotal,
    TelegramReactionValue,
    normalize_telegram_reaction_emoji,
)

router = Router(name="admin_broadcast_reactions")

_CALLBACK_RE = re.compile(r"^abr:(?P<delivery_id>[1-9]\d*):(?P<option_key>r[1-6])$")


def decode_broadcast_reaction_callback(value: str | None) -> tuple[int, str] | None:
    match = _CALLBACK_RE.fullmatch(value or "")
    if match is None:
        return None
    return int(match.group("delivery_id")), match.group("option_key")


def _user_snapshot(user) -> UserSnapshot:
    return UserSnapshot(
        telegram_user_id=int(user.id),
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_bot=bool(user.is_bot),
    )


def _chat_snapshot(chat) -> ChatSnapshot:
    raw_type = getattr(chat, "type", "group")
    return ChatSnapshot(
        telegram_chat_id=int(chat.id),
        chat_type=str(getattr(raw_type, "value", raw_type)),
        title=getattr(chat, "title", None),
    )


def _reaction_value(reaction) -> TelegramReactionValue | None:
    raw_type = getattr(reaction, "type", None)
    reaction_type = str(getattr(raw_type, "value", raw_type) or "")
    if reaction_type == "emoji":
        display = str(getattr(reaction, "emoji", "") or "").strip()
        value = normalize_telegram_reaction_emoji(display)
        return TelegramReactionValue("emoji", value, display) if value else None
    if reaction_type == "custom_emoji":
        value = str(getattr(reaction, "custom_emoji_id", "") or "").strip()
        return TelegramReactionValue("custom_emoji", value, "✨") if value else None
    if reaction_type == "paid":
        return TelegramReactionValue("paid", "paid", "⭐")
    return None


def _reaction_values(reactions) -> frozenset[TelegramReactionValue]:
    return frozenset(value for reaction in reactions if (value := _reaction_value(reaction)) is not None)


@router.callback_query(F.data.startswith("abr:"))
async def admin_broadcast_reaction_callback(query: CallbackQuery, activity_repo) -> None:
    decoded = decode_broadcast_reaction_callback(query.data)
    message = getattr(query, "message", None)
    if (
        decoded is None
        or query.from_user is None
        or message is None
        or getattr(message, "message_id", None) is None
        or getattr(getattr(message, "chat", None), "id", None) is None
    ):
        await query.answer("Эта кнопка устарела или повреждена.", show_alert=True)
        return
    delivery_id, option_key = decoded
    reacted_at = getattr(message, "date", None) or datetime.now(UTC)
    result = await activity_repo.toggle_admin_broadcast_inline_reaction(
        delivery_id=delivery_id,
        chat_id=int(message.chat.id),
        telegram_message_id=int(message.message_id),
        user=_user_snapshot(query.from_user),
        option_key=option_key,
        reacted_at=reacted_at,
    )
    if result == "selected":
        await query.answer("Реакция сохранена.")
    elif result == "removed":
        await query.answer("Реакция снята.")
    else:
        await query.answer("Рассылка или вариант реакции больше недоступны.", show_alert=True)


@router.message_reaction()
async def admin_broadcast_native_reaction(update: MessageReactionUpdated, activity_repo) -> None:
    await activity_repo.replace_admin_broadcast_native_reactions(
        chat=_chat_snapshot(update.chat),
        user=_user_snapshot(update.user) if update.user is not None else None,
        actor_chat_id=int(update.actor_chat.id) if update.actor_chat is not None else None,
        telegram_message_id=int(update.message_id),
        reactions=_reaction_values(update.new_reaction),
        reacted_at=update.date,
    )


@router.message_reaction_count()
async def admin_broadcast_native_reaction_count(
    update: MessageReactionCountUpdated,
    activity_repo,
) -> None:
    reactions: list[TelegramReactionTotal] = []
    for item in update.reactions:
        reaction = _reaction_value(item.type)
        if reaction is None:
            continue
        reactions.append(TelegramReactionTotal(reaction=reaction, count=max(0, int(item.total_count))))
    await activity_repo.replace_admin_broadcast_reaction_counts(
        chat_id=int(update.chat.id),
        telegram_message_id=int(update.message_id),
        reactions=reactions,
        observed_at=update.date,
    )
