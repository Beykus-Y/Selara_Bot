from __future__ import annotations

import shlex
from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from selara.core.text_aliases import ALIAS_MODE_VALUES, TEXT_ALIAS_MAX_LEN
from selara.domain.entities import ChatSnapshot, TextAliasMode
from selara.presentation.auth import has_permission
from selara.presentation.commands.catalog import (
    COMMAND_KEY_DEFAULT_SOURCE_TRIGGER,
    resolve_builtin_command_key,
)
from selara.presentation.commands.normalizer import normalize_text_command

router = Router(name="aliases")


def _mode_label(mode: TextAliasMode) -> str:
    labels: dict[TextAliasMode, str] = {
        "aliases_if_exists": "only aliases if exists",
        "both": "aliases + standard",
        "standard_only": "only standard",
    }
    return labels.get(mode, mode)


def _parse_setalias_args(raw_args: str) -> tuple[str, str, bool] | None:
    try:
        tokens = shlex.split(raw_args)
    except ValueError:
        return None
    if not tokens:
        return None

    force = False
    if "--force" in tokens:
        tokens = [token for token in tokens if token != "--force"]
        force = True
    if len(tokens) != 2:
        return None
    return tokens[0], tokens[1], force


def _parse_single_quoted_arg(raw_args: str) -> str | None:
    try:
        tokens = shlex.split(raw_args)
    except ValueError:
        return None
    if len(tokens) != 1:
        return None
    return tokens[0]


def _normalize_custom_alias(raw: str) -> tuple[str | None, str | None]:
    normalized = normalize_text_command(raw)
    if not normalized:
        return None, "Алиас не должен быть пустым."
    if normalized.startswith("/"):
        return None, "Алиас не должен начинаться со слэша."
    if len(normalized) > TEXT_ALIAS_MAX_LEN:
        return None, f"Алиас слишком длинный. Максимум {TEXT_ALIAS_MAX_LEN} символа(ов)."
    return normalized, None


def _build_chat_snapshot(message: Message) -> ChatSnapshot:
    return ChatSnapshot(
        telegram_chat_id=message.chat.id,
        chat_type=message.chat.type,
        title=message.chat.title,
    )


async def _require_settings_permission(message: Message, activity_repo) -> bool:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return False
    if message.from_user is None:
        return False

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
        await message.answer("Недостаточно прав для управления алиасами в этой группе.")
        return False
    return True


@router.message(Command("setalias"))
async def setalias_command(message: Message, command: CommandObject, activity_repo) -> None:
    if not await _require_settings_permission(message, activity_repo):
        return
    if message.from_user is None:
        return

    parsed = _parse_setalias_args((command.args or "").strip())
    if parsed is None:
        await message.answer('Формат: /setalias "стандартный триггер" "новый алиас" [--force]')
        return

    source_raw, alias_raw, force = parsed
    source_norm = normalize_text_command(source_raw)
    command_key = resolve_builtin_command_key(source_raw)
    if command_key is None:
        await message.answer("Неизвестный стандартный триггер. Используйте существующую RU текстовую команду.")
        return

    alias_norm, alias_error = _normalize_custom_alias(alias_raw)
    if alias_error is not None or alias_norm is None:
        await message.answer(alias_error or "Некорректный алиас.")
        return

    builtin_conflict = resolve_builtin_command_key(alias_norm)
    if builtin_conflict is not None:
        await message.answer("Этот алиас занят встроенной текстовой командой и не может быть переопределён.")
        return

    result = await activity_repo.upsert_chat_alias(
        chat=_build_chat_snapshot(message),
        command_key=command_key,
        source_trigger_norm=source_norm,
        alias_text_norm=alias_norm,
        actor_user_id=message.from_user.id,
        force=force,
    )
    if result.alias is None and result.conflict_alias is not None:
        existing_target = result.conflict_alias.command_key
        await message.answer(
            (
                f'Алиас <code>{escape(alias_norm)}</code> уже назначен на '
                f'<code>{escape(existing_target)}</code>. Добавьте <code>--force</code> для переназначения.'
            ),
            parse_mode="HTML",
        )
        return

    if result.alias is None:
        await message.answer("Не удалось сохранить алиас.")
        return

    if result.reassigned:
        status = "переназначен"
    elif result.created:
        status = "добавлен"
    else:
        status = "обновлён"

    await message.answer(
        (
            f'Алиас {status}: <code>{escape(result.alias.alias_text_norm)}</code> '
            f"→ <code>{escape(result.alias.command_key)}</code> "
            f'(source: <code>{escape(result.alias.source_trigger_norm)}</code>)'
        ),
        parse_mode="HTML",
    )


@router.message(Command("aliases"))
async def aliases_command(message: Message, activity_repo) -> None:
    if not await _require_settings_permission(message, activity_repo):
        return

    mode = await activity_repo.get_chat_alias_mode(chat_id=message.chat.id)
    rows = await activity_repo.list_chat_aliases(chat_id=message.chat.id)

    lines = [f"<b>Alias mode:</b> <code>{escape(mode)}</code> ({escape(_mode_label(mode))})"]
    if not rows:
        lines.append("Кастомные алиасы не заданы.")
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    lines.append("<b>Алиасы группы:</b>")
    for row in rows:
        source = row.source_trigger_norm or COMMAND_KEY_DEFAULT_SOURCE_TRIGGER.get(row.command_key, row.command_key)
        lines.append(
            f'• <code>{escape(row.alias_text_norm)}</code> → <code>{escape(row.command_key)}</code> '
            f'(source: <code>{escape(source)}</code>)'
        )

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("unalias"))
async def unalias_command(message: Message, command: CommandObject, activity_repo) -> None:
    if not await _require_settings_permission(message, activity_repo):
        return

    raw_alias = _parse_single_quoted_arg((command.args or "").strip())
    if raw_alias is None:
        await message.answer('Формат: /unalias "алиас"')
        return

    alias_norm, alias_error = _normalize_custom_alias(raw_alias)
    if alias_error is not None or alias_norm is None:
        await message.answer(alias_error or "Некорректный алиас.")
        return

    removed = await activity_repo.remove_chat_alias(chat_id=message.chat.id, alias_text_norm=alias_norm)
    if not removed:
        await message.answer("Алиас не найден.")
        return

    await message.answer(f'Алиас удалён: <code>{escape(alias_norm)}</code>', parse_mode="HTML")


@router.message(Command("aliasmode"))
async def aliasmode_command(message: Message, command: CommandObject, activity_repo) -> None:
    if not await _require_settings_permission(message, activity_repo):
        return

    raw_value = (command.args or "").strip().lower()
    if not raw_value:
        mode = await activity_repo.get_chat_alias_mode(chat_id=message.chat.id)
        await message.answer(
            f'Текущий режим алиасов: <code>{escape(mode)}</code> ({escape(_mode_label(mode))})',
            parse_mode="HTML",
        )
        return

    if raw_value not in ALIAS_MODE_VALUES:
        await message.answer("Режим должен быть одним из: aliases_if_exists, both, standard_only")
        return

    mode = await activity_repo.set_chat_alias_mode(
        chat=_build_chat_snapshot(message),
        mode=raw_value,  # type: ignore[arg-type]
    )
    await message.answer(
        f'Режим алиасов обновлён: <code>{escape(mode)}</code> ({escape(_mode_label(mode))})',
        parse_mode="HTML",
    )
