"""Registry of feature cards, collected from one module per navigation area.

Area modules start empty in PR-A so later PRs can add cards in parallel
without touching shared files.
"""

from __future__ import annotations

from selara.presentation.navigation.cards import (
    admin,
    ai,
    economy,
    games,
    pets,
    profile,
    social,
    subscriptions,
)
from selara.presentation.navigation.cards.model import CARD_AUDIENCES, CARD_CONTEXTS, FeatureCard

FEATURE_CARDS: tuple[FeatureCard, ...] = (
    *games.CARDS,
    *economy.CARDS,
    *social.CARDS,
    *pets.CARDS,
    *ai.CARDS,
    *profile.CARDS,
    *admin.CARDS,
    *subscriptions.CARDS,
)

__all__ = ["CARD_AUDIENCES", "CARD_CONTEXTS", "FEATURE_CARDS", "FeatureCard"]
