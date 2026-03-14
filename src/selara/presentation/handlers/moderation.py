from __future__ import annotations

import re
import shlex
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from selara.core.roles import (
    BOT_PERMISSIONS,
    SYSTEM_ROLE_BY_CODE,
    SYSTEM_ROLE_TEMPLATES,
    normalize_assigned_role_code,
)
from selara.domain.entities import BotRole, ModerationAction, UserSnapshot
from selara.domain.value_objects import display_name_from_parts
from selara.presentation.auth import (
    build_chat_snapshot,
    build_user_snapshot,
    get_actor_role,
    get_role_label_ru,
    has_command_access,
    has_permission,
)

router = Router(name="moderation")

_SLASH_MODERATION_COMMANDS: tuple[str, ...] = ("pred", "warn", "unwarn", "ban", "unban")
_MODERATION_ACTIONS: tuple[str, ...] = (*_SLASH_MODERATION_COMMANDS, "unpred")
_TEXT_MOD_ACTIONS: tuple[tuple[str, str], ...] = (
    ("снять пред", "unpred"),
    ("разпред", "unpred"),
    ("анпред", "unpred"),
    ("снять варн", "unwarn"),
    ("разварн", "unwarn"),
    ("анварн", "unwarn"),
    ("снять бан", "unban"),
    ("разбан", "unban"),
    ("анбан", "unban"),
    ("pred", "pred"),
    ("warn", "warn"),
    ("ban", "ban"),
    ("unban", "unban"),
    ("unwarn", "unwarn"),
    ("пред", "pred"),
    ("варн", "warn"),
    ("бан", "ban"),
)
_REPLY_MODERATION_PATTERN = re.compile(
    r"^\s*(?:снять\s+пред|разпред|анпред|снять\s+варн|разварн|анварн|снять\s+бан|разбан|анбан|pred|warn|ban|unban|unwarn|пред|варн|бан)\b",
    re.IGNORECASE,
)
_REPLY_ROLE_STEP_PATTERN = re.compile(r"^\s*(?:повысить|понизить)\b", re.IGNORECASE)
_PERMISSION_LABELS_RU: dict[str, str] = {
    "manage_roles": "управление ролями",
    "manage_settings": "управление настройками",
    "manage_games": "управление играми",
    "moderate_users": "модерация пользователей",
    "announce": "объявления",
    "manage_command_access": "доступ команд",
    "manage_role_templates": "шаблоны и кастомные роли",
}
_ROLE_ACTION_TO_COMMAND_KEY: dict[str, str] = {
    "promote": "roleadd",
    "demote": "roleremove",
}


def _target_label(target: UserSnapshot) -> str:
    return display_name_from_parts(
        user_id=target.telegram_user_id,
        username=target.username,
        first_name=target.first_name,
        last_name=target.last_name,
        chat_display_name=target.chat_display_name,
    )


def _split_first_token(raw: str) -> tuple[str | None, str]:
    value = raw.strip()
    if not value:
        return None, ""
    parts = value.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1].strip()


async def _resolve_target_user(message: Message, activity_repo, explicit_token: str | None) -> UserSnapshot | None:
    if message.reply_to_message and message.reply_to_message.from_user is not None:
        reply_user = message.reply_to_message.from_user
        chat_display_name = await activity_repo.get_chat_display_name(
            chat_id=message.chat.id,
            user_id=reply_user.id,
        )
        return build_user_snapshot(
            user_id=reply_user.id,
            username=reply_user.username,
            first_name=reply_user.first_name,
            last_name=reply_user.last_name,
            is_bot=bool(reply_user.is_bot),
            chat_display_name=chat_display_name,
        )

    if explicit_token is None:
        return None

    token = explicit_token.strip()
    if not token:
        return None

    if token.startswith("@"):
        snap = await activity_repo.find_chat_user_by_username(chat_id=message.chat.id, username=token)
        return snap

    if token.lstrip("-").isdigit():
        user_id = int(token)
        chat_display_name = await activity_repo.get_chat_display_name(chat_id=message.chat.id, user_id=user_id)
        existing = await activity_repo.get_user_snapshot(user_id=user_id)
        if existing is not None:
            if chat_display_name:
                return UserSnapshot(
                    telegram_user_id=existing.telegram_user_id,
                    username=existing.username,
                    first_name=existing.first_name,
                    last_name=existing.last_name,
                    is_bot=existing.is_bot,
                    chat_display_name=chat_display_name,
                )
            return existing
        return UserSnapshot(
            telegram_user_id=user_id,
            username=None,
            first_name=None,
            last_name=None,
            is_bot=False,
            chat_display_name=chat_display_name,
        )

    return None


def _resolve_system_role_rank(role_code: BotRole | None) -> int | None:
    normalized = normalize_assigned_role_code(role_code)
    if normalized is None:
        return None
    definition = SYSTEM_ROLE_BY_CODE.get(normalized)
    if definition is None:
        return None
    return definition.rank


def _can_manage_target(
    *,
    actor_role_code: BotRole | None = None,
    actor_rank: int | None = None,
    target_rank: int | None = None,
    actor_role: BotRole | None = None,
    target_role: BotRole | None = None,
) -> bool:
    actor_role_code = normalize_assigned_role_code(actor_role_code or actor_role)
    if actor_role_code is None:
        raise TypeError("actor_role_code or actor_role is required")
    if actor_rank is None:
        actor_rank = _resolve_system_role_rank(actor_role_code)
    if actor_rank is None:
        raise TypeError("actor_rank is required when actor role rank cannot be resolved")
    if target_rank is None and target_role is not None:
        target_rank = _resolve_system_role_rank(target_role)
    if actor_role_code == "owner":
        return True
    if target_rank is None:
        return True
    return actor_rank > target_rank


def _role_add_allowed(
    *,
    actor_role_code: BotRole | None = None,
    actor_rank: int | None = None,
    target_current_rank: int | None = None,
    target_new_rank: int | None = None,
    actor_role: BotRole | None = None,
    target_current_role: BotRole | None = None,
    target_new_role: BotRole | None = None,
) -> bool:
    actor_role_code = normalize_assigned_role_code(actor_role_code or actor_role)
    if actor_role_code is None:
        raise TypeError("actor_role_code or actor_role is required")
    if actor_rank is None:
        actor_rank = _resolve_system_role_rank(actor_role_code)
    if actor_rank is None:
        raise TypeError("actor_rank is required when actor role rank cannot be resolved")
    if target_current_rank is None and target_current_role is not None:
        target_current_rank = _resolve_system_role_rank(target_current_role)
    if target_new_rank is None:
        target_new_rank = _resolve_system_role_rank(target_new_role)
    if target_new_rank is None:
        raise TypeError("target_new_rank or target_new_role is required")
    if actor_role_code == "owner":
        return True

    if target_new_rank >= actor_rank:
        return False
    if target_current_rank is not None and target_current_rank >= actor_rank:
        return False
    return True


def _parse_roleadd_args(raw_args: str) -> list[tuple[str, str | None]]:
    try:
        tokens = shlex.split(raw_args)
    except ValueError:
        return []
    if not tokens:
        return []

    first = tokens[0].strip()
    second = tokens[1].strip() if len(tokens) >= 2 else None
    if not first:
        return []
    if second is None or not second:
        return [(first, None)]
    if first == second:
        return [(first, second)]
    return [(first, second), (second, first)]


def _parse_shlex_args(raw_args: str) -> list[str] | None:
    try:
        return shlex.split(raw_args)
    except ValueError:
        return None


def _permissions_to_text(permissions: tuple[str, ...]) -> str:
    if not permissions:
        return "нет прав"
    return ", ".join(_PERMISSION_LABELS_RU.get(permission, permission) for permission in permissions)


def _format_role_definition_line(*, role_code: str, title_ru: str, rank: int, permissions: tuple[str, ...], is_system: bool) -> str:
    marker = "system" if is_system else "custom"
    return (
        f"• <code>{escape(title_ru)}</code> "
        f"(<code>{escape(role_code)}</code>, rank=<code>{rank}</code>, {marker})\n"
        f"  Права: {escape(_permissions_to_text(permissions))}"
    )


async def _try_restrict_user(bot: Bot, *, chat_id: int, user_id: int) -> bool:
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


async def _try_unrestrict_user(bot: Bot, *, chat_id: int, user_id: int) -> bool:
    try:
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


def _parse_text_action(text: str) -> tuple[str, str] | None:
    raw = text.strip()
    if not raw or raw.startswith("/"):
        return None

    words = raw.split()
    if not words:
        return None

    lowered_words = [word.lower() for word in words]
    lowered = " ".join(lowered_words)
    for token, action in _TEXT_MOD_ACTIONS:
        token_words = token.split()
        if lowered == token:
            return action, ""
        if len(lowered_words) > len(token_words) and lowered_words[: len(token_words)] == token_words:
            reason = " ".join(words[len(token_words) :]).strip()
            return action, reason

    return None


def _parse_reply_text_action(text: str) -> tuple[str, str] | None:
    return _parse_text_action(text)


def _parse_role_step_action(text: str) -> str | None:
    raw = text.strip()
    if not raw or raw.startswith("/"):
        return None

    token = raw.split(maxsplit=1)[0].lower()
    if token == "повысить":
        return "promote"
    if token == "понизить":
        return "demote"
    return None


async def _apply_moderation_action(
    *,
    message: Message,
    activity_repo,
    bot: Bot,
    command_name: str,
    raw_tail: str,
    use_reply_target: bool,
) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    if command_name not in _MODERATION_ACTIONS:
        return

    command_allowed, actor_role_code, required_role_code, _ = await has_command_access(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        command_key=command_name,
        bootstrap_if_missing_owner=False,
    )
    if not command_allowed:
        actor_label = await get_role_label_ru(activity_repo, chat_id=message.chat.id, role_code=actor_role_code)
        required_label = await get_role_label_ru(activity_repo, chat_id=message.chat.id, role_code=required_role_code)
        await message.answer(
            (
                f"Недостаточно прав для команды <code>{escape(command_name)}</code>.\n"
                f"Ваш ранг: <code>{escape(actor_label)}</code>\n"
                f"Нужный ранг: <code>{escape(required_label)}</code>"
            ),
            parse_mode="HTML",
        )
        return

    allowed, actor_role, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="moderate_users",
        bootstrap_if_missing_owner=False,
    )
    if not allowed or actor_role is None:
        await message.answer("Недостаточно прав для модерации.")
        return

    actor_role_definition = await activity_repo.get_effective_role_definition(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
    )
    if actor_role_definition is None:
        await message.answer("Недостаточно прав для модерации.")
        return

    if use_reply_target and message.reply_to_message is not None:
        target_token = None
        reason = raw_tail.strip()
    else:
        target_token, reason = _split_first_token(raw_tail)

    target = await _resolve_target_user(message, activity_repo, target_token)
    if target is None:
        await message.answer("Укажите пользователя через reply или @username/id.")
        return

    if target.telegram_user_id == message.from_user.id:
        await message.answer("Нельзя применять модерацию к самому себе.")
        return

    if target.is_bot:
        await message.answer("Нельзя выдавать санкции боту.")
        return

    target_role_definition = await activity_repo.get_effective_role_definition(
        chat_id=message.chat.id,
        user_id=target.telegram_user_id,
    )
    if not _can_manage_target(
        actor_role_code=actor_role_definition.role_code,
        actor_rank=actor_role_definition.rank,
        target_rank=target_role_definition.rank if target_role_definition is not None else None,
    ):
        await message.answer("Недостаточно уровня доступа для этого пользователя.")
        return

    chat = build_chat_snapshot(chat_id=message.chat.id, chat_type=message.chat.type, chat_title=message.chat.title)
    actor = build_user_snapshot(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
    )

    action: ModerationAction = command_name  # type: ignore[assignment]
    result = await activity_repo.apply_moderation_action(
        chat=chat,
        actor=actor,
        target=target,
        action=action,
        reason=reason or None,
        amount=1,
    )

    restrict_applied = None
    if action == "ban" or result.auto_ban_triggered:
        restrict_applied = await _try_restrict_user(bot, chat_id=message.chat.id, user_id=target.telegram_user_id)
    elif action == "unban":
        restrict_applied = await _try_unrestrict_user(bot, chat_id=message.chat.id, user_id=target.telegram_user_id)

    st = result.state
    lines = [f"<b>{escape(_target_label(target))}</b>"]

    if action == "pred":
        lines.append("Пред.")
    elif action == "warn":
        lines.append("Варн.")
    elif action == "unpred":
        lines.append("Один пред снят.")
    elif action == "unwarn":
        lines.append("Один варн снят.")
    elif action == "ban":
        lines.append("Бан.")
    elif action == "unban":
        lines.append("Бан снят.")

    normalized_reason = (reason or "").strip()
    if action in {"pred", "warn", "ban"}:
        lines.append("Причина:")
        lines.append(escape(normalized_reason or "не указана"))

    if result.auto_warns_added > 0:
        lines.append(f"Авто-конвертация: +{result.auto_warns_added} варн(ов) из предов.")
    if result.auto_ban_triggered:
        lines.append("Авто-бан: достигнут порог 3 варна.")

    lines.append(f"Преды: <b>{st.pending_preds}</b>/3 | Варны: <b>{st.warn_count}</b>/3 | Банов: {st.total_bans}")

    if restrict_applied is False:
        lines.append("<i>Telegram-бан/разбан не применён (нет прав у бота), но внутренний статус обновлён.</i>")

    await message.answer("\n".join(lines), parse_mode="HTML")


async def _apply_role_step_action(*, message: Message, activity_repo, action: str) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return
    if action not in {"promote", "demote"}:
        return
    if message.reply_to_message is None:
        await message.answer("Команды «повысить/понизить» работают только reply на сообщение пользователя.")
        return

    command_key = _ROLE_ACTION_TO_COMMAND_KEY[action]
    command_allowed, actor_role_code, required_role_code, _ = await has_command_access(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        command_key=command_key,
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not command_allowed:
        actor_label = await get_role_label_ru(activity_repo, chat_id=message.chat.id, role_code=actor_role_code)
        required_label = await get_role_label_ru(activity_repo, chat_id=message.chat.id, role_code=required_role_code)
        await message.answer(
            (
                f"Недостаточно прав для команды <code>{escape(command_key)}</code>.\n"
                f"Ваш ранг: <code>{escape(actor_label)}</code>\n"
                f"Нужный ранг: <code>{escape(required_label)}</code>"
            ),
            parse_mode="HTML",
        )
        return

    allowed, _actor_role, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_roles",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed:
        await message.answer("Недостаточно прав для управления ролями.")
        return

    target = await _resolve_target_user(message, activity_repo, explicit_token=None)
    if target is None:
        await message.answer("Не удалось определить пользователя из reply.")
        return
    if target.is_bot:
        await message.answer("Нельзя менять роль у бота.")
        return

    actor_role_definition = await activity_repo.get_effective_role_definition(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
    )
    target_current_definition = await activity_repo.get_effective_role_definition(
        chat_id=message.chat.id,
        user_id=target.telegram_user_id,
    )
    target_assigned_role = await activity_repo.get_bot_role(chat_id=message.chat.id, user_id=target.telegram_user_id)

    if target.telegram_user_id == message.from_user.id and actor_role_definition.role_code != "owner":
        await message.answer("Нельзя менять свою роль, если вы не владелец.")
        return

    if not _can_manage_target(
        actor_role_code=actor_role_definition.role_code,
        actor_rank=actor_role_definition.rank,
        target_rank=target_current_definition.rank,
    ):
        await message.answer("Недостаточно уровня доступа для этого пользователя.")
        return

    roles = await activity_repo.list_chat_role_definitions(chat_id=message.chat.id)
    if not roles:
        await message.answer("Список ролей недоступен.")
        return

    roles_sorted = sorted(roles, key=lambda item: (item.rank, item.role_code))
    new_role = None
    if action == "promote":
        candidates = [item for item in roles_sorted if item.rank > target_current_definition.rank]
        if actor_role_definition.role_code != "owner":
            candidates = [item for item in candidates if item.rank < actor_role_definition.rank]
        if not candidates:
            await message.answer("Не могу повысить: достигнут максимум или не хватает ваших прав.")
            return
        new_role = candidates[0]
    else:
        if target_current_definition.role_code == "owner":
            role_items = await activity_repo.list_bot_roles(chat_id=message.chat.id)
            owner_count = sum(1 for _, role in role_items if role == "owner")
            if owner_count <= 1:
                await message.answer("Нельзя понизить последнего владельца.")
                return
        candidates = [item for item in roles_sorted if item.rank < target_current_definition.rank]
        if actor_role_definition.role_code != "owner":
            candidates = [item for item in candidates if item.rank < actor_role_definition.rank]
        if not candidates:
            await message.answer("Не могу понизить: уже минимальный ранг или не хватает ваших прав.")
            return
        new_role = candidates[-1]

    if new_role is None:
        await message.answer("Не удалось подобрать целевую роль.")
        return

    chat = build_chat_snapshot(chat_id=message.chat.id, chat_type=message.chat.type, chat_title=message.chat.title)
    if new_role.role_code == "participant":
        await activity_repo.remove_bot_role(chat_id=message.chat.id, user_id=target.telegram_user_id)
    else:
        await activity_repo.set_bot_role(
            chat=chat,
            target=target,
            role=new_role.role_code,
            assigned_by_user_id=message.from_user.id,
        )

    previous_title = target_current_definition.title_ru
    new_title = new_role.title_ru
    verb = "Повышено" if action == "promote" else "Понижено"
    if target_assigned_role is None and new_role.role_code == "participant":
        await message.answer("Изменений нет: у пользователя уже минимальный ранг.")
        return
    await message.answer(
        (
            f"{verb}: <b>{escape(_target_label(target))}</b>\n"
            f"Было: <code>{escape(previous_title)}</code>\n"
            f"Стало: <code>{escape(new_title)}</code>"
        ),
        parse_mode="HTML",
    )


@router.message(Command("roles"))
async def roles_command(message: Message, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    _actor_role, bootstrapped = await get_actor_role(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )

    items = await activity_repo.list_bot_roles(chat_id=message.chat.id)
    if not items:
        await message.answer("Назначенных ролей пока нет.")
        return

    role_definitions = await activity_repo.list_chat_role_definitions(chat_id=message.chat.id)
    role_titles = {item.role_code: item.title_ru for item in role_definitions}

    lines = ["<b>Роли бота в чате:</b>"]
    if bootstrapped:
        lines.append("<i>Инициализация: первый владелец назначен автоматически.</i>")

    for user, role in items:
        lines.append(f"- <code>{escape(role_titles.get(role, role))}</code>: {escape(_target_label(user))}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("roleadd"))
async def role_add_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    allowed, actor_role, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_roles",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed or actor_role is None:
        await message.answer("Недостаточно прав для управления ролями.")
        return

    raw_args = (command.args or "").strip()
    candidates = _parse_roleadd_args(raw_args)
    if not candidates:
        await message.answer('Формат: /roleadd "<роль|ранг>" [@user|id] или /roleadd [@user|id] "<роль|ранг>"')
        return

    target_role = None
    target = None
    role_found = False
    for role_token, target_token in candidates:
        resolved_role = await activity_repo.resolve_chat_role_definition(chat_id=message.chat.id, token=role_token)
        if resolved_role is None:
            continue
        role_found = True
        resolved_target = await _resolve_target_user(message, activity_repo, target_token)
        if resolved_target is None:
            continue
        target_role = resolved_role
        target = resolved_target
        break

    if target_role is None:
        if role_found:
            await message.answer("Укажите пользователя через reply или @username/id.")
            return
        roles = await activity_repo.list_chat_role_definitions(chat_id=message.chat.id)
        role_list = ", ".join(f"{item.title_ru} ({item.rank})" for item in roles)
        await message.answer(f"Неизвестная роль. Доступно: {role_list}")
        return

    actor_role_definition = await activity_repo.get_effective_role_definition(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
    )
    if target.telegram_user_id == message.from_user.id and actor_role_definition.role_code != "owner":
        await message.answer("Нельзя менять свою роль, если вы не владелец.")
        return

    current_target_definition = await activity_repo.get_effective_role_definition(
        chat_id=message.chat.id,
        user_id=target.telegram_user_id,
    )
    if not _role_add_allowed(
        actor_role_code=actor_role_definition.role_code,
        actor_rank=actor_role_definition.rank,
        target_current_rank=current_target_definition.rank if current_target_definition is not None else None,
        target_new_rank=target_role.rank,
    ):
        await message.answer("Эту роль выдать нельзя: недостаточно уровня доступа.")
        return

    chat = build_chat_snapshot(chat_id=message.chat.id, chat_type=message.chat.type, chat_title=message.chat.title)
    await activity_repo.set_bot_role(
        chat=chat,
        target=target,
        role=target_role.role_code,
        assigned_by_user_id=message.from_user.id,
    )
    await message.answer(
        f"Назначено: <b>{escape(_target_label(target))}</b> → <code>{escape(target_role.title_ru)}</code>",
        parse_mode="HTML",
    )


@router.message(Command("roleremove"))
async def role_remove_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    allowed, actor_role, _ = await has_permission(
        activity_repo,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        is_bot=bool(message.from_user.is_bot),
        permission="manage_roles",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed or actor_role is None:
        await message.answer("Недостаточно прав для управления ролями.")
        return

    target_token, _ = _split_first_token(command.args or "")
    target = await _resolve_target_user(message, activity_repo, target_token)
    if target is None:
        await message.answer("Укажите пользователя через reply или @username/id.")
        return

    actor_role_definition = await activity_repo.get_effective_role_definition(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
    )
    current_role = await activity_repo.get_bot_role(chat_id=message.chat.id, user_id=target.telegram_user_id)
    if current_role is None:
        await message.answer("У пользователя нет роли бота.")
        return
    current_role_definition = await activity_repo.get_chat_role_definition(chat_id=message.chat.id, role_code=current_role)
    if current_role_definition is None:
        await message.answer("У пользователя некорректная роль.")
        return

    if current_role_definition.role_code == "owner":
        role_items = await activity_repo.list_bot_roles(chat_id=message.chat.id)
        owner_count = sum(1 for _, role in role_items if role == "owner")
        if owner_count <= 1:
            await message.answer("Нельзя снять последнего владельца.")
            return

    if not _can_manage_target(
        actor_role_code=actor_role_definition.role_code,
        actor_rank=actor_role_definition.rank,
        target_rank=current_role_definition.rank,
    ):
        await message.answer("Недостаточно уровня доступа для снятия этой роли.")
        return

    if target.telegram_user_id == message.from_user.id and current_role_definition.role_code == "owner":
        await message.answer("Нельзя снять роль владельца у самого себя.")
        return

    removed = await activity_repo.remove_bot_role(chat_id=message.chat.id, user_id=target.telegram_user_id)
    if not removed:
        await message.answer("Роль не найдена.")
        return

    await message.answer(f"Роль снята у <b>{escape(_target_label(target))}</b>.", parse_mode="HTML")


@router.message(Command("roledefs"))
async def role_definitions_command(message: Message, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return

    roles = await activity_repo.list_chat_role_definitions(chat_id=message.chat.id)
    if not roles:
        await message.answer("Роли не настроены.")
        return

    lines = ["<b>Роли бота (доступные для назначения):</b>"]
    for role in roles:
        lines.append(
            _format_role_definition_line(
                role_code=role.role_code,
                title_ru=role.title_ru,
                rank=role.rank,
                permissions=role.permissions,
                is_system=role.is_system,
            )
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("roletemplates"))
async def role_templates_command(message: Message, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
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
        permission="manage_role_templates",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed:
        await message.answer("Недостаточно прав для управления шаблонами ролей.")
        return

    lines = ["<b>Системные шаблоны ролей:</b>"]
    for template in SYSTEM_ROLE_TEMPLATES:
        permissions = tuple(sorted(template.permissions))
        lines.append(
            _format_role_definition_line(
                role_code=template.role_code,
                title_ru=template.title_ru,
                rank=template.rank,
                permissions=permissions,
                is_system=True,
            )
        )
        lines.append(f"  Шаблон: <code>{escape(template.template_key)}</code>")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("rolecreate"))
async def role_create_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
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
        permission="manage_role_templates",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed:
        await message.answer("Недостаточно прав для создания кастомных ролей.")
        return

    tokens = _parse_shlex_args((command.args or "").strip())
    if not tokens:
        await message.answer('Формат: /rolecreate "<название>" ["шаблон"] [ранг]')
        return

    title_ru = tokens[0]
    template_token = tokens[1] if len(tokens) >= 2 else "participant"
    rank: int | None = None
    if len(tokens) >= 3:
        if not tokens[2].lstrip("-").isdigit():
            await message.answer("Ранг должен быть числом.")
            return
        rank = int(tokens[2])

    chat = build_chat_snapshot(chat_id=message.chat.id, chat_type=message.chat.type, chat_title=message.chat.title)
    try:
        created = await activity_repo.create_custom_role_from_template(
            chat=chat,
            title_ru=title_ru,
            template_token=template_token,
            rank=rank,
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return

    await message.answer(
        (
            "<b>Кастомная роль создана:</b>\n"
            f"Название: <code>{escape(created.title_ru)}</code>\n"
            f"Код: <code>{escape(created.role_code)}</code>\n"
            f"Ранг: <code>{created.rank}</code>\n"
            f"Права: {escape(_permissions_to_text(created.permissions))}"
        ),
        parse_mode="HTML",
    )


@router.message(Command("rolesettitle"))
async def role_set_title_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
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
        permission="manage_role_templates",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed:
        await message.answer("Недостаточно прав для редактирования кастомных ролей.")
        return

    tokens = _parse_shlex_args((command.args or "").strip())
    if tokens is None or len(tokens) != 2:
        await message.answer('Формат: /rolesettitle "<роль>" "<новое название>"')
        return

    try:
        updated = await activity_repo.update_custom_role(
            chat_id=message.chat.id,
            role_token=tokens[0],
            title_ru=tokens[1],
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return

    await message.answer(
        (
            "<b>Название роли обновлено:</b>\n"
            f"Роль: <code>{escape(updated.role_code)}</code>\n"
            f"Новое имя: <code>{escape(updated.title_ru)}</code>"
        ),
        parse_mode="HTML",
    )


@router.message(Command("rolesetrank"))
async def role_set_rank_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
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
        permission="manage_role_templates",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed:
        await message.answer("Недостаточно прав для редактирования кастомных ролей.")
        return

    tokens = _parse_shlex_args((command.args or "").strip())
    if tokens is None or len(tokens) != 2:
        await message.answer('Формат: /rolesetrank "<роль>" <ранг>')
        return
    if not tokens[1].lstrip("-").isdigit():
        await message.answer("Ранг должен быть числом.")
        return

    try:
        updated = await activity_repo.update_custom_role(
            chat_id=message.chat.id,
            role_token=tokens[0],
            rank=int(tokens[1]),
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return

    await message.answer(
        (
            "<b>Ранг роли обновлён:</b>\n"
            f"Роль: <code>{escape(updated.title_ru)}</code>\n"
            f"Новый ранг: <code>{updated.rank}</code>"
        ),
        parse_mode="HTML",
    )


@router.message(Command("roleperms"))
async def role_permissions_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
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
        permission="manage_role_templates",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed:
        await message.answer("Недостаточно прав для редактирования кастомных ролей.")
        return

    tokens = _parse_shlex_args((command.args or "").strip())
    if tokens is None or len(tokens) < 2:
        await message.answer(
            (
                'Формат: /roleperms "<роль>" +announce -manage_games ...\n'
                'Или полный набор: /roleperms "<роль>" =announce,manage_settings'
            )
        )
        return

    role_token = tokens[0]
    role = await activity_repo.resolve_chat_role_definition(chat_id=message.chat.id, token=role_token)
    if role is None:
        await message.answer("Роль не найдена.")
        return
    current_permissions = set(role.permissions)

    ops = tokens[1:]
    if len(ops) == 1 and ops[0].startswith("="):
        raw_items = [item.strip().lower() for item in ops[0][1:].split(",") if item.strip()]
        unknown = [item for item in raw_items if item not in BOT_PERMISSIONS]
        if unknown:
            await message.answer(f"Неизвестные права: {', '.join(unknown)}")
            return
        new_permissions = set(raw_items)
    else:
        new_permissions = set(current_permissions)
        for op in ops:
            if not op:
                continue
            sign = op[0]
            perm = op[1:] if sign in {"+", "-"} else op
            perm = perm.strip().lower()
            if perm not in BOT_PERMISSIONS:
                await message.answer(f"Неизвестное право: {perm}")
                return
            if sign == "-":
                new_permissions.discard(perm)
            else:
                new_permissions.add(perm)

    try:
        updated = await activity_repo.update_custom_role(
            chat_id=message.chat.id,
            role_token=role_token,
            permissions=tuple(sorted(new_permissions)),
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return

    await message.answer(
        (
            "<b>Права роли обновлены:</b>\n"
            f"Роль: <code>{escape(updated.title_ru)}</code>\n"
            f"Права: {escape(_permissions_to_text(updated.permissions))}"
        ),
        parse_mode="HTML",
    )


@router.message(Command("roledelete"))
async def role_delete_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
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
        permission="manage_role_templates",
        bootstrap_if_missing_owner=True,
        bot=message.bot,
    )
    if not allowed:
        await message.answer("Недостаточно прав для удаления кастомных ролей.")
        return

    tokens = _parse_shlex_args((command.args or "").strip())
    if tokens is None or len(tokens) != 1:
        await message.answer('Формат: /roledelete "<роль>"')
        return

    try:
        deleted = await activity_repo.delete_custom_role(chat_id=message.chat.id, role_token=tokens[0])
    except ValueError as exc:
        await message.answer(str(exc))
        return

    if not deleted:
        await message.answer("Роль не найдена.")
        return
    await message.answer("Кастомная роль удалена.")


@router.message(Command("modstat"))
async def modstat_command(message: Message, command: CommandObject, activity_repo) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Команда доступна только в группе.")
        return
    if message.from_user is None:
        return

    target_token, _ = _split_first_token(command.args or "")
    target = await _resolve_target_user(message, activity_repo, target_token)
    if target is None:
        target = build_user_snapshot(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            is_bot=bool(message.from_user.is_bot),
        )

    state = await activity_repo.get_moderation_state(chat_id=message.chat.id, user_id=target.telegram_user_id)
    if state is None:
        await message.answer(
            f"<b>{escape(_target_label(target))}</b>: предов 0/3, варнов 0/3, банов 0.",
            parse_mode="HTML",
        )
        return

    lines = [
        f"<b>{escape(_target_label(target))}</b>",
        f"Преды: <b>{state.pending_preds}</b>/3",
        f"Варны: <b>{state.warn_count}</b>/3",
        f"Всего предов: {state.total_preds}",
        f"Всего варнов: {state.total_warns}",
        f"Банов: {state.total_bans}",
        f"Статус: {'<b>ЗАБАНЕН</b>' if state.is_banned else 'не забанен'}",
    ]
    if state.last_reason:
        lines.append(f"Причина: {escape(state.last_reason)}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command(*_SLASH_MODERATION_COMMANDS))
async def moderation_action_command(message: Message, command: CommandObject, activity_repo, bot: Bot) -> None:
    command_name = (command.command or "").lower().strip()
    raw_tail = (command.args or "").strip()
    await _apply_moderation_action(
        message=message,
        activity_repo=activity_repo,
        bot=bot,
        command_name=command_name,
        raw_tail=raw_tail,
        use_reply_target=True,
    )


@router.message(F.text.regexp(_REPLY_MODERATION_PATTERN))
async def moderation_text_command(message: Message, activity_repo, bot: Bot) -> None:
    parsed = _parse_text_action(message.text or "")
    if parsed is None:
        return

    command_name, reason = parsed
    await _apply_moderation_action(
        message=message,
        activity_repo=activity_repo,
        bot=bot,
        command_name=command_name,
        raw_tail=reason,
        use_reply_target=True,
    )


@router.message(F.reply_to_message, F.text.regexp(_REPLY_ROLE_STEP_PATTERN))
async def role_step_reply_text_command(message: Message, activity_repo) -> None:
    action = _parse_role_step_action(message.text or "")
    if action is None:
        return
    await _apply_role_step_action(message=message, activity_repo=activity_repo, action=action)


@router.message(F.text.regexp(_REPLY_ROLE_STEP_PATTERN))
async def role_step_text_without_reply_command(message: Message) -> None:
    action = _parse_role_step_action(message.text or "")
    if action is None:
        return
    if message.reply_to_message is not None:
        return
    await message.answer("Не могу выполнить: используйте «повысить»/«понизить» reply на сообщение пользователя.")
