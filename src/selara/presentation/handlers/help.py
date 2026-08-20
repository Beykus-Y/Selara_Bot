from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from selara.core.config import Settings
from selara.presentation.commands.command_catalog import GAME_RULES_RU, get_command_spec

router = Router(name="help")

_HELP_SECTIONS_ORDER: tuple[tuple[str, str], ...] = (
    ("stats", "📊 Статистика"),
    ("games", "🎮 Игры"),
    ("economy", "💰 Экономика"),
    ("relationships", "💞 Отношения"),
    ("social", "🤝 Социальное"),
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
        "• ЛС-панель: <code>/start</code> в личке\n"
        "• С телефона: Mini App из <code>/start</code> в личке\n"
        "• С ПК: <code>/login</code> в личке выдаёт одноразовый код для /app"
    ),
}



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
        builder.adjust(2, 2, 2, 1)
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
    builder.adjust(2, 2, 2, 1, 1)
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
