"""Selara's character in a group: call names, character and member mode.

Admins configure everything with ``/selara`` (right ``manage_settings``). Any
member may then address Selara by a call name at the very start of a message
(«Селя, кто сегодня самый активный?») or reply to her member-mode answer. Member
mode has read-only tools only and its own dialogue history, apart from the admin
assistant (``?``/``??``). It is free for every chat with a daily limit per chat and
per member; Selara AI raises both and allows up to five names.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from selara.application.ai_character import ProfileValidationError
from selara.application.ai_character.group import (
    GROUP_CUSTOM_PRESET,
    GROUP_PRESETS,
    MAX_MEMBER_TEXT_LENGTH,
    MEMBER_RECENT_MESSAGES,
    LAST_ROUND_NOTICE,
    MEMBER_REPLY_MAX_TOKENS,
    group_tool_rounds,
    CallName,
    MemberTurn,
    active_call_names,
    build_member_messages,
    call_name_limit,
    find_call,
    group_preset_title,
    normalize_call_name,
    validate_call_name,
    validate_group_custom_character,
)
from selara.application.feature_access import (
    AccessReason,
    AccessTier,
    FeatureAccessService,
    GroupMemberQuotaLimits,
    message_idempotency_key,
)
from selara.core.config import Settings
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.ai_pets import AiPetService
from selara.infrastructure.db.chat_ai_character_repository import ChatAiCharacterRepository, GroupCharacterError
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.llm_repository import LlmRepository
from selara.infrastructure.db.telegram_stars import SqlAlchemyChatEntitlementResolver
from selara.infrastructure.llm.client import LlmAccountingContext, LlmCallResult, LlmClient, LlmClientError
from selara.infrastructure.llm.features import AiFeature
from selara.infrastructure.llm.group_member_tools import execute_member_tool, member_tool_definitions
from selara.infrastructure.llm.tools import ToolCall
from selara.presentation.auth import has_permission, resolve_owner_admin_exemption
from selara.presentation.commands.catalog import match_builtin_command
from selara.presentation.feature_access_messages import quota_exhausted_message
from selara.presentation.llm_formatting import html_to_plain_text, render_llm_html

log = logging.getLogger(__name__)

router = Router(name="group_character")

_GROUP_TYPES = {"group", "supergroup"}
_STATE_TTL_SECONDS = 30.0
_HINT_INTERVAL_SECONDS = 3600.0

# chat_id -> (expires_at, member_mode_enabled, active names): checked on every group message.
_state_cache: dict[int, tuple[float, bool, list[CallName]]] = {}
# (chat_id, actor or 0) -> monotonic time of the last no-LLM hint: one per hour, never a reply per message.
_hint_sent_at: dict[tuple[int, int], float] = {}


def invalidate_call_names(chat_id: int | None) -> None:
    if chat_id is not None:
        _state_cache.pop(chat_id, None)


def _hint_allowed(chat_id: int, actor_id: int = 0) -> bool:
    now = time.monotonic()
    last = _hint_sent_at.get((chat_id, actor_id))
    if last is not None and now - last < _HINT_INTERVAL_SECONDS:
        return False
    _hint_sent_at[(chat_id, actor_id)] = now
    return True


def _limits(settings: Settings) -> GroupMemberQuotaLimits:
    return GroupMemberQuotaLimits.from_settings(settings)


async def chat_has_selara_ai(session_factory, *, chat_id: int, settings: Settings) -> bool:
    """Whether the chat's Selara AI is active now; failures count as a free chat."""
    if session_factory is None:
        return False
    try:
        entitlement = await SqlAlchemyChatEntitlementResolver(
            session_factory, group_member_limits=_limits(settings)
        ).resolve(chat_id=chat_id, feature=AiFeature.GROUP_MEMBER, trigger="telegram_message")
    except Exception:
        log.warning("Selara AI status lookup failed chat_id=%s", chat_id, exc_info=True)
        return False
    if entitlement.access_tier != AccessTier.PAID:
        return False
    valid_until = entitlement.valid_until
    if valid_until is None:
        return True
    if valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=timezone.utc)
    return valid_until > datetime.now(timezone.utc)


async def _chat_state(
    db_session, *, chat_id: int, session_factory, settings: Settings
) -> tuple[bool, list[CallName]]:
    cached = _state_cache.get(chat_id)
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        return cached[1], cached[2]
    repo = ChatAiCharacterRepository(db_session)
    character = await repo.get_character(chat_id=chat_id)
    names: list[CallName] = []
    if character.member_mode_enabled:
        stored = await repo.list_names(chat_id=chat_id)
        if stored:
            paid = len(stored) > 1 and await chat_has_selara_ai(session_factory, chat_id=chat_id, settings=settings)
            names = active_call_names(stored, paid=paid)
    _state_cache[chat_id] = (now + _STATE_TTL_SECONDS, character.member_mode_enabled, names)
    return character.member_mode_enabled, names


async def resolve_group_call(
    message: Message, *, db_session, session_factory, settings: Settings
) -> str | None:
    """Return what the member asked when this group message addresses Selara, else ``None``."""
    if message.chat.type not in _GROUP_TYPES or db_session is None:
        return None
    user = message.from_user
    text = (message.text or "").strip()
    # Bots and anonymous admins/channels (sent on behalf of a bot account) are never members here.
    if user is None or user.is_bot or not text or text[0] in "/?":
        return None
    enabled, names = await _chat_state(
        db_session, chat_id=message.chat.id, session_factory=session_factory, settings=settings
    )
    if not enabled:
        return None
    reply = message.reply_to_message
    if reply is not None and reply.from_user is not None and reply.from_user.is_bot:
        if await ChatAiCharacterRepository(db_session).is_member_answer(
            chat_id=message.chat.id, telegram_message_id=reply.message_id
        ):
            return text
    if not names:
        return None
    return find_call(text, names)


def _plain_name(user) -> str:
    return " ".join(part for part in (user.first_name, user.last_name) if part) or (user.username or "Участник")


async def _display_name(activity_repo, *, chat_id: int, user_id: int | None, fallback: str) -> str:
    if user_id is None:
        return fallback
    try:
        name = await activity_repo.get_chat_display_name(chat_id=chat_id, user_id=user_id)
    except Exception:  # names are decoration; never fail an answer over them
        name = None
    return name or fallback


async def _send_answer(message: Message, text: str) -> Message | None:
    first: Message | None = None
    for chunk in render_llm_html(text):
        try:
            sent = await message.reply(chunk, parse_mode="HTML")
        except TelegramBadRequest:
            sent = await message.reply(html_to_plain_text(chunk), parse_mode=None)
        if first is None:
            first = sent
    return first


async def handle_group_call(
    message: Message,
    *,
    text: str,
    bot,
    activity_repo,
    db_session,
    settings: Settings,
    session_factory,
    llm_client: LlmClient | None,
) -> None:
    user = message.from_user
    chat_id = message.chat.id
    repo = ChatAiCharacterRepository(db_session)

    if llm_client is None or session_factory is None:
        if _hint_allowed(chat_id):
            await message.reply("Selara сейчас не может отвечать: AI временно недоступен.")
        return
    if len(text) > MAX_MEMBER_TEXT_LENGTH:
        await message.reply(f"Слишком длинный вопрос: до {MAX_MEMBER_TEXT_LENGTH} символов.")
        return

    now = datetime.now(timezone.utc)
    admission = await repo.admit_turn(
        chat_id=chat_id,
        author_user_id=user.id,
        content=text,
        idempotency_key=f"group_member:{chat_id}:{message.message_id}",
        telegram_message_id=message.message_id,
        cooldown=timedelta(seconds=max(0.0, float(settings.llm_cooldown_seconds))),
        now=now,
    )
    if admission.status != "ok":
        # duplicate/disabled need no answer; a cooldown stays silent so nobody can spam the chat through Selara.
        await repo.commit()
        return
    row_id = int(admission.message_id)
    character = await repo.get_character(chat_id=chat_id)
    names = await repo.list_names(chat_id=chat_id)
    primary = next((name.name_display for name in names if name.is_primary), None)
    speaker_name = await _display_name(activity_repo, chat_id=chat_id, user_id=user.id, fallback=_plain_name(user))
    recent: list[MemberTurn] = []
    for row in await repo.recent(chat_id=chat_id, limit=MEMBER_RECENT_MESSAGES):
        speaker = "Selara" if row.role == "assistant" else await _display_name(
            activity_repo, chat_id=chat_id, user_id=row.author_user_id, fallback="Участник"
        )
        recent.append(MemberTurn(speaker=speaker, role=row.role, content=row.content))
    # Admission is settled: release the character row lock before quota and the provider call.
    await repo.commit()

    limits = _limits(settings)
    access_service = FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(session_factory),
        entitlement_resolver=SqlAlchemyChatEntitlementResolver(session_factory, group_member_limits=limits),
        group_member_limits=limits,
    )
    owner_exempt = await resolve_owner_admin_exemption(bot=bot, chat_id=chat_id, admin_user_id=settings.admin_user_id)
    try:
        decision = await access_service.reserve_feature_usage(
            feature=AiFeature.GROUP_MEMBER,
            chat_id=chat_id,
            chat_type=message.chat.type,
            chat_title=message.chat.title,
            actor_user_id=user.id,
            actor_is_bot=False,
            trigger="telegram_message",
            timezone_name=settings.bot_timezone,
            idempotency_key=message_idempotency_key(
                feature=AiFeature.GROUP_MEMBER, chat_id=chat_id, source_message_id=message.message_id
            ),
            source_message_id=message.message_id,
            owner_exempt=owner_exempt,
        )
    except Exception:
        log.exception("Group member quota reservation failed chat_id=%s message_id=%s", chat_id, message.message_id)
        await repo.set_status(message_id=row_id, status="failed")
        await repo.commit()
        return

    if decision.reused:
        return
    if not decision.allowed:
        await repo.set_status(message_id=row_id, status="failed")
        await repo.commit()
        if decision.reason == AccessReason.QUOTA_EXHAUSTED:
            if _hint_allowed(chat_id):
                hint = quota_exhausted_message(decision, timezone_name=settings.bot_timezone)
                if decision.access_tier != AccessTier.PAID:
                    hint += " Selara AI для чата расширяет лимит: /premium в личке с ботом."
                await message.reply(hint)
        elif decision.reason == AccessReason.ACTOR_QUOTA_EXHAUSTED:
            if _hint_allowed(chat_id, user.id):
                await message.reply(
                    f"На сегодня твои вопросы к Selara закончились ({decision.quota_used}/{decision.quota_limit}). "
                    "Завтра можно снова."
                )
        return

    accounting = llm_client.accounting_service if isinstance(llm_client, LlmClient) else None
    invocation_id = decision.invocation_id
    call_context = (
        LlmAccountingContext(
            invocation_id=invocation_id,
            feature=AiFeature.GROUP_MEMBER,
            stage="member_round",
            chat_id=chat_id,
            actor_user_id=user.id,
            telegram_message_id=message.message_id,
        )
        if invocation_id is not None
        else None
    )
    outcome = {"status": "failed", "error_category": "handler_error"}
    try:
        has_ai = await chat_has_selara_ai(session_factory, chat_id=chat_id, settings=settings)
        answer = await _run_member_dialogue(
            message,
            total_rounds=group_tool_rounds(settings, has_subscription=has_ai),
            max_tokens=settings.group_member_max_tokens,
            bot=bot,
            messages=build_member_messages(
                character=character,
                call_name=primary,
                chat_title=message.chat.title,
                speaker_name=speaker_name,
                recent=recent,
                user_text=text,
            ),
            history_access=character.member_history_access,
            activity_repo=activity_repo,
            db_session=db_session,
            llm_client=llm_client,
            call_context=call_context,
            outcome=outcome,
        )
        if not answer:
            if outcome["error_category"] == "handler_error":
                outcome["error_category"] = "empty_answer"
            await repo.set_status(message_id=row_id, status="failed")
            await repo.commit()
            await message.reply("⚠️ Selara не смогла ответить. Попробуй чуть позже.")
            return

        await repo.set_status(message_id=row_id, status="ok")
        reply_row_id = await repo.add_reply(chat_id=chat_id, content=answer, now=datetime.now(timezone.utc))
        await repo.prune_history(chat_id=chat_id, now=now)
        await repo.commit()
        outcome["status"] = "succeeded"
        outcome["error_category"] = None

        sent = await _send_answer(message, answer)
        if sent is not None:
            await repo.set_reply_message_id(row_id=reply_row_id, telegram_message_id=sent.message_id)
            await repo.commit()
    finally:
        if accounting is not None and invocation_id is not None:
            if outcome["status"] != "succeeded":
                try:
                    await access_service.release_if_no_provider_attempts(
                        invocation_id=invocation_id, reason=outcome["error_category"] or "pre_provider_failure"
                    )
                except Exception:
                    log.exception("Could not release unused group member quota invocation_id=%s", invocation_id)
            try:
                await accounting.finish_invocation_outcome(
                    invocation_id=invocation_id, status=outcome["status"], error_category=outcome["error_category"]
                )
            except Exception:
                log.exception("Could not finalize group_member invocation id=%s", invocation_id)


async def _run_member_dialogue(
    message: Message,
    *,
    bot,
    messages: list[dict],
    history_access: bool,
    activity_repo,
    db_session,
    llm_client: LlmClient,
    call_context: LlmAccountingContext | None,
    outcome: dict,
    total_rounds: int,
    max_tokens: int,
) -> str:
    chat_snapshot = ChatSnapshot(
        telegram_chat_id=message.chat.id, chat_type=message.chat.type, title=message.chat.title
    )
    user = message.from_user
    actor = UserSnapshot(
        telegram_user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_bot=bool(user.is_bot),
    )
    tools = member_tool_definitions(history_access=history_access)
    for round_index in range(total_rounds):
        try:
            await bot.send_chat_action(message.chat.id, "typing")
        except Exception:
            pass
        # The last round offers no tools and says so, so the model answers with what it already has.
        is_last = round_index == total_rounds - 1
        if is_last and total_rounds > 1:
            messages.append({"role": "user", "content": LAST_ROUND_NOTICE})
        request: dict = {"messages": messages, "tools": [] if is_last else tools}
        # The short cap belongs to the tool-free final answer; tool rounds keep the previous ceiling.
        request["max_tokens"] = max_tokens if is_last else MEMBER_REPLY_MAX_TOKENS
        if call_context is not None:
            request["accounting_context"] = call_context
        try:
            response = await llm_client.chat_with_tools(**request)
        except LlmClientError as exc:
            outcome["error_category"] = exc.usages[-1].error_category if exc.usages else "provider_error"
            return ""
        except Exception:
            log.exception("group member: LLM request failed before reaching the provider")
            outcome["error_category"] = "accounting_unavailable"
            return ""
        response = response.value if isinstance(response, LlmCallResult) else response
        if not response or not response.choices:
            return ""
        msg = response.choices[0].message
        if not msg.tool_calls or is_last:
            text = (msg.content or "").strip()
            if text and getattr(response.choices[0], "finish_reason", None) == "length":
                text += "…"
            return text
        messages.append(msg.model_dump(exclude_none=True))
        for tool_call in msg.tool_calls:
            try:
                arguments = json.loads(tool_call.function.arguments or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object")
            except ValueError:
                arguments = {}
            result = await execute_member_tool(
                ToolCall(name=tool_call.function.name, arguments=arguments, call_id=tool_call.id),
                history_access=history_access,
                chat_snapshot=chat_snapshot,
                db_session=db_session,
                actor_snapshot=actor,
                activity_repo=activity_repo,
                llm_repo=LlmRepository(db_session),
                bot=bot,
            )
            messages.append({"role": "tool", "tool_call_id": result.call_id, "content": result.result_text})
        # Read-only tools: end the transaction so no connection idles through the next provider call.
        await db_session.commit()
    return ""


# ----- /selara: admin settings ------------------------------------------------------

_ON = {"on", "вкл", "включить", "да", "true", "1"}
_OFF = {"off", "выкл", "выключить", "нет", "false", "0"}
_ADD = {"кличка", "добавить", "name", "add"}
_REMOVE = {"убрать", "удалить", "remove", "del"}
_PRIMARY = {"основная", "главная", "primary", "main"}
_CHARACTER = {"характер", "character"}
_MEMBERS = {"участники", "members", "режим"}
_HISTORY = {"история", "history"}
_RESET = {"сброс", "reset"}

HELP_TEXT = (
    "<b>Selara в чате</b>\n"
    "<code>/selara</code> — текущие настройки\n"
    "<code>/selara кличка Селя</code> — добавить кличку (1 без Selara AI, до 5 с ней)\n"
    "<code>/selara убрать Селя</code>, <code>/selara основная Селя</code>\n"
    "<code>/selara характер</code> — пресеты; <code>/selara характер свой текст</code> — свой (до 500 символов)\n"
    "<code>/selara участники вкл|выкл</code> — отвечать участникам по кличке\n"
    "<code>/selara история вкл|выкл</code> — разрешить читать недавние сообщения чата\n"
    "<code>/selara сброс</code> — забыть разговор с участниками\n"
    "Менять настройки могут админы с правом настройки чата."
)


async def _can_manage(message: Message, activity_repo) -> bool:
    user = message.from_user
    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_bot=bool(user.is_bot),
        permission="manage_settings",
        bootstrap_if_missing_owner=False,
    )
    return allowed


async def name_conflict(name_norm: str, *, chat_id: int, activity_repo, db_session) -> str | None:
    """Why this call name would clash with something the chat already answers to, or ``None``."""
    if match_builtin_command(name_norm) is not None:
        return "Кличка совпадает с текстовой командой бота."
    try:
        aliases = await activity_repo.list_chat_aliases(chat_id=chat_id)
    except Exception:
        aliases = []
    for alias in aliases:
        if normalize_call_name(alias.alias_text_norm) == name_norm:
            return "Кличка совпадает с алиасом команды в этом чате."
    try:
        triggers = await activity_repo.list_chat_triggers(chat_id=chat_id)
    except Exception:
        triggers = []
    for trigger in triggers:
        if trigger.match_type in ("exact", "starts_with") and normalize_call_name(trigger.keyword) == name_norm:
            return "Кличка совпадает с триггером этого чата."
    try:
        pets = await AiPetService(db_session).list_chat_pets(chat_id=chat_id)
    except Exception:
        pets = []
    if any(normalize_call_name(pet.name) == name_norm for pet in pets):
        return "Так зовут питомца в этом чате."
    return None


def _status_text(character, names: list[CallName], *, paid: bool, settings: Settings) -> str:
    limits = _limits(settings)
    active = {name.name_norm for name in active_call_names(names, paid=paid)}
    if names:
        name_lines = []
        for name in names:
            marks = []
            if name.is_primary:
                marks.append("основная")
            if name.name_norm not in active:
                marks.append("не работает без Selara AI")
            suffix = f" ({', '.join(marks)})" if marks else ""
            name_lines.append(f"• {escape(name.name_display)}{suffix}")
        names_block = "\n".join(name_lines)
    else:
        names_block = "• пока нет — добавьте: <code>/selara кличка Селя</code>"
    if character.character_preset == GROUP_CUSTOM_PRESET and character.character_custom:
        char_line = f"свой: {escape(character.character_custom[:120])}"
    else:
        char_line = escape(group_preset_title(character.character_preset))
    daily, per_actor = (limits.paid_daily, limits.paid_per_actor) if paid else (limits.free_daily, limits.free_per_actor)
    return (
        "<b>Selara в чате</b>\n"
        f"Клички ({len(names)}/{call_name_limit(paid)}):\n{names_block}\n"
        f"Характер: {char_line}\n"
        f"Ответы участникам по кличке: {'включены' if character.member_mode_enabled else 'выключены'}\n"
        f"Чтение недавних сообщений: {'разрешено' if character.member_history_access else 'запрещено'}\n"
        f"Лимит: {daily} обращений в сутки на чат, {per_actor} на участника"
        + (" (Selara AI)" if paid else " (бесплатно)")
        + "\n\n<code>/selara помощь</code> — все команды"
    )


def _switch(value: str) -> bool | None:
    lowered = value.strip().casefold()
    if lowered in _ON:
        return True
    if lowered in _OFF:
        return False
    return None


def _resolve_preset(value: str) -> str | None:
    lowered = value.strip().casefold()
    for key, (title, _) in GROUP_PRESETS.items():
        if lowered in (key, title.casefold()):
            return key
    return None


@router.message(Command("selara"))
async def selara_command(
    message: Message,
    command: CommandObject,
    activity_repo,
    db_session,
    settings: Settings,
    session_factory=None,
) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in _GROUP_TYPES:
        await message.answer("Клички и характер Selara настраиваются в группе: <code>/selara</code> там.", parse_mode="HTML")
        return
    chat_id = message.chat.id
    repo = ChatAiCharacterRepository(db_session)
    args = (command.args or "").strip()
    verb, _, rest = args.partition(" ")
    verb = verb.casefold()
    rest = rest.strip()

    if verb in ("помощь", "help"):
        await message.answer(HELP_TEXT, parse_mode="HTML")
        return
    if not verb:
        paid = await chat_has_selara_ai(session_factory, chat_id=chat_id, settings=settings)
        character = await repo.get_character(chat_id=chat_id)
        names = await repo.list_names(chat_id=chat_id)
        await message.answer(_status_text(character, names, paid=paid, settings=settings), parse_mode="HTML")
        return

    if not await _can_manage(message, activity_repo):
        await message.answer("Настраивать Selara в чате могут админы с правом настройки чата.")
        return
    actor_id = message.from_user.id

    try:
        if verb in _ADD:
            display, norm = validate_call_name(rest)
            conflict = await name_conflict(norm, chat_id=chat_id, activity_repo=activity_repo, db_session=db_session)
            if conflict is not None:
                await message.answer(conflict)
                return
            paid = await chat_has_selara_ai(session_factory, chat_id=chat_id, settings=settings)
            added = await repo.add_name(chat_id=chat_id, display=display, norm=norm, actor_user_id=actor_id, paid=paid)
            await repo.commit()
            text = f"Кличка «{escape(added.name_display)}» добавлена" + (" и стала основной." if added.is_primary else ".")
            character = await repo.get_character(chat_id=chat_id)
            if not character.member_mode_enabled:
                text += "\nЧтобы Selara отвечала участникам, включите: <code>/selara участники вкл</code>"
        elif verb in _REMOVE:
            removed = await repo.remove_name(chat_id=chat_id, norm=normalize_call_name(rest))
            await repo.commit()
            text = f"Кличка «{escape(removed.name_display)}» удалена." if removed else "Такой клички нет."
        elif verb in _PRIMARY:
            primary = await repo.set_primary(chat_id=chat_id, norm=normalize_call_name(rest))
            await repo.commit()
            text = f"Основная кличка — «{escape(primary.name_display)}»." if primary else "Такой клички нет."
        elif verb in _CHARACTER:
            text = await _character_command(repo, chat_id=chat_id, actor_id=actor_id, rest=rest)
        elif verb in _MEMBERS or verb in _HISTORY:
            enabled = _switch(rest)
            if enabled is None:
                await message.answer(f"Формат: <code>/selara {escape(verb)} вкл|выкл</code>", parse_mode="HTML")
                return
            field = "member_mode_enabled" if verb in _MEMBERS else "member_history_access"
            await repo.update_character(chat_id=chat_id, actor_user_id=actor_id, **{field: enabled})
            await repo.commit()
            if field == "member_mode_enabled":
                text = "Selara отвечает участникам по кличке." if enabled else "Selara больше не отвечает участникам по кличке."
            else:
                text = (
                    "Selara может читать недавние сообщения чата (до суток), отвечая участникам."
                    if enabled
                    else "Selara не читает сообщения чата, отвечая участникам."
                )
        elif verb in _RESET:
            cleared = await repo.reset_history(chat_id=chat_id)
            await repo.commit()
            text = f"Разговор с участниками забыт ({cleared} сообщений)."
        else:
            await message.answer(HELP_TEXT, parse_mode="HTML")
            return
    except (ProfileValidationError, GroupCharacterError) as exc:
        await repo.commit()
        await message.answer(escape(str(exc)))
        return
    invalidate_call_names(chat_id)
    await message.answer(text, parse_mode="HTML")


async def _character_command(repo: ChatAiCharacterRepository, *, chat_id: int, actor_id: int, rest: str) -> str:
    if not rest:
        lines = [f"• <code>{key}</code> — {escape(title)}" for key, (title, _) in GROUP_PRESETS.items()]
        return (
            "Пресеты характера:\n" + "\n".join(lines)
            + "\n\n<code>/selara характер friendly</code> или <code>/selara характер свой описание</code>"
        )
    head, _, tail = rest.partition(" ")
    if head.casefold() in ("свой", "custom"):
        custom = validate_group_custom_character(tail)
        await repo.update_character(
            chat_id=chat_id, actor_user_id=actor_id, character_preset=GROUP_CUSTOM_PRESET, character_custom=custom
        )
        await repo.commit()
        return "Свой характер Selara сохранён."
    preset = _resolve_preset(rest)
    if preset is None:
        raise ProfileValidationError("Нет такого пресета. Список: /selara характер")
    await repo.update_character(chat_id=chat_id, actor_user_id=actor_id, character_preset=preset, character_custom=None)
    await repo.commit()
    return f"Характер Selara: {escape(group_preset_title(preset))}."

