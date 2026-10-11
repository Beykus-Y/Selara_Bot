from html import escape

import pytest

from selara.presentation.commands.command_catalog import (
    COMMAND_CATALOG,
    get_command_spec,
)
from selara.presentation.navigation.cards.model import FeatureCard
from selara.presentation.navigation.render import card_text, command_line, feature_block


def test_command_line_escapes_placeholders_and_shows_title() -> None:
    spec = get_command_spec("economy_farm")
    line = command_line(spec)
    assert line.startswith("• <code>")
    assert escape(spec.syntax[0]) in line
    assert escape(spec.title_ru) in line


def test_card_text_lists_context_audience_limits_and_next_steps() -> None:
    card = FeatureCard(
        spec_key="economy_farm",
        contexts=("private", "group"),
        audience="members",
        limits=("раз в час",),
        related_nodes=("economy",),
    )
    text = card_text(card)
    assert "Где: в личке с ботом, в группе" in text
    assert "Кому: участникам группы" in text
    assert "Ограничения: раз в час" in text
    assert "Дальше: 💰 Экономика" in text
    for syntax in get_command_spec("economy_farm").syntax:
        assert escape(syntax) in text


def test_card_text_omits_empty_limits_and_next_steps() -> None:
    card = FeatureCard(spec_key="economy_farm", contexts=("group",), audience="all")
    text = card_text(card)
    assert "Ограничения" not in text
    assert "Дальше" not in text


def test_feature_block_uses_a_card_where_one_exists_and_a_line_elsewhere() -> None:
    card = FeatureCard(spec_key="economy_farm", contexts=("group",), audience="all")
    block = feature_block(("economy_farm", "economy_panel"), {"economy_farm": card})
    assert block is not None
    assert block.startswith("<b>Функции</b>")
    assert "Где: в группе" in block
    assert escape(get_command_spec("economy_panel").title_ru) in block


def test_feature_block_is_none_without_commands() -> None:
    assert feature_block((), {}) is None


@pytest.mark.parametrize("spec_key", ["economy_farm", "pets_core"])
def test_command_line_is_one_line(spec_key: str) -> None:
    assert "\n" not in command_line(get_command_spec(spec_key))


@pytest.mark.parametrize("spec", COMMAND_CATALOG, ids=lambda spec: spec.key)
def test_every_catalog_spec_renders_a_line_and_a_card_without_syntax(spec) -> None:
    # Natural-language specs (clans, gacha) have no slash syntax; rendering must not index into it.
    line = command_line(spec)
    assert line.startswith("•") and escape(spec.title_ru) in line
    card = FeatureCard(spec_key=spec.key, contexts=("private",), audience="all")
    assert escape(spec.title_ru) in card_text(card)


def test_natural_language_spec_shows_its_trigger_when_it_has_no_syntax() -> None:
    spec = get_command_spec("clans_core")
    assert not spec.syntax
    assert "«клан»" in command_line(spec)
