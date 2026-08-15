from __future__ import annotations

from typing import Any

from selara.domain.entities import UserChatOverview
from selara.presentation.commands.catalog import build_social_action_docs
from selara.presentation.commands.command_catalog import get_command_spec
from selara.presentation.handlers.settings_common import SETTING_META


def _docs_item(
    title: str,
    *,
    text: str,
    badges: tuple[str, ...] = (),
    commands: tuple[str, ...] = (),
    triggers: tuple[str, ...] = (),
    examples: tuple[str, ...] = (),
    steps: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
    requires_settings: tuple[str, ...] = (),
) -> dict[str, Any]:
    # Availability markers derived from the single settings source (SETTING_META)
    # instead of hand-typed badge text, so a doc item can't silently drift from
    # the setting it actually depends on (see catalog.py's build_social_action_docs
    # for the equivalent pattern applied to RP-actions).
    badges = badges + tuple(f"{SETTING_META[key].short_ru} по настройке" for key in requires_settings)
    search_text = " ".join(
        (
            title,
            text,
            *badges,
            *commands,
            *triggers,
            *examples,
            *steps,
            *notes,
        )
    ).lower()
    return {
        "title": title,
        "text": text,
        "badges": badges,
        "commands": commands,
        "triggers": triggers,
        "examples": examples,
        "steps": steps,
        "notes": notes,
        "search_text": search_text,
    }


def _docs_section(
    anchor: str,
    title: str,
    summary: str,
    *items: dict[str, Any],
) -> dict[str, Any]:
    # Deep-linkable per-item anchor, e.g. "user-docs-social-item-3". Stable as
    # long as items keep their position within the section; reordering items
    # changes their link, same tradeoff as heading-based anchors elsewhere.
    numbered_items = [
        {**item, "anchor": f"{anchor}-item-{index}"} for index, item in enumerate(items, start=1)
    ]
    return {
        "anchor": anchor,
        "title": title,
        "summary": summary,
        "items": numbered_items,
    }


# Single source for both RP-action trigger lists below — derived from
# selara.presentation.commands.catalog so this page cannot drift out of sync
# with the bot's actual command set or its actions_18_enabled gate.
_SOCIAL_ACTION_DOCS = build_social_action_docs()
_SOCIAL_TRIGGERS_NON_18 = tuple(action.trigger for action in _SOCIAL_ACTION_DOCS if not action.is_18_plus)
_SOCIAL_TRIGGERS_18_PLUS = tuple(action.trigger for action in _SOCIAL_ACTION_DOCS if action.is_18_plus)

_USER_DOC_SECTIONS: tuple[dict[str, Any], ...] = (
    _docs_section(
        "user-docs-start",
        "С чего начать",
        "Базовая схема работы: что делается в группе, что приходит в личку и почему часть сценариев завязана на reply или кнопки.",
        _docs_item(
            "Где писать команды",
            text=(
                "Почти все повседневные команды работают в группе: статистика, экономика, отношения, семья, игры и reply-действия. "
                "Личный чат с ботом нужен для входа в веб-панель, скрытых ролей и deep-link переходов."
            ),
            badges=("группа", "ЛС"),
            commands=("/help", "/start", "/login", "/role"),
            steps=(
                "Откройте личный чат с ботом и нажмите Start, если хотите получать роли, карточки или код входа.",
                "В группе вызывайте команды обычным slash-форматом или текстовыми триггерами, если они включены в чате.",
                "Если игра использует скрытые роли, не закрывайте ЛС: бот шлёт важные подсказки именно туда.",
            ),
            notes=(
                "Если бот молчит в группе, причина часто в отключённых текстовых командах, правах команды или Telegram privacy mode.",
            ),
        ),
        _docs_item(
            "Что чаще всего делается через reply",
            text=(
                "Часть сценариев завязана на ответ на чужое сообщение: так бот точно понимает цель действия без лишних аргументов."
            ),
            badges=("reply", "группа"),
            examples=(
                "reply + /pair",
                "reply + /marry",
                "reply + /pay 100",
                "reply + обнять",
                "reply + соблазнить",
            ),
            notes=(
                "Reply-действия работают только по чужому сообщению, не по своему и не по боту.",
                "Предложения пары, брака, усыновления и статуса питомца подтверждаются кнопками согласия/отказа.",
            ),
        ),
        _docs_item(
            "Быстрый вход в справку и панель",
            text=(
                "Команда помощи показывает встроенное меню по разделам, а `/login` выдаёт одноразовый код для веб-панели `/app`."
            ),
            badges=("ЛС", "группа"),
            commands=("/help", "/login"),
            notes=(
                "Код входа выдаётся в личке, а не в группе.",
                "На сайте `/app/docs/user` собрана расширенная версия пользовательской документации.",
            ),
        ),
    ),
    _docs_section(
        "user-docs-stats",
        "Статистика и статус",
        "Команды, которые помогают понять своё положение в чате, посмотреть топы, роли и базовые публичные статусы.",
        _docs_item(
            "Профиль, репутация и лидерборды",
            text=(
                "Основной набор для повседневной активности: свой профиль, карма, общий рейтинг и отдельные топы по активности."
            ),
            badges=("группа", "текстовые аналоги"),
            commands=(
                "/me",
                "/rep",
                "/top [N]",
                "/top karma [N]",
                "/top неделя|сутки|час|месяц [N|<N]",
                "/active [N]",
            ),
            triggers=("кто я", "репутация", "мой рейтинг", "топ 10", "топ карма 15", "актив 20"),
            notes=(
                "В `/top` можно переключать режим и период через аргументы, а в части чатов ещё и через кнопки.",
                "Пример `/top неделя <100` покажет активных участников с активностью меньше 100 сообщений за неделю.",
                "Лимиты списка зависят от настроек группы.",
            ),
        ),
        _docs_item(
            "Когда пользователь был активен",
            text="Команда показывает, когда конкретный участник последний раз проявлял активность, и работает как по аргументу, так и по reply.",
            badges=("группа", "reply"),
            commands=("/lastseen [@username|user_id]",),
            triggers=("когда был", "когда была"),
            examples=("/lastseen @username", "reply + /lastseen"),
        ),
        _docs_item(
            "Публичные служебные команды",
            text=(
                "Эти команды не меняют настройки, а помогают понять состояние группы: список ролей, moderation-статус и параметры чата."
            ),
            badges=("группа",),
            commands=("/help", "/settings", "/roles", "/modstat [@username|user_id]"),
            examples=("/modstat", "/modstat @username", "reply + /modstat"),
            notes=(
                "`/settings` показывает текущие настройки, но менять их могут только роли с правом управления.",
                "`/roles` выводит назначенные роли бота в чате.",
                "`/modstat` без аргументов показывает ваш собственный moderation-статус.",
            ),
        ),
    ),
    _docs_section(
        "user-docs-games",
        "Игры",
        "Как запускать игровые лобби, где смотреть скрытую роль и как участвовать в конкретных режимах.",
        _docs_item(
            (_games_lobby := get_command_spec("games_lobby")).title_ru,
            text=_games_lobby.description_ru,
            badges=("группа",),
            commands=_games_lobby.syntax,
            triggers=_games_lobby.natural_triggers,
            notes=_games_lobby.notes,
        ),
        _docs_item(
            (_games_role := get_command_spec("games_role_reveal")).title_ru,
            text=_games_role.description_ru,
            badges=("ЛС", "группа"),
            commands=_games_role.syntax,
            triggers=_games_role.natural_triggers,
            steps=(
                "Перед первой партией откройте личный чат с ботом и нажмите Start.",
                "Во время игры используйте `/role`, если нужно повторно посмотреть секретную роль или карточку.",
                "Часть фаз `Мафии`, `Шпиона`, `Бункера` и `Кто я` сопровождается личными сообщениями и приватными кнопками.",
            ),
            notes=_games_role.notes,
        ),
        _docs_item(
            "Как играть в «Кто я»",
            text=(
                "После старта каждый получает карточку в приват. Активный игрок задаёт в группу вопрос с ответом «да / нет / не знаю», "
                "после чего остальные отвечают кнопками."
            ),
            badges=("группа", "ЛС"),
            commands=("/game whoami",),
            examples=("Я думаю, что я Шерлок Холмс", "Моя догадка: Бэтмен", "Кажется, я Ведьмак"),
            notes=(
                "Для догадки используйте естественную фразу вроде `Я думаю, что я ...`.",
                "Тема `18+ и пикантное` видна и доступна только в чатах, где включён `actions_18_enabled`.",
            ),
        ),
        _docs_item(
            "Как участвовать в остальных режимах",
            text=(
                "`Шпион`, `Мафия` и `Бункер` используют роли, приватные подсказки и голосования. "
                "`Кости`, `Викторина` и `Бредовуха` активнее работают через кнопки и публичные ответы."
            ),
            badges=("группа", "кнопки"),
            commands=("/game spy", "/game mafia", "/game dice", "/game quiz", "/game bredovukha", "/game bunker"),
            notes=(
                "В `Бункере` часть раундов идёт через приватное раскрытие характеристик и приватное голосование.",
                "В `Мафии` и `Шпионе` ключевые действия и роль приходят в личку.",
            ),
        ),
    ),
    _docs_section(
        "user-docs-economy",
        "Экономика и предметы",
        "Все пользовательские сценарии экономики: баланс, фарм, рынок, переводы, рост, лотерея и предметы.",
        _docs_item(
            (_eco_panel := get_command_spec("economy_panel")).title_ru,
            text=_eco_panel.description_ru,
            badges=("группа", "ЛС"),
            commands=_eco_panel.syntax,
            triggers=_eco_panel.natural_triggers,
            notes=_eco_panel.notes,
            requires_settings=("economy_enabled",),
        ),
        _docs_item(
            (_eco_farm := get_command_spec("economy_farm")).title_ru,
            text=_eco_farm.description_ru,
            badges=("группа", "ЛС"),
            commands=_eco_farm.syntax,
            triggers=_eco_farm.natural_triggers,
            examples=_eco_farm.examples,
            requires_settings=("economy_enabled",),
        ),
        _docs_item(
            (_eco_shop := get_command_spec("economy_shop_inventory_craft")).title_ru,
            text=_eco_shop.description_ru,
            badges=("группа", "ЛС"),
            commands=_eco_shop.syntax,
            triggers=_eco_shop.natural_triggers,
            notes=_eco_shop.notes,
            requires_settings=("economy_enabled", "craft_enabled"),
        ),
        _docs_item(
            (_eco_market := get_command_spec("economy_market_transfer_auction")).title_ru,
            text=_eco_market.description_ru,
            badges=("группа",),
            commands=_eco_market.syntax,
            triggers=_eco_market.natural_triggers,
            examples=_eco_market.examples,
            notes=_eco_market.notes,
            requires_settings=("economy_enabled", "auctions_enabled"),
        ),
        _docs_item(
            (_eco_growth := get_command_spec("economy_growth")).title_ru,
            text=_eco_growth.description_ru,
            badges=("группа", "ЛС"),
            commands=_eco_growth.syntax,
            triggers=_eco_growth.natural_triggers,
            notes=_eco_growth.notes,
            requires_settings=("economy_enabled", "actions_18_enabled"),
        ),
    ),
    _docs_section(
        "user-docs-relationships",
        "Пары и брак",
        "Как предложить отношения, какие действия доступны на каждой стадии и чем отличается пара от брака.",
        _docs_item(
            (_rel_start := get_command_spec("relationships_start")).title_ru,
            text=_rel_start.description_ru,
            badges=("группа", "reply"),
            commands=_rel_start.syntax,
            triggers=_rel_start.natural_triggers,
            examples=_rel_start.examples,
            notes=_rel_start.notes,
        ),
        _docs_item(
            (_rel_pair := get_command_spec("relationships_pair_actions")).title_ru,
            text=_rel_pair.description_ru,
            badges=("группа",),
            commands=_rel_pair.syntax,
            triggers=_rel_pair.natural_triggers,
            notes=_rel_pair.notes,
        ),
        _docs_item(
            (_rel_marriage := get_command_spec("relationships_marriage_actions")).title_ru,
            text=_rel_marriage.description_ru,
            badges=("группа",),
            commands=_rel_marriage.syntax,
            triggers=_rel_marriage.natural_triggers,
            notes=_rel_marriage.notes,
        ),
        _docs_item(
            (_rel_end := get_command_spec("relationships_end")).title_ru,
            text=_rel_end.description_ru,
            badges=("группа",),
            commands=_rel_end.syntax,
            triggers=_rel_end.natural_triggers,
        ),
    ),
    _docs_section(
        "user-docs-family",
        "Семья, титулы и имя",
        "Нейминг в конкретном чате, покупка титула и команды семейного графа.",
        _docs_item(
            "Нейминг",
            text=(
                "Нейминг задаёт локальное имя, которым бот будет называть вас именно в этом чате. "
                "Это влияет и на игры, и на упоминания в некоторых механиках."
            ),
            badges=("группа",),
            commands=("/naming <имя>",),
            triggers=("нейминг",),
            examples=("/naming Лорд Теста", "нейминг Котик", "нейминг сброс"),
            notes=(
                "Имя должно быть длиной от 2 до 32 символов.",
                "Сбросить нейминг можно словами `сброс`, `reset`, `off` и похожими командами.",
            ),
        ),
        _docs_item(
            "Титулы",
            text=(
                "Титул показывает префикс рядом с вашим именем в пределах чата. Первая установка платная, дальше титул можно менять или снять."
            ),
            badges=("группа",),
            commands=("/title", "/title buy <текст>", "/title set <текст>", "/title clear"),
            triggers=("титул",),
            requires_settings=("titles_enabled", "economy_enabled"),
        ),
        _docs_item(
            (_family_tree := get_command_spec("family_adopt_pet_tree")).title_ru,
            text=_family_tree.description_ru,
            badges=("группа", "reply"),
            commands=_family_tree.syntax,
            triggers=_family_tree.natural_triggers,
            examples=_family_tree.examples,
            notes=_family_tree.notes,
            requires_settings=("family_tree_enabled",),
        ),
        _docs_item(
            (_family_escape := get_command_spec("family_escape")).title_ru,
            text=_family_escape.description_ru,
            badges=("группа",),
            commands=_family_escape.syntax,
            triggers=_family_escape.natural_triggers,
            notes=_family_escape.notes,
            requires_settings=("family_tree_enabled",),
        ),
    ),
    _docs_section(
        "user-docs-social",
        "Социальные действия и мемы",
        "Карма, reply-действия, 18+ фразы, объявления и маленькие рандомные команды для чата.",
        _docs_item(
            "Карма",
            text="Самый быстрый социальный сигнал: ответьте на сообщение знаком `+` или `-`, чтобы отдать голос кармы.",
            badges=("reply", "группа"),
            triggers=("+", "-"),
            notes=(
                "Карма учитывается в рейтингах и в `/rep`.",
                "Лимиты и точная механика зависят от настроек чата.",
            ),
        ),
        _docs_item(
            "Reply-действия без 18+",
            text=(
                "Эти действия запускаются обычной фразой в ответ на сообщение участника. "
                "Бот генерирует готовую RP-реплику и упоминания."
            ),
            badges=("reply", "группа"),
            triggers=_SOCIAL_TRIGGERS_NON_18,
            examples=("reply + обнять", "reply + оттолкнуть", "reply + наругать"),
        ),
        _docs_item(
            "18+ reply-действия",
            text="Пикантные действия работают тем же способом, но отдельно завязаны на 18+ настройку.",
            badges=("reply", "группа"),
            triggers=_SOCIAL_TRIGGERS_18_PLUS,
            examples=("reply + трахнуть", "reply + поиметь", "reply + отсосать"),
            notes=(
                "Если 18+ выключен, бот покажет прямой отказ и подскажет, какая настройка отвечает за доступ.",
            ),
            requires_settings=("actions_18_enabled",),
        ),
        _docs_item(
            "Объявления, подписка и чатовые мемы",
            text=(
                "Кроме строгих команд есть набор бытовых фраз: объявление с тегами, шипперинг, рандом «кто сегодня ...», статья дня и жмых для фото."
            ),
            badges=("группа", "текстовые фразы"),
            commands=(_daily_article := get_command_spec("misc_daily_article")).syntax,
            triggers=(
                "объява",
                "рег",
                "анрег",
                "шипперим",
                "кто сегодня легенда",
                *_daily_article.natural_triggers,
                "жмых",
            ),
            examples=(
                'объява "Собираемся через 10 минут"',
                "рег",
                "анрег",
                "кто сегодня легенда",
                "жмых 4",
            ),
            notes=(
                "`объява` доступна только тем ролям, которым в чате разрешена эта команда.",
                "`рег` и `анрег` включают или выключают ваш пинг в объявлениях.",
                "`жмых` используется как подпись к фото: чем выше уровень 1..6, тем сильнее искажение картинки.",
            ),
        ),
    ),
    _docs_section(
        "user-docs-text",
        "Текстовые аналоги и ограничения",
        "Какие фразы заменяют slash-команды и почему бот иногда не реагирует даже на правильный ввод.",
        _docs_item(
            "Основные текстовые триггеры",
            text=(
                "Если в чате включены текстовые команды и русский locale, многие сценарии можно вызывать без `/`."
            ),
            badges=("группа", "текстовые команды"),
            triggers=(
                "кто я",
                "помощь",
                "бот",
                "актив 10",
                "топ неделя 15",
                "баланс",
                "магазин",
                "рынок",
                "отношения",
                "семья",
                "нейминг Имя",
            ),
            notes=(
                "Администраторы могут включить кастомные алиасы или полностью отключить стандартные текстовые триггеры.",
                "Отдельно от общего текстового роутинга обрабатывается только нейминг; остальные текстовые фразы и их slash-аналоги зависят от того, пропускает ли чат этот маршрут.",
            ),
        ),
        _docs_item(
            "Почему команда не сработала",
            text=(
                "В большинстве случаев проблема не в синтаксисе, а в контексте: не тот чат, нет reply, функция выключена настройками или для команды нужен больший ранг."
            ),
            badges=("проверка",),
            steps=(
                "Проверьте, где должна работать команда: в группе, в личке или только по reply.",
                "Убедитесь, что нужная механика включена в чате: экономика, титулы, семейное древо, 18+ режим, текстовые команды.",
                "Если это игра со скрытой ролью, откройте ЛС с ботом и нажмите Start.",
                "Если команда ранговая, бот обычно прямо пишет ваш текущий ранг и какой требуется.",
            ),
            notes=(
                "Для модерации, настройки, алиасов, смарт-триггеров и кастомных RP-действий есть отдельная админская документация `/app/docs/admin`.",
            ),
        ),
    ),
)


def build_user_docs_context(*, chat: UserChatOverview | None) -> dict[str, Any]:
    return {
        "page_title": "Selara • Документация пользователя",
        "page_name": "user-docs",
        "hero_title": "Документация пользователя",
        "hero_subtitle": (
            "Полная памятка по пользовательским возможностям бота: команды, текстовые триггеры, "
            "reply-действия, экономика, игры, отношения, семьи, титулы и типовые ограничения."
        ),
        "hero_chips": ("команды", "reply-действия", "игры + экономика"),
        "docs_sections": [dict(section) for section in _USER_DOC_SECTIONS],
        "origin_chat": (
            {
                "href": f"/app/chat/{chat.chat_id}",
                "label": chat.chat_title or f"chat:{chat.chat_id}",
            }
            if chat is not None
            else {
                "href": "/app",
                "label": "кабинет",
            }
        ),
    }
