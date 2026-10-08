"""Feature cards for the 'admin' navigation area: group settings, triggers, moderation, roles."""

from __future__ import annotations

from selara.presentation.navigation.cards.model import FeatureCard

CARDS: tuple[FeatureCard, ...] = (
    FeatureCard(
        spec_key="admin_autocfg",
        contexts=("private",),
        audience="admins",
        limits=("в группе /autocfg только подсказывает открыть личку с ботом",),
        related_nodes=("admin_settings",),
    ),
    FeatureCard(
        spec_key="admin_settings_tools",
        contexts=("group",),
        audience="admins",
        limits=("нужно право manage_settings",),
        related_nodes=("admin_settings",),
    ),
    FeatureCard(
        spec_key="admin_aliases",
        contexts=("group",),
        audience="admins",
        limits=("/aliasmode без аргумента показывает текущий режим и ничего не меняет",),
        related_nodes=("admin_settings",),
    ),
    FeatureCard(
        spec_key="admin_smart_triggers",
        contexts=("group",),
        audience="admins",
        limits=("нужно право manage_settings", "/deltrigger принимает числовой id из списка /triggers"),
        related_nodes=("admin_settings",),
    ),
    FeatureCard(
        spec_key="admin_custom_rp_actions",
        contexts=("group",),
        audience="admins",
        limits=("нужно право manage_settings", "переменные шаблона те же, что показывает /triggervars"),
        related_nodes=("admin_settings",),
    ),
    FeatureCard(
        spec_key="admin_role_step",
        contexts=("group",),
        audience="admins",
        limits=(
            "нужен reply на сообщение участника",
            "нельзя понизить последнего владельца; повышение ограничено вашим рангом",
        ),
        related_nodes=("roles",),
    ),
    FeatureCard(
        spec_key="admin_moderation_actions",
        contexts=("group",),
        audience="admins",
        limits=(
            "и слэш-команда, и слово в ответ требуют reply на сообщение цели",
            "снять можно синонимами: разпред/анпред, разварн/анварн, разбан/анбан",
        ),
        related_nodes=("moderation",),
    ),
)
