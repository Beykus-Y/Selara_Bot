from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from selara.infrastructure.db.models import UserFeatureRequestModel, UserModel

router = Router(name="feedback")
_FEEDBACK_PREFIXES = {
    "идея": "Предложение",
    "предложение": "Предложение",
    "ошибка": "Проблема",
    "проблема": "Проблема",
    "поддержка": "Поддержка",
}


@router.message(Command("feedback"))
async def feedback_command(message: Message, command: CommandObject, db_session: AsyncSession) -> None:
    if message.chat.type != "private":
        await message.answer("Отправь обращение мне в личку: /feedback текст обращения")
        return
    if message.from_user is None:
        return

    text = (command.args or "").strip()
    if not text:
        await message.answer(
            "Напиши обращение после команды:\n"
            "<code>/feedback предложение: добавить ...</code>\n"
            "<code>/feedback проблема: бот не отвечает ...</code>",
            parse_mode="HTML",
        )
        return
    if len(text) > 4000:
        await message.answer("Обращение слишком длинное. Максимум — 4000 символов.")
        return

    category = "Обратная связь"
    first_line = text.splitlines()[0].strip()
    if ":" in first_line:
        prefix, remainder = first_line.split(":", 1)
        matched_category = _FEEDBACK_PREFIXES.get(prefix.strip().lower())
        if matched_category:
            category = matched_category
            first_line = remainder.strip() or category
    title = f"{category}: {first_line}"[:160]

    user = message.from_user
    await db_session.execute(
        pg_insert(UserModel)
        .values(
            telegram_user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            is_bot=bool(user.is_bot),
        )
        .on_conflict_do_update(
            index_elements=[UserModel.telegram_user_id],
            set_={
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "is_bot": bool(user.is_bot),
                "updated_at": func.now(),
            },
        )
    )
    request_row = UserFeatureRequestModel(
        user_id=user.id,
        title=title,
        details=text,
        status="open",
    )
    db_session.add(request_row)
    await db_session.flush()
    request_id = int(request_row.id)
    await db_session.commit()
    await message.answer(f"Обращение #{request_id} отправлено. Спасибо! Его можно будет посмотреть в админке Selara.")
