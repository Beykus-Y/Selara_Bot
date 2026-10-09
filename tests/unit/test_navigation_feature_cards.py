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


def test_group_ai_summary_and_troubleshooting_are_reachable_from_help_root() -> None:
    from selara.presentation.commands.command_catalog import COMMAND_CATALOG, get_command_spec
    from selara.presentation.navigation.tree import get_nav_node, path_to_root

    ai = get_nav_node("ai")
    group_ai = get_nav_node("ai_group")
    summaries = get_nav_node("ai_summary")
    trouble = get_nav_node("troubleshooting")
    assert "ai_group_questions" in group_ai.spec_keys
    assert "ai_daily_summary" in summaries.spec_keys
    assert "ai_summary" in ai.children
    assert trouble.parent == "root"
    for key in ("ai_group", "ai_summary", "troubleshooting"):
        assert path_to_root(key)[-1].key == "root"
    assert get_command_spec("ai_group_questions").natural_triggers == (
        "? вопрос", "?? вопрос", "?reset",
    )
    assert get_command_spec("ai_daily_summary").syntax == ("/summary",)
    catalog_keys = {spec.key for spec in COMMAND_CATALOG}
    assert all(card.spec_key in catalog_keys for card in FEATURE_CARDS)
