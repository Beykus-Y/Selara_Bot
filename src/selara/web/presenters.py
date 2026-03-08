from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from selara.application.dto import RepStats
from selara.application.use_cases.economy.growth import effective_growth_stress_pct
from selara.application.use_cases.economy.results import EconomyDashboard
from selara.core.chat_settings import CHAT_SETTINGS_KEYS, ChatSettings
from selara.core.trigger_templates import build_trigger_template_variable_rows
from selara.domain.entities import (
    ActivityStats,
    ChatAuditLogEntry,
    ChatActivitySummary,
    ChatCommandAccessRule,
    ChatRoleDefinition,
    ChatTextAlias,
    ChatTrigger,
    LeaderboardItem,
    UserChatOverview,
    UserSnapshot,
)
from selara.domain.value_objects import display_name_from_parts
from selara.presentation.handlers.settings_common import (
    CFG_BOOL_KEYS,
    CFG_ENUM_VALUES,
    CFG_TEXTAREA_KEYS,
    SETTINGS_GROUPS,
    setting_description_ru,
    setting_short_ru,
    setting_title_ru,
    setting_value_hint_ru,
    settings_to_dict,
)
from selara.presentation.commands.catalog import SOCIAL_COMMAND_KEY_TO_ACTION
from selara.presentation.game_state import GAME_DEFINITIONS, GAME_LAUNCHABLE_KINDS
from selara.web.admin_docs import setting_anchor, trigger_match_type_label_ru

_UTC = timezone.utc
_PERMISSION_LABELS_RU: dict[str, str] = {
    "manage_roles": "управление ролями",
    "manage_settings": "управление настройками",
    "manage_games": "управление играми",
    "moderate_users": "модерация пользователей",
    "announce": "объявления",
    "manage_command_access": "доступ к командам",
    "manage_role_templates": "шаблоны и кастомные роли",
}
_ECONOMY_MODE_LABELS_RU: dict[str, str] = {
    "global": "общая",
    "local": "по группе",
}
_TRIGGER_TEMPLATE_QUICK_NAMES: tuple[str, ...] = (
    "{user}",
    "{user_name}",
    "{reply_user}",
    "{reply_text}",
    "{chat}",
    "{text}",
    "{args}",
    "{date}",
    "{time}",
)


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "нет"
    return value.astimezone(_UTC).strftime("%d.%m.%Y %H:%M UTC")


def permission_label_ru(permission: str) -> str:
    return _PERMISSION_LABELS_RU.get(permission, permission.replace("_", " "))


def permissions_text_ru(permissions: tuple[str, ...] | list[str]) -> str:
    if not permissions:
        return "нет прав"
    return ", ".join(permission_label_ru(permission) for permission in permissions)


def economy_mode_label_ru(value: str) -> str:
    return _ECONOMY_MODE_LABELS_RU.get(value, value)


def user_label(value: UserSnapshot | ActivityStats | LeaderboardItem) -> str:
    user_id = getattr(value, "telegram_user_id", None)
    if user_id is None:
        user_id = getattr(value, "user_id")
    return display_name_from_parts(
        user_id=int(user_id),
        username=getattr(value, "username", None),
        first_name=getattr(value, "first_name", None),
        last_name=getattr(value, "last_name", None),
        chat_display_name=getattr(value, "chat_display_name", None),
    )


def build_metric(*, label: str, value: str, note: str, tone: str = "violet") -> dict[str, str]:
    return {
        "label": label,
        "value": value,
        "note": note,
        "tone": tone,
    }


def build_group_link(group: UserChatOverview, *, is_admin: bool) -> dict[str, str]:
    meta_parts = [f"ID {group.chat_id}", f"роль: {group.bot_role or 'participant'}"]
    if group.message_count is not None:
        meta_parts.append(f"сообщений: {group.message_count}")
    if group.last_seen_at is not None:
        meta_parts.append(f"активность: {format_datetime(group.last_seen_at)}")
    return {
        "href": f"/app/chat/{group.chat_id}",
        "title": group.chat_title or f"chat:{group.chat_id}",
        "meta": " • ".join(meta_parts),
        "badge": "admin" if is_admin else "user",
    }


def build_dashboard_panel(*, title: str, dashboard: EconomyDashboard | None, empty_text: str) -> dict[str, Any]:
    if dashboard is None:
        return {
            "title": title,
            "empty_text": empty_text,
            "rows": [],
        }

    effective_stress = effective_growth_stress_pct(
        last_growth_at=dashboard.account.last_growth_at,
        stress_pct=dashboard.account.growth_stress_pct,
        as_of=datetime.now(timezone.utc),
    )

    return {
        "title": title,
        "empty_text": None,
        "rows": [
            {
                "title": "Баланс",
                "meta": f"scope: {dashboard.scope.scope_id}",
                "value": str(dashboard.account.balance),
            },
            {
                "title": "Ферма",
                "meta": f"уровень {dashboard.farm.farm_level}, участков {len(dashboard.plots)}",
                "value": dashboard.farm.size_tier,
            },
            {
                "title": "Инвентарь",
                "meta": f"уникальных предметов {len(dashboard.inventory)}",
                "value": str(sum(item.quantity for item in dashboard.inventory)),
            },
            {
                "title": "Рост",
                "meta": f"действий {dashboard.account.growth_actions}, stress {effective_stress}%",
                "value": f"{dashboard.account.growth_size_mm} мм",
            },
        ],
    }


def build_leaderboard_section(
    *,
    title: str,
    subtitle: str,
    rows: list[dict[str, str]],
    accent: str,
) -> dict[str, Any]:
    return {
        "title": title,
        "subtitle": subtitle,
        "rows": rows,
        "accent": accent,
    }


def build_activity_rows(items: list[ActivityStats]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, item in enumerate(items, start=1):
        rows.append(
            {
                "position": f"{index:02d}",
                "name": user_label(item),
                "primary": f"{item.message_count} сообщений",
                "secondary": f"Последняя активность: {format_datetime(item.last_seen_at)}",
            }
        )
    return rows


def build_leaderboard_rows(items: list[LeaderboardItem], *, kind: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, item in enumerate(items, start=1):
        if kind == "mix":
            primary = f"Рейтинг {item.hybrid_score:.3f}"
            secondary = f"Сообщений: {item.activity_value} • Карма: {item.karma_value}"
        elif kind == "karma":
            primary = f"Карма {item.karma_value}"
            secondary = f"Сообщений: {item.activity_value} • Активность: {format_datetime(item.last_seen_at)}"
        else:
            primary = f"{item.activity_value} сообщений"
            secondary = f"Карма: {item.karma_value} • Активность: {format_datetime(item.last_seen_at)}"
        rows.append(
            {
                "position": f"{index:02d}",
                "name": user_label(item),
                "primary": primary,
                "secondary": secondary,
            }
        )
    return rows


def build_roles(roles: list[ChatRoleDefinition]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for role in roles:
        items.append(
            {
                "title": role.title_ru,
                "code": role.role_code,
                "rank": str(role.rank),
                "meta": f"код: {role.role_code} • ранг: {role.rank}",
                "permissions": permissions_text_ru(list(role.permissions)),
            }
        )
    return items


def build_command_rules(rules: list[ChatCommandAccessRule], role_titles: dict[str, str]) -> list[dict[str, str]]:
    return [
        {
            "command": f"/{rule.command_key}",
            "role": role_titles.get(rule.min_role_code, rule.min_role_code),
        }
        for rule in rules
    ]


def build_trigger_rows(triggers: list[ChatTrigger]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for trigger in triggers:
        preview = trigger.response_text or trigger.media_type or "ответ"
        rows.append(
            {
                "id": str(trigger.id),
                "keyword": trigger.keyword,
                "match_type": trigger.match_type,
                "match_type_label": trigger_match_type_label_ru(trigger.match_type),
                "preview": preview[:80],
                "response_text": trigger.response_text or "",
                "media_file_id": trigger.media_file_id or "",
                "media_type": trigger.media_type or "",
            }
        )
    return rows


def build_trigger_template_quick_rows() -> list[dict[str, str]]:
    rows_by_token = {item["token"]: item for item in build_trigger_template_variable_rows()}
    return [rows_by_token[token] for token in _TRIGGER_TEMPLATE_QUICK_NAMES if token in rows_by_token]


def build_alias_rows(aliases: list[ChatTextAlias]) -> list[dict[str, str]]:
    return [
        {
            "id": str(alias.id),
            "alias": alias.alias_text_norm,
            "command": alias.command_key,
            "source": alias.source_trigger_norm,
        }
        for alias in aliases
    ]


def build_audit_rows(entries: list[ChatAuditLogEntry]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for entry in entries:
        rows.append(
            {
                "when": format_datetime(entry.created_at),
                "action": entry.action_code,
                "description": entry.description,
                "actor": str(entry.actor_user_id) if entry.actor_user_id is not None else "system",
                "target": str(entry.target_user_id) if entry.target_user_id is not None else "—",
            }
        )
    return rows


def build_settings_sections(
    *,
    current: ChatSettings,
    defaults: ChatSettings,
    editable: bool,
) -> list[dict[str, Any]]:
    current_map = settings_to_dict(current)
    default_map = settings_to_dict(defaults)
    grouped_keys: set[str] = set()
    sections: list[dict[str, Any]] = []

    def _build_item(key: str) -> dict[str, Any]:
        current_value = current_map[key]
        default_value = default_map[key]
        if key in CFG_BOOL_KEYS:
            input_kind = "select"
            options = [
                {"value": "true", "label": "true", "selected": str(current_value).lower() == "true"},
                {"value": "false", "label": "false", "selected": str(current_value).lower() == "false"},
            ]
        elif key in CFG_ENUM_VALUES:
            input_kind = "select"
            options = [
                {"value": item, "label": item, "selected": str(current_value) == item}
                for item in CFG_ENUM_VALUES[key]
            ]
        elif key in CFG_TEXTAREA_KEYS:
            input_kind = "textarea"
            options = []
        else:
            input_kind = "text"
            options = []

        return {
            "key": key,
            "title": setting_short_ru(key),
            "description": setting_description_ru(key),
            "hint": setting_value_hint_ru(key),
            "current_value": str(current_value),
            "default_value": str(default_value),
            "editable": editable,
            "input_kind": input_kind,
            "options": options,
            "doc_anchor": setting_anchor(key),
        }

    for section_title, keys in SETTINGS_GROUPS:
        items = []
        for key in keys:
            if key not in current_map:
                continue
            grouped_keys.add(key)
            items.append(_build_item(key))
        sections.append({"title": section_title, "items": items})

    remaining = [key for key in CHAT_SETTINGS_KEYS if key not in grouped_keys]
    if remaining:
        sections.append({"title": "Прочее", "items": [_build_item(key) for key in remaining]})

    return sections


def build_home_context(
    *,
    user: UserSnapshot,
    admin_groups: list[UserChatOverview],
    activity_groups: list[UserChatOverview],
    global_dashboard: EconomyDashboard | None,
    flash: str | None,
    error: str | None,
) -> dict[str, Any]:
    admin_ids = {group.chat_id for group in admin_groups}
    return {
        "page_title": "Selara Panel",
        "page_name": "home",
        "flash": flash,
        "error": error,
        "user_name": user_label(user),
        "hero_title": user_label(user),
        "hero_subtitle": (
            "Веб-панель работает параллельно с ботом: здесь собраны ваши группы, права, "
            "экономика и быстрый доступ к админским разделам."
        ),
        "metrics": [
            build_metric(label="Админ-группы", value=str(len(admin_groups)), note="где у вас есть права управления ботом"),
            build_metric(label="Активные группы", value=str(len(activity_groups)), note="группы из вашей активности и ролей", tone="cyan"),
            build_metric(
                label="Global balance",
                value=str(global_dashboard.account.balance if global_dashboard else 0),
                note="общий баланс экономики",
                tone="magenta",
            ),
            build_metric(
                label="Инвентарь",
                value=str(sum(item.quantity for item in global_dashboard.inventory) if global_dashboard else 0),
                note="сумма предметов в global-аккаунте",
                tone="indigo",
            ),
        ],
        "admin_groups": [build_group_link(group, is_admin=True) for group in admin_groups[:8]],
        "activity_groups": [build_group_link(group, is_admin=group.chat_id in admin_ids) for group in activity_groups[:12]],
        "global_dashboard": build_dashboard_panel(
            title="Global экономика",
            dashboard=global_dashboard,
            empty_text="Экономический аккаунт ещё не создан.",
        ),
        "security_items": [
            {
                "title": "Только код из Telegram",
                "text": "Бот выдаёт код по /login в личке. Код одноразовый и живёт ограниченное время.",
            },
            {
                "title": "Сессия браузера",
                "text": "После входа создаётся отдельная cookie-сессия, которую можно завершить кнопкой «Выйти».",
            },
            {
                "title": "Доступ к группам",
                "text": "Страница группы откроется только если вы реально есть в её активности или имеете роль бота.",
            },
        ],
    }


def build_landing_context(
    *,
    bot_username: str,
    bot_dm_url: str,
    user: UserSnapshot | None,
    flash: str | None,
    error: str | None,
) -> dict[str, Any]:
    launchable_game_titles = tuple(GAME_DEFINITIONS[kind].title for kind in GAME_LAUNCHABLE_KINDS)
    session_active = user is not None
    session_label = None if user is None else user_label(user)

    hero_ctas = (
        [
            {"href": "/app", "label": "Открыть кабинет", "variant": "primary"},
            {"href": "/app/docs/user", "label": "Справка пользователя", "variant": "ghost"},
            {"href": bot_dm_url, "label": f"Telegram @{bot_username}", "variant": "ghost"},
        ]
        if session_active
        else [
            {"href": "/login", "label": "Войти через Telegram", "variant": "primary"},
            {"href": bot_dm_url, "label": f"Открыть @{bot_username}", "variant": "ghost"},
            {"href": "/app/docs/user", "label": "Что умеет бот", "variant": "ghost"},
        ]
    )

    return {
        "page_title": "Selara • Лендинг",
        "page_name": "landing",
        "flash": flash,
        "error": error,
        "home_href": "/",
        "brand_subtitle": "бот для Telegram-групп",
        "hero_eyebrow": "Платформа для Telegram-сообществ",
        "hero_title_primary": "Selara",
        "hero_title_secondary": "бот для групп, игр и экономики",
        "hero_subtitle": (
            "Один бот закрывает повседневную жизнь чата: статистику, игры, экономику, отношения, "
            "семьи, reply-действия, роли, настройки, веб-панель и документацию без отдельной регистрации."
        ),
        "hero_ctas": hero_ctas,
        "session_note": (f"Сессия активна для {session_label}" if session_label is not None else None),
        "developer_credit": "Разработчик: Beykus",
        "signal_cards": [
            {
                "label": "командный слой",
                "value": "slash + text + reply",
                "note": "slash-команды, текстовые триггеры и reply-сценарии живут в одном маршруте.",
                "tone": "cyan",
            },
            {
                "label": "игры",
                "value": f"{len(GAME_LAUNCHABLE_KINDS)} live-режимов",
                "note": "От лобби и скрытых ролей до веб-управления активными партиями.",
                "tone": "violet",
            },
            {
                "label": "социальное",
                "value": f"{len(SOCIAL_COMMAND_KEY_TO_ACTION)} reply-действие",
                "note": "От дружелюбных реакций до 18+ сценариев с отдельным gate по настройке.",
                "tone": "magenta",
            },
            {
                "label": "панель",
                "value": "вход и кабинет",
                "note": "Одноразовый код из Telegram, затем кабинет, документация, игры и аудит.",
                "tone": "indigo",
            },
        ],
        "metrics": [
            build_metric(
                label="Игровые режимы",
                value=str(len(GAME_DEFINITIONS)),
                note="в коде доступны party-сценарии, скрытые роли и быстрые дуэли",
            ),
            build_metric(
                label="Командная модель",
                value="текст и slash",
                note="одни и те же сценарии доступны через slash-команды и обычные текстовые фразы",
                tone="cyan",
            ),
            build_metric(
                label="Экономика",
                value="global / local",
                note="баланс, ферма, магазин, инвентарь, рынок, переводы, лотерея и рост",
                tone="magenta",
            ),
            build_metric(
                label="Роли и веб",
                value="вход через Telegram",
                note="вход только по коду из бота, без ручной регистрации на сайте",
                tone="indigo",
            ),
        ],
        "overview_text": (
            "Selara полезна и обычным участникам, и владельцам чатов. Пользователь получает игры, "
            "статистику, экономику, отношения, семьи и быстрые reply-действия. Администратор получает "
            "роли, ранги, алиасы, триггеры, настройки, аудит и удобный браузерный интерфейс."
        ),
        "overview_pills": (
            "статистика",
            "игры",
            "экономика",
            "отношения",
            "семьи",
            "социальные действия",
            "алиасы",
            "настройки",
            "журнал изменений",
        ),
        "step_cards": [
            {
                "step": "01",
                "title": "Откройте бота в Telegram",
                "text": f"Личный чат с @{bot_username} нужен для /start, /login, скрытых ролей и быстрых переходов по ссылкам.",
            },
            {
                "step": "02",
                "title": "Получите код входа",
                "text": "Команда /login выдаёт одноразовый шестизначный код. Пароли и ручная регистрация не нужны.",
            },
            {
                "step": "03",
                "title": "Перейдите в /app",
                "text": "После авторизации открывается кабинет с группами, активными играми, экономикой и ссылками на документацию.",
            },
            {
                "step": "04",
                "title": "Используйте Selara в группе",
                "text": "Команды /help, /game, /eco, /pair, /family и текстовые триггеры закрывают большую часть сценариев прямо в чате.",
            },
        ],
        "feature_cards": [
            {
                "kicker": "пользовательский слой",
                "title": "Команды для обычного участника",
                "text": "Статистика, карма, лидерборды, последняя активность, пользовательская справка и текстовые аналоги без лишней рутины.",
                "items": ("/help", "/me", "/rep", "/top", "/active", "/lastseen"),
                "href": "/app/docs/user",
                "link_label": "Открыть документацию",
            },
            {
                "kicker": "игровой слой",
                "title": "Игры и лобби",
                "text": "Selara запускает быстрые игровые режимы в группе и умеет вести их через Telegram и веб-панель.",
                "items": launchable_game_titles,
                "href": "/app/games",
                "link_label": "Открыть игры",
            },
            {
                "kicker": "экономика",
                "title": "Экономика чата",
                "text": "Два режима экономики, предметы, ферма, рынок, переводы, лотерея, рост и аукционы.",
                "items": ("/eco", "/farm", "/shop", "/inventory", "/market", "/pay", "/lottery", "/growth"),
                "href": "/app",
                "link_label": "К кабинетам",
            },
            {
                "kicker": "социальный граф",
                "title": "Отношения, семьи и титулы",
                "text": "Пары, браки, семейное древо, питомцы, нейминг и локальные титулы прямо в рамках конкретного чата.",
                "items": ("/relation", "/pair", "/marry", "/family", "/title", "/naming"),
                "href": "/app/docs/user",
                "link_label": "Смотреть сценарии",
            },
            {
                "kicker": "reply-действия",
                "title": "Социальные реакции",
                "text": "Reply-действия работают как быстрый RP-слой: дружелюбные жесты, мемные фразы и 18+ сценарии с отдельной настройкой.",
                "items": ("обнять", "поцеловать", "дать пять", "подмигнуть", "угостить", "соблазнить", "засосать"),
                "href": "/app/docs/user",
                "link_label": "Все действия",
            },
            {
                "kicker": "управление",
                "title": "Управление группой",
                "text": "Роли, права доступа, настройки, кастомные алиасы, смарт-триггеры, командные ранги и аудит изменений.",
                "items": ("/roles", "/settings", "/setcfg", "/setalias", "/settrigger", "/app/docs/admin"),
                "href": "/app/docs/admin",
                "link_label": "Открыть админ-документацию",
            },
        ],
        "route_cards": [
            {
                "title": "Вход",
                "href": "/login",
                "display_href": "/login",
                "description": "Авторизация по одноразовому коду из Telegram. Подходит для пользователей и админов.",
                "note": "без пароля",
            },
            {
                "title": "Кабинет",
                "href": "/app",
                "display_href": "/app",
                "description": "Группы, экономические панели, быстрые переходы и персональная веб-панель.",
                "note": "после авторизации",
            },
            {
                "title": "Документация пользователя",
                "href": "/app/docs/user",
                "display_href": "/app/docs/user",
                "description": "Полная пользовательская справка по командам, играм, отношениям, семьям и reply-действиям.",
                "note": "команды и сценарии",
            },
            {
                "title": "Игровой центр",
                "href": "/app/games",
                "display_href": "/app/games",
                "description": "Создание новых лобби, управление партиями и просмотр активных игр из браузера.",
                "note": "живые игры",
            },
            {
                "title": "Админ-документация",
                "href": "/app/docs/admin",
                "display_href": "/app/docs/admin",
                "description": "Настройки, роли, доступы, триггеры, алиасы и прочий управленческий слой Selara.",
                "note": "для владельцев и модерации",
            },
            {
                "title": "Telegram бот",
                "href": bot_dm_url,
                "display_href": f"@{bot_username}",
                "description": "Личный чат с ботом для /start, /login, ролей, deep-link и приватных игровых сообщений.",
                "note": "основная точка входа",
            },
        ],
    }


def build_chat_context(
    *,
    user: UserSnapshot,
    chat: UserChatOverview,
    summary: ChatActivitySummary,
    stats: ActivityStats | None,
    rep_stats: RepStats,
    role_definition: ChatRoleDefinition,
    current_settings: ChatSettings,
    defaults: ChatSettings,
    can_manage_settings: bool,
    roles: list[ChatRoleDefinition],
    command_rules: list[ChatCommandAccessRule],
    aliases: list[ChatTextAlias],
    triggers: list[ChatTrigger],
    audit_entries: list[ChatAuditLogEntry],
    global_dashboard: EconomyDashboard | None,
    local_dashboard: EconomyDashboard | None,
    top_activity: list[ActivityStats],
    top_mix: list[LeaderboardItem],
    top_karma: list[LeaderboardItem],
    top_mix_7d: list[LeaderboardItem],
    flash: str | None,
    error: str | None,
) -> dict[str, Any]:
    role_titles = {role.role_code: role.title_ru for role in roles}
    metrics = [
        build_metric(
            label="Участники",
            value=str(summary.participants_count),
            note=f"последняя активность: {format_datetime(summary.last_activity_at)}",
        ),
        build_metric(
            label="Сообщения",
            value=str(summary.total_messages),
            note=f"ваших сообщений: {stats.message_count if stats is not None else chat.message_count or 0}",
            tone="cyan",
        ),
        build_metric(
            label="Карма: всё время / 7 дней",
            value=f"{rep_stats.karma_all} / {rep_stats.karma_7d}",
            note=f"активность за 7 дней: {rep_stats.activity_7d}",
            tone="magenta",
        ),
        build_metric(
            label="Мой ранг",
            value=str(rep_stats.rank_all if rep_stats.rank_all is not None else "-"),
            note=f"ранг за 7 дней: {rep_stats.rank_7d if rep_stats.rank_7d is not None else '-'}",
            tone="indigo",
        ),
    ]

    return {
        "page_title": f"Selara • {chat.chat_title or chat.chat_id}",
        "page_name": "chat",
        "flash": flash,
        "error": error,
        "chat_title": chat.chat_title or f"chat:{chat.chat_id}",
        "chat_id": chat.chat_id,
        "hero_subtitle": (
            f"Ваша роль: {role_definition.title_ru}. Здесь собраны общая статистика группы, "
            "рейтинги участников, экономика и настройки."
        ),
        "metrics": metrics,
        "access_rows": [
            {
                "title": "Пользователь",
                "meta": f"Telegram ID {user.telegram_user_id}",
                "value": user_label(user),
            },
            {
                "title": "Роль в боте",
                "meta": permissions_text_ru(list(role_definition.permissions)),
                "value": role_definition.title_ru,
            },
            {
                "title": "Экономика группы",
                "meta": f"режим: {economy_mode_label_ru(current_settings.economy_mode)}",
                "value": "включена" if current_settings.economy_enabled else "выключена",
            },
        ],
        "dashboard_panels": [
            build_dashboard_panel(
                title="Локальная экономика",
                dashboard=local_dashboard,
                empty_text="Локальный аккаунт для этой группы ещё не создан.",
            ),
            build_dashboard_panel(
                title="Глобальная экономика",
                dashboard=global_dashboard,
                empty_text="Глобальный аккаунт ещё не создан.",
            ),
        ],
        "leaderboards": [
            build_leaderboard_section(
                title="Топ по сообщениям",
                subtitle="активность за всё время",
                rows=build_activity_rows(top_activity),
                accent="cyan",
            ),
            build_leaderboard_section(
                title="Гибридный рейтинг",
                subtitle="карма + активность за всё время",
                rows=build_leaderboard_rows(top_mix, kind="mix"),
                accent="violet",
            ),
            build_leaderboard_section(
                title="Гибридный рейтинг 7d",
                subtitle=f"окно {current_settings.leaderboard_7d_days} дней",
                rows=build_leaderboard_rows(top_mix_7d, kind="mix"),
                accent="indigo",
            ),
            build_leaderboard_section(
                title="Топ по карме",
                subtitle="карма за всё время",
                rows=build_leaderboard_rows(top_karma, kind="karma"),
                accent="magenta",
            ),
        ],
        "roles": build_roles(roles),
        "command_rules": build_command_rules(command_rules, role_titles),
        "aliases": build_alias_rows(aliases),
        "triggers": build_trigger_rows(triggers),
        "trigger_template_quick_rows": build_trigger_template_quick_rows(),
        "trigger_template_examples": [
            'Сейчас тут {user}, чат: {chat}, время: {time}',
            'Привет, {user_name}. Ты ответил(а) на: {reply_text}',
            '{actor} кусает {target}. Комментарий: {args}',
        ],
        "trigger_template_docs_url": f"/app/docs/admin?chat_id={chat.chat_id}#docs-trigger-variables",
        "audit_rows": build_audit_rows(audit_entries),
        "settings_sections": build_settings_sections(current=current_settings, defaults=defaults, editable=can_manage_settings),
        "admin_docs_url": f"/app/docs/admin?chat_id={chat.chat_id}",
        "can_manage_settings": can_manage_settings,
        "manage_settings_note": (
            "У вашей роли есть право на управление настройками, поэтому значения можно менять прямо из браузера."
            if can_manage_settings
            else "Настройки видны только для чтения: у вашей роли нет права на управление настройками."
        ),
        "manage_settings_tone": "ok" if can_manage_settings else "error",
    }
