from __future__ import annotations

import re

from selara.presentation.navigation.cards import FEATURE_CARDS
from selara.presentation.navigation.cards.pets import CARDS as PET_CARDS
from selara.presentation.navigation.cards.social import CARDS as SOCIAL_CARDS
from selara.presentation.navigation.render import card_text, feature_block
from selara.presentation.navigation.tree import get_nav_node

SOCIAL_NODES = ("social", "couples")
PETS_NODES = ("pets",)


def _spec_keys_of(node_keys: tuple[str, ...]) -> set[str]:
    return {key for node_key in node_keys for key in get_nav_node(node_key).spec_keys}


def test_social_and_pets_cards_sit_on_their_own_area_nodes() -> None:
    social_keys = _spec_keys_of(SOCIAL_NODES)
    pets_keys = _spec_keys_of(PETS_NODES)

    for card in SOCIAL_CARDS:
        assert card.spec_key in social_keys, f"{card.spec_key} is not placed in the social area"
    for card in PET_CARDS:
        assert card.spec_key in pets_keys, f"{card.spec_key} is not placed in the pets area"


def test_each_command_has_at_most_one_feature_card() -> None:
    spec_keys = [card.spec_key for card in FEATURE_CARDS]
    assert len(spec_keys) == len(set(spec_keys)), "two cards describe the same command"


def test_area_cards_render_every_block_of_the_card() -> None:
    for card in (*SOCIAL_CARDS, *PET_CARDS):
        text = card_text(card)
        assert "Где: " in text
        assert "Кому: " in text
        if card.limits:
            assert "Ограничения: " in text


def test_area_card_limits_avoid_copied_numbers() -> None:
    # Numbers belong to Settings and resolve_feature_policy; a copied figure goes stale.
    for card in (*SOCIAL_CARDS, *PET_CARDS):
        for limit in card.limits:
            assert not re.search(r"\d", limit), f"{card.spec_key}: limit copies a number: {limit!r}"


def test_couples_node_shows_cards_instead_of_bare_command_lines() -> None:
    cards_by_spec = {card.spec_key: card for card in FEATURE_CARDS}
    block = feature_block(get_nav_node("couples").spec_keys, cards_by_spec)

    assert block is not None
    assert "Кому: участникам группы" in block
    assert "Ограничения: " in block
