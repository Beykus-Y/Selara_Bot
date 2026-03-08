from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from string import Formatter


@dataclass(frozen=True)
class TemplateVariableDoc:
    name: str
    label_ru: str
    description_ru: str
    group_ru: str
    availability_ru: str
    aliases: tuple[str, ...] = ()


_VARIABLE_DOCS: tuple[TemplateVariableDoc, ...] = (
    TemplateVariableDoc(
        name="user",
        aliases=("actor", "sender"),
        label_ru="Отправитель упоминанием",
        description_ru="HTML-упоминание автора текущего сообщения.",
        group_ru="Отправитель",
        availability_ru="смарт-триггеры и RP",
    ),
    TemplateVariableDoc(
        name="user_name",
        aliases=("actor_name", "sender_name"),
        label_ru="Имя отправителя",
        description_ru="Локальное имя в чате или display-name пользователя без HTML-ссылки.",
        group_ru="Отправитель",
        availability_ru="смарт-триггеры и RP",
    ),
    TemplateVariableDoc(
        name="user_first_name",
        aliases=("actor_first_name", "sender_first_name"),
        label_ru="Имя Telegram",
        description_ru="Поле first_name отправителя из Telegram.",
        group_ru="Отправитель",
        availability_ru="смарт-триггеры и RP",
    ),
    TemplateVariableDoc(
        name="user_last_name",
        aliases=("actor_last_name", "sender_last_name"),
        label_ru="Фамилия Telegram",
        description_ru="Поле last_name отправителя, если оно заполнено.",
        group_ru="Отправитель",
        availability_ru="смарт-триггеры и RP",
    ),
    TemplateVariableDoc(
        name="user_username",
        aliases=("actor_username", "sender_username"),
        label_ru="Username отправителя",
        description_ru="Username с префиксом @. Если username нет, подставляется пустая строка.",
        group_ru="Отправитель",
        availability_ru="смарт-триггеры и RP",
    ),
    TemplateVariableDoc(
        name="user_id",
        aliases=("actor_id", "sender_id"),
        label_ru="Telegram ID отправителя",
        description_ru="Числовой Telegram ID автора сообщения.",
        group_ru="Отправитель",
        availability_ru="смарт-триггеры и RP",
    ),
    TemplateVariableDoc(
        name="reply_user",
        aliases=("target",),
        label_ru="Цель упоминанием",
        description_ru="HTML-упоминание пользователя из reply-сообщения.",
        group_ru="Reply / цель",
        availability_ru="если сообщение отправлено reply",
    ),
    TemplateVariableDoc(
        name="reply_user_name",
        aliases=("target_name",),
        label_ru="Имя цели",
        description_ru="Локальное имя или display-name пользователя из reply.",
        group_ru="Reply / цель",
        availability_ru="если сообщение отправлено reply",
    ),
    TemplateVariableDoc(
        name="reply_user_first_name",
        aliases=("target_first_name",),
        label_ru="Имя цели в Telegram",
        description_ru="Поле first_name пользователя из reply.",
        group_ru="Reply / цель",
        availability_ru="если сообщение отправлено reply",
    ),
    TemplateVariableDoc(
        name="reply_user_last_name",
        aliases=("target_last_name",),
        label_ru="Фамилия цели в Telegram",
        description_ru="Поле last_name пользователя из reply.",
        group_ru="Reply / цель",
        availability_ru="если сообщение отправлено reply",
    ),
    TemplateVariableDoc(
        name="reply_user_username",
        aliases=("target_username",),
        label_ru="Username цели",
        description_ru="Username пользователя из reply с префиксом @.",
        group_ru="Reply / цель",
        availability_ru="если сообщение отправлено reply",
    ),
    TemplateVariableDoc(
        name="reply_user_id",
        aliases=("target_id",),
        label_ru="Telegram ID цели",
        description_ru="Telegram ID пользователя из reply.",
        group_ru="Reply / цель",
        availability_ru="если сообщение отправлено reply",
    ),
    TemplateVariableDoc(
        name="reply_text",
        aliases=("target_text",),
        label_ru="Текст сообщения-цели",
        description_ru="Текст или caption из reply-сообщения.",
        group_ru="Reply / цель",
        availability_ru="если сообщение отправлено reply",
    ),
    TemplateVariableDoc(
        name="reply_message_id",
        aliases=("target_message_id",),
        label_ru="ID reply-сообщения",
        description_ru="Telegram message_id сообщения, на которое сделан reply.",
        group_ru="Reply / цель",
        availability_ru="если сообщение отправлено reply",
    ),
    TemplateVariableDoc(
        name="chat",
        aliases=("chat_title",),
        label_ru="Название чата",
        description_ru="Title текущего чата или технический идентификатор, если title недоступен.",
        group_ru="Чат и сообщение",
        availability_ru="смарт-триггеры и RP",
    ),
    TemplateVariableDoc(
        name="chat_id",
        label_ru="ID чата",
        description_ru="Telegram chat_id текущего чата.",
        group_ru="Чат и сообщение",
        availability_ru="смарт-триггеры и RP",
    ),
    TemplateVariableDoc(
        name="text",
        aliases=("message_text",),
        label_ru="Текст сообщения",
        description_ru="Текст текущего сообщения, которое вызвало триггер.",
        group_ru="Чат и сообщение",
        availability_ru="смарт-триггеры и RP",
    ),
    TemplateVariableDoc(
        name="message_id",
        label_ru="ID текущего сообщения",
        description_ru="Telegram message_id сообщения, которое активировало шаблон.",
        group_ru="Чат и сообщение",
        availability_ru="смарт-триггеры и RP",
    ),
    TemplateVariableDoc(
        name="trigger",
        aliases=("keyword",),
        label_ru="Сработавший триггер",
        description_ru="Ключ триггера или текст кастомного RP-действия, который совпал.",
        group_ru="Триггер и аргументы",
        availability_ru="смарт-триггеры и RP",
    ),
    TemplateVariableDoc(
        name="match_type",
        label_ru="Тип совпадения",
        description_ru="exact / contains / starts_with. Для RP-действий будет пустым.",
        group_ru="Триггер и аргументы",
        availability_ru="только смарт-триггеры",
    ),
    TemplateVariableDoc(
        name="args",
        label_ru="Аргументы после ключа",
        description_ru="Хвост сообщения после ключевой фразы. Особенно полезно для starts_with и кастомных RP-действий.",
        group_ru="Триггер и аргументы",
        availability_ru="смарт-триггеры и RP",
    ),
    TemplateVariableDoc(
        name="date",
        label_ru="Текущая дата",
        description_ru="Дата рендера в формате DD.MM.YYYY по UTC.",
        group_ru="Дата и время",
        availability_ru="смарт-триггеры и RP",
    ),
    TemplateVariableDoc(
        name="time",
        label_ru="Текущее время",
        description_ru="Время рендера в формате HH:MM UTC.",
        group_ru="Дата и время",
        availability_ru="смарт-триггеры и RP",
    ),
    TemplateVariableDoc(
        name="datetime",
        label_ru="Текущая дата и время",
        description_ru="Дата и время рендера в формате DD.MM.YYYY HH:MM UTC.",
        group_ru="Дата и время",
        availability_ru="смарт-триггеры и RP",
    ),
    TemplateVariableDoc(
        name="weekday",
        label_ru="День недели",
        description_ru="День недели на русском: понедельник, вторник и т.д.",
        group_ru="Дата и время",
        availability_ru="смарт-триггеры и RP",
    ),
)

TRIGGER_TEMPLATE_VARIABLE_DOCS: tuple[TemplateVariableDoc, ...] = _VARIABLE_DOCS
TRIGGER_TEMPLATE_VARIABLE_NAMES: frozenset[str] = frozenset(
    alias
    for item in TRIGGER_TEMPLATE_VARIABLE_DOCS
    for alias in (item.name, *item.aliases)
)


class _SafeTemplateValues(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def validate_template_variables(template: str | None) -> None:
    if not template:
        return

    unknown: set[str] = set()
    formatter = Formatter()
    try:
        parsed_fields = list(formatter.parse(template))
    except ValueError as exc:
        raise ValueError(
            "Шаблон содержит некорректные фигурные скобки. Для текста используйте {{ и }}."
        ) from exc
    for _, field_name, format_spec, conversion in parsed_fields:
        if field_name is None:
            continue
        normalized_field = field_name.strip()
        if not normalized_field:
            raise ValueError("Пустая переменная {} не поддерживается. Используйте имена вида {user}.")
        if normalized_field != field_name or any(symbol in normalized_field for symbol in ".[]"):
            raise ValueError(
                f"Некорректная переменная {{{field_name}}}. Используйте только простые имена вроде {{user}}."
            )
        if format_spec or conversion:
            raise ValueError(
                f"Форматирование {{{field_name}}} не поддерживается. Используйте только переменные без : и !."
            )
        if normalized_field not in TRIGGER_TEMPLATE_VARIABLE_NAMES:
            unknown.add(normalized_field)

    if unknown:
        unknown_text = ", ".join(f"{{{name}}}" for name in sorted(unknown))
        raise ValueError(
            f"Неизвестные переменные шаблона: {unknown_text}. Используйте /triggervars или веб-справку."
        )


def render_template_variables(template: str | None, values: Mapping[str, str]) -> str:
    if not template:
        return ""
    try:
        return template.format_map(_SafeTemplateValues(values))
    except (AttributeError, IndexError, KeyError, ValueError):
        return template


def build_trigger_template_variable_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in TRIGGER_TEMPLATE_VARIABLE_DOCS:
        rows.append(
            {
                "name": item.name,
                "token": f"{{{item.name}}}",
                "aliases": ", ".join(f"{{{alias}}}" for alias in item.aliases) if item.aliases else "—",
                "label": item.label_ru,
                "description": item.description_ru,
                "group": item.group_ru,
                "availability": item.availability_ru,
            }
        )
    return rows


def build_trigger_template_variable_groups() -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in build_trigger_template_variable_rows():
        grouped[item["group"]].append(item)
    return [
        {
            "title": group,
            "items": items,
        }
        for group, items in grouped.items()
    ]
