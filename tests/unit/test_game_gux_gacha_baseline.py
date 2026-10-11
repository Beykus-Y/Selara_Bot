"""Post-change GUX-00 gacha baseline: protect public group button contracts."""
from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

ui = importlib.import_module("selara.presentation.handlers.text_commands")
DOC = Path(__file__).resolve().parents[2] / "docs" / "GAME_UX_GACHA_PHASE_MATRIX.md"


@pytest.mark.parametrize("banner", ["genshin", "hsr"])
def test_gacha_group_info_separates_pull_and_currency_actions_by_banner(banner: str) -> None:
    markup = ui._build_gacha_info_markup(
        owner_user_id=123456, banners=[banner], animation_enabled=True,
        use_custom_emojis=False,
    )
    assert markup is not None
    assert len(markup.inline_keyboard) == 3
    paid, currency, toggle = [row[0] for row in markup.inline_keyboard]
    assert paid.callback_data == f"gacha:buy:{banner}:u123456"
    assert currency.callback_data is not None
    assert currency.callback_data.startswith(f"gacha:currency:{banner}:")
    assert currency.callback_data.endswith(":u123456")
    assert "160" in paid.text
    assert "монет" in currency.text
    assert toggle.callback_data == "gacha:animtoggle:u123456"
    assert all(
        len(button.callback_data.encode("utf-8")) <= 64
        for row in markup.inline_keyboard for button in row
        if button.callback_data is not None
    )


def test_gacha_two_banner_info_has_no_mixed_currency_or_authorization() -> None:
    markup = ui._build_gacha_info_markup(
        owner_user_id=3, banners=["genshin", "hsr"], use_custom_emojis=False,
    )
    assert markup is not None
    assert len(markup.inline_keyboard) == 5
    callbacks = [row[0].callback_data for row in markup.inline_keyboard]
    assert callbacks[0] == "gacha:buy:genshin:u3"
    assert callbacks[2] == "gacha:buy:hsr:u3"
    assert all(item.endswith(":u3") for item in callbacks)
    for index, banner in [(0, "genshin"), (2, "hsr")]:
        action, parsed_banner, _, owner, _ = ui._parse_gacha_callback_data(callbacks[index])
        assert (action, parsed_banner, owner) == ("buy", banner, 3)


@pytest.mark.parametrize("banner,command", [
    ("genshin", "гача генш"),
    ("hsr", "гача хср"),
])
def test_gacha_empty_collection_helps_first_time_users_in_group(banner: str, command: str) -> None:
    player = SimpleNamespace(
        adventure_rank=1, xp_into_rank=0, xp_for_next_rank=100,
        total_points=0, total_primogems=0,
    )
    response = SimpleNamespace(
        player=player, rarity_counts=[], unique_cards=0,
        total_copies=0, recent_pulls=[],
    )
    text = ui._render_gacha_info_section(
        banner=banner, response=response, use_custom_emojis=False,
    )
    assert "Коллекция пока пуста" in text
    assert command in text
    assert "160" in text
    assert "Бесплатная крутка от валюты не зависит" in text


def test_gacha_baseline_document_covers_banners_safety_and_gaps() -> None:
    doc = DOC.read_text(encoding="utf-8")
    assert "Post-change" in doc
    for required in (
        "Genshin", "HSR", "group", "owner", "local", "shared",
        "subscription", "cooldown", "buy", "currency", "sell",
    ):
        assert required.casefold() in doc.casefold()
