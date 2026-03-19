import shlex
from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from selara.core.chat_settings import ChatSettings, default_chat_settings
from selara.core.config import Settings
from selara.domain.entities import ChatSnapshot
from selara.presentation.auth import has_permission
from selara.presentation.commands.access import resolve_command_key_input
from selara.presentation.commands.normalizer import normalize_text_command
from selara.presentation.interesting_facts import (
    INTERESTING_FACT_CATALOG,
    format_interesting_fact_message,
    select_next_interesting_fact,
)
from selara.presentation.handlers.settings_common import (
    apply_setting_update,
    render_settings,
    split_html_message,
    setting_title_ru,
    settings_to_dict,
)

router = Router(name="settings")


@router.message(Command("settings"))
async def settings_command(message: Message, settings: Settings, chat_settings: ChatSettings) -> None:
    defaults = default_chat_settings(settings)
    for chunk in split_html_message(render_settings(chat_settings, defaults)):
        await message.answer(chunk, parse_mode="HTML")


@router.message(Command("setcfg"))
async def setcfg_command(
    message: Message,
    command: CommandObject,
    activity_repo,
    settings: Settings,
    chat_settings: ChatSettings,
) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе")
        return

    if message.from_user is None:
        return

    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_settings",
        bootstrap_if_missing_owner=False,
    )
    if not allowed:
        await message.answer("Недостаточно прав для изменения настроек этой группы.")
        return

    raw_args = (command.args or "").strip()
    if not raw_args:
        await message.answer("Формат: /setcfg <key> <value>\nПодсказки по ключам: /settings")
        return

    parts = raw_args.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Формат: /setcfg <key> <value>\nПодсказки по ключам: /settings")
        return

    key, raw_value = parts[0], parts[1]
    defaults = default_chat_settings(settings)
    updated_values, error = apply_setting_update(
        key=key,
        raw_value=raw_value,
        current=settings_to_dict(chat_settings),
        defaults=settings_to_dict(defaults),
    )
    if error is not None or updated_values is None:
        await message.answer(error or "Не удалось применить значение")
        return

    chat = ChatSnapshot(
        telegram_chat_id=message.chat.id,
        chat_type=message.chat.type,
        title=message.chat.title,
    )
    updated = await activity_repo.upsert_chat_settings(chat=chat, values=updated_values)
    await message.answer(
        f"Обновлено: <b>{setting_title_ru(key)}</b>\n"
        f"Ключ: <code>{key}</code>\n"
        f"Новое значение: <code>{getattr(updated, key)}</code>",
        parse_mode="HTML",
    )


@router.message(Command("facttest"))
async def facttest_command(message: Message, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе")
        return

    if message.from_user is None:
        return

    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_settings",
        bootstrap_if_missing_owner=False,
    )
    if not allowed:
        await message.answer("Недостаточно прав для превью автофактов в этой группе.")
        return

    facts = INTERESTING_FACT_CATALOG.get_facts()
    if not facts:
        await message.answer("Каталог автофактов пуст или не удалось его загрузить.")
        return

    state = await activity_repo.get_chat_interesting_fact_state(chat_id=message.chat.id)
    fact, _ = select_next_interesting_fact(facts=facts, state=state)
    if fact is None:
        await message.answer("Не удалось подобрать факт для превью.")
        return

    await message.answer(
        format_interesting_fact_message(fact.text),
        disable_web_page_preview=True,
    )


@router.message(Command("setrank"))
async def setrank_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе")
        return
    if message.from_user is None:
        return

    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_command_access",
        bootstrap_if_missing_owner=False,
    )
    if not allowed:
        await message.answer("Недостаточно прав для настройки рангов команд.")
        return

    raw_args = (command.args or "").strip()
    if not raw_args:
        await message.answer(
            'Формат: /setrank "<команда>" "<ранг>"\n'
            'Сброс: /setrank "<команда>" default\n'
            'Текстом: установить "команда" ранг внутри бота <ранг>'
        )
        return

    try:
        tokens = shlex.split(raw_args)
    except ValueError:
        await message.answer("Некорректные кавычки в аргументах.")
        return

    if len(tokens) < 2:
        await message.answer('Формат: /setrank "<команда>" "<ранг>"')
        return

    command_input = tokens[0]
    command_key = resolve_command_key_input(command_input)
    if command_key is None:
        normalized_input = normalize_text_command(command_input)
        if normalized_input:
            aliases = await activity_repo.list_chat_aliases(chat_id=message.chat.id)
            for alias in aliases:
                if alias.alias_text_norm == normalized_input or alias.source_trigger_norm == normalized_input:
                    command_key = alias.command_key
                    break
    if command_key is None:
        await message.answer("Не удалось распознать команду.")
        return

    role_input = " ".join(tokens[1:]).strip()
    if role_input.lower() in {"default", "none", "reset", "сброс"}:
        removed = await activity_repo.remove_command_access_rule(chat_id=message.chat.id, command_key=command_key)
        if removed:
            await message.answer(f'Ограничение ранга снято для <code>{escape(command_key)}</code>.', parse_mode="HTML")
        else:
            await message.answer(f'Для <code>{escape(command_key)}</code> отдельного ранга не было.', parse_mode="HTML")
        return

    chat = ChatSnapshot(
        telegram_chat_id=message.chat.id,
        chat_type=message.chat.type,
        title=message.chat.title,
    )
    try:
        rule = await activity_repo.upsert_command_access_rule(
            chat=chat,
            command_key=command_key,
            min_role_token=role_input,
            updated_by_user_id=message.from_user.id,
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return

    role = await activity_repo.get_chat_role_definition(chat_id=message.chat.id, role_code=rule.min_role_code)
    role_label = role.title_ru if role is not None else rule.min_role_code
    await message.answer(
        (
            f'Для команды <code>{escape(command_key)}</code> установлен минимальный ранг '
            f'<code>{escape(role_label)}</code>.'
        ),
        parse_mode="HTML",
    )


@router.message(Command("ranks"))
async def ranks_command(message: Message, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе")
        return
    if message.from_user is None:
        return

    allowed, _, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_command_access",
        bootstrap_if_missing_owner=False,
    )
    if not allowed:
        await message.answer("Недостаточно прав для просмотра рангов команд.")
        return

    rules = await activity_repo.list_command_access_rules(chat_id=message.chat.id)
    if not rules:
        await message.answer("Кастомные ранги команд не настроены.")
        return

    lines = ["<b>Ранги команд в чате:</b>"]
    for rule in rules:
        role = await activity_repo.get_chat_role_definition(chat_id=message.chat.id, role_code=rule.min_role_code)
        role_label = role.title_ru if role is not None else rule.min_role_code
        lines.append(f'• <code>{escape(rule.command_key)}</code> → <code>{escape(role_label)}</code>')
    await message.answer("\n".join(lines), parse_mode="HTML")
