"""Feature cards for the 'profile' navigation area: profile, leaderboards, achievements."""

from __future__ import annotations

from selara.presentation.navigation.cards.model import FeatureCard

CARDS: tuple[FeatureCard, ...] = (
    FeatureCard(
        spec_key="stats_profile",
        contexts=("private", "group"),
        audience="all",
        limits=("описание профиля задаётся только командой /desc, текстового аналога нет",),
    ),
    FeatureCard(
        spec_key="stats_leaderboards",
        contexts=("group",),
        audience="all",
        limits=("рейтинги считаются по чату, в котором вызвана команда",),
    ),
    FeatureCard(
        spec_key="stats_achievements",
        contexts=("private", "group"),
        audience="all",
        limits=("/achsync доступен только администраторам чата и только в группе",),
    ),
    FeatureCard(
        spec_key="misc_public_service_commands",
        contexts=("private", "group"),
        audience="all",
        limits=("/settings показывает настройки, менять их могут только роли с правом управления",),
        related_nodes=("admin_settings",),
    ),
)
