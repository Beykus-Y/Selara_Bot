"""Feature cards for the 'games' navigation area: game lobby and gacha."""

from __future__ import annotations

from selara.presentation.navigation.cards.model import FeatureCard

CARDS: tuple[FeatureCard, ...] = (
    FeatureCard(
        spec_key="games_lobby",
        contexts=("group",),
        audience="members",
        limits=("запуск лобби требует права manage_games в этом чате",),
        related_nodes=("games_roles", "games_quick"),
    ),
    FeatureCard(
        spec_key="misc_gacha",
        contexts=("group",),
        audience="members",
        limits=(
            "работает только в чате, где включена гача; это включает отдельная служебная команда, а не /settings",
            "чат должен быть подписан на служебный Telegram-канал бота, иначе бот присылает ссылку вместо результата",
            "валюта баннера пополняется из обычных монет экономики",
        ),
        related_nodes=("economy",),
    ),
)
