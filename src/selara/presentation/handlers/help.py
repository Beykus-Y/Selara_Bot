from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from selara.core.config import Settings

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
    ("spy", "🕵️ Найди шпиона"),
    ("mafia", "🕴 Мафия"),
    ("dice", "🎲 Дуэль кубиков"),
    ("number", "🔢 Угадай число"),
    ("quiz", "❓ Викторина"),
    ("bredovukha", "🧠 Бредовуха"),
    ("bunker", "🏚 Бункер"),
)

_HELP_SECTION_TEXT: dict[str, str] = {
    "stats": (
        "<b>Статистика</b>\n"
        "• <code>/me</code> — профиль в чате\n"
        "• <code>/rep</code> — карма и активность\n"
        "• <code>/top [N]</code> — топ пользователей за всё время по сообщениям\n"
        "• <code>/active [N]</code> — топ по активности\n"
        "• <code>/top karma [N]</code> — топ по карме\n"
        "• <code>/top гибрид [N]</code> — гибридный топ\n"
        "• <code>/top неделя|сутки|час|месяц [N|&lt;N]</code> — топ или список ниже порога за период\n"
        "• <code>/lastseen [@username|user_id]</code> — когда был активен"
    ),
    "games": (
        "<b>Игры</b>\n"
        "Выберите конкретную игру кнопками ниже — покажу описание и правила.\n"
        "• <code>/game</code> — открыть меню игр\n"
        "• <code>/role [game_id]</code> — узнать свою роль (для скрытых игр)\n"
        "• Лобби запускает создатель или участник с правом управления играми"
    ),
    "economy": (
        "<b>Экономика</b>\n"
        "• <code>/eco</code> — панель\n"
        "• <code>/farm</code>, <code>/shop</code>, <code>/inventory</code>\n"
        "• <code>/tap</code>, <code>/daily</code>, <code>/lottery</code>\n"
        "• <code>/market</code>, <code>/pay</code>, <code>/growth</code>\n"
        "• Кнопки панели персональные: другим нужно открыть свою через <code>/eco</code>"
    ),
    "relationships": (
        "<b>Отношения</b>\n"
        "• <code>мои отношения</code> / <code>/relation</code> — статус, кулдауны и кнопки действий\n"
        "• <code>мой брак</code> — отдельная карточка активного брака\n"
        "• <code>браки</code> — все активные браки беседы\n"
        "• <code>/pair @user</code> или <code>предложить встречаться @user</code> — предложение пары\n"
        "• <code>/breakup</code> — расстаться\n"
        "• <code>/marry @user</code> или <code>предложить брак @user</code> — предложение брака\n"
        "• <code>/divorce</code> — развод\n"
        "• Для пары: <code>/care</code>, <code>/date</code>, <code>/gift</code>, <code>/support</code>, <code>/flirt</code>, <code>/surprise</code>\n"
        "• Для брака: <code>/love</code>, <code>/care</code>, <code>/date</code>, <code>/gift</code>, <code>/support</code>, <code>/vow</code>"
    ),
    "social": (
        "<b>Социальное</b>\n"
        "• Карма: reply <code>+</code> / <code>-</code>\n"
        "• Нейминг: <code>/naming Имя</code> или <code>нейминг Имя</code>\n"
        "• Образы чата: reply <code>выдать образ \"Венти\"</code>, <code>снять образ</code>, <code>образы</code>\n"
        "• Reply <code>цитировать</code> — карточка цитаты с аватаром, ником и датой\n"
        "• Reply-действия: шлепнуть/сжечь/убить/трахнуть/соблазнить/засосать/провести ночь с/сесть на/нагнуть/ударить/обнять/поцеловать/пожать руку/дать пять/погладить/куснуть/пнуть/ущипнуть/прижать/наступить/пощекотать/ткнуть/оттолкнуть/утешить/успокоить/защитить/поднять на руки/утащить/выпроводить/подмигнуть/потанцевать/поклониться/подбодрить/угостить/похвалить/поздравить/укрыть/наругать/дать кулак/отсосать/минет\n"
        "• Объявления: <code>объява \"текст\"</code> (по рангу команды)\n"
        "• Подписка объявлений: <code>рег</code> / <code>анрег</code>"
    ),
    "moderation": (
        "<b>Модерация</b>\n"
        "• <code>/pred</code>, <code>/warn</code>, <code>/unwarn</code>\n"
        "• <code>/ban</code>, <code>/unban</code>, <code>/modstat</code>\n"
        "• <code>/roles</code>, <code>/roleadd</code>, <code>/roleremove</code>\n"
        "• <code>/roledefs</code>, <code>/roletemplates</code>, <code>/rolecreate</code>\n"
        "• Без <code>/</code>: пред / варн / снять пред / снять варн / бан / снять бан — по reply или с <code>@username/id</code>"
    ),
    "settings": (
        "<b>Настройки и алиасы</b>\n"
        "• <code>/settings</code> — текущие настройки\n"
        "• <code>/setcfg key value</code> — изменить настройку\n"
        "• <code>/setrank</code>, <code>/ranks</code> — ранги доступа команд\n"
        "• <code>/setalias</code>, <code>/aliases</code>, <code>/unalias</code>, <code>/aliasmode</code>\n"
        "• <code>/settrigger</code>, <code>/triggers</code>, <code>/deltrigger</code>, <code>/triggervars</code>\n"
        "• <code>/rpadd</code>, <code>/rps</code>, <code>/rpdel</code> — кастомные reply-действия с шаблонами\n"
        "• ЛС-панель: <code>/start</code> в личке\n"
        "• С телефона: Mini App из <code>/start</code> в личке\n"
        "• С ПК: <code>/login</code> в личке выдаёт одноразовый код для /app"
    ),
}

_HELP_GAME_TEXT: dict[str, str] = {
    "spy": (
        "<b>🕵️ Найди шпиона</b>\n"
        "Описание:\n"
        "• Один или два шпиона, остальные мирные.\n"
        "• Мирные знают локацию, шпион — нет.\n"
        "Правила:\n"
        "• Обсуждайте в чате и задавайте вопросы.\n"
        "• Голосованием исключите подозреваемого.\n"
        "• Победа мирных: исключён шпион.\n"
        "• Победа шпиона: дожил до финала или сбил мирных."
    ),
    "mafia": (
        "<b>🕴 Мафия</b>\n"
        "Описание:\n"
        "• Фазы: ночь -> день -> голосование.\n"
        "• У ролей есть ночные действия в ЛС.\n"
        "Правила:\n"
        "• Ночью роли делают ходы, днём обсуждение и казнь.\n"
        "• Условия победы зависят от состава: мирные/мафия/нейтралы/вампиры.\n"
        "• Используйте <code>/role</code>, чтобы посмотреть свою роль.\n\n"
        "<b>🟢 Мирная команда</b>\n"
        "• <b>Мирный житель</b> (от 4): без способности, голосует днём.\n"
        "• <b>Комиссар</b> (от 5): ночью проверяет команду игрока.\n"
        "• <b>Доктор</b> (от 5): ночью спасает цель от убийства.\n"
        "• <b>Красотка</b> (от 7): блокирует ночное действие цели.\n"
        "• <b>Телохранитель</b> (от 8): принимает удар на себя.\n"
        "• <b>Журналист</b> (от 9): сравнивает двух игроков, в одной ли они команде.\n"
        "• <b>Инспектор</b> (от 9): узнаёт конкретную роль цели.\n"
        "• <b>Ребёнок</b> (от 8): может раскрыться как подтверждённый мирный.\n"
        "• <b>Священник</b> (от 10): защищает от маньяка/проклятий.\n"
        "• <b>Ветеран</b> (от 10): боеготовность, убивает ночных гостей.\n"
        "• <b>Реаниматор</b> (от 11): один раз воскрешает игрока.\n"
        "• <b>Психолог</b> (от 9): проверяет, убивал ли игрок прошлой ночью.\n"
        "• <b>Детектив</b> (от 8): проверяет, выходил ли игрок ночью.\n\n"
        "<b>🔴 Команда мафии</b>\n"
        "• <b>Рядовая мафия</b> (база, от 4): участвует в ночном убийстве.\n"
        "• <b>Дон мафии</b> (добавляется от 8): лидер мафии, может искать комиссара.\n"
        "• <b>Адвокат</b> (от 9): даёт цели дневную неприкосновенность.\n"
        "• <b>Оборотень</b> (от 9): для комиссара может выглядеть как мирный.\n"
        "• <b>Ниндзя</b> (от 10): его ночной выход не видно детективу.\n"
        "• <b>Отравитель</b> (от 10): травит с отложенной смертью.\n"
        "• <b>Террорист</b> (от 8): при смерти уводит за собой ещё игрока.\n\n"
        "<b>⚫ Нейтральные роли</b>\n"
        "• <b>Маньяк</b> (от 7): играет сам за себя, убивает ночью.\n"
        "• <b>Шут</b> (от 7): побеждает, если его казнят днём.\n"
        "• <b>Ведьма</b> (от 10): по одному зелью спасения и убийства.\n"
        "• <b>Серийный убийца</b> (от 8): как маньяк, устойчив к части блоков.\n"
        "• <b>Вампир</b> (от 11): обращает игроков, формируя свою команду.\n"
        "• <b>Подрывник</b> (от 10): минирует цель, взрыв при казни.\n\n"
        "<b>📊 Баланс (кратко)</b>\n"
        "• До 7 игроков: максимум 2 активные спецроли.\n"
        "• 8-10 игроков: 3-5 активных ролей.\n"
        "• 11+ игроков: можно добавлять нейтралов и сложные механики.\n"
        "• Мафия обычно около 1 к 3 от общего числа игроков."
    ),
    "dice": (
        "<b>🎲 Дуэль кубиков</b>\n"
        "Описание:\n"
        "• Каждый игрок бросает кубик один раз.\n"
        "Правила:\n"
        "• Кто выбросил больше — побеждает.\n"
        "• При равенстве максимума — ничья между лидерами."
    ),
    "number": (
        "<b>🔢 Угадай число</b>\n"
        "Описание:\n"
        "• Бот загадывает число от 1 до 100.\n"
        "Правила:\n"
        "• Игроки пишут числа в чат.\n"
        "• Бот отвечает выше/ниже и насколько близко.\n"
        "• Кто первым угадал — победитель."
    ),
    "quiz": (
        "<b>❓ Викторина</b>\n"
        "Описание:\n"
        "• Раунды с вопросами и вариантами ответа.\n"
        "Правила:\n"
        "• Выбирайте вариант кнопкой.\n"
        "• За верный ответ начисляются очки.\n"
        "• Побеждает игрок с максимальным счётом после финального раунда."
    ),
    "bredovukha": (
        "<b>🧠 Бредовуха</b>\n"
        "Описание:\n"
        "• Есть фраза с пропуском и правильный ответ.\n"
        "• Игроки отправляют фейковые ответы.\n"
        "Правила:\n"
        "• Потом все голосуют, где правда.\n"
        "• Очки даются за угадывание правды и за голоса за вашу ложь.\n"
        "• После заданного числа раундов побеждает лидер по очкам."
    ),
    "bunker": (
        "<b>🏚 Бункер</b>\n"
        "Описание:\n"
        "• После катастрофы в бункере ограничено число мест.\n"
        "• У каждого игрока скрытая карточка персонажа с характеристиками.\n"
        "Правила:\n"
        "• По очереди игроки раскрывают по одной характеристике через ЛС.\n"
        "• После полного круга запускается голосование на выбывание.\n"
        "• Голосуют в ЛС, но бот публикует в группе, кто против кого.\n"
        "• При ничьей никто не выбывает.\n"
        "• Выбывший раскрывает карточку полностью.\n"
        "• Побеждают те, кто остался в числе мест бункера."
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
        builder.adjust(2, 2, 2, 1)
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
        builder.adjust(2, 2, 2, 1, 1)
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
        game_text = _HELP_GAME_TEXT.get(game_key)
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
