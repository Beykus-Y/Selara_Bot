"""Feature cards for the 'pets' navigation area.

Limits are written in words, not numbers: the real values live in Settings and
resolve_feature_policy, and a number copied here would drift.
"""

from __future__ import annotations

from selara.presentation.navigation.cards.model import FeatureCard

CARDS: tuple[FeatureCard, ...] = (
    FeatureCard(
        spec_key="pets_core",
        contexts=("group", "private"),
        audience="all",
        limits=(
            "Завести и ухаживать за питомцем можно бесплатно; живые ответы и путешествия нужны Selara Personal у хозяина.",
            "Питомец и его память привязаны к чату, где он живёт: в личке бот показывает только вашего питомца.",
            "Разговор с питомцем и /pet_do ограничены дневным лимитом на каждого участника (у хозяина он больше); обычные действия идут с паузой.",
            "Если админ выключил pets_enabled, питомцев в чате нет.",
        ),
        related_nodes=("ai_group",),
    ),
)
