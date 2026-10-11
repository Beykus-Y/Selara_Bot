"""Feature cards for the 'ai' navigation area: personal AI and Selara in a group."""

from __future__ import annotations

from selara.presentation.navigation.cards.model import FeatureCard

CARDS: tuple[FeatureCard, ...] = (
    FeatureCard(
        spec_key="ai_personal",
        contexts=("private",),
        audience="all",
        limits=(
            "обычное сообщение в личке без / уходит личному AI",
            "/ai_reset работает только в личных сообщениях с ботом",
        ),
        related_nodes=("ai_models",),
    ),
    FeatureCard(
        spec_key="group_character",
        contexts=("group",),
        audience="all",
        limits=("менять клички и характер могут админы с правом настройки чата; смотреть может любой участник",),
        related_nodes=("ai_group",),
    ),
)
