"""Feature cards for the 'subscriptions' navigation area: Personal and Chat AI plans, feedback."""

from __future__ import annotations

from selara.presentation.navigation.cards.model import FeatureCard

CARDS: tuple[FeatureCard, ...] = (
    FeatureCard(
        spec_key="subscriptions_selara",
        contexts=("private", "group"),
        audience="all",
        limits=(
            "в группе /premium отправляет в личку с ботом, оформление и оплата идут там",
            "Personal привязан к пользователю, Chat AI — к выбранному чату",
        ),
        related_nodes=("ai",),
    ),
    FeatureCard(
        spec_key="user_feedback",
        contexts=("private",),
        audience="all",
        limits=("тип сообщения можно указать: предложение, проблема или поддержка",),
    ),
)
