from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from html import escape
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.feature_access import AccessReason, FeatureAccessService, message_idempotency_key
from selara.core.chat_settings import ChatSettings
from selara.core.config import Settings
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.llm_repository import LlmRepository
from selara.infrastructure.db.artifact_repository import ArtifactRepository
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.telegram_stars import SqlAlchemyChatEntitlementResolver
from selara.infrastructure.http.web_search import WebSearchClient
from selara.infrastructure.llm.client import LlmCallResult, LlmClient, LlmClientError
from selara.infrastructure.llm.client import LlmAccountingContext
from selara.infrastructure.llm.context import (
    build_glossary_context,
    load_context,
    maybe_compress,
    save_interaction,
)
from selara.infrastructure.llm.features import AiFeature
from selara.infrastructure.llm.prompts import (
    ADMIN_SYSTEM_PROMPT,
)
from selara.infrastructure.llm.tools import (
    ToolCall,
    ToolResult,
    _untrusted,
    build_rollback_call,
    execute_tool,
    get_tool_definitions,
    get_tool_status,
)
from selara.infrastructure.llm.web_tools import (
    WEB_TOOL_NAMES,
    WebToolContext,
    restrict_tools_after_web,
)
from selara.presentation.auth import has_permission, resolve_owner_admin_exemption
from selara.presentation.feature_access_messages import quota_exhausted_message
from selara.presentation.llm_formatting import html_to_plain_text, render_llm_html, split_telegram_html

log = logging.getLogger(__name__)

router = Router(name="llm_admin")

_MAX_TOOL_ROUNDS = 8


@router.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.text.regexp(r"^\?reset\s*$"),
)
async def llm_context_reset_handler(
    message: Message,
    activity_repo: Any,
    db_session: AsyncSession,
) -> None:
    """#11: manual escape hatch for a poisoned/bad conversation context --
    only automatic threshold-triggered summarization existed before, which
    rolls forward potentially-bad context rather than discarding it.
    Registered before the generic `?(?!\\?)` handler so `?reset` is matched
    here first, not treated as a query to the assistant."""
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
        await message.reply("⛔ Недостаточно прав для сброса контекста AI-ассистента.")
        return

    llm_repo = LlmRepository(db_session)
    cleared = await llm_repo.reset_context(chat_id=message.chat.id)
    await message.reply(f"✅ Контекст AI-ассистента сброшен ({cleared} сообщений очищено).")


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
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    web_search_client: WebSearchClient | None = None,
) -> None:
    await _handle(
        message, bot, activity_repo, chat_settings, llm_client, db_session,
        with_context=True, settings=settings, session_factory=session_factory,
        web_search_client=web_search_client,
    )


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
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    web_search_client: WebSearchClient | None = None,
) -> None:
    await _handle(
        message, bot, activity_repo, chat_settings, llm_client, db_session,
        with_context=False, settings=settings, session_factory=session_factory,
        web_search_client=web_search_client,
    )


async def _handle(
    message: Message,
    bot: Bot,
    activity_repo: Any,
    chat_settings: ChatSettings,
    llm_client: LlmClient,
    db_session: AsyncSession,
    *,
    with_context: bool,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    web_search_client: WebSearchClient | None = None,
) -> None:
    if not chat_settings.llm_enabled:
        return

    if message.from_user is None:
        return

    # #34: moderate_users OR the lesser use_llm_readonly permission grants
    # entry to the assistant. Every mutating tool independently re-checks
    # moderate_users (or manage_roles for set_rank) inside execute_tool
    # regardless of which permission got the actor in here.
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
            permission="use_llm_readonly",
            bootstrap_if_missing_owner=False,
        )
    if not allowed:
        # #27: junior_admin's default template does NOT grant moderate_users
        # (only senior_admin+ does by default) -- the old message named the
        # wrong role, misleading admins configuring roles.
        await message.reply(
            "⛔ Недостаточно прав для AI-ассистента (нужна роль senior_admin и выше, "
            "либо кастомная роль с правом использования AI-ассистента)."
        )
        return

    raw_text = message.text or ""
    prefix = "??" if with_context else "?"
    query = raw_text[len(prefix):].strip()
    if not query:
        await message.reply(f"Введите запрос после {prefix}")
        return

    llm_repo = LlmRepository(db_session)

    # #3: cooldown prevents an admin from repeating an invocation immediately.
    if settings is None:
        from selara.core.config import get_settings
        settings = get_settings()
    last_at = await llm_repo.get_last_user_message_at(
        chat_id=message.chat.id, admin_user_id=message.from_user.id,
    )
    if last_at is not None:
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last_at).total_seconds()
        if elapsed < settings.llm_cooldown_seconds:
            wait_left = int(settings.llm_cooldown_seconds - elapsed) + 1
            await message.reply(f"⏳ Слишком часто. Подожди {wait_left} сек.")
            return

    accounting = llm_client.accounting_service if isinstance(llm_client, LlmClient) else None
    thinking_msg = await message.reply("⏳ Думаю...")
    if session_factory is None:
        log.error("Feature quota service unavailable: session factory was not injected chat_id=%s", message.chat.id)
        await thinking_msg.edit_text("⚠️ Проверка доступа временно недоступна. Попробуйте позже.")
        return

    access_service = FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(session_factory),
        entitlement_resolver=SqlAlchemyChatEntitlementResolver(session_factory),
    )
    owner_exempt = await resolve_owner_admin_exemption(
        bot=bot,
        chat_id=message.chat.id,
        admin_user_id=settings.admin_user_id,
    )
    try:
        decision = await access_service.reserve_feature_usage(
            feature=AiFeature.LLM_ADMIN,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            chat_title=message.chat.title,
            actor_user_id=message.from_user.id,
            actor_is_bot=bool(message.from_user.is_bot),
            trigger="telegram_message",
            timezone_name=settings.bot_timezone,
            idempotency_key=message_idempotency_key(
                feature=AiFeature.LLM_ADMIN,
                chat_id=message.chat.id,
                source_message_id=message.message_id,
            ),
            source_message_id=message.message_id,
            mode="context" if with_context else "no_context",
            owner_exempt=owner_exempt,
        )
    except Exception:
        log.exception("Feature quota reservation failed chat_id=%s message_id=%s", message.chat.id, message.message_id)
        await thinking_msg.edit_text("⚠️ Проверка доступа временно недоступна. Попробуйте позже.")
        return

    if decision.reused:
        await thinking_msg.edit_text("Этот запрос уже был обработан. Повторный запуск не выполнялся.")
        return
    if not decision.allowed:
        if decision.reason == AccessReason.QUOTA_EXHAUSTED:
            text = quota_exhausted_message(decision, timezone_name=settings.bot_timezone)
        else:
            text = "⚠️ Сейчас не удалось разрешить запрос. Попробуйте позже."
        await thinking_msg.edit_text(text)
        return

    invocation_id = decision.invocation_id
    call_context = LlmAccountingContext(
        invocation_id=invocation_id, feature=AiFeature.LLM_ADMIN, stage="assistant_round",
        chat_id=message.chat.id, actor_user_id=message.from_user.id,
        telegram_message_id=message.message_id,
    ) if invocation_id is not None else None

    outcome = {"status": "failed", "error_category": "handler_error"}

    async def _run_invocation() -> None:
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

        # #1: chat title is renameable by anyone with Telegram's "change group
        # info" right (not any bot permission) and rides into the system prompt
        # on every future call. Marking it as untrusted data is defense-in-depth
        # only, not a security boundary -- authorization stays entirely in
        # execute_tool()'s deterministic checks regardless of what the model
        # does with this text.
        system_prompt = ADMIN_SYSTEM_PROMPT.format(
            chat_title=_untrusted(message.chat.title) if message.chat.title else str(message.chat.id),
            chat_id=message.chat.id,
            admin_tag=admin_tag,
            admin_user_id=message.from_user.id,
            doc_files_list=doc_files_list,
        )

        user_content = f"[{message.from_user.first_name or admin_tag}] {admin_tag}: {query}"
        glossary_context = await build_glossary_context(chat_id=message.chat.id, query=query, llm_repo=llm_repo)

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            *context_messages,
            *([glossary_context] if glossary_context else []),
            {"role": "user", "content": user_content},
        ]

        tool_results: list[ToolResult] = []
        tool_messages: list[dict] = []
        final_answer = ""
        empty_completions = 0

        from selara.infrastructure.llm.artifact_tools import ArtifactRequestContext
        artifact_context = ArtifactRequestContext(
            repository=ArtifactRepository(db_session), renderer_url=getattr(settings, "artifact_renderer_url", "http://artifact-renderer:8090"),
            chat_id=message.chat.id, creator_id=message.from_user.id, message_id=message.message_id,
            thread_id=message.message_thread_id,
        )

        tool_ctx = dict(
            artifact_context=artifact_context,
            chat_snapshot=chat_snapshot,
            actor_snapshot=actor,
            activity_repo=activity_repo,
            llm_repo=llm_repo,
            bot=bot,
            web_context=WebToolContext(
                client=web_search_client,
                max_calls=settings.web_search_max_calls_per_invocation,
                max_results=settings.web_search_max_results,
                max_page_chars=settings.web_search_max_page_chars,
            ),
        )
        # Web tools are only advertised when a search client is wired in; a
        # stray model call still gets a corrective error from the executor.
        available_tools = get_tool_definitions(
            exclude=None if web_search_client is not None else WEB_TOOL_NAMES
        )

        for _round in range(_MAX_TOOL_ROUNDS):
            try:
                await bot.send_chat_action(message.chat.id, "typing")
            except Exception:
                pass
            try:
                request_kwargs = {"messages": messages, "tools": available_tools}
                if call_context is not None:
                    request_kwargs["accounting_context"] = call_context
                response = await llm_client.chat_with_tools(**request_kwargs)
            except LlmClientError as exc:
                outcome["status"] = "failed"
                outcome["error_category"] = exc.usages[-1].error_category if exc.usages else "provider_error"
                error_text = f"⚠️ Ошибка AI-ассистента: {exc.message}"
                try:
                    await thinking_msg.edit_text(error_text)
                except Exception:
                    await message.reply(error_text)
                return
            except Exception:
                # e.g. the accounting marker could not be committed before the provider call
                # (fail-closed). Without this the "Думаю..." placeholder would stay forever.
                log.exception("llm_admin: LLM request failed before reaching the provider")
                outcome["status"] = "failed"
                outcome["error_category"] = "accounting_unavailable"
                error_text = "⚠️ Ошибка AI-ассистента: не удалось выполнить запрос. Попробуйте позже."
                try:
                    await thinking_msg.edit_text(error_text)
                except Exception:
                    await message.reply(error_text)
                return

            response = response.value if isinstance(response, LlmCallResult) else response
            if not response or not response.choices:
                log.warning("llm_admin: empty choices after %d tools", len(tool_results))
                if empty_completions < 2:
                    empty_completions += 1
                    messages.append(_empty_answer_recovery())
                    continue
                final_answer = _verified_fallback(tool_results)
                break

            choice = response.choices[0]
            msg = choice.message

            # Some compatible providers report stop even with executable tool calls.
            # Actual calls take precedence over that metadata.
            if not msg.tool_calls:
                if (msg.content or "").strip():
                    final_answer = msg.content
                    break
                log.warning("llm_admin: empty completion finish_reason=%s after %d tools; created=%d sent=%d",
                    choice.finish_reason, len(tool_results), len(artifact_context.created_artifacts),
                    len(artifact_context.sent_artifacts))
                if empty_completions < 2:
                    empty_completions += 1
                    # Do not send a null assistant message back to strict providers.
                    messages.append(_empty_answer_recovery())
                    continue
                final_answer = _verified_fallback(tool_results)
                break

            messages.append(msg.model_dump(exclude_none=True))
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
                if result.success and result.db_action_id is not None:
                    # #22: commit immediately so a crash on a *later* round can
                    # no longer roll back an already-completed action's DB state
                    # and audit row while the real Telegram side effect stands.
                    await db_session.commit()
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": result.call_id,
                    "content": result.result_text,
                }
                messages.append(tool_msg)
                tool_messages.append(tool_msg)
                if call.name in WEB_TOOL_NAMES:
                    # Deterministic confused-deputy guard: untrusted web content
                    # is now in the model context, so mutating tools are
                    # withdrawn for the rest of this invocation (web tools go
                    # too once their budget is spent) -- see
                    # web_tools.restrict_tools_after_web.
                    available_tools = restrict_tools_after_web(available_tools, web_context)
                if call.name == "send_artifact" and result.success and artifact_context.sent_artifacts:
                    # The caption is the answer. Do not request another completion or
                    # execute trailing tools after a confirmed delivered answer.
                    final_answer = str(call.arguments.get("caption", ""))
                    break
            if artifact_context.sent_artifacts and result.success and call.name == "send_artifact":
                break
        else:
            final_answer = _verified_fallback(tool_results)

        if artifact_context.sent_artifacts:
            try:
                await thinking_msg.delete()
            except Exception:
                log.warning("Could not remove artifact progress message", exc_info=True)
        else:
            final_answer = final_answer.strip() or _verified_fallback(tool_results)
            await _send_formatted_answer(message, thinking_msg, final_answer)
        chat_answer = final_answer
        if artifact_context.sent_artifacts:
            final_answer += "\nАртефакты этого чата: " + ", ".join(artifact_context.sent_artifacts)

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
                accounting_context=(LlmAccountingContext(
                    invocation_id=invocation_id, feature=AiFeature.LLM_CONTEXT_COMPRESSION, stage="context_compression",
                    chat_id=message.chat.id, actor_user_id=message.from_user.id,
                    telegram_message_id=message.message_id,
                ) if invocation_id is not None else None),
            )

        await _send_dm_summary(
            bot=bot,
            admin_user_id=message.from_user.id,
            chat_title=message.chat.title or str(message.chat.id),
            query=query,
            tool_results=tool_results,
            final_answer=chat_answer,
            sent_artifacts=artifact_context.sent_artifacts,
        )
        outcome["status"] = "succeeded"
        outcome["error_category"] = None

    try:
        await _run_invocation()
    finally:
        if accounting is not None and invocation_id is not None:
            if outcome["status"] != "succeeded":
                try:
                    await access_service.release_if_no_provider_attempts(
                        invocation_id=invocation_id,
                        reason=outcome["error_category"] or "pre_provider_failure",
                    )
                except Exception:
                    log.exception("Could not release unused feature quota invocation_id=%s", invocation_id)
            try:
                await accounting.finish_invocation_outcome(
                    invocation_id=invocation_id,
                    status=outcome["status"],
                    error_category=outcome["error_category"],
                )
            except Exception:
                log.exception("Could not finalize llm_admin invocation id=%s", invocation_id)


def _empty_answer_recovery() -> dict:
    return {"role": "user", "content": (
        "Предыдущий шаг не вернул текста или инструментов. Продолжи исходный запрос, используя уже полученные результаты. "
        "Не повторяй выполненные действия. Если нужен артефакт, создай его по прочитанному скиллу и отправь через send_artifact; "
        "если уже создан — используй его известный ID. Не повторяй отправку со статусом uncertain. "
        "Если выполнить запрос не можешь, дай честный непустой ответ с доступными данными. "
        "Не заявляй, что картинка отправлена, без успешного send_artifact.")}


def _verified_fallback(tool_results: list[ToolResult]) -> str:
    lines = ["Не удалось завершить подготовку ответа."]
    tops = {}
    for tr in tool_results:
        if tr.name != "get_top" or not tr.success:
            continue
        try:
            data = json.loads(tr.result_text)
            if data.get("mode") in {"activity", "karma"} and isinstance(data.get("top"), list):
                tops[(data["mode"], data.get("period"))] = data
        except (ValueError, TypeError, AttributeError):
            continue
    if tops:
        lines.append("Показываю полученные данные текстом.")
    periods = {"30d": "последние 30 дней", "7d": "последние 7 дней", "all_time": "всё время"}
    for (mode, period), data in tops.items():
        lines.append("\n" + ("Активность" if mode == "activity" else "Карма") + " — " + periods.get(period, "указанный период") + ":")
        if not data["top"]:
            lines.append("Участников в этом топе нет.")
        for i, row in enumerate(data["top"][:10], 1):
            if not isinstance(row, dict):
                continue
            value = row.get("messages" if mode == "activity" else "karma")
            if type(value) not in {int, float}:
                continue
            name = row.get("username") or f"Участник {row.get('user_id', i)}"
            lines.append(f"{i}. {name} — {value}" + (" сообщений" if mode == "activity" else ""))
    actions = [tr.action_description for tr in tool_results if tr.success and tr.db_action_id is not None]
    if actions:
        lines.append("\nПодтверждённые действия: " + "; ".join(actions))
    if any(tr.name in {"read_skill", "create_artifact", "send_artifact"} for tr in tool_results):
        lines.append("\nОтправка изображения не подтверждена.")
    return "\n".join(lines)


async def _send_formatted_answer(message: Message, thinking_msg: Message, text: str) -> None:
    for index, chunk in enumerate(render_llm_html(text)):
        if index == 0:
            try:
                await thinking_msg.edit_text(chunk, parse_mode="HTML")
                continue
            except Exception as exc:
                log.warning("llm_admin: editing answer failed, sending reply: %s", exc)
        try:
            await message.reply(chunk, parse_mode="HTML")
        except TelegramBadRequest:
            # Preserve the answer if Telegram rejects a particular entity.
            await message.reply(html_to_plain_text(chunk), parse_mode=None)


async def _send_dm_summary(
    bot: Bot,
    *,
    admin_user_id: int,
    chat_title: str,
    query: str,
    tool_results: list[ToolResult],
    final_answer: str,
    sent_artifacts: list[str] | None = None,
) -> None:
    # This is an operational receipt, not a generative retelling. Preserve
    # actual outcomes and the actual sent answer, including failures.
    lines = [f"Чат: {chat_title[:200]}", f"Запрос: {query[:500]}"]
    for tr in tool_results:
        if tr.success:
            lines.append(f"Получен результат: {tr.action_description or tr.name}")
        else:
            try:
                error = json.loads(tr.result_text).get("error", tr.result_text)
            except (ValueError, AttributeError):
                error = tr.result_text
            lines.append(f"Ошибка {tr.name}: {str(error)[:700]}")
    if not tool_results:
        lines.append("Инструменты не выполнялись.")
    if sent_artifacts or any(tr.name in {"read_skill", "create_artifact", "send_artifact"} for tr in tool_results):
        lines.append(f"Подтверждённо отправлено артефактов: {len(sent_artifacts or [])}.")
        if not sent_artifacts:
            lines.append("Отправка изображения не подтверждена.")
    lines.append("Ответ в чате:\n" + (final_answer[:2000] or "Текстовый ответ отсутствует."))
    dm_text = "\n\n".join(lines)

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
        for index, chunk in enumerate(split_telegram_html(escape(f"AI-ассистент: сводка\n\n{dm_text}"))):
            kwargs = {"chat_id": admin_user_id, "reply_markup": keyboard if index == 0 else None}
            try:
                await bot.send_message(text=chunk, parse_mode="HTML", **kwargs)
            except TelegramBadRequest:
                await bot.send_message(text=html_to_plain_text(chunk), parse_mode=None, **kwargs)
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

    # #35: claim the rollback atomically *before* executing the side effect,
    # so a concurrent second click can never observe "not yet rolled back"
    # and double-fire a non-idempotent undo (unwarn/unpred).
    claimed = await llm_repo.mark_rolled_back(
        action_id=action_id,
        rolled_back_by_user_id=callback.from_user.id,
    )
    if not claimed:
        await callback.answer("Это действие уже было откачено.", show_alert=True)
        return

    try:
        result = await _execute_rollback(
            payload=action.undo_payload_json,
            chat_id=action.chat_id,
            rollback_by=callback.from_user,
            activity_repo=activity_repo,
            llm_repo=llm_repo,
            bot=bot,
        )
    except Exception as exc:
        log.exception("llm_admin: ошибка отката действия %d", action_id)
        await llm_repo.clear_rollback_claim(action_id=action_id)
        await callback.answer(f"Ошибка отката: {exc}", show_alert=True)
        return

    if not result.success:
        # No side effect happened (auth/validation failed at click time) --
        # release the claim so the action isn't falsely reported as rolled
        # back and can be retried once the underlying condition changes.
        await llm_repo.clear_rollback_claim(action_id=action_id)
        error_text = json.loads(result.result_text).get("error", "неизвестная ошибка")
        await callback.answer(f"Ошибка отката: {error_text}", show_alert=True)
        return

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
    llm_repo: LlmRepository,
    bot: Bot,
) -> ToolResult:
    """Route a rollback through the exact same execute_tool()/
    _moderation_target_error dispatcher as every forward moderation tool
    call (fixes #21 -- this used to call repository methods directly,
    bypassing authorization; only set_rank re-implemented its check by
    hand, and the other 5 rollback types had none at all)."""
    call = build_rollback_call(payload, call_id=f"rollback:{payload.get('tool')}")

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

    return await execute_tool(
        call,
        chat_snapshot=chat_snapshot,
        actor_snapshot=rollback_actor,
        activity_repo=activity_repo,
        llm_repo=llm_repo,
        bot=bot,
    )
