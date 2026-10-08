"""Checks for the feature cards of the 'games', 'ai', 'subscriptions', 'profile'
and 'admin' areas: each card sits on a screen that lists its command, has no
duplicates, and renders its place, audience and limits."""

from __future__ import annotations

from selara.presentation.navigation.cards import FEATURE_CARDS
from selara.presentation.navigation.render import card_text
from selara.presentation.navigation.tree import NAV_NODES


def test_card_spec_keys_are_unique() -> None:
    keys = [card.spec_key for card in FEATURE_CARDS]
    assert len(keys) == len(set(keys))


def test_every_card_sits_on_a_node_that_lists_its_command() -> None:
    placed = {key for node in NAV_NODES for key in node.spec_keys}
    for card in FEATURE_CARDS:
        assert card.spec_key in placed, f"{card.spec_key}: no navigation node lists this command"


def test_every_card_renders_where_who_and_limits() -> None:
    for card in FEATURE_CARDS:
        text = card_text(card)
        assert "Где: " in text, card.spec_key
        assert "Кому: " in text, card.spec_key
        if card.limits:
            assert "Ограничения: " in text, card.spec_key
