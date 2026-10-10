from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from selara.application.ai_character.group import (
    FREE_CALL_NAMES,
    MAX_GROUP_CUSTOM_LENGTH,
    MAX_MEMBER_TEXT_LENGTH,
    PAID_CALL_NAMES,
)
from selara.application.feature_access import resolve_feature_policy
from selara.application.model_catalog import (
    PROFILE_DESCRIPTIONS,
    PROFILE_EMOJI,
    PROFILE_NAMES,
    PROFILE_ORDER,
)
from selara.core.chat_settings import ChatSettings
from selara.core.config import Settings
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.commands.command_catalog import GAME_RULES_RU, get_command_spec
from selara.presentation.handlers.settings_common import split_html_message
from selara.presentation.navigation.cards import FEATURE_CARDS
from selara.presentation.navigation.contract import (
    BACK_LABEL,
    NAV_CALLBACK_PREFIX,
    SECTIONS_LABEL,
)
from selara.presentation.navigation.render import feature_block
from selara.presentation.navigation.tree import (
    ROOT_KEY,
    NavNode,
    get_nav_node,
    nav_callback,
)

router = Router(name="help")

# Telegram caps a message at 4096 characters; stay well under it.
_PAGE_MAX_LEN = 3500

# Old help:<key> callbacks (still sitting in chats) and where their content lives now.
_LEGACY_HELP_KEYS: dict[str, str] = {
    "home": ROOT_KEY,
    "stats": "profile",
    "relationships": "couples",
    "ai": "ai_group",
    "ai_plus": "subscriptions",
    "models": "ai_models",
    "settings": "admin_settings",
}

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
    "profile": (
        "<b>Статистика</b>\n"
        f"• {_code_join(_base_words('stats_profile'))} — профиль, карма и своё описание\n"
        f"• {_code_join(_base_words('stats_leaderboards'))} — топ пользователей и активности "
        "(<code>karma</code>, <code>гибрид</code>, <code>неделя|сутки|час|месяц</code>)\n"
        f"• {_code_join(_base_words('misc_lastseen'))} — когда был активен\n"
        f"• {_code_join(_base_words('stats_achievements'))} — достижения и награды\n"
        "• С телефона: Mini App из <code>/start</code> в личке\n"
        "• С ПК: <code>/login</code> в личке выдаёт одноразовый код для /app"
    ),
    "games": (
        "<b>Игры</b>\n"
        "Выберите группу игр, затем игру: правила и порядок партии — на её экране.\n"
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
    "couples": (
        "<b>Отношения</b>\n"
        "• <code>мои отношения</code> / <code>/relation</code> — статус, кулдауны и кнопки действий\n"
        "• <code>мой брак</code> — отдельная карточка активного брака\n"
        "• <code>браки</code> — все активные браки беседы\n"
        "• <code>/pair @user</code> или <code>предложить встречаться @user</code> — предложение пары\n"
        "• В тексте цель можно указать именем персоны из беседы: <code>пара Коломбина</code>, <code>брак Коломбина</code>\n"
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
    "admin_settings": (
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
        "• Условия и помощь по оплате: <code>/terms</code>, <code>/paysupport</code>"
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
        "• <code>/setcfg instant_stt_enabled false</code> — отключить автоматические ответы "
        "расшифровкой в этом чате, независимо от итогов дня\n"
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


def _strip_owner_suffix(payload: str) -> str:
    """Buttons sent before help became public carry a `:u<owner_id>` suffix; drop it so they keep working."""
    head, separator, tail = payload.rpartition(":u")
    if separator and tail.isdigit():
        return head
    return payload


def _resolve_node_key(data: str | None) -> str:
    """Catalog node a callback points at. Empty, unknown or foreign payloads open the root."""
    if not data:
        return ROOT_KEY
    prefix, _, payload = data.partition(":")
    if prefix == NAV_CALLBACK_PREFIX:
        key = payload
    elif prefix == "help":
        payload = _strip_owner_suffix(payload)
        key = _LEGACY_HELP_KEYS.get(payload, payload)
    else:
        return ROOT_KEY
    try:
        get_nav_node(key)
    except KeyError:
        return ROOT_KEY
    return key


def _node_body(settings: Settings, key: str) -> str | None:
    """Hand-written text of a node; nodes without one show only their area summary and command entries."""
    if key == "troubleshooting":
        return (
            "<b>Если команда не отвечает</b>\n"
            "• Проверьте, где вы пишете: некоторые команды работают только в группе или только в ЛС.\n"
            "• Для скрытых ролей и карточек сначала откройте ЛС с ботом через /start.\n"
            "• Если текстовая команда не срабатывает, попробуйте slash-вариант: админ мог выключить "
            "<code>text_commands_enabled</code> или включить режим алиасов.\n"
            "• Для действий над человеком часто нужен ответ на его сообщение (reply).\n"
            "• Управление игрой, настройками и AI-запросы могут требовать отдельного права Selara.\n"
            "• Для AI проверьте включённую функцию, подписку, дневной/месячный лимит и кулдаун.\n"
            "• Если проблема остаётся: в личке <code>/feedback проблема: описание</code>. "
            "Не присылайте пароли и токены."
        )
    if key == "ai_summary":
        limit = _policy_limit(AiFeature.DAILY_SUMMARY, "manual")
        hour, minimum = _summary_defaults()
        return (
            "<b>Как получить итоги</b>\n"
            f"• <code>/summary</code> в группе — ручная сводка, {limit} раз в месяц на чат.\n"
            "• Нужны право настройки чата и сохранение сообщений "
            "(<code>save_message true</code>).\n"
            f"• Минимум сообщений по умолчанию: {minimum}; порог задаёт "
            "<code>daily_summary_min_messages</code>.\n"
            "• Автоматические итоги работают только с активным Selara AI для чата; "
            "<code>daily_summary_enabled</code> включает их, "
            f"<code>daily_summary_hour</code> по умолчанию {hour} (время бота).\n"
            "• Настройки доступны администратору через ЛС-панель /start или /settings."
        )
    if key == "ai_group":
        return _ai_help_text(settings)
    if key == "ai_models":
        return _models_help_text(settings)
    if key == "subscriptions":
        return _ai_plus_help_text(settings)
    if key.startswith("game_"):
        # GAME_RULES_RU opens with its own bold title; the screen header already shows it.
        return GAME_RULES_RU[key[len("game_") :]].split("\n", 1)[1]
    return _HELP_SECTION_TEXT.get(key)


def _node_text(settings: Settings, node: NavNode) -> str:
    cards = {card.spec_key: card for card in FEATURE_CARDS}
    blocks = [f"<b>{escape(node.title)}</b>\n{escape(node.summary)}"]
    body = _node_body(settings, node.key)
    if body:
        blocks.append(body)
    features = feature_block(node.spec_keys, cards)
    if features:
        blocks.append(features)
    return "\n\n".join(blocks)


def _nav_button(text: str, key: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=nav_callback(key))


def _node_keyboard(node: NavNode) -> InlineKeyboardMarkup:
    children = [get_nav_node(key) for key in node.children]
    rows: list[list[InlineKeyboardButton]] = [
        [_nav_button(child.title, child.key) for child in children[index : index + 2]]
        for index in range(0, len(children), 2)
    ]
    footer: list[InlineKeyboardButton] = []
    if node.parent is not None:
        footer.append(_nav_button(BACK_LABEL, node.parent))
    if node.parent not in (None, ROOT_KEY):
        footer.append(_nav_button(SECTIONS_LABEL, ROOT_KEY))
    if footer:
        rows.append(footer)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _requested_help_page(data: str | None) -> tuple[str, int]:
    """Parse public per-page callbacks, preserving old help: and nv: links."""
    if data and data.startswith("nvp:"):
        payload = data[len("nvp:"):]
        key, separator, raw_page = payload.rpartition(":")
        if separator and raw_page.isdigit():
            try:
                get_nav_node(key)
            except KeyError:
                pass
            else:
                return key, int(raw_page)
        return ROOT_KEY, 0
    return _resolve_node_key(data), 0


def _page_keyboard(key: str, page_index: int, page_count: int) -> InlineKeyboardMarkup:
    """Keep navigation in a single editable message, including long screens."""
    keyboard = _node_keyboard(get_nav_node(key))
    if page_count <= 1:
        return keyboard
    row: list[InlineKeyboardButton] = []
    if page_index:
        row.append(InlineKeyboardButton(
            text="◀️ Предыдущая",
            callback_data=f"nvp:{key}:{page_index - 1}",
        ))
    if page_index < page_count - 1:
        row.append(InlineKeyboardButton(
            text="Следующая ▶️",
            callback_data=f"nvp:{key}:{page_index + 1}",
        ))
    return InlineKeyboardMarkup(inline_keyboard=[row, *keyboard.inline_keyboard])


def _render_screen(settings: Settings, key: str) -> tuple[list[str], InlineKeyboardMarkup]:
    """Pages for a node and its keyboard. Long screens are split; only the last page carries the keyboard."""
    node = get_nav_node(key)
    return split_html_message(_node_text(settings, node), max_len=_PAGE_MAX_LEN), _node_keyboard(node)


async def send_help(message: Message, settings: Settings) -> None:
    pages, _ = _render_screen(settings, ROOT_KEY)
    await message.answer(
        pages[0], parse_mode="HTML",
        reply_markup=_page_keyboard(ROOT_KEY, 0, len(pages)),
    )


@router.message(Command("help"))
async def help_command(message: Message, settings: Settings) -> None:
    await send_help(message, settings)


@router.callback_query(
    F.data.startswith("help:")
    | F.data.startswith(f"{NAV_CALLBACK_PREFIX}:")
    | F.data.startswith("nvp:")
)
async def help_callback(query: CallbackQuery, settings: Settings) -> None:
    if query.data is None or query.message is None:
        try:
            await query.answer()
        except TelegramBadRequest:
            pass
        return

    key, requested_page = _requested_help_page(query.data)
    pages, _ = _render_screen(settings, key)
    page_index = min(requested_page, len(pages) - 1)
    try:
        await query.message.edit_text(
            pages[page_index], parse_mode="HTML",
            reply_markup=_page_keyboard(key, page_index, len(pages)),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    try:
        await query.answer()
    except TelegramBadRequest:
        return
