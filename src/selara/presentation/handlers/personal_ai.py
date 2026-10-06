"""Personal AI in private chats: settings wizard (/ai), reset (/ai_reset) and the dialogue itself."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, Filter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.ai_character import (
    CHARACTER_PRESETS,
    CUSTOM_PRESET_KEY,
    MAX_ADDRESS_LENGTH,
    MAX_CUSTOM_CHARACTER_LENGTH,
    MAX_DISPLAY_NAME_LENGTH,
    ProfileValidationError,
    preset_title,
    validate_address,
    validate_custom_character,
    validate_display_name,
)
from selara.application.feature_access import (
    AccessReason,
    AccessTier,
    FeatureAccessService,
    QuotaScope,
    message_idempotency_key,
)
from selara.application.personal_config import PersonalConfigProvider
from selara.core.config import Settings
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.personal_ai_repository import PersonalAiRepository, StoredProfile
from selara.infrastructure.db.telegram_stars import SqlAlchemyUserEntitlementResolver
from selara.infrastructure.llm.client import LlmAccountingContext, LlmClient, LlmClientError
from selara.infrastructure.llm.features import AiFeature
from selara.infrastructure.llm.personal_ai import (
    MAX_USER_TEXT_LENGTH,
    generate_reply,
    maybe_compress_personal,
)
from selara.presentation.auth import resolve_owner_private_exemption
from selara.presentation.feature_access_messages import quota_exhausted_message
from selara.presentation.handlers.premium import personal_offer_available
from selara.presentation.handlers.private_panel import _get_pending_admin_input, _get_pending_cfg_input
from selara.presentation.llm_formatting import html_to_plain_text, render_llm_html

log = logging.getLogger(__name__)

router = Router(name="personal_ai")
# Included after text_commands: it only receives private text that no text command recognised.
chat_router = Router(name="personal_ai_chat")

_PENDING_TTL = timedelta(minutes=10)
_UNAVAILABLE_TEXT = "Selara AI в личных сообщениях сейчас недоступен. Попробуйте позже."
_ACCESS_ERROR_TEXT = "⚠️ Проверка доступа временно недоступна. Попробуйте позже."
_LENGTH_TITLES = {"short": "короткие", "medium": "средние", "long": "подробные"}
_INPUT_PROMPTS = {
    "name": f"Пришлите новое имя (до {MAX_DISPLAY_NAME_LENGTH} символов).",
    "custom": f"Опишите характер своими словами (до {MAX_CUSTOM_CHARACTER_LENGTH} символов).",
    "address": f"Как к вам обращаться? Имя или прозвище (до {MAX_ADDRESS_LENGTH} символов).",
}


@dataclass(frozen=True, slots=True)
class _PendingInput:
    field: str
    expires_at: datetime


# In-memory like the other private-panel inputs: losing it on restart only asks the user to press the button again.
_pending_inputs: dict[int, _PendingInput] = {}


def _get_pending_input(user_id: int) -> _PendingInput | None:
    state = _pending_inputs.get(user_id)
    if state is not None and state.expires_at <= datetime.now(timezone.utc):
        _pending_inputs.pop(user_id, None)
        return None
    return state


def _set_pending_input(user_id: int, field: str) -> None:
    _pending_inputs[user_id] = _PendingInput(field=field, expires_at=datetime.now(timezone.utc) + _PENDING_TTL)


# --- filters -----------------------------------------------------------------


class PendingPersonalInputFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        if message.chat.type != "private" or message.from_user is None or not message.text:
            return False
        if message.text.lstrip().startswith("/"):
            return False
        return _get_pending_input(message.from_user.id) is not None


class PersonalChatFilter(Filter):
    """A plain private text that nothing else is waiting for or understands as a command."""

    async def __call__(self, message: Message) -> bool:
        if message.chat.type != "private" or message.from_user is None or message.from_user.is_bot:
            return False
        if getattr(message, "successful_payment", None) is not None:
            return False
        text = message.text
        if not text or not text.strip() or text.lstrip().startswith("/"):
            return False
        user_id = message.from_user.id
        # Expected input of other private flows wins over the dialogue.
        if _get_pending_cfg_input(user_id) is not None or _get_pending_admin_input(user_id) is not None:
            return False
        if _get_pending_input(user_id) is not None:
            return False
        # Text commands are not filtered here: this router sits after text_commands, which hands over
        # (SkipHandler) only the private text it did not recognise itself.
        return True


# --- settings wizard -----------------------------------------------------------


def _cb(action: str, *parts: object) -> str:
    return ":".join(["pai", action, *(str(part) for part in parts)])


def _profile_text(stored: StoredProfile) -> str:
    p = stored.profile
    character = escape(p.character_custom) if p.character_preset == CUSTOM_PRESET_KEY and p.character_custom else escape(preset_title(p.character_preset))
    lines = [
        "<b>Моя Selara</b>",
        "",
        f"Имя: <b>{escape(p.display_name)}</b>",
        f"Характер: {character}",
        f"Обращение: {escape(p.address_form) if p.address_form else 'по умолчанию'}, на «{'вы' if p.formality == 'vy' else 'ты'}»",
        f"Ответы: {_LENGTH_TITLES.get(p.reply_length, p.reply_length)}, эмодзи {'да' if p.emoji_enabled else 'нет'}",
        f"Режим: {'ролевая игра' if p.mode == 'roleplay' else 'помощник'}",
        "",
        "Просто напишите мне сообщение, и я отвечу. /ai_reset — начать диалог заново.",
    ]
    if p.mode == "roleplay":
        lines.append("В ролевой игре у диалога своя отдельная история; сцену и роли задайте в обычном сообщении.")
    return "\n".join(lines)


def _main_keyboard(stored: StoredProfile) -> InlineKeyboardMarkup:
    p, rev = stored.profile, stored.revision
    builder = InlineKeyboardBuilder()
    builder.button(text="Имя", callback_data=_cb("in", "name", rev))
    builder.button(text="Характер", callback_data=_cb("presets", rev))
    builder.button(text="Обращение", callback_data=_cb("in", "address", rev))
    builder.button(text=f"Ты/вы: {'вы' if p.formality == 'vy' else 'ты'}", callback_data=_cb("set", "formality", "ty" if p.formality == "vy" else "vy", rev))
    next_length = {"short": "medium", "medium": "long", "long": "short"}[p.reply_length]
    builder.button(text=f"Длина: {_LENGTH_TITLES[p.reply_length]}", callback_data=_cb("set", "length", next_length, rev))
    builder.button(text=f"Эмодзи: {'вкл' if p.emoji_enabled else 'выкл'}", callback_data=_cb("set", "emoji", 0 if p.emoji_enabled else 1, rev))
    builder.button(
        text="Режим: " + ("ролевая игра" if p.mode == "roleplay" else "помощник"),
        callback_data=_cb("set", "mode", "assistant" if p.mode == "roleplay" else "roleplay", rev),
    )
    builder.button(text="Закрыть", callback_data=_cb("close"))
    builder.adjust(2, 2, 2, 1, 1)
    return builder.as_markup()


def _presets_keyboard(stored: StoredProfile) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, (title, _) in CHARACTER_PRESETS.items():
        marker = "✅ " if stored.profile.character_preset == key else ""
        builder.button(text=marker + title, callback_data=_cb("preset", key, stored.revision))
    marker = "✅ " if stored.profile.character_preset == CUSTOM_PRESET_KEY else ""
    builder.button(text=marker + "Свой вариант", callback_data=_cb("in", "custom", stored.revision))
    builder.button(text="Назад", callback_data=_cb("home"))
    builder.adjust(1)
    return builder.as_markup()


async def _edit(query: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None) -> None:
    if query.message is None:
        return
    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except TelegramBadRequest:
        # "message is not modified" and stale messages are harmless.
        pass


async def _show_home(query: CallbackQuery, repo: PersonalAiRepository, notice: str | None = None) -> None:
    stored = await repo.get_or_create_profile(query.from_user.id)
    text = _profile_text(stored)
    if notice:
        text = f"{escape(notice)}\n\n{text}"
    await _edit(query, text, _main_keyboard(stored))


def _is_private_callback(query: CallbackQuery) -> bool:
    return query.message is not None and query.message.chat.type == "private" and query.from_user is not None


@router.message(Command("ai"))
async def ai_settings_command(message: Message, db_session: AsyncSession) -> None:
    if message.chat.type != "private" or message.from_user is None:
        await message.answer("Личные настройки Selara AI доступны в личных сообщениях с ботом: откройте диалог и отправьте /ai.")
        return
    _pending_inputs.pop(message.from_user.id, None)
    stored = await PersonalAiRepository(db_session).get_or_create_profile(message.from_user.id)
    await message.answer(_profile_text(stored), parse_mode="HTML", reply_markup=_main_keyboard(stored))


@router.message(Command("ai_reset"))
async def ai_reset_command(message: Message, db_session: AsyncSession) -> None:
    if message.chat.type != "private" or message.from_user is None:
        await message.answer("/ai_reset работает только в личных сообщениях с ботом.")
        return
    repo = PersonalAiRepository(db_session)
    stored = await repo.get_or_create_profile(message.from_user.id)
    removed = await repo.reset_thread(user_id=message.from_user.id, thread=stored.profile.thread)
    label = "ролевой игры" if stored.profile.thread == "roleplay" else "диалога"
    await message.answer(f"Готово, история {label} очищена ({removed} сообщ.). Настройки сохранены.")


@router.callback_query(F.data.startswith("pai:"))
async def ai_settings_callback(query: CallbackQuery, db_session: AsyncSession) -> None:
    if not _is_private_callback(query):
        await query.answer()
        return
    parts = (query.data or "").split(":")
    action, args = (parts[1] if len(parts) > 1 else ""), parts[2:]
    repo = PersonalAiRepository(db_session)
    user_id = query.from_user.id

    if action == "close":
        _pending_inputs.pop(user_id, None)
        await query.answer()
        try:
            await query.message.delete()
        except TelegramBadRequest:
            pass
        return
    if action == "home":
        _pending_inputs.pop(user_id, None)
        await query.answer()
        await _show_home(query, repo)
        return

    try:
        revision = int(args[-1])
    except (ValueError, IndexError):
        await query.answer()
        return

    stored = await repo.get_or_create_profile(user_id)
    if stored.revision != revision:
        await query.answer("Настройки уже изменились, показываю актуальные.")
        await _show_home(query, repo)
        return

    changes: dict[str, Any] | None = None
    if action == "presets":
        await query.answer()
        await _edit(query, "<b>Характер</b>\nВыберите готовый вариант или опишите свой.", _presets_keyboard(stored))
        return
    if action == "in" and args and args[0] in _INPUT_PROMPTS:
        await query.answer()
        _set_pending_input(user_id, args[0])
        await _edit(query, escape(_INPUT_PROMPTS[args[0]]) + "\n\nЧтобы отменить, отправьте /ai.", None)
        return
    if action == "preset" and len(args) == 2 and args[0] in CHARACTER_PRESETS:
        changes = {"character_preset": args[0]}
    elif action == "set" and len(args) == 3:
        field, value = args[0], args[1]
        if field == "formality" and value in ("ty", "vy"):
            changes = {"formality": value}
        elif field == "length" and value in ("short", "medium", "long"):
            changes = {"reply_length": value}
        elif field == "emoji" and value in ("0", "1"):
            changes = {"emoji_enabled": value == "1"}
        elif field == "mode" and value in ("assistant", "roleplay"):
            changes = {"mode": value}
    if changes is None:
        await query.answer()
        return

    updated = await repo.update_profile(user_id, expected_revision=revision, **changes)
    await query.answer("Сохранено" if updated is not None else "Настройки уже изменились.")
    await _show_home(query, repo)


@router.message(PendingPersonalInputFilter())
async def ai_settings_input(message: Message, db_session: AsyncSession) -> None:
    user_id = message.from_user.id
    state = _get_pending_input(user_id)
    if state is None:
        return
    try:
        if state.field == "name":
            changes: dict[str, Any] = {"display_name": validate_display_name(message.text or "")}
        elif state.field == "custom":
            changes = {"character_preset": CUSTOM_PRESET_KEY, "character_custom": validate_custom_character(message.text or "")}
        else:
            changes = {"address_form": validate_address(message.text or "")}
    except ProfileValidationError as exc:
        await message.answer(f"{exc} Попробуйте ещё раз или отправьте /ai, чтобы отменить.")
        return
    repo = PersonalAiRepository(db_session)
    # Text input is the whole new value, so it may overwrite concurrent button changes of other fields.
    stored = await repo.get_or_create_profile(user_id)
    updated = await repo.update_profile(user_id, expected_revision=stored.revision, **changes)
    _pending_inputs.pop(user_id, None)
    if updated is None:
        await message.answer("Настройки изменились одновременно. Откройте /ai и повторите.")
        return
    await message.answer("Сохранено.\n\n" + _profile_text(updated), parse_mode="HTML", reply_markup=_main_keyboard(updated))


# --- dialogue -------------------------------------------------------------------


def _offer_markup(settings: Settings, config, decision) -> InlineKeyboardMarkup | None:
    if decision.access_tier == AccessTier.PAID or not personal_offer_available(settings, config):
        return None
    builder = InlineKeyboardBuilder()
    builder.button(text="Оформить Selara Personal", callback_data="premium:self")
    return builder.as_markup()


async def _send_answer(message: Message, thinking: Message, text: str) -> None:
    for index, chunk in enumerate(render_llm_html(text)):
        if index == 0:
            try:
                await thinking.edit_text(chunk, parse_mode="HTML")
                continue
            except Exception as exc:
                log.warning("personal_ai: editing answer failed, sending reply: %s", exc)
        try:
            await message.answer(chunk, parse_mode="HTML")
        except TelegramBadRequest:
            await message.answer(html_to_plain_text(chunk), parse_mode=None)
        except TelegramForbiddenError:
            # The user blocked the bot while the model was answering: the turn is already stored and charged.
            log.info("personal_ai: user blocked the bot before the answer was delivered")
            return


# One turn per user at a time: a second message sent while the first is still being answered would
# pass the cooldown, spend quota and generate from the same stale history.
_inflight_users: set[int] = set()


@chat_router.message(PersonalChatFilter())
async def personal_chat_handler(
    message: Message,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    personal_config: PersonalConfigProvider,
    llm_client: LlmClient | None = None,
) -> None:
    user_id = message.from_user.id
    if user_id in _inflight_users:
        await message.answer("⏳ Я ещё отвечаю на предыдущее сообщение. Подожди немного. Квота не потрачена.")
        return
    _inflight_users.add(user_id)
    try:
        await _handle_personal_chat(message, db_session, session_factory, settings, personal_config, llm_client)
    finally:
        _inflight_users.discard(user_id)


async def _handle_personal_chat(
    message: Message,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    personal_config: PersonalConfigProvider,
    llm_client: LlmClient | None,
) -> None:
    user = message.from_user
    text = (message.text or "").strip()
    if llm_client is None:
        await message.answer(_UNAVAILABLE_TEXT)
        return
    if len(text) > MAX_USER_TEXT_LENGTH:
        await message.answer(f"Сообщение слишком длинное: до {MAX_USER_TEXT_LENGTH} символов. Квота не потрачена.")
        return

    repo = PersonalAiRepository(db_session)
    last_at = await repo.last_user_message_at(user_id=user.id)
    if last_at is not None:
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last_at).total_seconds()
        if elapsed < settings.llm_cooldown_seconds:
            await message.answer(f"⏳ Слишком часто. Подожди {int(settings.llm_cooldown_seconds - elapsed) + 1} сек.")
            return

    config = await personal_config.get()
    access_service = FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(session_factory),
        user_entitlement_resolver=SqlAlchemyUserEntitlementResolver(session_factory, personal_config),
        personal_config=personal_config,
    )
    stored = await repo.get_or_create_profile(user.id)
    # The quota service works in its own transactions and also upserts the user row: commit ours first,
    # otherwise a brand-new user's first request would wait on a lock held by this very handler.
    await db_session.commit()
    try:
        # One user message is exactly one request; internal calls below ride the same invocation.
        decision = await access_service.reserve_feature_usage(
            feature=AiFeature.PERSONAL_CHAT,
            chat_id=message.chat.id,
            chat_type="private",
            chat_title=None,
            scope=QuotaScope.user(user.id),
            actor_user_id=user.id,
            actor_is_bot=False,
            trigger="telegram_message",
            timezone_name=settings.bot_timezone,
            idempotency_key=message_idempotency_key(
                feature=AiFeature.PERSONAL_CHAT, chat_id=message.chat.id, source_message_id=message.message_id
            ),
            source_message_id=message.message_id,
            mode=stored.profile.mode,
            owner_exempt=resolve_owner_private_exemption(user_id=user.id, admin_user_id=settings.admin_user_id),
        )
    except Exception:
        log.exception("Personal AI quota reservation failed message_id=%s", message.message_id)
        await message.answer(_ACCESS_ERROR_TEXT)
        return

    if decision.reused:
        await message.answer("Этот запрос уже был обработан. Повторный запуск не выполнялся.")
        return
    if not decision.allowed:
        if decision.reason == AccessReason.QUOTA_EXHAUSTED:
            await message.answer(
                quota_exhausted_message(decision, timezone_name=settings.bot_timezone),
                reply_markup=_offer_markup(settings, config, decision),
            )
        else:
            await message.answer("⚠️ Сейчас не удалось разрешить запрос. Попробуйте позже.")
        return

    accounting = llm_client.accounting_service if isinstance(llm_client, LlmClient) else None
    invocation_id = decision.invocation_id
    thread = stored.profile.thread

    def _context(feature: AiFeature, stage: str) -> LlmAccountingContext | None:
        if invocation_id is None:
            return None
        return LlmAccountingContext(
            invocation_id=invocation_id,
            feature=feature,
            stage=stage,
            chat_id=message.chat.id,
            actor_user_id=user.id,
            telegram_message_id=message.message_id,
        )

    outcome = {"status": "failed", "error_category": "handler_error"}
    try:
        thinking = await message.answer("⏳ Думаю...")
        try:
            await message.bot.send_chat_action(message.chat.id, "typing")
        except Exception:
            pass
        try:
            answer = await generate_reply(
                llm_client=llm_client,
                repo=repo,
                user_id=user.id,
                profile=stored.profile,
                user_text=text,
                accounting_context=_context(AiFeature.PERSONAL_CHAT, "chat_turn"),
            )
        except LlmClientError as exc:
            outcome["error_category"] = exc.usages[-1].error_category if exc.usages else "provider_error"
            await thinking.edit_text("⚠️ Не удалось получить ответ от AI. Попробуйте позже.")
            return
        except Exception:
            log.exception("personal_ai: LLM request failed before reaching the provider")
            outcome["error_category"] = "accounting_unavailable"
            await thinking.edit_text("⚠️ Не удалось выполнить запрос. Попробуйте позже.")
            return

        if not answer:
            outcome["error_category"] = "empty_answer"
            await thinking.edit_text("⚠️ AI не дал ответа. Попробуйте переформулировать.")
            return

        await repo.add_message(
            user_id=user.id, thread=thread, role="user", content=text, telegram_message_id=message.message_id
        )
        await repo.add_message(user_id=user.id, thread=thread, role="assistant", content=answer)
        # Persist the turn now and give the connection back before delivery and compression.
        await db_session.commit()
        outcome["status"] = "succeeded"
        outcome["error_category"] = None
        await _send_answer(message, thinking, answer)
        try:
            await maybe_compress_personal(
                repo=repo,
                llm_client=llm_client,
                user_id=user.id,
                thread=thread,
                accounting_context=_context(AiFeature.LLM_CONTEXT_COMPRESSION, "context_compression"),
            )
        except Exception:
            log.exception("personal_ai: context compression crashed user_id=%s", user.id)
    finally:
        if accounting is not None and invocation_id is not None:
            if outcome["status"] != "succeeded":
                try:
                    await access_service.release_if_no_provider_attempts(
                        invocation_id=invocation_id, reason=outcome["error_category"] or "pre_provider_failure"
                    )
                except Exception:
                    log.exception("Could not release unused personal quota invocation_id=%s", invocation_id)
            try:
                await accounting.finish_invocation_outcome(
                    invocation_id=invocation_id,
                    status=outcome["status"],
                    error_category=outcome["error_category"],
                )
            except Exception:
                log.exception("Could not finalize personal_chat invocation id=%s", invocation_id)
