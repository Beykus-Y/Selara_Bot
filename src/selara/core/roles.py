from __future__ import annotations

import re
from dataclasses import dataclass

BotPermissionName = str

PERM_MANAGE_ROLES = "manage_roles"
PERM_MANAGE_SETTINGS = "manage_settings"
PERM_MANAGE_GAMES = "manage_games"
PERM_MODERATE_USERS = "moderate_users"
PERM_ANNOUNCE = "announce"
PERM_MANAGE_COMMAND_ACCESS = "manage_command_access"
PERM_MANAGE_ROLE_TEMPLATES = "manage_role_templates"
# #34: lets an actor invoke the LLM assistant (?/??) for informational
# queries without also being trusted with moderate_users -- every mutating
# tool still independently requires moderate_users (or manage_roles for
# set_rank) regardless of this permission, enforced in
# infrastructure/llm/tools.py.
PERM_USE_LLM_READONLY = "use_llm_readonly"

BOT_PERMISSIONS: tuple[BotPermissionName, ...] = (
    PERM_MANAGE_ROLES,
    PERM_MANAGE_SETTINGS,
    PERM_MANAGE_GAMES,
    PERM_MODERATE_USERS,
    PERM_ANNOUNCE,
    PERM_MANAGE_COMMAND_ACCESS,
    PERM_MANAGE_ROLE_TEMPLATES,
    PERM_USE_LLM_READONLY,
)


@dataclass(frozen=True)
class RoleTemplate:
    template_key: str
    role_code: str
    title_ru: str
    rank: int
    permissions: frozenset[BotPermissionName]
    is_system: bool = True


SYSTEM_ROLE_TEMPLATES: tuple[RoleTemplate, ...] = (
    RoleTemplate(
        template_key="participant",
        role_code="participant",
        title_ru="Участник",
        rank=0,
        permissions=frozenset(),
    ),
    RoleTemplate(
        template_key="junior_admin",
        role_code="junior_admin",
        title_ru="Мл. админ",
        rank=10,
        permissions=frozenset({PERM_ANNOUNCE}),
    ),
    RoleTemplate(
        template_key="senior_admin",
        role_code="senior_admin",
        title_ru="Старший админ",
        rank=20,
        permissions=frozenset({PERM_ANNOUNCE, PERM_MANAGE_GAMES, PERM_MODERATE_USERS}),
    ),
    RoleTemplate(
        template_key="co_owner",
        role_code="co_owner",
        title_ru="Совладелец",
        rank=30,
        permissions=frozenset(
            {
                PERM_ANNOUNCE,
                PERM_MANAGE_GAMES,
                PERM_MODERATE_USERS,
                PERM_MANAGE_SETTINGS,
                PERM_MANAGE_ROLES,
                PERM_MANAGE_COMMAND_ACCESS,
                PERM_MANAGE_ROLE_TEMPLATES,
            }
        ),
    ),
    RoleTemplate(
        template_key="owner",
        role_code="owner",
        title_ru="Владелец",
        rank=40,
        permissions=frozenset(BOT_PERMISSIONS),
    ),
)

SYSTEM_ROLE_BY_CODE: dict[str, RoleTemplate] = {
    item.role_code: item for item in SYSTEM_ROLE_TEMPLATES
}
SYSTEM_ROLE_BY_TEMPLATE_KEY: dict[str, RoleTemplate] = {
    item.template_key: item for item in SYSTEM_ROLE_TEMPLATES
}

PERMISSION_LABELS_RU: dict[BotPermissionName, str] = {
    PERM_MANAGE_ROLES: "управление ролями",
    PERM_MANAGE_SETTINGS: "управление настройками",
    PERM_MANAGE_GAMES: "управление играми",
    PERM_MODERATE_USERS: "модерация пользователей",
    PERM_ANNOUNCE: "объявления",
    PERM_MANAGE_COMMAND_ACCESS: "доступ к командам",
    PERM_MANAGE_ROLE_TEMPLATES: "шаблоны и кастомные роли",
    PERM_USE_LLM_READONLY: "AI-ассистент (только чтение)",
}


def permission_label_ru(permission: BotPermissionName) -> str:
    return PERMISSION_LABELS_RU.get(permission, permission.replace("_", " "))


def permissions_text_ru(permissions: "tuple[BotPermissionName, ...] | list[BotPermissionName] | frozenset[BotPermissionName]") -> str:
    if not permissions:
        return "нет прав"
    return ", ".join(permission_label_ru(permission) for permission in permissions)


ROLE_TEMPLATE_ALIASES: dict[str, str] = {
    "participant": "participant",
    "участник": "participant",
    "junior_admin": "junior_admin",
    "junior": "junior_admin",
    "мл. админ": "junior_admin",
    "мл админ": "junior_admin",
    "младший": "junior_admin",
    "senior_admin": "senior_admin",
    "senior": "senior_admin",
    "старший": "senior_admin",
    "старший админ": "senior_admin",
    "co_owner": "co_owner",
    "coowner": "co_owner",
    "совладелец": "co_owner",
    "owner": "owner",
    "владелец": "owner",
}

LEGACY_ROLE_CODE_MAP: dict[str, str] = {
    "helper": "junior_admin",
    "moderator": "senior_admin",
    "admin": "co_owner",
    "owner": "owner",
}

ROLE_CODE_ALIASES: dict[str, str] = {
    **LEGACY_ROLE_CODE_MAP,
    "participant": "participant",
    "junior_admin": "junior_admin",
    "senior_admin": "senior_admin",
    "co_owner": "co_owner",
    "owner": "owner",
}

_ROLE_CODE_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9а-яА-ЯёЁ_]+")
_ROLE_CODE_WS_RE = re.compile(r"\s+")
_ROLE_TITLE_WS_RE = re.compile(r"\s+")


def normalize_role_code(raw: str) -> str:
    normalized = _ROLE_CODE_WS_RE.sub("_", (raw or "").strip().lower())
    normalized = _ROLE_CODE_SANITIZE_RE.sub("_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized[:64]


def normalize_role_title(raw: str) -> str:
    return _ROLE_TITLE_WS_RE.sub(" ", (raw or "").strip())


def resolve_role_template_key(token: str) -> str | None:
    normalized = normalize_role_title(token).lower()
    return ROLE_TEMPLATE_ALIASES.get(normalized)


def normalize_assigned_role_code(raw_code: str | None) -> str | None:
    if raw_code is None:
        return None
    normalized = normalize_role_code(raw_code)
    if not normalized:
        return None
    return ROLE_CODE_ALIASES.get(normalized, normalized)
