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
    CommandSpec(
        key="misc_gacha",
        category="misc",
        dispatch_kind="natural_language",
        syntax=(),
        title_ru="Гача (Genshin / HSR)",
        description_ru=(
            "Мини-игра с двумя баннерами — Genshin Impact («генш»/«геншин») и Honkai: Star "
            "Rail («хср»). Работает через внешний gacha-сервис бота, а не через обычную "
            "экономику напрямую."
        ),
        natural_triggers=(
            "гача генш",
            "гача геншин",
            "гача хср",
            "моя гача генш",
            "моя гача геншин",
            "моя гача хср",
            "гача инфо",
            "гача скип генш",
        ),
        examples=("гача генш", "моя гача хср", "гача инфо", "гача скип генш @username"),
        notes=(
            "Валюта баннера пополняется из обычных монет экономики по курсу 10 монет за "
            "1 единицу валюты гачи, а не выдаётся отдельно.",
            "Действие блокируется, если чат не подписан на служебный Telegram-канал бота — "
            "при первой попытке бот пришлёт ссылку на подписку вместо результата пула.",
            "Работает только если в чате включена gacha_enabled — эта настройка не входит в "
            "обычный список /settings и переключается отдельной служебной командой, а не "
            "рядовыми правами чата.",
            "«гача скип» может дополнительно указывать @username — для кого выполняется скип.",
        ),
    ),
    CommandSpec(
        key="admin_role_definitions",
        category="admin",
        dispatch_kind="slash",
        syntax=("/roledefs", "/roletemplates"),
        title_ru="Просмотр ролей и шаблонов",
        description_ru=(
            "/roledefs показывает роли, уже настроенные в этом чате. /roletemplates — "
            "системные шаблоны ролей, из которых можно создавать кастомные."
        ),
        notes=("/roletemplates требует прав manage_role_templates.",),
    ),
    CommandSpec(
        key="admin_role_assignment",
        category="admin",
        dispatch_kind="slash",
        syntax=('/roleadd "<роль|ранг>" [@user|id]', "/roleremove [@user|id]"),
        title_ru="Назначение и снятие ролей",
        description_ru=(
            "Выдаёт или снимает роль бота у конкретного участника — по reply, @username или "
            "id. Требует права manage_roles и достаточного ранга относительно цели."
        ),
        examples=('/roleadd "senior_admin" @username', "reply + /roleremove"),
        notes=(
            "Нельзя выдать себе роль выше своей, если вы не владелец.",
            "Нельзя снять последнего владельца чата.",
        ),
    ),
    CommandSpec(
        key="admin_role_custom",
        category="admin",
        dispatch_kind="slash",
        syntax=(
            '/rolecreate "<название>" ["шаблон"] [ранг]',
            '/rolesettitle "<роль>" "<новое название>"',
            '/rolesetrank "<роль>" <ранг>',
            '/roleperms "<роль>" +permission -permission ...',
            '/roledelete "<роль>"',
        ),
        title_ru="Кастомные роли",
        description_ru=(
            "Создаёт и настраивает роли сверх системных шаблонов: имя, ранг и точный набор "
            "прав. Требует права manage_role_templates."
        ),
        examples=('/rolecreate "Модератор ивентов" "senior_admin" 15', '/roleperms "Модератор ивентов" +announce -manage_games'),
    ),
    CommandSpec(
        key="admin_command_ranks",
        category="admin",
        dispatch_kind="slash",
        syntax=('/setrank "<команда>" "<ранг>"', "/ranks"),
        title_ru="Ранги команд",
        description_ru=(
            "Задаёт минимальный ранг роли, необходимый для конкретной команды бота. /ranks "
            "показывает текущий список — если он пуст, действуют стандартные правила доступа."
        ),
        examples=('/setrank "roleadd" "30"',),
    ),
    CommandSpec(
        key="admin_aliases",
        category="admin",
        dispatch_kind="slash",
        syntax=(
            '/setalias "стандартный триггер" "новый алиас" [--force]',
            '/unalias "алиас"',
            "/aliases",
            "/aliasmode [aliases_if_exists|both|standard_only]",
        ),
        title_ru="Управление алиасами",
        description_ru=(
            "Позволяет переименовать стандартные текстовые триггеры под словарь конкретного "
            "чата, удалить кастомный алиас и переключить режим их работы."
        ),
        examples=('/setalias "баланс" "кошель"', "/aliasmode both"),
        notes=("/aliasmode без аргумента показывает текущий режим, не меняя его.",),
    ),
    CommandSpec(
        key="admin_settings_tools",
        category="admin",
        dispatch_kind="slash",
        syntax=("/setcfg <key> <value>", "/facttest"),
        title_ru="Инструменты настроек",
        description_ru=(
            "/setcfg меняет значение любой настройки чата напрямую по ключу. /facttest "
            "показывает превью следующего автофакта, не дожидаясь расписания."
        ),
        examples=("/setcfg vote_daily_limit 30", "/setcfg actions_18_enabled true"),
        notes=("Оба требуют права manage_settings.",),
    ),
    CommandSpec(
        key="stats_profile",
        category="stats",
        dispatch_kind="both",
        syntax=("/me", "/rep", "/desc <текст>"),
        title_ru="Профиль и репутация",
        description_ru=(
            "/me — ваш профиль в чате. /rep — карма и активность. /desc задаёт короткое "
            "описание, которое видно в профиле."
        ),
        natural_triggers=("кто я", "кто ты", "репутация", "мой рейтинг"),
        notes=("У /desc нет текстового аналога — только слэш-команда.",),
    ),
    CommandSpec(
        key="stats_leaderboards",
        category="stats",
        dispatch_kind="both",
        syntax=(
            "/top [N]",
            "/top karma|гибрид [N]",
            "/top неделя|сутки|час|месяц [N|<N]",
            "/active [N]",
        ),
        title_ru="Топы и активность",
        description_ru=(
            "/top — рейтинг по сообщениям, карме, гибридному счёту или за период. "
            "/active — топ по активности."
        ),
        natural_triggers=("топ", "актив"),
    ),
    CommandSpec(
        key="stats_achievements",
        category="stats",
        dispatch_kind="both",
        syntax=("/achievements", "/awards", "/achsync [global]"),
        title_ru="Достижения и награды",
        description_ru=(
            "/achievements — список достижений с прогрессом. /awards — полученные награды. "
            "/achsync пересчитывает achievement-статистику чата (или всех чатов с global)."
        ),
        natural_triggers=("достижения", "мои достижения", "ачивки"),
        notes=(
            "/achsync доступен только администраторам чата и только в группе.",
            "/awards текстового аналога не имеет.",
        ),
    ),
    CommandSpec(
        key="stats_award_grant",
        category="stats",
        dispatch_kind="both",
        syntax=("наградить @username <текст награды>", "reply + наградить <текст награды>", "/award"),
        title_ru="Выдача награды в профиль",
        description_ru=(
            "Награда выдаётся текстовой фразой «наградить», по имени пользователя или reply на его "
            "сообщение — не слэш-командой."
        ),
        natural_triggers=("наградить",),
        notes=("/award сам по себе ничего не выдаёт — только показывает подсказку по формату.",),
    ),
    CommandSpec(
        key="admin_moderation_actions",
        category="moderation",
        dispatch_kind="both",
        syntax=(
            "/pred [причина]",
            "/warn [причина]",
            "/unwarn",
            "/ban [причина]",
            "/unban",
            "reply + пред|варн|бан|снять пред|снять варн|снять бан [причина]",
        ),
        title_ru="Модерация: предупреждения и баны",
        description_ru=(
            "Выдаёт или снимает предупреждение либо бан участнику — слэш-командой или коротким "
            "словом в ответ на его сообщение."
        ),
        natural_triggers=("пред", "варн", "бан", "снять пред", "снять варн", "снять бан"),
        notes=(
            "Оба пути (слэш и текстовое слово) требуют reply на сообщение цели.",
            "Есть синонимы снятия: разпред/анпред, разварн/анварн, разбан/анбан.",
        ),
    ),
    CommandSpec(
        key="admin_smart_triggers",
        category="admin",
        dispatch_kind="both",
        syntax=(
            '/settrigger "ключ" "ответ" [exact|contains|starts_with]',
            'научить "ключ" "ответ" [exact|contains|starts_with]',
            "/triggers",
            "/deltrigger <id>",
            "/triggervars",
        ),
        title_ru="Смарт-триггеры чата",
        description_ru=(
            "Настраивает автоответы на ключевые слова: создание, список, удаление и доступные "
            "переменные шаблона."
        ),
        natural_triggers=("научить",),
        notes=(
            "Требует права manage_settings.",
            "/triggers, /deltrigger и /triggervars — только слэш-командой, без текстового аналога.",
            "/deltrigger принимает числовой id из списка /triggers.",
        ),
    ),
    CommandSpec(
        key="admin_custom_rp_actions",
        category="admin",
        dispatch_kind="both",
        syntax=('/rpadd "триггер" "шаблон"', 'добавить_действие "триггер" "шаблон"', "/rps", '/rpdel "триггер"'),
        title_ru="Кастомные RP-действия",
        description_ru=(
            "Добавляет собственные reply-действия сверх встроенных: свой триггер и шаблон ответа."
        ),
        natural_triggers=("добавить_действие",),
        notes=(
            "Требует права manage_settings.",
            "Переменные шаблона — те же, что показывает /triggervars.",
            "/rps и /rpdel — только слэш-командой, без текстового аналога.",
        ),
    ),
    CommandSpec(
        key="social_naming",
        category="social",
        dispatch_kind="both",
        syntax=("/naming <имя>", "нейминг <имя>"),
        title_ru="Нейминг чата",
        description_ru="Задаёт кастомное обращение бота к участникам в этом чате.",
        natural_triggers=("нейминг",),
        notes=(
            "/naming — не aiogram Command(), а тот же regex-путь, что и «нейминг» "
            "(тот же класс диспетчеризации, что у /article).",
        ),
    ),
    CommandSpec(
        key="social_karma_reply",
        category="social",
        dispatch_kind="natural_language",
        syntax=('reply + "+"', 'reply + "-"'),
        title_ru="Карма по реплаю",
        description_ru="Ответ «+» или «-» на сообщение участника меняет его карму. Слэш-команды нет.",
        natural_triggers=("+", "-"),
    ),
    CommandSpec(
        key="social_quote_card",
        category="social",
        dispatch_kind="natural_language",
        syntax=("reply + цитировать",),
        title_ru="Карточка цитаты",
        description_ru="Reply с текстом «цитировать» собирает карточку цитаты с аватаром, ником и датой.",
        natural_triggers=("цитировать",),
    ),
    CommandSpec(
        key="social_personas",
        category="social",
        dispatch_kind="natural_language",
        syntax=('выдать образ "<имя>" [@user]', "снять образ [@user]", "образы"),
        title_ru="Образы участников",
        description_ru="Временно подменяет отображаемое имя участника («образ») в ответах бота.",
        natural_triggers=("выдать образ", "снять образ", "образы"),
        notes=("Функция отключаема настройкой чата — если выключена, бот сообщит об этом.",),
    ),
    CommandSpec(
        key="social_announcements",
        category="social",
        dispatch_kind="natural_language",
        syntax=('объява "текст"', "рег", "анрег"),
        title_ru="Объявления в чате",
        description_ru=(
            "Рассылает объявление всем подписанным участникам чата. рег/анрег — подписка и отписка."
        ),
        natural_triggers=("объява", "рег", "анрег"),
        notes=("объява требует ранг команды, назначаемый /setrank.",),
    ),
)


def commands_for_category(category: str) -> tuple[CommandSpec, ...]:
    return tuple(spec for spec in COMMAND_CATALOG if spec.category == category)


def get_command_spec(key: str) -> CommandSpec:
    for spec in COMMAND_CATALOG:
        if spec.key == key:
            return spec
    raise KeyError(f"Unknown command catalog key: {key!r}")


# Full game-rules prose (win conditions, role tables, phase mechanics) —
# qualitatively different from CommandSpec (which answers "how do I invoke
# this", not "how does the game work") and used by /help's per-game
# deep-dive menu. Kept here, not duplicated in help.py, so any future
# consumer (e.g. a generated guide) can pull the same canonical text.
# HTML-formatted for Telegram's parse_mode="HTML".
GAME_RULES_RU: dict[str, str] = {
    "zlobcards": (
        "<b>🃏 500 Злобных Карт</b>\n"
        "Описание:\n"
        "• Каждый раунд выходит одна чёрная карта с пропуском.\n"
        "• У каждого игрока в приват приходит рука белых карт.\n"
        "Правила:\n"
        "• Приватно выберите белую карту (или две, если чёрная карта просит две), чтобы закрыть пропуск.\n"
        "• Когда все сдали ответы, начинается анонимное голосование за самый смешной вариант.\n"
        "• Авторы вариантов скрыты до конца голосования.\n"
        "• Побеждает игрок с наибольшим числом очков после нужного количества раундов."
    ),
    "whoami": (
        "<b>🎭 Кто я</b>\n"
        "Описание:\n"
        "• Каждому в приват приходит карточка с персонажем, которую сам игрок не видит.\n"
        "• Остальные видят чужие карточки как на столе.\n"
        "Правила:\n"
        "• Ходит один игрок за раз: задаёт столу вопрос с ответом «да / нет / не знаю».\n"
        "• В свой ход можно вместо вопроса назвать догадку о своей карточке.\n"
        "• Верная догадка выводит игрока из круга вопросов, но партия продолжается для остальных.\n"
        "• Побеждают все, кто успел разгадать свою карточку; порядок финиша фиксируется."
    ),
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
