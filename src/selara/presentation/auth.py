from __future__ import annotations

from typing import Literal

from sqlalchemy.exc import SQLAlchemyError

from selara.domain.entities import BotRole, ChatRoleDefinition, ChatSnapshot, UserSnapshot
from selara.presentation.db_recovery import safe_rollback

BotPermission = Literal[
    "manage_roles",
    "manage_settings",
    "manage_games",
    "moderate_users",
    "announce",
    "manage_command_access",
    "manage_role_templates",
]

_DEFAULT_MIN_ROLE_BY_COMMAND_KEY: dict[str, str] = {
    "inactive": "junior_admin",
}


def build_chat_snapshot(*, chat_id: int, chat_type: str, chat_title: str | None) -> ChatSnapshot:
    return ChatSnapshot(
        telegram_chat_id=chat_id,
        chat_type=chat_type,
        title=chat_title,
    )


def build_user_snapshot(
    *,
    user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    is_bot: bool,
    chat_display_name: str | None = None,
) -> UserSnapshot:
    return UserSnapshot(
        telegram_user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        is_bot=is_bot,
        chat_display_name=chat_display_name,
    )


async def get_actor_role(
    activity_repo,
    *,
    chat_id: int,
    chat_type: str,
    chat_title: str | None,
    user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    is_bot: bool,
    bootstrap_if_missing_owner: bool,
) -> tuple[BotRole | None, bool]:
    definition, bootstrapped = await get_actor_role_definition(
        activity_repo,
        chat_id=chat_id,
        chat_type=chat_type,
        chat_title=chat_title,
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        is_bot=is_bot,
        bootstrap_if_missing_owner=bootstrap_if_missing_owner,
    )
    if definition is None:
        return None, bootstrapped
    return definition.role_code, bootstrapped


async def get_actor_role_definition(
    activity_repo,
    *,
    chat_id: int,
    chat_type: str,
    chat_title: str | None,
    user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    is_bot: bool,
    bootstrap_if_missing_owner: bool,
) -> tuple[ChatRoleDefinition | None, bool]:
    chat = build_chat_snapshot(chat_id=chat_id, chat_type=chat_type, chat_title=chat_title)
    user = build_user_snapshot(
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        is_bot=is_bot,
    )

    try:
        bootstrapped = False
        if bootstrap_if_missing_owner:
            role, bootstrapped = await activity_repo.bootstrap_chat_owner_role(chat=chat, user=user)
            if role is not None:
                definition = await activity_repo.get_chat_role_definition(chat_id=chat_id, role_code=role)
                if definition is not None:
                    return definition, bootstrapped

        definition = await activity_repo.get_effective_role_definition(chat_id=chat_id, user_id=user_id)
        return definition, bootstrapped
    except SQLAlchemyError:
        # Roles tables may be absent before migrations; fail closed without crashing handlers.
        await safe_rollback(activity_repo)
        return None, False


async def get_role_label_ru(activity_repo, *, chat_id: int, role_code: str | None) -> str:
    if role_code is None:
        return "не назначен"
    try:
        definition = await activity_repo.get_chat_role_definition(chat_id=chat_id, role_code=role_code)
    except SQLAlchemyError:
        await safe_rollback(activity_repo)
        return role_code
    if definition is None:
        return role_code
    return definition.title_ru


async def has_permission(
    activity_repo,
    *,
    chat_id: int,
    chat_type: str,
    chat_title: str | None,
    user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    is_bot: bool,
    permission: BotPermission,
    bootstrap_if_missing_owner: bool,
) -> tuple[bool, BotRole | None, bool]:
    definition, bootstrapped = await get_actor_role_definition(
        activity_repo,
        chat_id=chat_id,
        chat_type=chat_type,
        chat_title=chat_title,
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        is_bot=is_bot,
        bootstrap_if_missing_owner=bootstrap_if_missing_owner,
    )
    if definition is None:
        return False, None, bootstrapped
    return permission in set(definition.permissions), definition.role_code, bootstrapped


async def has_command_access(
    activity_repo,
    *,
    chat_id: int,
    chat_type: str,
    chat_title: str | None,
    user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    is_bot: bool,
    command_key: str,
    bootstrap_if_missing_owner: bool,
) -> tuple[bool, BotRole | None, str | None, bool]:
    definition, bootstrapped = await get_actor_role_definition(
        activity_repo,
        chat_id=chat_id,
        chat_type=chat_type,
        chat_title=chat_title,
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        is_bot=is_bot,
        bootstrap_if_missing_owner=bootstrap_if_missing_owner,
    )
    if definition is None:
        return True, None, None, bootstrapped

    try:
        rule = await activity_repo.get_command_access_rule(chat_id=chat_id, command_key=command_key)
    except SQLAlchemyError:
        await safe_rollback(activity_repo)
        return True, definition.role_code, None, bootstrapped
    required_role_code = rule.min_role_code if rule is not None else _DEFAULT_MIN_ROLE_BY_COMMAND_KEY.get(command_key)
    if required_role_code is None:
        return True, definition.role_code, None, bootstrapped

    try:
        required = await activity_repo.get_chat_role_definition(chat_id=chat_id, role_code=required_role_code)
    except SQLAlchemyError:
        await safe_rollback(activity_repo)
        return False, definition.role_code, required_role_code, bootstrapped
    if required is None:
        return False, definition.role_code, required_role_code, bootstrapped

    return definition.rank >= required.rank, definition.role_code, required.role_code, bootstrapped
