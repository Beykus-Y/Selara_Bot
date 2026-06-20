from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from selara.infrastructure.db.models import ClanMemberModel, ClanModel, UserChatActivityModel, UserModel
from selara.presentation.formatters import format_user_link
from selara.presentation.handlers.common import safe_callback_answer as _safe_callback_answer

router = Router(name="clans")

_CLAN_CB = "clan"
_CLANS_LIST_PAGE_SIZE = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_user_clan_row(session: AsyncSession, *, chat_id: int, user_id: int):
    result = await session.execute(
        select(ClanModel.id, ClanModel.name, ClanModel.creator_user_id)
        .join(ClanMemberModel, ClanMemberModel.clan_id == ClanModel.id)
        .where(ClanMemberModel.chat_id == chat_id, ClanMemberModel.user_id == user_id)
    )
    return result.first()


async def _get_clan_by_id(session: AsyncSession, *, clan_id: int, chat_id: int):
    result = await session.execute(
        select(ClanModel).where(ClanModel.id == clan_id, ClanModel.chat_id == chat_id)
    )
    return result.scalar_one_or_none()


async def _get_member_count(session: AsyncSession, *, clan_id: int) -> int:
    result = await session.execute(
        select(func.count()).where(ClanMemberModel.clan_id == clan_id)
    )
    return result.scalar_one()


async def _get_member_ids_ordered(session: AsyncSession, *, clan_id: int) -> list[int]:
    result = await session.execute(
        select(ClanMemberModel.user_id)
        .where(ClanMemberModel.clan_id == clan_id)
        .order_by(ClanMemberModel.joined_at)
    )
    return [row[0] for row in result.fetchall()]


async def _resolve_member_name(
    bot: Bot, session: AsyncSession, *, user_id: int, chat_id: int
) -> str:
    # Single query: UCA fields + UserModel fields
    result = await session.execute(
        select(
            UserChatActivityModel.display_name_override,
            UserChatActivityModel.persona_label,
            UserChatActivityModel.title_prefix,
            UserModel.first_name,
            UserModel.last_name,
            UserModel.username,
        )
        .outerjoin(UserModel, UserModel.telegram_user_id == user_id)
        .where(
            UserChatActivityModel.user_id == user_id,
            UserChatActivityModel.chat_id == chat_id,
        )
    )
    row = result.first()

    if row:
        display_override, persona_label, title_prefix, first_name, last_name, username = row
        base = (display_override or "").strip() or None
        if base is None:
            full = " ".join(filter(None, [first_name, last_name])).strip()
            base = full or (f"@{username}" if username else None)

        if base and persona_label:
            return f"[{persona_label}] {base}"
        if persona_label:
            return f"[{persona_label}]"
        if base:
            return base

    # Fallback: only UserModel (user never appeared in this chat)
    result2 = await session.execute(
        select(UserModel.first_name, UserModel.last_name, UserModel.username)
        .where(UserModel.telegram_user_id == user_id)
    )
    urow = result2.first()
    if urow:
        first_name, last_name, username = urow
        if first_name:
            return f"{first_name} {last_name}".strip() if last_name else first_name
        if username:
            return f"@{username}"

    # Last resort: Telegram API
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        u = member.user
        if u.first_name:
            return f"{u.first_name} {u.last_name}".strip() if u.last_name else u.first_name
        if u.username:
            return f"@{u.username}"
    except Exception:
        pass

    return f"id:{user_id}"


async def _get_members_with_names(
    bot: Bot, session: AsyncSession, *, clan_id: int, chat_id: int
) -> list[tuple[int, str]]:
    member_ids = await _get_member_ids_ordered(session, clan_id=clan_id)
    members = []
    for uid in member_ids:
        name = await _resolve_member_name(bot, session, user_id=uid, chat_id=chat_id)
        members.append((uid, name))
    return members


async def _find_clan_by_name(session: AsyncSession, *, chat_id: int, name: str):
    result = await session.execute(
        select(ClanModel).where(
            ClanModel.chat_id == chat_id,
            func.lower(ClanModel.name) == name.lower(),
        )
    )
    return result.scalar_one_or_none()


async def _list_clans_in_chat(session: AsyncSession, *, chat_id: int) -> list[tuple]:
    """Returns list of (clan_id, name, member_count) sorted by member count desc."""
    result = await session.execute(
        select(ClanModel.id, ClanModel.name, func.count(ClanMemberModel.user_id).label("cnt"))
        .outerjoin(ClanMemberModel, ClanMemberModel.clan_id == ClanModel.id)
        .where(ClanModel.chat_id == chat_id)
        .group_by(ClanModel.id, ClanModel.name)
        .order_by(func.count(ClanMemberModel.user_id).desc())
        .limit(_CLANS_LIST_PAGE_SIZE)
    )
    return result.fetchall()


def _clan_card_markup(
    clan_id: int,
    *,
    viewer_is_member: bool,
    viewer_is_creator: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if not viewer_is_member:
        builder.button(text="Вступить", callback_data=f"{_CLAN_CB}:join:{clan_id}")
    if viewer_is_creator:
        builder.button(text="Удалить клан", callback_data=f"{_CLAN_CB}:delete:{clan_id}")
    elif viewer_is_member:
        builder.button(text="Выйти из клана", callback_data=f"{_CLAN_CB}:leave:{clan_id}")
    builder.adjust(1)
    return builder.as_markup()


def _clan_list_markup(clans: list[tuple]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for clan_id, name, cnt in clans:
        builder.button(
            text=f"{escape(name)} ({cnt} участн.)",
            callback_data=f"{_CLAN_CB}:info:{clan_id}",
        )
    builder.adjust(1)
    return builder.as_markup()


async def _build_clan_card_text(
    bot: Bot, session: AsyncSession, *, clan: ClanModel, chat_id: int
) -> str:
    members = await _get_members_with_names(bot, session, clan_id=clan.id, chat_id=chat_id)
    creator_link = next(
        (format_user_link(user_id=uid, label=name) for uid, name in members if uid == clan.creator_user_id),
        format_user_link(user_id=clan.creator_user_id, label=f"id:{clan.creator_user_id}"),
    )
    members_html = "\n".join(
        f"  {'👑' if uid == clan.creator_user_id else '•'} {format_user_link(user_id=uid, label=name)}"
        for uid, name in members
    )
    return (
        f"🏰 <b>Клан «{escape(clan.name)}»</b>\n"
        f"<b>Создатель:</b> {creator_link}\n"
        f"<b>Участников:</b> {len(members)}\n\n"
        f"{members_html}"
    )


async def _send_clan_card(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    *,
    clan: ClanModel,
    viewer_user_id: int,
) -> None:
    chat_id = message.chat.id
    viewer_row = await _get_user_clan_row(session, chat_id=chat_id, user_id=viewer_user_id)
    viewer_is_member = viewer_row is not None and viewer_row.id == clan.id
    viewer_is_creator = clan.creator_user_id == viewer_user_id

    text = await _build_clan_card_text(bot, session, clan=clan, chat_id=chat_id)
    markup = _clan_card_markup(clan.id, viewer_is_member=viewer_is_member, viewer_is_creator=viewer_is_creator)
    await message.reply(text, parse_mode="HTML", reply_markup=markup)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@router.message(F.text.lower().in_({"клан", "мой клан", "мой клан "}))
async def my_clan_handler(message: Message, bot: Bot, db_session: AsyncSession) -> None:
    if message.from_user is None or message.chat is None:
        return
    user_id = message.from_user.id
    chat_id = message.chat.id

    row = await _get_user_clan_row(db_session, chat_id=chat_id, user_id=user_id)
    if row is None:
        await message.reply(
            "Ты не состоишь ни в одном клане.\n\n"
            "• <b>кланы</b> — посмотреть все кланы чата\n"
            "• <b>создать клан [название]</b> — создать свой клан",
            parse_mode="HTML",
        )
        return

    clan = await _get_clan_by_id(db_session, clan_id=row.id, chat_id=chat_id)
    if clan is None:
        await message.reply("Клан не найден.")
        return

    await _send_clan_card(message, bot, db_session, clan=clan, viewer_user_id=user_id)


@router.message(F.text.lower().in_({"кланы", "список кланов", "все кланы"}))
async def clans_list_handler(message: Message, db_session: AsyncSession) -> None:
    if message.chat is None:
        return
    chat_id = message.chat.id

    clans = await _list_clans_in_chat(db_session, chat_id=chat_id)
    if not clans:
        await message.reply(
            "В этом чате пока нет кланов.\n"
            "Создай первый: <b>создать клан [название]</b>",
            parse_mode="HTML",
        )
        return

    lines = [f"🏰 <b>Кланы чата ({len(clans)})</b>"]
    for clan_id, name, cnt in clans:
        lines.append(f"  • <b>{escape(name)}</b> — {cnt} участн. (ID: <code>{clan_id}</code>)")

    markup = _clan_list_markup(clans)
    await message.reply("\n".join(lines), parse_mode="HTML", reply_markup=markup)


@router.message(F.text.lower().startswith("создать клан "))
async def create_clan_handler(message: Message, bot: Bot, db_session: AsyncSession) -> None:
    if message.from_user is None or message.chat is None:
        return
    user_id = message.from_user.id
    chat_id = message.chat.id

    raw = (message.text or "").strip()
    name = raw[len("создать клан "):].strip()
    if not name:
        await message.reply("Укажи название: <b>создать клан [название]</b>", parse_mode="HTML")
        return
    if len(name) > 64:
        await message.reply("Название клана не может быть длиннее 64 символов.")
        return

    existing = await _get_user_clan_row(db_session, chat_id=chat_id, user_id=user_id)
    if existing is not None:
        await message.reply(
            f"Ты уже состоишь в клане <b>{escape(existing.name)}</b>. Сначала выйди из него.",
            parse_mode="HTML",
        )
        return

    clan = ClanModel(chat_id=chat_id, name=name, creator_user_id=user_id)
    db_session.add(clan)
    try:
        await db_session.flush()
    except IntegrityError:
        await db_session.rollback()
        await message.reply(
            f"Клан с названием <b>{escape(name)}</b> уже существует в этом чате.",
            parse_mode="HTML",
        )
        return

    member = ClanMemberModel(clan_id=clan.id, user_id=user_id, chat_id=chat_id)
    db_session.add(member)
    await db_session.flush()

    await _send_clan_card(message, bot, db_session, clan=clan, viewer_user_id=user_id)


@router.message(F.text.lower().in_({"удалить клан", "удалить клан "}))
async def delete_clan_handler(message: Message, db_session: AsyncSession) -> None:
    if message.from_user is None or message.chat is None:
        return
    user_id = message.from_user.id
    chat_id = message.chat.id

    row = await _get_user_clan_row(db_session, chat_id=chat_id, user_id=user_id)
    if row is None:
        await message.reply("Ты не состоишь ни в одном клане.")
        return
    if row.creator_user_id != user_id:
        await message.reply("Только создатель клана может его удалить.")
        return

    await db_session.execute(delete(ClanModel).where(ClanModel.id == row.id))
    await db_session.flush()

    await message.reply(
        f"Клан <b>{escape(row.name)}</b> удалён.",
        parse_mode="HTML",
    )


@router.message(F.text.lower().startswith("вступить в клан "))
async def join_clan_handler(message: Message, bot: Bot, db_session: AsyncSession) -> None:
    if message.from_user is None or message.chat is None:
        return
    user_id = message.from_user.id
    chat_id = message.chat.id

    raw = (message.text or "").strip()
    query = raw[len("вступить в клан "):].strip()
    if not query:
        await message.reply("Укажи название или ID клана: <b>вступить в клан [название или id]</b>\nСписок кланов: <b>кланы</b>", parse_mode="HTML")
        return

    existing = await _get_user_clan_row(db_session, chat_id=chat_id, user_id=user_id)
    if existing is not None:
        await message.reply(
            f"Ты уже состоишь в клане <b>{escape(existing.name)}</b>. Сначала выйди из него.",
            parse_mode="HTML",
        )
        return

    # Resolve by ID or by name
    clan = None
    if query.isdigit():
        clan = await _get_clan_by_id(db_session, clan_id=int(query), chat_id=chat_id)
    if clan is None:
        clan = await _find_clan_by_name(db_session, chat_id=chat_id, name=query)
    if clan is None:
        await message.reply(
            f"Клан <b>{escape(query)}</b> не найден. Посмотри список: <b>кланы</b>",
            parse_mode="HTML",
        )
        return

    member = ClanMemberModel(clan_id=clan.id, user_id=user_id, chat_id=chat_id)
    db_session.add(member)
    try:
        await db_session.flush()
    except IntegrityError:
        await db_session.rollback()
        await message.reply("Не удалось вступить в клан. Возможно, ты уже в нём.")
        return

    await _send_clan_card(message, bot, db_session, clan=clan, viewer_user_id=user_id)


@router.message(F.text.lower().in_({"выйти из клана", "выйти из клана "}))
async def leave_clan_handler(message: Message, db_session: AsyncSession) -> None:
    if message.from_user is None or message.chat is None:
        return
    user_id = message.from_user.id
    chat_id = message.chat.id

    row = await _get_user_clan_row(db_session, chat_id=chat_id, user_id=user_id)
    if row is None:
        await message.reply("Ты не состоишь ни в одном клане.")
        return
    if row.creator_user_id == user_id:
        await message.reply(
            "Создатель не может покинуть клан — только удалить его:\n<b>удалить клан</b>",
            parse_mode="HTML",
        )
        return

    await db_session.execute(
        delete(ClanMemberModel).where(
            ClanMemberModel.clan_id == row.id,
            ClanMemberModel.user_id == user_id,
        )
    )
    await db_session.flush()

    await message.reply(
        f"Ты вышел из клана <b>{escape(row.name)}</b>.",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith(f"{_CLAN_CB}:info:"))
async def clan_info_callback(query: CallbackQuery, bot: Bot, db_session: AsyncSession) -> None:
    await _safe_callback_answer(query)
    if query.message is None or query.from_user is None:
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return
    clan_id = int(parts[2])
    chat_id = query.message.chat.id
    viewer_user_id = query.from_user.id

    clan = await _get_clan_by_id(db_session, clan_id=clan_id, chat_id=chat_id)
    if clan is None:
        await query.message.answer("Клан не найден.")
        return

    viewer_row = await _get_user_clan_row(db_session, chat_id=chat_id, user_id=viewer_user_id)
    viewer_is_member = viewer_row is not None and viewer_row.id == clan_id
    viewer_is_creator = clan.creator_user_id == viewer_user_id

    text = await _build_clan_card_text(bot, db_session, clan=clan, chat_id=chat_id)
    markup = _clan_card_markup(clan_id, viewer_is_member=viewer_is_member, viewer_is_creator=viewer_is_creator)
    await query.message.answer(text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data.startswith(f"{_CLAN_CB}:join:"))
async def join_clan_callback(query: CallbackQuery, bot: Bot, db_session: AsyncSession) -> None:
    await _safe_callback_answer(query)
    if query.message is None or query.from_user is None:
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return
    clan_id = int(parts[2])
    chat_id = query.message.chat.id
    user_id = query.from_user.id

    existing = await _get_user_clan_row(db_session, chat_id=chat_id, user_id=user_id)
    if existing is not None:
        await query.answer(f"Ты уже в клане «{existing.name}».", show_alert=True)
        return

    clan = await _get_clan_by_id(db_session, clan_id=clan_id, chat_id=chat_id)
    if clan is None:
        await query.answer("Клан не найден.", show_alert=True)
        return

    member = ClanMemberModel(clan_id=clan_id, user_id=user_id, chat_id=chat_id)
    db_session.add(member)
    try:
        await db_session.flush()
    except IntegrityError:
        await db_session.rollback()
        await query.answer("Не удалось вступить в клан.", show_alert=True)
        return

    await query.answer(f"Ты вступил в клан «{clan.name}»!", show_alert=True)

    viewer_is_creator = clan.creator_user_id == user_id
    text = await _build_clan_card_text(bot, db_session, clan=clan, chat_id=chat_id)
    markup = _clan_card_markup(clan_id, viewer_is_member=True, viewer_is_creator=viewer_is_creator)
    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except Exception:
        pass


@router.callback_query(F.data.startswith(f"{_CLAN_CB}:leave:"))
async def leave_clan_callback(query: CallbackQuery, bot: Bot, db_session: AsyncSession) -> None:
    await _safe_callback_answer(query)
    if query.message is None or query.from_user is None:
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return
    clan_id = int(parts[2])
    chat_id = query.message.chat.id
    user_id = query.from_user.id

    row = await _get_user_clan_row(db_session, chat_id=chat_id, user_id=user_id)
    if row is None or row.id != clan_id:
        await query.answer("Ты не в этом клане.", show_alert=True)
        return
    if row.creator_user_id == user_id:
        await query.answer("Создатель не может покинуть клан — только удалить его.", show_alert=True)
        return

    await db_session.execute(
        delete(ClanMemberModel).where(
            ClanMemberModel.clan_id == clan_id,
            ClanMemberModel.user_id == user_id,
        )
    )
    await db_session.flush()

    await query.answer(f"Ты вышел из клана «{row.name}».", show_alert=True)

    clan = await _get_clan_by_id(db_session, clan_id=clan_id, chat_id=chat_id)
    if clan is None:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    text = await _build_clan_card_text(bot, db_session, clan=clan, chat_id=chat_id)
    markup = _clan_card_markup(clan_id, viewer_is_member=False, viewer_is_creator=False)
    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except Exception:
        pass


@router.callback_query(F.data.startswith(f"{_CLAN_CB}:delete:"))
async def delete_clan_callback(query: CallbackQuery, db_session: AsyncSession) -> None:
    await _safe_callback_answer(query)
    if query.message is None or query.from_user is None:
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return
    clan_id = int(parts[2])
    chat_id = query.message.chat.id
    user_id = query.from_user.id

    clan = await _get_clan_by_id(db_session, clan_id=clan_id, chat_id=chat_id)
    if clan is None:
        await query.answer("Клан не найден.", show_alert=True)
        return
    if clan.creator_user_id != user_id:
        await query.answer("Только создатель может удалить клан.", show_alert=True)
        return

    clan_name = clan.name
    await db_session.execute(delete(ClanModel).where(ClanModel.id == clan_id))
    await db_session.flush()

    await query.answer(f"Клан «{clan_name}» удалён.", show_alert=True)
    try:
        await query.message.edit_text(
            f"🏚 Клан <b>{escape(clan_name)}</b> был удалён.",
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception:
        pass
