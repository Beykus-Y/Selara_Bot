from aiogram import F, Router
from aiogram.types import Message

from selara.application.use_cases.vote_karma import execute as vote_karma
from selara.core.chat_settings import ChatSettings
from selara.domain.entities import UserSnapshot
from selara.presentation.handlers.activity import format_user_label

router = Router(name="engagement")


def _vote_value(text: str) -> int | None:
    cleaned = text.strip()
    if cleaned == "+":
        return 1
    if cleaned == "-":
        return -1
    return None


@router.message(F.text.regexp(r"^\s*[+-]\s*$"))
async def vote_message_handler(message: Message, activity_repo, chat_settings: ChatSettings) -> None:
    text = message.text or ""
    vote_value = _vote_value(text)
    if vote_value is None:
        return

    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Голосование доступно только в группах")
        return

    if message.from_user is None:
        return

    if message.reply_to_message is None or message.reply_to_message.from_user is None:
        await message.answer("Чтобы поставить голос, ответьте '+' или '-' на сообщение пользователя")
        return

    voter = UserSnapshot(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=message.from_user.is_bot,
    )
    target_user = message.reply_to_message.from_user
    target = UserSnapshot(
        telegram_user_id=target_user.id,
        username=target_user.username,
        first_name=target_user.first_name,
        last_name=target_user.last_name,
        is_bot=target_user.is_bot,
    )

    result = await vote_karma(
        repo=activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        voter=voter,
        target=target,
        vote_value=vote_value,
        event_at=message.date,
        daily_limit=chat_settings.vote_daily_limit,
        days_for_7d=chat_settings.leaderboard_7d_days,
    )

    if not result.accepted:
        await message.answer(result.reason or "Голос не принят")
        return

    target_label = await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=target_user.id)
    if not target_label:
        target_label = format_user_label(target_user)
    sign = "+" if vote_value > 0 else "-"
    await message.answer(
        f"Голос {sign} засчитан для {target_label}. "
        f"Карма за всё время: {result.target_karma_all_time}; за 7 дней: {result.target_karma_7d}"
    )
