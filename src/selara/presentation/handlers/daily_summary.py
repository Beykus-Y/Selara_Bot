from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

from selara.core.chat_settings import ChatSettings
from selara.domain.entities import ChatSnapshot
from selara.infrastructure.llm.client import LlmClient
from selara.presentation.auth import has_permission
from selara.presentation.daily_summary import attempt_daily_summary_run

router = Router(name="daily_summary")

_NOT_ELIGIBLE_MESSAGES: dict[str, str] = {
    "disabled": "Итоги дня выключены в настройках чата (/setcfg daily_summary_enabled true).",
    "save_message_disabled": "Нужно включить сохранение сообщений: /setcfg save_message true.",
    "chat_write_locked": "Чат сейчас заблокирован (антирейд) — итоги временно недоступны.",
    "not_enough_messages": "Пока маловато сообщений за последние сутки для итогов.",
    "no_settings": "Не удалось прочитать настройки чата.",
}


def _describe_outcome_reason(reason: str) -> str:
    if reason == "already_run_today":
        return "Сегодня итоги уже были отправлены."
    if reason == "claim_lost":
        return "Итоги уже формируются — подождите немного."
    if reason == "pipeline_failed":
        return "Не получилось собрать итоги за последние сутки — попробуйте позже."
    if reason == "send_failed":
        return "Итоги собраны, но не получилось отправить сообщение — попробуйте позже."
    if reason.startswith("not_eligible:"):
        gate = reason.split(":", 1)[1]
        return _NOT_ELIGIBLE_MESSAGES.get(gate, "Итоги дня сейчас недоступны.")
    return "Итоги дня сейчас недоступны."


@router.message(Command("summary"))
async def summary_command(
    message: Message,
    bot: Bot,
    activity_repo,
    chat_settings: ChatSettings,
    session_factory,
    llm_client: LlmClient | None = None,
) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return
    if llm_client is None:
        await message.answer("AI-функции сейчас недоступны — обратитесь к администратору бота.")
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
        permission="manage_settings",
        bootstrap_if_missing_owner=False,
    )
    if not allowed:
        await message.answer("Недостаточно прав для запроса итогов дня.")
        return

    status_message = await message.answer("🧪 Собираю итоги дня, это может занять немного времени…")

    now_utc = datetime.now(timezone.utc)
    chat = ChatSnapshot(telegram_chat_id=message.chat.id, chat_type=message.chat.type, title=message.chat.title)

    outcome = await attempt_daily_summary_run(
        bot=bot,
        session_factory=session_factory,
        llm_client=llm_client,
        chat=chat,
        trigger="manual",
        window_to=now_utc,
        summary_date=now_utc.date(),
        now_utc=now_utc,
    )

    if outcome.sent:
        try:
            await status_message.delete()
        except Exception:
            pass
        return

    await status_message.edit_text(_describe_outcome_reason(outcome.reason))
