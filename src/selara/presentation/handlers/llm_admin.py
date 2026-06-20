from __future__ import annotations

import json
import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from selara.core.chat_settings import ChatSettings
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.llm_repository import LlmRepository
from selara.infrastructure.llm.client import LlmClient, LlmClientError
from selara.infrastructure.llm.context import load_context, maybe_compress, save_interaction
from selara.infrastructure.llm.prompts import (
    ADMIN_SYSTEM_PROMPT,
    DM_SUMMARY_SYSTEM_PROMPT,
    MAX_TOKENS_DM_SUMMARY,
)
from selara.infrastructure.llm.tools import ToolCall, ToolResult, execute_tool, get_tool_definitions, get_tool_status
from selara.presentation.auth import has_permission

log = logging.getLogger(__name__)

router = Router(name="llm_admin")

_MAX_TOOL_ROUNDS = 8


@router.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.text.regexp(r"^\?\?"),
)
async def llm_admin_context_handler(
    message: Message,
    bot: Bot,
    activity_repo: Any,
    chat_settings: ChatSettings,
    llm_client: LlmClient,
    db_session: AsyncSession,
) -> None:
    await _handle(message, bot, activity_repo, chat_settings, llm_client, db_session, with_context=True)


@router.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.text.regexp(r"^\?(?!\?)"),
)
async def llm_admin_nocontext_handler(
    message: Message,
    bot: Bot,
    activity_repo: Any,
    chat_settings: ChatSettings,
    llm_client: LlmClient,
    db_session: AsyncSession,
) -> None:
    await _handle(message, bot, activity_repo, chat_settings, llm_client, db_session, with_context=False)


async def _handle(
    message: Message,
    bot: Bot,
    activity_repo: Any,
    chat_settings: ChatSettings,
    llm_client: LlmClient,
    db_session: AsyncSession,
    *,
    with_context: bool,
) -> None:
    if not chat_settings.llm_enabled:
        return

    if message.from_user is None:
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
        permission="moderate_users",
        bootstrap_if_missing_owner=False,
    )
    if not allowed:
        await message.reply("⛔ Недостаточно прав для AI-ассистента (нужна роль junior_admin и выше).")
        return

    raw_text = message.text or ""
    prefix = "??" if with_context else "?"
    query = raw_text[len(prefix):].strip()
    if not query:
        await message.reply(f"Введите запрос после {prefix}")
        return

    thinking_msg = await message.reply("⏳ Думаю...")

    actor = UserSnapshot(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
    )
    chat_snapshot = ChatSnapshot(
        telegram_chat_id=message.chat.id,
        chat_type=message.chat.type,
        title=message.chat.title,
    )
    llm_repo = LlmRepository(db_session)

    context_messages: list[dict] = []
    if with_context:
        loaded = await load_context(chat_id=message.chat.id, llm_repo=llm_repo)
        context_messages = loaded.messages

    admin_tag = f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
    import os
    from selara.infrastructure.llm.tools import _BOT_DOCS_DIR
    doc_files = []
    if os.path.exists(_BOT_DOCS_DIR):
        for filename in sorted(os.listdir(_BOT_DOCS_DIR)):
            if filename.endswith(".md"):
                filepath = os.path.join(_BOT_DOCS_DIR, filename)
                title = filename
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        first_line = f.readline().strip()
                        if first_line.startswith("#"):
                            title = first_line.lstrip("#").strip()
                except Exception:
                    pass
                doc_files.append(f"- {filename}: {title}")
    doc_files_list = "\n".join(doc_files) if doc_files else "(нет доступных документов)"

    system_prompt = ADMIN_SYSTEM_PROMPT.format(
        chat_title=message.chat.title or str(message.chat.id),
        chat_id=message.chat.id,
        admin_tag=admin_tag,
        admin_user_id=message.from_user.id,
        doc_files_list=doc_files_list,
    )

    user_content = f"[{message.from_user.first_name or admin_tag}] {admin_tag}: {query}"

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        *context_messages,
        {"role": "user", "content": user_content},
    ]

    tool_results: list[ToolResult] = []
    tool_messages: list[dict] = []
    final_answer = ""

    tool_ctx = dict(
        chat_snapshot=chat_snapshot,
        actor_snapshot=actor,
        activity_repo=activity_repo,
        llm_repo=llm_repo,
        bot=bot,
    )

    for _round in range(_MAX_TOOL_ROUNDS):
        await bot.send_chat_action(message.chat.id, "typing")
        try:
            response = await llm_client.chat_with_tools(messages=messages, tools=get_tool_definitions())
        except LlmClientError as exc:
            error_text = f"⚠️ Ошибка AI-ассистента: {exc.message}"
            try:
                await thinking_msg.edit_text(error_text)
            except Exception:
                await message.reply(error_text)
            return

        if not response or not response.choices:
            final_answer = "Ассистент не вернул ответ (пустой choices)."
            break

        choice = response.choices[0]
        msg = choice.message

        msg_dict = msg.model_dump(exclude_none=True)
        messages.append(msg_dict)

        if choice.finish_reason == "stop" or not msg.tool_calls:
            final_answer = msg.content or ""
            break

        for tc in msg.tool_calls:
            call = ToolCall(
                name=tc.function.name,
                arguments=json.loads(tc.function.arguments),
                call_id=tc.id,
            )
            status = get_tool_status(call.name, call.arguments)
            if status:
                try:
                    await thinking_msg.edit_text(f"⚙️ {status}")
                except Exception:
                    pass
            result = await execute_tool(call, **tool_ctx)
            tool_results.append(result)
            tool_msg = {
                "role": "tool",
                "tool_call_id": result.call_id,
                "content": result.result_text,
            }
            messages.append(tool_msg)
            tool_messages.append(tool_msg)
    else:
        final_answer = "Ассистент не смог завершить задачу за отведённое число шагов."

    display_text = final_answer or "Ассистент не дал ответа."
    try:
        await thinking_msg.edit_text(display_text)
    except Exception:
        if final_answer:
            await message.reply(final_answer)

    await save_interaction(
        chat_id=message.chat.id,
        admin_user_id=message.from_user.id,
        user_query_content=user_content,
        assistant_response=final_answer,
        tool_messages=tool_messages,
        llm_repo=llm_repo,
        is_context=with_context,
    )

    if with_context:
        await maybe_compress(
            chat_id=message.chat.id,
            threshold=chat_settings.llm_context_threshold,
            llm_repo=llm_repo,
            llm_client=llm_client,
        )

    await _send_dm_summary(
        bot=bot,
        admin_user_id=message.from_user.id,
        chat_title=message.chat.title or str(message.chat.id),
        query=query,
        tool_results=tool_results,
        final_answer=final_answer,
        llm_client=llm_client,
    )


async def _send_dm_summary(
    bot: Bot,
    *,
    admin_user_id: int,
    chat_title: str,
    query: str,
    tool_results: list[ToolResult],
    final_answer: str,
    llm_client: LlmClient,
) -> None:
    trace_parts = [
        f"Чат: {chat_title}",
        f"Запрос: {query}",
    ]
    for tr in tool_results:
        trace_parts.append(f"Вызван инструмент: {tr.name}\nРезультат: {tr.result_text[:500]}")
    trace_parts.append(f"Ответ в чате: {final_answer[:500]}")
    trace_text = "\n\n".join(trace_parts)

    summary_prompt = [
        {"role": "system", "content": DM_SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": trace_text},
    ]

    try:
        dm_text = await llm_client.chat_simple(summary_prompt, max_tokens=MAX_TOKENS_DM_SUMMARY)
    except LlmClientError:
        dm_text = trace_text[:2000]

    reversible = [tr for tr in tool_results if tr.undo_payload is not None and tr.success and tr.db_action_id is not None]
    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text=f"↩ Откатить: {tr.action_description[:40]}",
            callback_data=f"llm_rollback:{tr.db_action_id}",
        )]
        for tr in reversible
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

    try:
        await bot.send_message(
            chat_id=admin_user_id,
            text=f"AI-ассистент: сводка\n\n{dm_text}",
            reply_markup=keyboard,
        )
    except TelegramForbiddenError:
        log.warning("llm_admin: не удалось отправить DM администратору %d (бот заблокирован)", admin_user_id)
    except Exception as exc:
        log.warning("llm_admin: ошибка отправки DM: %s", exc)


@router.callback_query(F.data.startswith("llm_rollback:"))
async def llm_rollback_callback(
    callback: CallbackQuery,
    bot: Bot,
    activity_repo: Any,
    db_session: AsyncSession,
) -> None:
    await callback.answer()

    if callback.from_user is None or callback.message is None:
        return

    parts = (callback.data or "").split(":", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        return
    action_id = int(parts[1])

    llm_repo = LlmRepository(db_session)
    action = await llm_repo.get_admin_action(action_id=action_id)

    if action is None:
        await callback.answer("Действие не найдено.", show_alert=True)
        return
    if action.rolled_back_at is not None:
        await callback.answer("Это действие уже было откачено.", show_alert=True)
        return
    if action.undo_payload_json is None:
        await callback.answer("Это действие нельзя откатить.", show_alert=True)
        return

    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=action.chat_id,
        chat_type="supergroup",
        chat_title=None,
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        first_name=callback.from_user.first_name,
        last_name=callback.from_user.last_name,
        is_bot=bool(callback.from_user.is_bot),
        permission="moderate_users",
        bootstrap_if_missing_owner=False,
    )
    if not allowed:
        await callback.answer("Недостаточно прав для отката.", show_alert=True)
        return

    try:
        await _execute_rollback(
            payload=action.undo_payload_json,
            chat_id=action.chat_id,
            rollback_by=callback.from_user,
            activity_repo=activity_repo,
            bot=bot,
        )
    except Exception as exc:
        log.exception("llm_admin: ошибка отката действия %d", action_id)
        await callback.answer(f"Ошибка отката: {exc}", show_alert=True)
        return

    await llm_repo.mark_rolled_back(
        action_id=action_id,
        rolled_back_by_user_id=callback.from_user.id,
    )

    try:
        original_text = callback.message.text or callback.message.caption or ""
        await callback.message.edit_text(
            original_text + f"\n\n✅ Откат выполнен: {action.action_description}",
            reply_markup=None,
        )
    except Exception:
        pass


async def _execute_rollback(
    payload: dict,
    *,
    chat_id: int,
    rollback_by: Any,
    activity_repo: Any,
    bot: Bot,
) -> None:
    tool = payload.get("tool")
    target_user_id = payload.get("target_user_id")

    rollback_actor = UserSnapshot(
        telegram_user_id=rollback_by.id,
        username=rollback_by.username,
        first_name=rollback_by.first_name,
        last_name=rollback_by.last_name,
        is_bot=bool(rollback_by.is_bot),
    )
    chat_snapshot = ChatSnapshot(
        telegram_chat_id=chat_id,
        chat_type="supergroup",
        title=None,
    )

    if target_user_id is None:
        raise ValueError("undo_payload missing target_user_id")

    from selara.infrastructure.db.models import UserModel

    user_row = await activity_repo._session.get(UserModel, int(target_user_id))
    if user_row is None:
        raise ValueError(f"Пользователь {target_user_id} не найден в БД.")

    target = UserSnapshot(
        telegram_user_id=int(user_row.telegram_user_id),
        username=user_row.username,
        first_name=user_row.first_name,
        last_name=user_row.last_name,
        is_bot=bool(user_row.is_bot),
    )

    if tool == "revoke_rest":
        await activity_repo.revoke_rest(chat=chat_snapshot, actor=rollback_actor, target=target)

    elif tool == "unwarn":
        await activity_repo.apply_moderation_action(
            chat=chat_snapshot,
            actor=rollback_actor,
            target=target,
            action="unwarn",
        )

    elif tool == "unban":
        await activity_repo.apply_moderation_action(
            chat=chat_snapshot,
            actor=rollback_actor,
            target=target,
            action="unban",
        )
        try:
            await bot.unban_chat_member(chat_id=chat_id, user_id=int(target_user_id))
        except Exception as exc:
            log.warning("llm rollback unban: Telegram unban failed: %s", exc)

    elif tool == "set_rank":
        previous_rank = payload.get("previous_rank", "participant")
        await activity_repo.set_bot_role(
            chat=chat_snapshot,
            target=target,
            role=previous_rank,
            assigned_by_user_id=rollback_by.id,
        )

    elif tool == "unpred":
        await activity_repo.apply_moderation_action(
            chat=chat_snapshot,
            actor=rollback_actor,
            target=target,
            action="unpred",
        )

    elif tool == "revoke_persona":
        await activity_repo.clear_chat_persona_label(
            chat_id=chat_id,
            user_id=int(target_user_id),
        )

    else:
        raise ValueError(f"Неизвестный тип отката: {tool}")
