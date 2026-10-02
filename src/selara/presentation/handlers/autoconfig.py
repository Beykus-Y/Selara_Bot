"""Private AI settings wizard: drafts first, explicit versioned save last."""
from __future__ import annotations

import asyncio
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, Filter
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from selara.application.autoconfig import MAX_INPUT, MAX_TURNS, changes, rebase_draft, review_text, run_assistant
from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.domain.entities import ChatSnapshot
from selara.infrastructure.db.autoconfig_repository import AutoConfigRepository
from selara.infrastructure.db.models import ChatModel, ChatSettingsModel, UserModel, UserChatActivityModel
from selara.infrastructure.llm.pricing import estimate_llm_cost_usd
from selara.presentation.auth import has_permission
from selara.presentation.handlers.settings_common import settings_to_dict, validate_settings_payload
from selara.presentation.llm_formatting import render_llm_html, split_telegram_html

router = Router(name='autoconfig')
_PAGE_SIZE = 6


def _data(row, action, arg=''):
    return f'ac:{row.id}:{row.revision}:{action}:{arg}'


def keyboard(row, *, page=0):
    buttons = []
    if row.state == 'choosing':
        for i, chat in enumerate(row.candidates[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE], page * _PAGE_SIZE):
            buttons.append([InlineKeyboardButton(text=(chat['title'] or str(chat['id']))[:50], callback_data=_data(row, 'pick', str(i)))])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text='← Назад', callback_data=_data(row, 'page', str(page - 1))))
        if (page + 1) * _PAGE_SIZE < len(row.candidates):
            nav.append(InlineKeyboardButton(text='Далее →', callback_data=_data(row, 'page', str(page + 1))))
        if nav:
            buttons.append(nav)
    elif row.state == 'review':
        if changes(row.baseline, row.draft):
            buttons.append([InlineKeyboardButton(text='Сохранить', callback_data=_data(row, 'save'))])
        buttons.append([InlineKeyboardButton(text='Продолжить настройку', callback_data=_data(row, 'continue'))])
    elif row.state == 'active':
        buttons.append([InlineKeyboardButton(text='Проверить изменения', callback_data=_data(row, 'review'))])
    buttons.append([InlineKeyboardButton(text='Отменить изменения', callback_data=_data(row, 'cancel'))])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def can_configure(*, bot, repo, user, chat):
    """Existing bot permission AND current Telegram membership. Failure is denial."""
    if chat is None or chat.type not in {'group', 'supergroup'} or user.is_bot:
        return False
    allowed, _, _ = await has_permission(repo, chat_id=chat.telegram_chat_id, chat_type=chat.type,
        chat_title=chat.title, user_id=user.id, username=user.username, first_name=user.first_name,
        last_name=user.last_name, is_bot=user.is_bot, permission='manage_settings', bootstrap_if_missing_owner=False)
    state = await repo.get_moderation_state(chat_id=chat.telegram_chat_id, user_id=user.id)
    if not allowed or (state is not None and state.is_banned):
        return False
    try:
        actor = await bot.get_chat_member(chat_id=chat.telegram_chat_id, user_id=user.id)
        own = await bot.get_chat_member(chat_id=chat.telegram_chat_id, user_id=bot.id)
    except Exception:
        return False
    def present(member):
        return member.status in {'creator', 'administrator', 'member'} or (
            member.status == 'restricted' and bool(getattr(member, 'is_member', False)))
    return present(actor) and present(own)


async def _current(session, repo, chat_id, settings, *, lock=False):
    if lock:
        await session.scalar(select(ChatModel).where(ChatModel.telegram_chat_id == chat_id).with_for_update())
    stmt = select(ChatSettingsModel).where(ChatSettingsModel.chat_id == chat_id).execution_options(populate_existing=True)
    if lock:
        stmt = stmt.with_for_update()
    stored = await session.scalar(stmt)
    value = await repo.get_chat_settings(chat_id=chat_id) if stored is not None else default_chat_settings(settings)
    return settings_to_dict(value), stored is not None


async def show_review(message, row, chat, *, timezone_name='UTC'):
    chunks = split_telegram_html(review_text(title=chat.title or str(chat.telegram_chat_id),
        baseline=row.baseline, draft=row.draft, touched=row.touched, timezone_name=timezone_name))
    for i, chunk in enumerate(chunks):
        await message.answer(chunk, parse_mode='HTML', reply_markup=keyboard(row) if i == len(chunks) - 1 else None)


@router.message(Command('autocfg'))
async def start(message: Message, bot: Bot, db_session, activity_repo, llm_client=None, settings: Settings | None = None):
    if message.chat.type != 'private':
        await message.answer('Настройка через ИИ доступна в личке с ботом: /autocfg')
        return
    if message.from_user is None:
        return
    repository = AutoConfigRepository(db_session)
    # Authorized group roles reference an existing user. Lock serializes repeated /autocfg.
    await db_session.scalar(select(UserModel).where(UserModel.telegram_user_id == message.from_user.id).with_for_update())
    row = await repository.get(message.from_user.id, lock=True)
    if row is not None:
        if row.state == 'review':
            chat = await db_session.get(ChatModel, row.chat_id)
            if await can_configure(bot=bot, repo=activity_repo, user=message.from_user, chat=chat):
                await show_review(message, row, chat, timezone_name=settings.bot_timezone if settings else 'UTC')
            else:
                await message.answer('Черновик не применён. Не удалось подтвердить права на группу. Его можно отменить командой /autocfgcancel.')
        else:
            await message.answer('У тебя уже есть черновик. Он ещё не применён. Продолжи его или отмени.', reply_markup=keyboard(row))
        return
    if llm_client is None:
        await message.answer('AI-настройка сейчас недоступна: администратор бота должен подключить модель.')
        return
    candidates = []
    # Include inherited/default-role permissions, not only explicitly assigned admin roles.
    chats = (await db_session.scalars(select(ChatModel).join(UserChatActivityModel,
        UserChatActivityModel.chat_id == ChatModel.telegram_chat_id).where(
            UserChatActivityModel.user_id == message.from_user.id,
            UserChatActivityModel.is_active_member.is_(True), ChatModel.type.in_(['group', 'supergroup'])
        ).order_by(ChatModel.title, ChatModel.telegram_chat_id))).all()
    for chat in chats:
        if await can_configure(bot=bot, repo=activity_repo, user=message.from_user, chat=chat):
            candidates.append({'id': chat.telegram_chat_id, 'title': chat.title, 'type': chat.type})
    if not candidates:
        await message.answer('Нет доступных групп. Нужны право Selara «Управление настройками», участие в группе и доступ бота к проверке участников. При ошибке проверки чат не показывается.')
        return
    try:
        row = await repository.start(message.from_user.id, candidates)
        await db_session.commit()
    except IntegrityError:
        await db_session.rollback()
        await message.answer('Черновик уже открыт другим запросом. Нажми /autocfg ещё раз.')
        return
    await message.answer('Выбери группу для настройки. Это черновик на 24 часа: изменения применятся только после сводки и кнопки «Сохранить».', reply_markup=keyboard(row))


@router.callback_query(F.data.startswith('ac:'))
async def action(callback: CallbackQuery, bot: Bot, db_session, activity_repo, settings: Settings):
    if callback.message is None or callback.message.chat.type != 'private' or callback.message.chat.id != callback.from_user.id:
        await callback.answer('Открой /autocfg в личке с ботом.', show_alert=True)
        return
    try:
        _, session_id, revision, name, arg = callback.data.split(':', 4)
        revision = int(revision)
    except (ValueError, AttributeError):
        await callback.answer('Кнопка недействительна.', show_alert=True)
        return
    await callback.answer()  # Acknowledge before Telegram permission checks or row-lock waits.
    repository = AutoConfigRepository(db_session)
    row = await repository.get(callback.from_user.id, lock=True)
    if row is None or row.id != session_id or row.revision != revision:
        await callback.message.answer('Эта кнопка устарела. Используй последнее сообщение или /autocfg.')
        return
    if name == 'cancel':
        repository.close(row)
        await db_session.commit()
        await callback.message.answer('Черновик отменён. Настройки группы не изменились.')
        return
    if row.state == 'choosing':
        try:
            index = int(arg)
            if name == 'page' and 0 <= index < (len(row.candidates) + _PAGE_SIZE - 1) // _PAGE_SIZE:
                await callback.message.edit_reply_markup(reply_markup=keyboard(row, page=index))
                return
            if name != 'pick' or not 0 <= index < len(row.candidates):
                raise ValueError()
        except ValueError:
            await callback.message.answer('Кнопка недействительна.')
            return
        chat = await db_session.get(ChatModel, row.candidates[index]['id'])
        if not await can_configure(bot=bot, repo=activity_repo, user=callback.from_user, chat=chat):
            await callback.message.answer('Прав на этот чат больше нет или проверка недоступна.')
            return
        current, _ = await _current(db_session, activity_repo, chat.telegram_chat_id, settings)
        row.chat_id, row.baseline, row.draft, row.state = chat.telegram_chat_id, current, dict(current), 'active'
        row.revision += 1
        await db_session.commit()
        await callback.message.answer(f'<b>Настраиваем: {escape(chat.title or str(chat.telegram_chat_id))}</b>\nЧасовой пояс расписания: {escape(settings.bot_timezone)}.\nОпиши, что хочешь изменить. Например: «Выключи экономику и включи итоги дня в 21:00».\nИзменения пока не сохраняются. Скажи «На этом завершим» или нажми «Проверить изменения», когда закончишь. Если время местное, укажи город.',
            parse_mode='HTML', reply_markup=keyboard(row))
        return
    chat = await db_session.get(ChatModel, row.chat_id)
    if not await can_configure(bot=bot, repo=activity_repo, user=callback.from_user, chat=chat):
        await callback.message.answer('Права потеряны или проверка недоступна. Черновик не применён; его можно отменить.')
        return
    if name == 'review' and row.state == 'active':
        row.state = 'review'
        row.revision += 1
        await db_session.commit()
        await show_review(callback.message, row, chat, timezone_name=settings.bot_timezone)
    elif name == 'continue' and row.state == 'review':
        row.state = 'active'
        row.revision += 1
        await db_session.commit()
        await callback.message.answer('Продолжаем этот же черновик. Что ещё изменить?', reply_markup=keyboard(row))
    elif name == 'save' and row.state == 'review':
        current, stored = await _current(db_session, activity_repo, row.chat_id, settings, lock=True)
        if current != row.baseline:
            try:
                row.draft = rebase_draft(row.baseline, row.draft, current)
                row.state = 'review'
                note = 'Настройки группы изменились во время диалога. Черновик пересчитан относительно актуальных значений. Проверь новую сводку и нажми «Сохранить» ещё раз.'
            except ValueError as exc:
                row.draft = {**current, **changes(row.baseline, row.draft)}
                row.state = 'active'
                note = f'Настройки группы изменились; в черновике конфликт: {exc}. Продолжи настройку и исправь его.'
            row.baseline = current
            row.revision += 1
            await db_session.commit()
            await callback.message.answer(note, reply_markup=keyboard(row) if row.state == 'active' else None)
            if row.state == 'review':
                await show_review(callback.message, row, chat, timezone_name=settings.bot_timezone)
            return
        error = validate_settings_payload(row.draft)
        if error:
            await callback.message.answer(error)
            return
        # Repeat authorization inside the locked save transaction.
        if not await can_configure(bot=bot, repo=activity_repo, user=callback.from_user, chat=chat):
            await callback.message.answer('Не удалось подтвердить права; ничего не сохранено.')
            return
        patch = changes(row.baseline, row.draft)
        if not patch:
            await callback.message.answer('Изменений нет.')
            return
        snapshot = ChatSnapshot(telegram_chat_id=chat.telegram_chat_id, chat_type=chat.type, title=chat.title)
        await activity_repo.upsert_chat_settings(chat=snapshot, values=patch if stored else row.draft)
        await activity_repo.add_audit_log(chat=snapshot, actor_user_id=callback.from_user.id,
            action_code='autocfg_saved', description='Сохранён подтверждённый пользователем AI-черновик настроек.',
            meta_json={'session_id': row.id, 'changes': {k: {'before': row.baseline[k], 'after': v} for k, v in patch.items()}})
        repository.close(row)
        await db_session.commit()
        from selara.presentation.handlers.chat_assistant import invalidate_chat_feature_cache
        invalidate_chat_feature_cache(chat.telegram_chat_id)
        await callback.message.answer(f'Настройки группы «{chat.title or chat.telegram_chat_id}» сохранены. Изменено параметров: {len(patch)}.')
    else:
        await callback.message.answer('Сейчас это действие недоступно.')


@router.message(Command('autocfgcancel'))
async def cancel(message: Message, db_session):
    if message.chat.type != 'private' or message.from_user is None:
        return
    repository = AutoConfigRepository(db_session)
    row = await repository.get(message.from_user.id, lock=True)
    if row:
        repository.close(row)
        await db_session.commit()
    await message.answer('Черновик отменён. Настройки группы не изменились.')


class DraftMessageFilter(Filter):
    async def __call__(self, message: Message, db_session):
        if message.chat.type != 'private' or message.from_user is None or (message.text or '').startswith('/'):
            return False
        return await AutoConfigRepository(db_session).get(message.from_user.id) is not None


@router.message(DraftMessageFilter())
async def talk(message: Message, bot: Bot, db_session, activity_repo, settings: Settings, llm_client=None):
    repository = AutoConfigRepository(db_session)
    row = await repository.get(message.from_user.id)
    if row is None:
        return
    if row.state not in {'active', 'busy'}:
        await message.answer('Сейчас нужно выбрать действие кнопкой. Для диалога нажми «Продолжить настройку».', reply_markup=keyboard(row))
        return
    if llm_client is None:
        await message.answer('Ассистент недоступен. Можно открыть сводку или отменить черновик.', reply_markup=keyboard(row))
        return
    text = message.text or ''
    if not text.strip() or len(text) > MAX_INPUT:
        await message.answer('Напиши текстовый запрос до 4000 символов.', reply_markup=keyboard(row))
        return
    chat = await db_session.get(ChatModel, row.chat_id)
    if not await can_configure(bot=bot, repo=activity_repo, user=message.from_user, chat=chat):
        await message.answer('Права на группу потеряны или проверка недоступна. Черновик не применён; его можно отменить.', reply_markup=keyboard(row))
        return
    if row.turns >= MAX_TURNS:
        await message.answer('Достигнут лимит 50 запросов. Открой сводку или отмени этот черновик.', reply_markup=keyboard(row))
        return
    token = await repository.claim_turn(row, cooldown=settings.llm_cooldown_seconds)
    if token is None:
        row = await repository.get(message.from_user.id)
        await message.answer('Запрос уже обрабатывается или отправлен слишком быстро. Подожди несколько секунд.', reply_markup=keyboard(row) if row else None)
        return
    row = await repository.get(message.from_user.id)
    # Copy immutable request data before awaiting the model; cancelled sessions cannot be resurrected.
    baseline, draft, touched, history = dict(row.baseline), dict(row.draft), list(row.touched), list(row.history)
    await db_session.commit()
    thinking = await message.answer('Думаю над черновиком…', reply_markup=keyboard(row))
    try:
        async with asyncio.timeout(180):
            result = await run_assistant(client=llm_client, text=text, baseline=baseline, draft=draft,
                defaults=settings_to_dict(default_chat_settings(settings)), touched=touched, history=history, timezone_name=settings.bot_timezone)
    except TimeoutError:
        from selara.application.autoconfig import AssistantResult
        result = AssistantResult(draft, touched, 'Запрос занял слишком много времени. Черновик не изменён; можно повторить или открыть сводку.')
    row = await repository.finish_turn(user_id=message.from_user.id, token=token, result=result, text=text)
    if row is None:
        await thinking.edit_text('Этот запрос больше не актуален. Черновик отменён или заменён.')
        return
    if result.usages:
        usages = []
        for model, usage in result.usages:
            prompt_tokens, completion_tokens = usage or (None, None)
            usages.append({'model': model or 'unknown', 'prompt_tokens': prompt_tokens,
                'completion_tokens': completion_tokens, 'estimated_cost_usd': estimate_llm_cost_usd(
                    model=model or 'unknown', prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)})
        await activity_repo.add_audit_log(
            chat=ChatSnapshot(telegram_chat_id=chat.telegram_chat_id, chat_type=chat.type, title=chat.title),
            actor_user_id=message.from_user.id, action_code='autocfg_usage',
            description='Использование AI-мастера настроек (без применения параметров).', meta_json={'usages': usages})
    await db_session.commit()
    # The assistant's answer/review carries the actual outcome; avoid a second
    # unconditional 'updated' message when nothing was changed or a tool failed.
    try:
        await thinking.delete()
    except Exception:
        await thinking.edit_text('Проверка изменений.' if result.finished else 'Ответ подготовлен.', reply_markup=None)
    if result.finished:
        await show_review(message, row, chat, timezone_name=settings.bot_timezone)
    else:
        chunks = render_llm_html(result.answer)
        for i, chunk in enumerate(chunks):
            await message.answer(chunk, parse_mode='HTML', reply_markup=keyboard(row) if i == len(chunks) - 1 else None)
