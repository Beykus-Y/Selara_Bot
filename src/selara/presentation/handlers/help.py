from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from selara.application.ai_character.group import (
    MAX_GROUP_CUSTOM_LENGTH,
    FREE_CALL_NAMES,
    MAX_MEMBER_TEXT_LENGTH,
    PAID_CALL_NAMES,
)
from selara.application.feature_access import resolve_feature_policy
from selara.application.model_catalog import PROFILE_DESCRIPTIONS, PROFILE_EMOJI, PROFILE_NAMES, PROFILE_ORDER
from selara.core.chat_settings import ChatSettings
from selara.core.config import Settings
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.commands.command_catalog import GAME_RULES_RU, get_command_spec

router = Router(name="help")

_HELP_SECTIONS_ORDER: tuple[tuple[str, str], ...] = (
    ("stats", "📊 Статистика"),
    ("games", "🎮 Игры"),
    ("economy", "💰 Экономика"),
    ("relationships", "💞 Отношения"),
    ("social", "🤝 Социальное"),
    ("pets", "🐾 Питомцы"),
    ("ai", "🤖 AI в группе"),
    ("ai_plus", "💎 Подписка и итоги"),
    ("models", "🧠 Модели и лимиты"),
    ("moderation", "🛡 Модерация"),
    ("settings", "⚙️ Настройки"),
)

_HELP_GAMES_ORDER: tuple[tuple[str, str], ...] = (
    ("zlobcards", "🃏 500 Злобных Карт"),
    ("spy", "🕵️ Найди шпиона"),
    ("whoami", "🎭 Кто я"),
    ("mafia", "🕴 Мафия"),
    ("dice", "🎲 Дуэль кубиков"),
    ("quiz", "❓ Викторина"),
    ("bredovukha", "🧠 Бредовуха"),
    ("bunker", "🏚 Бункер"),
)

def _base_words(*keys: str) -> list[str]:
    """Base command words (e.g. "/farm plant <культура>" -> "/farm") for a
    catalog spec's syntax, deduplicated and in catalog order. Pulls the
    actual syntax from command_catalog.py instead of retyping it, so a
    command's real argument shape can change without this list drifting —
    only the base word itself is shown here, matching help.py's established
    terse-overview style (full argument syntax lives in USER_GUIDE.md/
    user_docs.py, not in the /help quick menu).
    """
    seen: list[str] = []
    for key in keys:
        for entry in get_command_spec(key).syntax:
            base = entry.split()[0]
            if base.startswith("/") and base not in seen:
                seen.append(base)
    return seen


def _code_join(words: list[str]) -> str:
    return ", ".join(f"<code>{word}</code>" for word in words)


_HELP_SECTION_TEXT: dict[str, str] = {
    "stats": (
        "<b>Статистика</b>\n"
        f"• {_code_join(_base_words('stats_profile'))} — профиль, карма и своё описание\n"
        f"• {_code_join(_base_words('stats_leaderboards'))} — топ пользователей и активности "
        "(<code>karma</code>, <code>гибрид</code>, <code>неделя|сутки|час|месяц</code>)\n"
        f"• {_code_join(_base_words('misc_lastseen'))} — когда был активен\n"
        f"• {_code_join(_base_words('stats_achievements'))} — достижения и награды"
    ),
    "games": (
        "<b>Игры</b>\n"
        "Выберите конкретную игру кнопками ниже — покажу описание и правила.\n"
        f"• {_code_join(_base_words('games_lobby'))} — открыть меню игр\n"
        f"• {_code_join(_base_words('games_role_reveal'))} — узнать свою роль (для скрытых игр)\n"
        "• Лобби запускает создатель или участник с правом управления играми"
    ),
    "economy": (
        "<b>Экономика</b>\n"
        f"• {_code_join(_base_words('economy_panel'))}\n"
        f"• {_code_join(_base_words('economy_farm'))}\n"
        f"• {_code_join(_base_words('economy_shop_inventory_craft'))}\n"
        f"• {_code_join(_base_words('economy_market_transfer_auction'))}\n"
        f"• Кнопки панели персональные: другим нужно открыть свою через {_code_join(_base_words('economy_panel')[:1])}"
    ),
    "relationships": (
        "<b>Отношения</b>\n"
        "• <code>мои отношения</code> / <code>/relation</code> — статус, кулдауны и кнопки действий\n"
        "• <code>мой брак</code> — отдельная карточка активного брака\n"
        "• <code>браки</code> — все активные браки беседы\n"
        "• <code>/pair @user</code> или <code>предложить встречаться @user</code> — предложение пары\n"
        f"• {_code_join(_base_words('relationships_end')[:1])} — расстаться\n"
        "• <code>/marry @user</code> или <code>предложить брак @user</code> — предложение брака\n"
        f"• {_code_join(_base_words('relationships_end')[1:2])} — развод\n"
        f"• Для пары: {_code_join(_base_words('relationships_pair_actions'))}\n"
        f"• Для брака: {_code_join(_base_words('relationships_marriage_actions'))}"
    ),
    "social": (
        "<b>Социальное</b>\n"
        "• Карма: reply <code>+</code> / <code>-</code>\n"
        "• Нейминг: <code>/naming Имя</code> или <code>нейминг Имя</code>\n"
        "• Образы чата: reply <code>выдать образ \"Венти\"</code>, <code>снять образ</code>, <code>образы</code>\n"
        "• Reply <code>цитировать</code> — карточка цитаты с аватаром, ником и датой\n"
        "• Reply-действия: шлепнуть/сжечь/убить/трахнуть/отдаться/соблазнить/засосать/провести ночь с/сесть на/нагнуть/ударить/обнять/поцеловать/пожать руку/дать пять/погладить/куснуть/пнуть/ущипнуть/прижать/наступить/пощекотать/ткнуть/оттолкнуть/утешить/успокоить/защитить/поднять на руки/утащить/выпроводить/подмигнуть/потанцевать/поклониться/подбодрить/угостить/похвалить/поздравить/укрыть/наругать/дать кулак/отсосать/минет\n"
        "• Объявления: <code>объява \"текст\"</code> (по рангу команды)\n"
        "• Подписка объявлений: <code>рег</code> / <code>анрег</code>"
    ),
    "pets": (
        "<b>AI-питомцы</b>\n"
        f"• {_code_join(_base_words('pets_core'))}\n"
        "• Без /: <code>пет</code>, <code>петы</code>, <code>пет погладить Мурка</code>, <code>пет покормить</code>\n"
        "• Поговорить: <code>Мурка, как дела?</code> или ответ на реплику питомца (лимит Selara Personal хозяина)\n"
        "• <code>/pet_do чешу за ухом</code> — своё действие словами: модель отвечает по характеру, а эффект берёт код (платит хозяин с Selara Personal)\n"
        "• Черты складываются сами из того, как с питомцем обращаются (<code>/pet_traits</code>); он помнит отношение к людям и чату (<code>/pet_memory</code>) и у него есть «настроение дня»\n"
        "• <code>/pet_bag</code> — рюкзак и гардероб: еда и игрушки в запас, косметика (📦 в магазине)\n"
        "• С 10 уровня (нужен Selara Personal): <code>/pet_travel</code> в другом чате — взять питомца в гости, <code>/pet_home</code> — сделать чат домом\n"
        "• Питомцы включены в чатах по умолчанию (админ может выключить: <code>pets_enabled</code>); завести и ухаживать можно бесплатно\n"
        "• Ролевое «стать питомцем» — <code>/bepet</code>"
    ),
    "moderation": (
        "<b>Модерация</b>\n"
        f"• {_code_join(_base_words('admin_moderation_actions'))}\n"
        f"• {_code_join(_base_words('misc_public_service_commands')[2:4])}\n"
        f"• {_code_join(_base_words('admin_role_assignment'))}\n"
        f"• {_code_join(_base_words('admin_role_definitions') + _base_words('admin_role_custom')[:1])}\n"
        "• Без <code>/</code>: пред / варн / снять пред / снять варн / бан / снять бан — по reply или с <code>@username/id</code>"
    ),
    "settings": (
        "<b>Настройки и алиасы</b>\n"
        f"• {_code_join(_base_words('misc_public_service_commands')[1:2])} — текущие настройки\n"
        f"• {_code_join(_base_words('admin_settings_tools')[:1])} key value — изменить настройку\n"
        f"• {_code_join(_base_words('admin_command_ranks'))} — ранги доступа команд\n"
        f"• {_code_join(_base_words('admin_aliases'))}\n"
        f"• {_code_join(_base_words('admin_smart_triggers'))}\n"
        f"• {_code_join(_base_words('admin_custom_rp_actions'))} — кастомные reply-действия с шаблонами\n"
        "• Selara в чате: <code>/selara</code> — клички («Селя, ...»), характер и ответы участникам\n"
        "• ЛС-панель: <code>/start</code> в личке\n"
        "• Selara AI: <code>/premium</code> в личке — выбрать чат и оформить доступ\n"
        "• Условия и помощь по оплате: <code>/terms</code>, <code>/paysupport</code>\n"
        "• С телефона: Mini App из <code>/start</code> в личке\n"
        "• С ПК: <code>/login</code> в личке выдаёт одноразовый код для /app"
    ),
}



def _summary_defaults() -> tuple[int, int]:
    fields = ChatSettings.__dataclass_fields__
    return fields["daily_summary_hour"].default, fields["daily_summary_min_messages"].default


def _policy_limit(feature: AiFeature, trigger: str) -> int:
    policy = resolve_feature_policy(feature=feature, trigger=trigger)
    assert policy is not None  # these two features have an explicit free quota
    return policy.limit


def _ai_help_text(settings: Settings) -> str:
    """AI-in-group guide; limits come from the same policy/settings the handlers enforce."""
    admin_limit = _policy_limit(AiFeature.LLM_ADMIN, "telegram_message")
    return (
        "<b>AI в группе</b>\n"
        "\n"
        "<b>Ассистент админов: ? и ??</b>\n"
        "• <code>? вопрос</code> — запрос с чистого листа, без памяти прошлых запросов\n"
        "• <code>?? вопрос</code> — с контекстом прошлых <code>??</code> в этом чате\n"
        "• <code>?reset</code> — сбросить накопленный контекст (нужно право модерации)\n"
        "• Работает, если в чате включено <code>llm_enabled</code> (по умолчанию выключено)\n"
        "• Кто может: senior_admin и выше или кастомная роль с правом AI-ассистента\n"
        "• Умеет: топы и статистика, участники, журнал модерации, словарь чата, поиск в интернете (если включён у бота); "
        "действия (варн, бан, роли) — только если право есть у спрашивающего\n"
        f"• Лимит: {admin_limit} запросов в сутки на весь чат (сутки по времени бота), "
        "Selara AI этот лимит не меняет; пауза между запросами одного человека "
        f"{settings.llm_cooldown_seconds:g} сек.\n"
        "• Когда лимит исчерпан, бот пишет, сколько использовано и когда он обновится\n"
        "\n"
        "<b>Обращение по кличке</b>\n"
        "• Любой участник: <code>Селя, кто самый активный?</code> — кличка должна стоять в самом начале сообщения; "
        "ответ на реплику Selara продолжает разговор\n"
        "• <code>/selara</code> — текущие настройки, <code>/selara помощь</code> — все команды\n"
        "• Админ с правом настройки чата: <code>/selara кличка Селя</code>, <code>/selara убрать Селя</code>, "
        "<code>/selara основная Селя</code>\n"
        "• <code>/selara участники вкл</code> — включить ответы участникам (по умолчанию выключено)\n"
        f"• <code>/selara характер</code> — пресеты, <code>/selara характер свой текст</code> — свой (до {MAX_GROUP_CUSTOM_LENGTH} символов)\n"
        "• <code>/selara история вкл</code> — разрешить читать недавние сообщения чата (нужен <code>save_message true</code>); "
        "<code>/selara действия вкл|выкл</code> — Selara сама может обнять и т.п.; "
        "<code>/selara сброс</code> — забыть разговор\n"
        f"• Кличек: {FREE_CALL_NAMES} без Selara AI, до {PAID_CALL_NAMES} с ним; вопрос до {MAX_MEMBER_TEXT_LENGTH} символов\n"
        f"• Лимит обращений в сутки: {settings.group_member_free_daily_limit} на чат и "
        f"{settings.group_member_free_per_user_daily_limit} на участника, с Selara AI — "
        f"{settings.group_member_paid_daily_limit} и {settings.group_member_paid_per_user_daily_limit}\n"
        "\n"
        "<b>Питомцы в группе</b>\n"
        "• Питомец отвечает на <code>Мурка, как дела?</code> или на ответ на его реплику; "
        "сам пишет только при включённом <code>pets_spontaneous_enabled</code> (по умолчанию выключено, ночью молчит); подробности — в разделе «Питомцы»\n"
        "• Разговоры и свои действия (<code>/pet_do</code>) оплачивает хозяин из Selara Personal, не чат: "
        f"{settings.pet_talk_daily_limit} AI-реплик в сутки на питомца (разговоры и самостоятельные сообщения расходуют один лимит), из них гостям — "
        f"{settings.pet_talk_guests_daily_limit} всего и {settings.pet_talk_guest_daily_limit} на человека; "
        f"<code>/pet_do</code>: пауза 10 минут у каждого человека; хозяину до {settings.pet_custom_actions_daily_limit} в сутки, "
        f"каждому гостю до {settings.pet_custom_actions_guest_daily_limit} в сутки, всем гостям вместе до "
        f"{settings.pet_custom_actions_guests_daily_limit} в сутки\n"
        "• В режиме AI Limits реплики питомца списываются из общего суточного бюджета хозяина по фактической стоимости\n"
        "• Без Selara Personal у хозяина питомец отвечает заготовкой; гладить и кормить можно без AI-лимита (у ухода свои кулдауны)"
    )


def _ai_plus_help_text(settings: Settings) -> str:
    manual_limit = _policy_limit(AiFeature.DAILY_SUMMARY, "manual")
    summary_hour, summary_min = _summary_defaults()
    return (
        "<b>Подписка, итоги и AI-настройка</b>\n"
        "\n"
        "<b>Selara AI для чата</b>\n"
        "• <code>/premium</code> в личке с ботом — выбрать чат и оплатить Telegram Stars\n"
        f"• Даёт: до {PAID_CALL_NAMES} кличек, лимит обращений по кличке "
        f"{settings.group_member_paid_daily_limit}/{settings.group_member_paid_per_user_daily_limit} в сутки, "
        "автоматические итоги дня\n"
        "• Не меняет: <code>?</code>/<code>??</code> и ручной <code>/summary</code>; "
        "питомцы идут по Selara Personal хозяина\n"
        "• Когда подписка закончилась: лимиты возвращаются к бесплатным, лишние клички перестают работать "
        "(не удаляются, основная работает), автоматические итоги прекращаются; настройки сохраняются\n"
        "• Условия и помощь с оплатой: <code>/terms</code>, <code>/paysupport</code>\n"
        "• Владелец бота может подарить подписку (Personal или группе): тогда в <code>/premium</code> написано "
        "«выдана администратором», а вы получите сообщение о выдаче и об отзыве\n"
        "\n"
        "<b>Итоги дня</b>\n"
        f"• <code>/summary</code> — собрать итоги сейчас (право настройки чата), {manual_limit} раз в месяц на чат\n"
        "• Автоматические: <code>/setcfg daily_summary_enabled true</code>, только с Selara AI; "
        f"час — <code>daily_summary_hour</code> (по умолчанию {summary_hour}, время бота), "
        "стиль — <code>daily_summary_style</code> (neutral, lively, snarky)\n"
        f"• Нужны <code>save_message true</code> и не меньше {summary_min} сообщений за сутки "
        "(порог — <code>daily_summary_min_messages</code>)\n"
        "• <code>daily_summary_include_voice</code> и <code>daily_summary_include_video_notes</code> — "
        "учитывать голосовые и кружки\n"
        "\n"
        "<b>AI-настройка группы</b>\n"
        "• <code>/autocfg</code> в личке: выберите группу и опишите словами, что изменить; "
        "<code>/autocfgcancel</code> — отменить черновик\n"
        "• Черновик живёт 24 часа, изменения применяются только после сводки и кнопки «Сохранить»\n"
        "• Нужно право настройки чата; лимиты подписки не тратятся"
    )


def _models_help_text(settings: Settings) -> str:
    """Model profiles and limit modes; request counts come from the bot settings, the mode is the owner's."""
    profiles = "\n".join(
        f"• {PROFILE_EMOJI[key]} {PROFILE_NAMES[key]} — {PROFILE_DESCRIPTIONS[key].lower()}" for key in PROFILE_ORDER
    )
    return (
        "<b>Модели и лимиты</b>\n"
        "\n"
        "<b>Личный AI в личке с ботом</b>\n"
        "• <code>/ai</code> — меню: характер, поведение, память, модель; остаток на сегодня виден там же\n"
        "• Профили модели (владелец бота сам назначает модели и цены):\n"
        f"{profiles}\n"
        "• Если профиль недоступен, отвечает Базовая; в режиме запросов профили не различаются, отвечает модель по умолчанию\n"
        "\n"
        "<b>Два режима лимитов (выбирает владелец бота)</b>\n"
        "• Запросы: фиксированное число запросов в сутки (бесплатно и с Selara Personal), любой профиль считается "
        "как один запрос; сколько осталось сегодня — в <code>/ai</code>\n"
        "• AI Limits: суточный бюджет в AIL; разные профили стоят по-разному, а списывается столько, "
        "сколько ответ стоил на самом деле (короткий дешевле, длинный дороже). "
        "Для старта нужен резерв профиля: не хватает AIL — запрос не уходит и ничего не списывается\n"
        "• Тот же бюджет тратят реплики питомцев хозяина\n"
        "\n"
        "<b>Группы</b>\n"
        "• Модель для групповых функций выбирает владелец бота, участники её не меняют\n"
        "• У <code>?</code>/<code>??</code>, обращений по кличке и итогов дня свои лимиты на чат: смотрите «AI в группе» "
        "и «Подписка и итоги»\n"
        "• Подписка: <code>/premium</code> в личке; подарочная подписка помечена «выдана администратором»"
    )


def _help_callback_data(*, section: str, owner_user_id: int | None) -> str:
    if owner_user_id is None:
        return f"help:{section}"
    return f"help:{section}:u{owner_user_id}"


def _parse_help_callback_data(data: str | None) -> tuple[str, int | None]:
    if not data or not data.startswith("help:"):
        return "home", None

    payload = data[5:]
    if not payload:
        return "home", None

    owner_user_id: int | None = None
    section = payload
    possible_owner_split = payload.rsplit(":u", maxsplit=1)
    if len(possible_owner_split) == 2 and possible_owner_split[1].isdigit():
        section = possible_owner_split[0]
        owner_user_id = int(possible_owner_split[1])

    return (section or "home"), owner_user_id


def _build_help_keyboard(*, section: str | None, owner_user_id: int | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if section is None:
        for key, title in _HELP_SECTIONS_ORDER:
            builder.button(text=title, callback_data=_help_callback_data(section=key, owner_user_id=owner_user_id))
        builder.adjust(2, 2, 2, 2, 2, 1)
        return builder.as_markup()

    if section == "games":
        for key, title in _HELP_GAMES_ORDER:
            builder.button(
                text=title,
                callback_data=_help_callback_data(section=f"game_{key}", owner_user_id=owner_user_id),
            )
        builder.button(text="🏠 Главное", callback_data=_help_callback_data(section="home", owner_user_id=owner_user_id))
        builder.adjust(2)
        return builder.as_markup()

    if section.startswith("game_"):
        current_game_key = section[5:]
        for key, title in _HELP_GAMES_ORDER:
            marker = " •" if key == current_game_key else ""
            builder.button(
                text=f"{title}{marker}",
                callback_data=_help_callback_data(section=f"game_{key}", owner_user_id=owner_user_id),
            )
        builder.button(text="🎮 К играм", callback_data=_help_callback_data(section="games", owner_user_id=owner_user_id))
        builder.button(text="🏠 Главное", callback_data=_help_callback_data(section="home", owner_user_id=owner_user_id))
        builder.adjust(2)
        return builder.as_markup()

    for key, title in _HELP_SECTIONS_ORDER:
        marker = " •" if key == section else ""
        builder.button(
            text=f"{title}{marker}",
            callback_data=_help_callback_data(section=key, owner_user_id=owner_user_id),
        )
    builder.button(text="🏠 Главное", callback_data=_help_callback_data(section="home", owner_user_id=owner_user_id))
    builder.adjust(2, 2, 2, 2, 2, 2)
    return builder.as_markup()


def _main_help_text(settings: Settings) -> str:
    return (
        f"<b>{settings.bot_name}</b>\n"
        "Короткая навигация по командам.\n"
        "Выберите раздел кнопками ниже."
    )


def _section_help_text(settings: Settings, section: str) -> str:
    if section.startswith("game_"):
        game_key = section[5:]
        game_text = GAME_RULES_RU.get(game_key)
        if game_text is None:
            return _main_help_text(settings)
        return f"<b>{settings.bot_name}</b>\n\n{game_text}"

    if section == "ai":
        body: str | None = _ai_help_text(settings)
    elif section == "ai_plus":
        body = _ai_plus_help_text(settings)
    elif section == "models":
        body = _models_help_text(settings)
    else:
        body = _HELP_SECTION_TEXT.get(section)
    if body is None:
        return _main_help_text(settings)
    return f"<b>{settings.bot_name}</b>\n\n{body}"


def _resolve_help_payload(settings: Settings, section: str | None, owner_user_id: int | None = None) -> tuple[str, InlineKeyboardMarkup]:
    if section in (None, "", "home"):
        return _main_help_text(settings), _build_help_keyboard(section=None, owner_user_id=owner_user_id)
    return _section_help_text(settings, section), _build_help_keyboard(section=section, owner_user_id=owner_user_id)


async def send_help(message: Message, settings: Settings) -> None:
    owner_user_id = message.from_user.id if message.from_user else None
    text, keyboard = _resolve_help_payload(settings, section=None, owner_user_id=owner_user_id)
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("help"))
async def help_command(message: Message, settings: Settings) -> None:
    await send_help(message, settings)


@router.callback_query(F.data.startswith("help:"))
async def help_callback(query: CallbackQuery, settings: Settings) -> None:
    if query.data is None or query.message is None:
        try:
            await query.answer()
        except TelegramBadRequest:
            pass
        return

    section, owner_user_id = _parse_help_callback_data(query.data)
    if owner_user_id is not None and query.from_user is not None and query.from_user.id != owner_user_id:
        try:
            await query.answer("Это меню помощи другого пользователя. Откройте своё: /help", show_alert=True)
        except TelegramBadRequest:
            pass
        return

    effective_owner_user_id = owner_user_id
    if effective_owner_user_id is None and query.from_user is not None:
        effective_owner_user_id = query.from_user.id

    text, keyboard = _resolve_help_payload(settings, section=section, owner_user_id=effective_owner_user_id)
    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    try:
        await query.answer()
    except TelegramBadRequest:
        return
