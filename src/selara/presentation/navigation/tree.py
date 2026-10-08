"""Category tree behind the feature catalog: 8 top-level areas, with nested
sections where one area has too many features for a single screen.

Nodes reference CommandSpec keys from command_catalog.py instead of copying
syntax or descriptions, so the wording of a command lives in one place.
"""

from __future__ import annotations

from dataclasses import dataclass

from selara.presentation.navigation.contract import NAV_CALLBACK_PREFIX, safe_callback

ROOT_KEY = "root"


@dataclass(frozen=True)
class NavNode:
    key: str
    title: str
    summary: str
    parent: str | None
    children: tuple[str, ...] = ()
    spec_keys: tuple[str, ...] = ()


NAV_NODES: tuple[NavNode, ...] = (
    NavNode(
        key=ROOT_KEY,
        title="✨ Возможности Selara",
        summary="Выберите раздел. Внутри каждого раздела — функции и примеры.",
        parent=None,
        children=("games", "economy", "social", "pets", "ai", "profile", "admin", "subscriptions"),
    ),
    NavNode(
        key="games",
        title="🎮 Игры и развлечения",
        summary="Игры в группах и гача.",
        parent=ROOT_KEY,
        children=("gacha",),
        spec_keys=("games_lobby", "games_role_reveal"),
    ),
    NavNode(
        key="gacha",
        title="🎴 Гача Genshin и HSR",
        summary="Крутки персонажей и оружия.",
        parent="games",
        spec_keys=("misc_gacha",),
    ),
    NavNode(
        key="economy",
        title="💰 Экономика",
        summary="Монеты, ферма, магазин и торговля.",
        parent=ROOT_KEY,
        spec_keys=(
            "economy_panel",
            "economy_farm",
            "economy_shop_inventory_craft",
            "economy_market_transfer_auction",
            "economy_growth",
        ),
    ),
    NavNode(
        key="social",
        title="💞 Общение и социальные функции",
        summary="Отношения, семья, кланы и оформление чата.",
        parent=ROOT_KEY,
        children=("couples",),
        spec_keys=(
            "clans_core",
            "social_naming",
            "social_karma_reply",
            "social_quote_card",
            "social_personas",
            "social_announcements",
            "misc_daily_article",
        ),
    ),
    NavNode(
        key="couples",
        title="💍 Отношения и семья",
        summary="Пары, брак, усыновление и семейное древо.",
        parent="social",
        spec_keys=(
            "relationships_start",
            "relationships_pair_actions",
            "relationships_marriage_actions",
            "relationships_end",
            "family_adopt_pet_tree",
            "family_escape",
        ),
    ),
    NavNode(
        key="pets",
        title="🐾 Питомцы",
        summary="AI-питомцы: уход, разговор и развитие.",
        parent=ROOT_KEY,
        spec_keys=("pets_core",),
    ),
    NavNode(
        key="ai",
        title="🤖 Искусственный интеллект",
        summary="Личный AI и AI в группе.",
        parent=ROOT_KEY,
        spec_keys=("ai_personal", "group_character"),
    ),
    NavNode(
        key="profile",
        title="📊 Профиль и статистика",
        summary="Профиль, топы, достижения и активность.",
        parent=ROOT_KEY,
        spec_keys=(
            "stats_profile",
            "stats_leaderboards",
            "stats_achievements",
            "stats_award_grant",
            "misc_lastseen",
            "misc_public_service_commands",
        ),
    ),
    NavNode(
        key="admin",
        title="🛡 Администраторам",
        summary="Роли, модерация и настройка группы. Каждая функция требует своего права Selara; часть справки доступна всем.",
        parent=ROOT_KEY,
        children=("roles", "moderation"),
        spec_keys=(
            "admin_aliases",
            "admin_settings_tools",
            "admin_smart_triggers",
            "admin_custom_rp_actions",
            "admin_autocfg",
        ),
    ),
    NavNode(
        key="roles",
        title="👥 Роли и ранги",
        summary="Роли участников, ранги команд и шаги повышения.",
        parent="admin",
        spec_keys=(
            "admin_role_definitions",
            "admin_role_assignment",
            "admin_role_custom",
            "admin_command_ranks",
            "admin_role_step",
        ),
    ),
    NavNode(
        key="moderation",
        title="⚖️ Модерация",
        summary="Предупреждения, пред-статусы и баны.",
        parent="admin",
        spec_keys=("admin_moderation_actions",),
    ),
    NavNode(
        key="subscriptions",
        title="💎 Подписки и поддержка",
        summary="Подписки Personal и Chat AI, обратная связь.",
        parent=ROOT_KEY,
        spec_keys=("subscriptions_selara", "user_feedback"),
    ),
)

_NODES_BY_KEY: dict[str, NavNode] = {node.key: node for node in NAV_NODES}


def get_nav_node(key: str) -> NavNode:
    try:
        return _NODES_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"Unknown navigation node: {key!r}") from exc


def root_node() -> NavNode:
    return get_nav_node(ROOT_KEY)


def path_to_root(key: str) -> tuple[NavNode, ...]:
    """Nodes from the given node up to the root, inclusive, nearest first.

    Raises if the parent chain loops or reaches a missing node, so a bad edit
    to NAV_NODES fails loudly instead of hanging a screen.
    """
    path: list[NavNode] = []
    seen: set[str] = set()
    current: str | None = key
    while current is not None:
        if current in seen:
            raise ValueError(f"navigation cycle at {current!r}")
        seen.add(current)
        node = get_nav_node(current)
        path.append(node)
        current = node.parent
    return tuple(path)


def nav_callback(key: str) -> str:
    """callback_data that opens the given node."""
    get_nav_node(key)
    return safe_callback(NAV_CALLBACK_PREFIX, key)
