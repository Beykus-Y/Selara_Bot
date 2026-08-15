"""Single structured source for the bot's slash commands, used to keep
docs/WEB_UI_MODERNIZATION_TODO.md's four consumer surfaces (help.py,
USER_GUIDE.md, ADMIN_GUIDE.md, the web docs) from silently drifting apart —
the same problem RP-actions and roles/permissions had before this file
existed (see catalog.py's build_social_action_docs() and core/roles.py for
the equivalent pattern applied to those).

This is being filled in category by category (see the roadmap in
WEB_UI_MODERNIZATION_TODO.md's "Модель контента" section), not all at once —
each command's `dispatch_kind` was found to matter after discovering that
some commands (e.g. /article) are dispatched through neither aiogram's
Command() decorator nor catalog.py's trigger dicts, but a third, ad-hoc
regex-matching path. Get dispatch_kind wrong and a doc claims a command
works when it doesn't (or omits a real natural-language trigger).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DispatchKind = Literal["slash", "natural_language", "both"]


@dataclass(frozen=True)
class CommandSpec:
    key: str
    category: str
    dispatch_kind: DispatchKind
    syntax: tuple[str, ...]
    title_ru: str
    description_ru: str
    examples: tuple[str, ...] = ()
    natural_triggers: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


COMMAND_CATALOG: tuple[CommandSpec, ...] = (
    CommandSpec(
        key="economy_panel",
        category="economy",
        dispatch_kind="both",
        syntax=(
            "/eco [global|local]",
            "/tap",
            "/daily",
            "/lottery [free|paid|item|status]",
            "/growth",
        ),
        title_ru="Панель экономики и базовый заработок",
        description_ru=(
            "Экономический профиль открывается через /eco. Дальше от него идут клики, "
            "ежедневка, лотерея и профиль роста."
        ),
        natural_triggers=("баланс", "тап", "дейлик", "лотерея", "рост", "профиль"),
        notes=("В личке /eco local имеет смысл только если для вас уже установлен local-контекст чата.",),
    ),
    CommandSpec(
        key="economy_farm",
        category="economy",
        dispatch_kind="both",
        syntax=(
            "/farm",
            "/farm plant <культура> [грядка]",
            "/farm harvest <грядка>",
            "/farm sell <культура> <кол-во>",
            "/farm upfarm",
            "/farm upsize <средний|большой>",
        ),
        title_ru="Ферма",
        description_ru=(
            "Ферма умеет показывать статус, сажать культуры, собирать урожай, продавать "
            "его и покупать апгрейды уровня и размера."
        ),
        natural_triggers=("ферма",),
        examples=("/farm plant wheat", "/farm harvest 2", "/farm sell wheat 10", "/farm upsize большой"),
    ),
    CommandSpec(
        key="economy_shop_inventory_craft",
        category="economy",
        dispatch_kind="both",
        syntax=(
            "/shop",
            "/shop buy <номер_оффера>",
            "/inventory",
            "/inventory use <предмет> [грядка]",
            "/craft",
            "/craft <recipe_code>",
        ),
        title_ru="Магазин, инвентарь и крафт",
        description_ru=(
            "Через магазин покупаются офферы, через инвентарь применяются предметы, а "
            "через крафт собираются новые вещи по рецептам."
        ),
        natural_triggers=("магазин", "инвентарь", "крафт"),
        notes=("Названия предметов в командах можно писать как кодами, так и многими русскими алиасами.",),
    ),
    CommandSpec(
        key="economy_market_transfer_auction",
        category="economy",
        dispatch_kind="both",
        syntax=(
            "/market",
            "/market sell <предмет> <кол-во> <цена>",
            "/market buy <лот_id> <кол-во>",
            "/market cancel <лот_id>",
            "/pay @username 100",
            "/auction",
            "/auction start <item_code> <qty> <start_price> [minutes]",
            "/auction cancel",
            "/bid <сумма>",
        ),
        title_ru="Рынок, переводы и аукцион",
        description_ru=(
            "Игроки могут торговать друг с другом через рынок и прямые переводы. Аукцион "
            "живёт в группе: смотреть его может любой участник, а запускать и отменять — "
            "только роли с нужным доступом."
        ),
        natural_triggers=("рынок", "перевод", "платеж", "аукцион", "ставка"),
        examples=("/pay 123456789 250", "reply + /pay 100", "/market sell crop:wheat 20 15", "/bid 5000"),
        notes=(
            "/pay можно сделать по @username, user_id или reply.",
            "/auction start и /auction cancel требуют прав на управление настройками или аналогичного ранга команды.",
        ),
    ),
    CommandSpec(
        key="economy_growth",
        category="economy",
        dispatch_kind="both",
        syntax=("/growth", "/growth do"),
        title_ru="Рост",
        description_ru=(
            "Команда /growth показывает профиль роста, стресс и доступные действия. "
            "Активное действие запускается отдельной командой."
        ),
        natural_triggers=("рост", "профиль", "дрочка", "дрочить", "подрочить"),
        notes=("Команда без do открывает профиль и кнопки состояния.",),
    ),
    CommandSpec(
        key="misc_daily_article",
        category="misc",
        dispatch_kind="both",
        syntax=("/article",),
        title_ru="Статья дня",
        description_ru="Показывает одну случайную статью из локального каталога, детерминированно выбранную на день.",
        natural_triggers=("статья", "моя статья"),
        notes=(
            "Не завязана на настройки экономики — работает независимо от economy_enabled.",
            "Диспетчеризуется через _is_daily_article_command() в text_commands.py "
            "(regex для /article + прямая проверка фраз статья/моя статья), а не через "
            "aiogram Command() или catalog.py — первая найденная команда с таким путём; "
            "маршрутизация команд в этом боте сложнее двух механизмов и требует чтения "
            "кода, а не только грепа по Command(\"...\").",
        ),
    ),
    CommandSpec(
        key="games_lobby",
        category="games",
        dispatch_kind="both",
        syntax=(
            "/game",
            "/game spy",
            "/game whoami",
            "/game mafia",
            "/game dice",
            "/game quiz",
            "/game bredovukha",
            "/game bunker",
        ),
        title_ru="Запуск лобби",
        description_ru=(
            "Команда /game открывает меню выбора. Если указать режим сразу, бот попробует "
            "создать лобби без дополнительных кликов."
        ),
        natural_triggers=("игра",),
        notes=(
            "Для запуска нужен доступ manage_games в конкретном чате.",
            "Для новых запусков доступны spy, whoami, mafia, dice, quiz, bredovukha, bunker.",
            "Режим number больше не запускается заново, но может встретиться как уже идущая старая партия.",
        ),
    ),
    CommandSpec(
        key="games_role_reveal",
        category="games",
        dispatch_kind="both",
        syntax=("/role [game_id]", "/start"),
        title_ru="Скрытая роль, карточка и ЛС",
        description_ru=(
            "Игры со скрытой информацией присылают роль или личную карточку в приват. Если "
            "бот не может написать вам первым, роль вы не увидите."
        ),
        natural_triggers=("роль", "старт"),
        notes=(
            "Если /role ничего не показывает, скорее всего у вас сейчас нет активной секретной роли или ЛС с ботом не открыт.",
        ),
    ),
    CommandSpec(
        key="relationships_start",
        category="relationships",
        dispatch_kind="both",
        syntax=("/relation", "/pair @username", "/marry @username"),
        title_ru="Как начать отношения",
        description_ru=(
            "Сначала откройте «мои отношения» или /relation, а для брака отдельно доступен "
            "«мой брак». Предложение пары или брака отправляется через reply или @username, "
            "а подтверждение идёт кнопками."
        ),
        natural_triggers=(
            "мои отношения",
            "мой брак",
            "браки",
            "отношения",
            "брак",
            "пара",
            "жениться",
            "предложить встречаться",
            "предложить брак",
        ),
        examples=("reply + /pair", "reply + /marry", "предложить встречаться @username", "предложить брак @username"),
        notes=(
            "Срок ответа на предложение: 24 часа.",
            "В «мои отношения» и /relation бот показывает текущий статус, партнёра, кулдауны и inline-кнопки действий.",
            "«браки» показывает все активные браки беседы и их длительность.",
        ),
    ),
    CommandSpec(
        key="relationships_pair_actions",
        category="relationships",
        dispatch_kind="both",
        syntax=("/care", "/date", "/gift", "/support", "/flirt", "/surprise"),
        title_ru="Действия стадии пары",
        description_ru=(
            "Для пары доступны ежедневные или периодические действия, которые поднимают "
            "уровень отношений. Часть из них есть только до брака."
        ),
        natural_triggers=("забота", "свидание", "подарок", "поддержка", "флирт", "сюрприз"),
        notes=(
            "/flirt и /surprise работают только на стадии пары.",
            "Кулдаун на relation-действия составляет 30 минут, а точный остаток видно в /relation.",
        ),
    ),
    CommandSpec(
        key="relationships_marriage_actions",
        category="relationships",
        dispatch_kind="both",
        syntax=("/love", "/care", "/date", "/gift", "/support", "/vow"),
        title_ru="После свадьбы",
        description_ru=(
            "У брака остаётся базовый набор действий, но вместо флирта и сюрприза открываются "
            "собственные механики любви и клятвы."
        ),
        natural_triggers=("любовь", "забота", "свидание", "подарок", "поддержка", "клятва"),
        notes=(
            "/love и /vow доступны только в браке.",
            "Если вы ответили reply не своему партнёру, бот отклонит действие.",
        ),
    ),
    CommandSpec(
        key="relationships_end",
        category="relationships",
        dispatch_kind="both",
        syntax=("/breakup", "/divorce"),
        title_ru="Как завершить связь",
        description_ru="Пара и брак закрываются разными командами. После брака связь супруга также убирается из семейного графа.",
        natural_triggers=("расстаться", "развод"),
    ),
    CommandSpec(
        key="family_adopt_pet_tree",
        category="family",
        dispatch_kind="both",
        syntax=(
            "/adopt @username",
            "/adoptdaughter @username",
            "/pet @username",
            "/family",
            "/family @username",
        ),
        title_ru="Усыновление, питомцы и семейное древо",
        description_ru=(
            "Семейные команды строят отдельный граф отношений: родители, дети, питомцы и "
            "супруги. Создание связи подтверждается кнопками согласия."
        ),
        natural_triggers=("усыновить", "удочерить", "стать питомцем", "семья"),
        examples=("reply + /adopt", "reply + /pet", "/family @username"),
        notes=(
            "/adopt усыновляет (роль «сын»), /adoptdaughter — удочеряет (роль «дочь») — единственная "
            "разница между ними.",
            "/family без аргумента показывает ваши связи, а с аргументом или reply — связи выбранного участника.",
        ),
    ),
    CommandSpec(
        key="family_escape",
        category="family",
        dispatch_kind="both",
        syntax=("/escapefamily", "/escapepet"),
        title_ru="Уйти из семьи",
        description_ru=(
            "Позволяет разорвать семейную связь: сбежать от родителя или перестать быть "
            "чьим-то питомцем. Оба действия подтверждаются кнопкой и необратимы."
        ),
        natural_triggers=("сбежать из семьи", "сбежать от хозяина"),
        notes=("/escapefamily доступна только если у вас есть родитель в этом чате.",),
    ),
    CommandSpec(
        key="misc_lastseen",
        category="misc",
        dispatch_kind="both",
        syntax=("/lastseen [@username|user_id]",),
        title_ru="Когда пользователь был активен",
        description_ru="Команда показывает, когда конкретный участник последний раз проявлял активность, и работает как по аргументу, так и по reply.",
        natural_triggers=("когда был", "когда была"),
        examples=("/lastseen @username", "reply + /lastseen"),
    ),
    CommandSpec(
        key="misc_public_service_commands",
        category="misc",
        dispatch_kind="both",
        syntax=("/help", "/settings", "/roles", "/modstat [@username|user_id]"),
        title_ru="Публичные служебные команды",
        description_ru="Эти команды не меняют настройки, а помогают понять состояние группы: список ролей, moderation-статус и параметры чата.",
        natural_triggers=("помощь",),
        examples=("/modstat", "/modstat @username", "reply + /modstat"),
        notes=(
            "/settings показывает текущие настройки, но менять их могут только роли с правом управления.",
            "/roles выводит назначенные роли бота в чате.",
            "/modstat без аргументов показывает ваш собственный moderation-статус.",
        ),
    ),
)


def commands_for_category(category: str) -> tuple[CommandSpec, ...]:
    return tuple(spec for spec in COMMAND_CATALOG if spec.category == category)


def get_command_spec(key: str) -> CommandSpec:
    for spec in COMMAND_CATALOG:
        if spec.key == key:
            return spec
    raise KeyError(f"Unknown command catalog key: {key!r}")
