from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from selara.infrastructure.db.models import ClanMemberModel, ClanModel
from selara.presentation.formatters import format_user_link
from selara.presentation.handlers.common import safe_callback_answer as _safe_callback_answer

router = Router(name="clans")

_CLAN_CB = "clan"

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


async def _get_member_ids(session: AsyncSession, *, clan_id: int) -> list[int]:
    result = await session.execute(
        select(ClanMemberModel.user_id).where(ClanMemberModel.clan_id == clan_id)
    )
    return [row[0] for row in result.fetchall()]


def _clan_info_markup(clan_id: int, *, can_join: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_join:
        builder.button(text="Вступить", callback_data=f"{_CLAN_CB}:join:{clan_id}")
    return builder.as_markup()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@router.message(F.text.lower().startswith("создать клан "))
async def create_clan_handler(message: Message, db_session: AsyncSession) -> None:
    if message.from_user is None or message.chat is None:
        return
    user_id = message.from_user.id
    chat_id = message.chat.id

    raw = (message.text or "").strip()
    name = raw[len("создать клан "):].strip()
    if not name:
        await message.reply("Укажи название клана: <b>создать клан [название]</b>", parse_mode="HTML")
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

    await message.reply(
        f"Клан <b>{escape(name)}</b> создан! ID клана: <code>{clan.id}</code>\n"
        "Другие могут вступить командой: <b>вступить в клан {id}</b>",
        parse_mode="HTML",
    )


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
async def join_clan_handler(message: Message, db_session: AsyncSession) -> None:
    if message.from_user is None or message.chat is None:
        return
    user_id = message.from_user.id
    chat_id = message.chat.id

    raw = (message.text or "").strip()
    raw_id = raw[len("вступить в клан "):].strip()
    if not raw_id.isdigit():
        await message.reply("Укажи числовой ID клана: <b>вступить в клан [id]</b>", parse_mode="HTML")
        return
    clan_id = int(raw_id)

    existing = await _get_user_clan_row(db_session, chat_id=chat_id, user_id=user_id)
    if existing is not None:
        await message.reply(
            f"Ты уже состоишь в клане <b>{escape(existing.name)}</b>. Сначала выйди из него.",
            parse_mode="HTML",
        )
        return

    clan = await _get_clan_by_id(db_session, clan_id=clan_id, chat_id=chat_id)
    if clan is None:
        await message.reply("Клан с таким ID не найден в этом чате.")
        return

    member = ClanMemberModel(clan_id=clan_id, user_id=user_id, chat_id=chat_id)
    db_session.add(member)
    try:
        await db_session.flush()
    except IntegrityError:
        await db_session.rollback()
        await message.reply("Не удалось вступить в клан. Возможно, ты уже в нём.")
        return

    await message.reply(
        f"Ты вступил в клан <b>{escape(clan.name)}</b>!",
        parse_mode="HTML",
    )


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
        await message.reply("Создатель не может покинуть клан — только удалить его командой <b>удалить клан</b>.", parse_mode="HTML")
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
async def clan_info_callback(query: CallbackQuery, db_session: AsyncSession) -> None:
    await _safe_callback_answer(query)
    if query.message is None or query.from_user is None:
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return
    clan_id = int(parts[2])
    chat_id = query.message.chat.id

    clan = await _get_clan_by_id(db_session, clan_id=clan_id, chat_id=chat_id)
    if clan is None:
        await query.message.answer("Клан не найден.")
        return

    member_count = await _get_member_count(db_session, clan_id=clan_id)
    member_ids = await _get_member_ids(db_session, clan_id=clan_id)

    creator_link = format_user_link(user_id=clan.creator_user_id, label=f"id:{clan.creator_user_id}")
    members_html = "\n".join(
        f"  • {format_user_link(user_id=uid, label=f'id:{uid}')}"
        for uid in member_ids
    )

    viewer_in_clan = await _get_user_clan_row(db_session, chat_id=chat_id, user_id=query.from_user.id)
    can_join = viewer_in_clan is None

    text = (
        f"<b>Клан:</b> {escape(clan.name)} <code>#{clan.id}</code>\n"
        f"<b>Создатель:</b> {creator_link}\n"
        f"<b>Участников:</b> {member_count}\n\n"
        f"<b>Состав:</b>\n{members_html}"
    )

    await query.message.answer(
        text,
        parse_mode="HTML",
        reply_markup=_clan_info_markup(clan_id, can_join=can_join),
    )


@router.callback_query(F.data.startswith(f"{_CLAN_CB}:join:"))
async def join_clan_callback(query: CallbackQuery, db_session: AsyncSession) -> None:
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
    # Refresh the clan info message
    member_count = await _get_member_count(db_session, clan_id=clan_id)
    member_ids = await _get_member_ids(db_session, clan_id=clan_id)
    creator_link = format_user_link(user_id=clan.creator_user_id, label=f"id:{clan.creator_user_id}")
    members_html = "\n".join(
        f"  • {format_user_link(user_id=uid, label=f'id:{uid}')}"
        for uid in member_ids
    )
    text = (
        f"<b>Клан:</b> {escape(clan.name)} <code>#{clan.id}</code>\n"
        f"<b>Создатель:</b> {creator_link}\n"
        f"<b>Участников:</b> {member_count}\n\n"
        f"<b>Состав:</b>\n{members_html}"
    )
    try:
        await query.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=_clan_info_markup(clan_id, can_join=False),
        )
    except Exception:
        pass
