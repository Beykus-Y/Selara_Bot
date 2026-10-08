"""Structural checks for the navigation contract, the category tree and the
feature-card registry. They run on the live catalog, so a new command that
no screen can reach fails here instead of silently disappearing from /help.
"""

from __future__ import annotations

import pytest

from selara.presentation.commands.command_catalog import COMMAND_CATALOG
from selara.presentation.navigation.cards import FEATURE_CARDS
from selara.presentation.navigation.cards.model import FeatureCard
from selara.presentation.navigation.contract import (
    BACK_LABEL,
    CANCEL_LABEL,
    HOME_LABEL,
    MAX_CALLBACK_DATA_BYTES,
    NAV_CALLBACK_PREFIX,
    callback_data_size,
    safe_callback,
)
from selara.presentation.navigation.tree import (
    NAV_NODES,
    NavNode,
    ROOT_KEY,
    get_nav_node,
    nav_callback,
    path_to_root,
    root_node,
)

# Catalog specs that intentionally have no route from the tree. Keep this
# list explicit and short; every entry needs a reason.
NAVIGATION_EXEMPTIONS: frozenset[str] = frozenset()

# Command words already used as callback prefixes or deep links. The
# navigation prefix must never collide with them.
LEGACY_CALLBACK_PREFIXES = frozenset({"pm", "help", "pai", "premium", "game", "eco"})


def _nodes_by_key() -> dict[str, NavNode]:
    return {node.key: node for node in NAV_NODES}


def test_node_keys_are_unique() -> None:
    keys = [node.key for node in NAV_NODES]
    assert len(keys) == len(set(keys))


def test_root_is_the_only_node_without_parent() -> None:
    roots = [node.key for node in NAV_NODES if node.parent is None]
    assert roots == [ROOT_KEY]


def test_parent_and_children_links_agree() -> None:
    nodes = _nodes_by_key()
    for node in NAV_NODES:
        if node.parent is not None:
            assert node.parent in nodes, f"{node.key}: unknown parent {node.parent!r}"
            assert node.key in nodes[node.parent].children, f"{node.key} is not listed under its parent"
        for child_key in node.children:
            assert child_key in nodes, f"{node.key}: unknown child {child_key!r}"
            assert nodes[child_key].parent == node.key, f"{child_key}: parent does not point back to {node.key}"


def test_every_node_reaches_root_without_cycles() -> None:
    for node in NAV_NODES:
        path = path_to_root(node.key)
        assert path[0].key == node.key
        assert path[-1].key == ROOT_KEY


def test_root_offers_eight_top_level_areas_with_distinct_titles() -> None:
    areas = [get_nav_node(key) for key in root_node().children]
    assert len(areas) == 8
    assert len({area.title for area in areas}) == len(areas)


def test_nodes_have_titles_and_summaries() -> None:
    for node in NAV_NODES:
        assert node.title.strip(), f"{node.key}: empty title"
        assert node.summary.strip(), f"{node.key}: empty summary"


def test_every_catalog_command_is_reachable_from_exactly_one_node() -> None:
    catalog_keys = [spec.key for spec in COMMAND_CATALOG]
    placed = [key for node in NAV_NODES for key in node.spec_keys]

    assert len(placed) == len(set(placed)), "a command is placed on more than one node"
    unknown = set(placed) - set(catalog_keys)
    assert not unknown, f"navigation references unknown command keys: {sorted(unknown)}"

    missing = set(catalog_keys) - set(placed) - NAVIGATION_EXEMPTIONS
    assert not missing, f"commands with no route from the navigation tree: {sorted(missing)}"


def test_navigation_exemptions_are_real_commands_and_still_needed() -> None:
    catalog_keys = {spec.key for spec in COMMAND_CATALOG}
    placed = {key for node in NAV_NODES for key in node.spec_keys}
    assert NAVIGATION_EXEMPTIONS <= catalog_keys
    assert not (NAVIGATION_EXEMPTIONS & placed), "an exempt command is already reachable; drop the exemption"


def test_callback_data_for_every_node_fits_telegram_limit() -> None:
    for node in NAV_NODES:
        data = nav_callback(node.key)
        assert callback_data_size(data) <= MAX_CALLBACK_DATA_BYTES
        assert data.startswith(f"{NAV_CALLBACK_PREFIX}:")


def test_nav_callback_rejects_unknown_node() -> None:
    with pytest.raises(KeyError):
        nav_callback("no_such_node")


def test_safe_callback_counts_utf8_bytes_not_characters() -> None:
    # 40 Cyrillic letters are 80 bytes in UTF-8 even though they are 40 characters.
    with pytest.raises(ValueError):
        safe_callback("nv", "я" * 40)
    assert callback_data_size(safe_callback("nv", "я" * 20)) == 2 + 1 + 40


def test_safe_callback_rejects_separator_inside_a_part() -> None:
    with pytest.raises(ValueError):
        safe_callback("nv", "a:b")


def test_navigation_prefix_does_not_collide_with_legacy_prefixes() -> None:
    assert NAV_CALLBACK_PREFIX not in LEGACY_CALLBACK_PREFIXES


def test_contract_labels_are_distinct_and_non_empty() -> None:
    labels = [BACK_LABEL, HOME_LABEL, CANCEL_LABEL]
    assert all(label.strip() for label in labels)
    assert len(set(labels)) == len(labels)


def test_path_to_root_detects_a_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    from selara.presentation.navigation import tree

    looped = (
        NavNode(key="a", title="A", summary="a", parent="b"),
        NavNode(key="b", title="B", summary="b", parent="a"),
    )
    monkeypatch.setattr(tree, "_NODES_BY_KEY", {node.key: node for node in looped})
    with pytest.raises(ValueError, match="cycle"):
        tree.path_to_root("a")


def test_feature_card_rejects_unknown_context_and_audience() -> None:
    with pytest.raises(ValueError):
        FeatureCard(spec_key="x", contexts=("dm",), audience="all")
    with pytest.raises(ValueError):
        FeatureCard(spec_key="x", contexts=("private",), audience="everyone")
    with pytest.raises(ValueError):
        FeatureCard(spec_key="x", contexts=(), audience="all")


def test_feature_cards_point_at_real_commands_and_nodes() -> None:
    catalog_keys = {spec.key for spec in COMMAND_CATALOG}
    node_keys = {node.key for node in NAV_NODES}
    for card in FEATURE_CARDS:
        assert card.spec_key in catalog_keys, f"card references unknown command {card.spec_key!r}"
        assert set(card.related_nodes) <= node_keys, f"{card.spec_key}: unknown related node"
