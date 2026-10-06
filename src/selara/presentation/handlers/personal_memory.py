"""Personal AI memory in private chats: /memory, /remember, "запомни, что ..." and /forget_all.

Everything here is keyed by the Telegram user id of the person pressing the button or writing the message;
there is no code path that reads or changes another user's data, and nothing works outside a private chat.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.feature_access import AccessReason, AccessTier, FeatureAccessService, QuotaScope
from selara.application.personal_config import PersonalConfigProvider
from selara.application.personal_memory import MemoryValidationError, normalize_memory_text
from selara.core.config import Settings
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.personal_ai_repository import AddMemoryStatus, PersonalAiRepository
from selara.infrastructure.db.telegram_stars import SqlAlchemyUserEntitlementResolver
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.auth import resolve_owner_private_exemption

log = logging.getLogger(__name__)

router = Router(name="personal_memory")

PAGE_SIZE = 8
_PENDING_TTL = timedelta(minutes=10)
_MAX_PENDING_PER_USER = 5
_EXPORT_CHUNK = 3500
_EXPORT_COOLDOWN_SECONDS = 30.0
_ACCESS_ERROR_TEXT = "⚠️ Проверка доступа временно недоступна. Попробуйте позже."


@dataclass(frozen=True, slots=True)
class _PendingMemory:
    text: str
    expires_at: datetime


# Proposed facts waiting for the confirmation button. In memory like the other private inputs: after a restart
# the user simply sends the fact again.
_pending_memories: dict[int, dict[str, _PendingMemory]] = {}


# Last export per user (monotonic seconds): a repeated tap would send up to ~18 messages each time.
_last_export: dict[int, float] = {}


def _store_pending(user_id: int, text: str) -> str:
    now = datetime.now(timezone.utc)
    # Lazy global cleanup: abandoned proposals of users who never come back must not pile up.
    for owner in list(_pending_memories):
        owned = _pending_memories[owner]
        for token in [t for t, p in owned.items() if p.expires_at is not None and p.expires_at <= now]:
            owned.pop(token, None)
        if not owned and owner != user_id:
            _pending_memories.pop(owner, None)
    bucket = _pending_memories.setdefault(user_id, {})
    while len(bucket) >= _MAX_PENDING_PER_USER:
        bucket.pop(next(iter(bucket)))
    token = secrets.token_urlsafe(6)
    bucket[token] = _PendingMemory(text=text, expires_at=now + _PENDING_TTL)
    return token


def _take_pending(user_id: int, token: str, *, pop: bool = True) -> _PendingMemory | None:
    bucket = _pending_memories.get(user_id, {})
    pending = bucket.pop(token, None) if pop else bucket.get(token)
    if pending is None or pending.expires_at <= datetime.now(timezone.utc):
        bucket.pop(token, None)
        return None
    return pending


def _cb(action: str, *parts: object) -> str:
    return ":".join(["pam", action, *(str(part) for part in parts)])


async def _memory_limit(
    *,
    user_id: int,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    personal_config: PersonalConfigProvider,
) -> int | None:
    """Fact limit of this user's tier, or ``None`` when access cannot be resolved (fail closed, never paid)."""
    config = await personal_config.get()
    service = FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(session_factory),
        user_entitlement_resolver=SqlAlchemyUserEntitlementResolver(session_factory, personal_config),
        personal_config=personal_config,
    )
    try:
        decision = await service.resolve_feature_access(
            feature=AiFeature.PERSONAL_CHAT,
            chat_id=user_id,
            trigger="memory",
            scope=QuotaScope.user(user_id),
            owner_exempt=resolve_owner_private_exemption(user_id=user_id, admin_user_id=settings.admin_user_id),
        )
    except Exception:
        log.exception("personal memory: access resolution failed user_id=%s", user_id)
        return None
    if decision.reason == AccessReason.ACCESS_UNAVAILABLE:
        return None
    paid = decision.access_tier in (AccessTier.PAID, AccessTier.OWNER_INTERNAL)
    return config.memory_paid_limit if paid else config.memory_free_limit


# --- proposing a fact ------------------------------------------------------------------------


async def propose_memory(
    message: Message,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    personal_config: PersonalConfigProvider,
    fact_text: str,
    *,
    from_phrase: bool,
) -> bool:
    """Ask the user to confirm a fact. ``False`` means "not handled, treat the message as ordinary text"."""
    user_id = message.from_user.id
    repo = PersonalAiRepository(db_session)
    stored = await repo.get_or_create_profile(user_id)
    if from_phrase and stored.profile.mode == "roleplay":
        # In role play "запомни, что ..." is a line of the story, not an instruction to the bot.
        return False
    if not stored.memory_enabled:
        await message.answer("Память выключена, поэтому я ничего не сохраняю. Включить её можно в /ai.")
        return True
    if not fact_text.strip():
        await message.answer(
            "Напишите, что запомнить: «запомни, что я веган» или /remember я не ем орехи. Список фактов: /memory."
        )
        return True
    try:
        fact = normalize_memory_text(fact_text)
    except MemoryValidationError as exc:
        await message.answer(str(exc))
        return True
    limit = await _memory_limit(
        user_id=user_id, settings=settings, session_factory=session_factory, personal_config=personal_config
    )
    if limit is None:
        await message.answer(_ACCESS_ERROR_TEXT)
        return True
    existing = [item.content.casefold() for item in await repo.memory_items(user_id=user_id)]
    if fact.casefold() in existing:
        await message.answer("Это я уже помню. Все факты: /memory.")
        return True
    if len(existing) >= limit:
        await message.answer(_limit_text(limit))
        return True
    token = _store_pending(user_id, fact)
    builder = InlineKeyboardBuilder()
    builder.button(text="Запомнить", callback_data=_cb("ok", token))
    builder.button(text="Не надо", callback_data=_cb("no", token))
    builder.adjust(2)
    await message.answer(f"Запомнить: «{escape(fact)}»?", parse_mode="HTML", reply_markup=builder.as_markup())
    return True


def _limit_text(limit: int) -> str:
    return (
        f"Достигнут лимит памяти: {limit} фактов. Я ничего не стираю сама: удалите лишнее в /memory, "
        "и тогда сохраню новое."
    )


@router.message(Command("remember"))
async def remember_command(
    message: Message,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    personal_config: PersonalConfigProvider,
) -> None:
    if message.chat.type != "private" or message.from_user is None:
        await message.answer("/remember работает только в личных сообщениях с ботом.")
        return
    parts = (message.text or "").split(maxsplit=1)
    await propose_memory(
        message, db_session, session_factory, settings, personal_config, parts[1] if len(parts) > 1 else "",
        from_phrase=False,
    )


# --- /memory ----------------------------------------------------------------------------------


async def _list_view(
    repo: PersonalAiRepository, *, user_id: int, page: int, limit: int | None, memory_enabled: bool
) -> tuple[str, InlineKeyboardMarkup]:
    total = await repo.count_memories(user_id=user_id)
    last_page = max((total - 1) // PAGE_SIZE, 0)
    page = min(max(page, 0), last_page)
    rows = await repo.list_memories(user_id=user_id, limit=PAGE_SIZE, offset=page * PAGE_SIZE)

    counter = f"{total}/{limit}" if limit is not None else str(total)
    lines = [f"<b>Моя память</b> ({counter})"]
    if not memory_enabled:
        lines.append("Сейчас память выключена: факты хранятся, но в разговоре не используются. Включить: /ai.")
    lines.append("")
    if not rows:
        lines.append("Пока пусто. Напишите «запомни, что я веган» или /remember …, и я сохраню факт после вашего подтверждения.")
    builder = InlineKeyboardBuilder()
    for index, row in enumerate(rows, start=page * PAGE_SIZE + 1):
        marker = "📌 " if row.pinned else ""
        source = "" if row.source == "explicit" else " <i>(авто)</i>"
        lines.append(f"{index}. {marker}{escape(row.content)}{source}")
        builder.row(
            *[
                _button(f"🗑 {index}", _cb("del", row.id, page)),
                _button(("📍 открепить " if row.pinned else "📌 закрепить ") + str(index), _cb("pin", row.id, page)),
            ]
        )
    if rows:
        lines += ["", "🗑 — забыть факт, 📌 — всегда помнить в разговоре."]
    nav = []
    if page > 0:
        nav.append(_button("◀", _cb("list", page - 1)))
    if page < last_page:
        nav.append(_button("▶", _cb("list", page + 1)))
    if nav:
        builder.row(*nav)
    builder.row(_button("Экспорт", _cb("exp")), _button("Закрыть", _cb("close")))
    return "\n".join(lines), builder.as_markup()


def _button(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


@router.message(Command("memory"))
async def memory_command(
    message: Message,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    personal_config: PersonalConfigProvider,
) -> None:
    if message.chat.type != "private" or message.from_user is None:
        await message.answer("Память доступна только в личных сообщениях с ботом: откройте диалог и отправьте /memory.")
        return
    user_id = message.from_user.id
    repo = PersonalAiRepository(db_session)
    stored = await repo.get_or_create_profile(user_id)
    limit = await _memory_limit(
        user_id=user_id, settings=settings, session_factory=session_factory, personal_config=personal_config
    )
    text, markup = await _list_view(repo, user_id=user_id, page=0, limit=limit, memory_enabled=stored.memory_enabled)
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


# --- /forget_all -------------------------------------------------------------------------------


@router.message(Command("forget_all"))
async def forget_all_command(message: Message) -> None:
    if message.chat.type != "private" or message.from_user is None:
        await message.answer("/forget_all работает только в личных сообщениях с ботом.")
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, удалить всё", callback_data=_cb("fy"))
    builder.button(text="Отмена", callback_data=_cb("fn"))
    builder.adjust(1)
    await message.answer(
        "<b>Удалить все личные данные Selara AI?</b>\n\n"
        "Будут удалены: профиль и настройки, история диалогов и ролевой игры, резюме и все факты в памяти. "
        "Подписка Selara Personal и платежи не затрагиваются. Это нельзя отменить; в резервных копиях бота "
        "данные исчезнут по мере их ротации.",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


# --- callbacks ---------------------------------------------------------------------------------


async def _edit(query: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None, *, html: bool = True) -> None:
    if query.message is None:
        return
    try:
        await query.message.edit_text(text, parse_mode="HTML" if html else None, reply_markup=markup)
    except TelegramBadRequest:
        pass  # "message is not modified" and stale messages are harmless


def _int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _export_chunks(facts: list[str]) -> list[str]:
    chunks, current = [], ""
    for index, fact in enumerate(facts, start=1):
        line = f"{index}. {fact}\n"
        if current and len(current) + len(line) > _EXPORT_CHUNK:
            chunks.append(current.rstrip())
            current = ""
        current += line
    if current:
        chunks.append(current.rstrip())
    return chunks


@router.callback_query(F.data.startswith("pam:"))
async def memory_callback(
    query: CallbackQuery,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    personal_config: PersonalConfigProvider,
) -> None:
    if query.message is None or query.message.chat.type != "private" or query.from_user is None:
        await query.answer()
        return
    from selara.presentation.handlers import personal_ai  # local import: personal_ai imports this module

    user_id = query.from_user.id
    parts = (query.data or "").split(":")
    action, args = (parts[1] if len(parts) > 1 else ""), parts[2:]
    repo = PersonalAiRepository(db_session)

    if action == "close":
        await query.answer()
        try:
            await query.message.delete()
        except TelegramBadRequest:
            pass
        return

    if action in ("ok", "no") and args:
        # Keep the proposal until the decision is final, so a temporary access error can be retried.
        pending = _take_pending(user_id, args[0], pop=False)
        if pending is None:
            await query.answer("Это предложение устарело. Отправьте факт ещё раз.", show_alert=True)
            return
        if action == "no":
            _take_pending(user_id, args[0])
            await query.answer()
            await _edit(query, "Хорошо, не запоминаю.", html=False)
            return
        stored = await repo.get_or_create_profile(user_id)
        if not stored.memory_enabled:
            _take_pending(user_id, args[0])
            await query.answer()
            await _edit(query, "Память выключена, ничего не сохранено. Включить её можно в /ai.", html=False)
            return
        limit = await _memory_limit(
            user_id=user_id, settings=settings, session_factory=session_factory, personal_config=personal_config
        )
        if limit is None:
            await query.answer(_ACCESS_ERROR_TEXT, show_alert=True)
            return
        _take_pending(user_id, args[0])
        result = await repo.add_memory(user_id=user_id, content=pending.text, source="explicit", limit=limit)
        # Persist before talking to Telegram: a failed edit must not roll back what the user was told is saved.
        await db_session.commit()
        await query.answer()
        if result.status == AddMemoryStatus.ADDED:
            await _edit(query, f"Запомнила: «{escape(pending.text)}». Все факты: /memory.")
        elif result.status == AddMemoryStatus.DUPLICATE:
            await _edit(query, "Это я уже помню. Все факты: /memory.", html=False)
        else:
            await _edit(query, _limit_text(limit), html=False)
        return

    if action == "fy":
        if user_id in personal_ai._inflight_users:
            await query.answer("Я ещё отвечаю на ваше сообщение. Повторите через пару секунд.", show_alert=True)
            return
        # Hold the same per-user lock a reply does, so a turn cannot start from the old data mid-deletion.
        personal_ai._inflight_users.add(user_id)
        try:
            removed = await repo.delete_all_user_data(user_id=user_id)
            personal_ai._pending_inputs.pop(user_id, None)
            _pending_memories.pop(user_id, None)
            # Persist the deletion before any Telegram call: a failed edit must not roll the user's data back.
            await db_session.commit()
        finally:
            personal_ai._inflight_users.discard(user_id)
        await query.answer("Удалено")
        await _edit(
            query,
            "Всё удалено: профиль, история, резюме и память "
            f"({removed.messages} сообщ., {removed.memories} фактов). Чтобы начать заново, напишите мне или откройте /ai.",
            html=False,
        )
        return
    if action == "fn":
        await query.answer()
        await _edit(query, "Отменено, ничего не удалено.", html=False)
        return

    if action == "exp":
        now = time.monotonic()
        if now - _last_export.get(user_id, -_EXPORT_COOLDOWN_SECONDS) < _EXPORT_COOLDOWN_SECONDS:
            await query.answer("Экспорт уже отправлен выше. Повторите чуть позже.", show_alert=True)
            return
        _last_export[user_id] = now
        await query.answer()
        facts = [item.content for item in await repo.memory_items(user_id=user_id)]
        if not facts:
            await query.message.answer("Память пока пуста.")
            return
        for chunk in _export_chunks(facts):
            await query.message.answer(chunk, parse_mode=None)
        return

    stored = await repo.get_or_create_profile(user_id)
    limit = await _memory_limit(
        user_id=user_id, settings=settings, session_factory=session_factory, personal_config=personal_config
    )

    page = 0
    if action == "list" and args and _int(args[0]) is not None:
        page = _int(args[0])
    elif action in ("del", "pin") and len(args) == 2 and _int(args[0]) is not None and _int(args[1]) is not None:
        memory_id, page = _int(args[0]), _int(args[1])
        if action == "del":
            done = await repo.delete_memory(user_id=user_id, memory_id=memory_id)
            await db_session.commit()
            await query.answer("Забыла" if done else "Этого факта уже нет.")
        else:
            current = {item.id: item.pinned for item in await repo.memory_items(user_id=user_id)}
            if memory_id in current:
                await repo.set_memory_pinned(user_id=user_id, memory_id=memory_id, pinned=not current[memory_id])
                await db_session.commit()
                await query.answer("Готово")
            else:
                await query.answer("Этого факта уже нет.")
    else:
        await query.answer()
        return
    if action == "list":
        await query.answer()
    text, markup = await _list_view(repo, user_id=user_id, page=page, limit=limit, memory_enabled=stored.memory_enabled)
    await _edit(query, text, markup)
